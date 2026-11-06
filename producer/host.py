"""Bounded Linux Docker execution of a fixed, isolated pytest producer.

No project command is executed on the host. Docker, daemon, image and these
scripts are trusted operator inputs. Project source and test output are not.
The tmpfs local volume is kept mounted by a separate fixed, read-only keeper so
all guest processes can be stopped before copying the single report.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from delivery_review.adapters.pytest_junit import JUnitError, parse_pytest_junit
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.source_snapshot import SourceSnapshot, verify_snapshot
from .source_identity import measured_source, read_commit

HERE = Path(__file__).resolve().parent
IDLE = ('/usr/local/bin/python3', '-I', '-c', 'import time; time.sleep(1800)')
SECCOMP_SHA256 = '6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6'


class ProducerError(ContractError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProducerLimits:
    setup_seconds: int = 60
    test_seconds: int = 120
    control_seconds: int = 15
    output_bytes: int = 1024 * 1024
    junit_bytes: int = 2 * 1024 * 1024
    memory_bytes: int = 512 * 1024 * 1024
    volume_bytes: int = 256 * 1024 * 1024
    file_bytes: int = 8 * 1024 * 1024
    pids: int = 64
    cpus: int = 1


@dataclass(frozen=True)
class DockerRuntime:
    executable: Path
    executable_sha256: str
    host: str
    config_directory: Path


@dataclass(frozen=True)
class ProcessObservation:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    error: str | None


@dataclass(frozen=True)
class ProducerExecution:
    source_inventory_bytes: bytes
    image_digest: str
    test_path: str
    command: tuple[str, ...]
    setup: ProcessObservation | None
    test: ProcessObservation | None
    junit_bytes: bytes | None
    junit_observation_bytes: bytes | None
    error_codes: tuple[str, ...]
    stopped: bool
    cleaned: bool
    source_mode: str = 'pr_head'
    pr_head_sha: str | None = None
    checked_out_commit_bytes: bytes | None = None

    @property
    def junit_observation(self):
        return None if self.junit_observation_bytes is None else loads_strict(self.junit_observation_bytes)


def _need(condition, code):
    if not condition:
        raise ProducerError(code)


def _regular(path):
    result = path.lstat()
    _need(stat.S_ISREG(result.st_mode) and result.st_nlink == 1, 'unsafe_producer_file')
    return result


def _environment():
    # Docker configuration is explicit and checked empty. In particular no
    # DOCKER_CONTEXT/HOST, proxies, loader injection, GitHub or OIDC credentials.
    return {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR') if key in os.environ}


def _run(argv, *, timeout, max_bytes):
    """Capture bounded bytes without ever forwarding child text to the console."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=_environment(), shell=False,
                                   start_new_session=os.name != 'nt')
    except OSError:
        return ProcessObservation(None, b'', b'', 'process_unavailable')
    output = [bytearray(), bytearray()]
    total = 0
    overflow = threading.Event()
    lock = threading.Lock()

    def read(pipe, index):
        nonlocal total
        try:
            while chunk := pipe.read1(65536):
                with lock:
                    remaining = max(0, max_bytes - total)
                    output[index].extend(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        overflow.set()
        except (OSError, ValueError):
            overflow.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=read, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    error = None
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            error = 'output_limit' if overflow.is_set() else 'execution_timeout'
            try:
                if os.name == 'nt':
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.01)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        error = 'process_stop_unconfirmed'
    if os.name != 'nt':
        # A successful direct child exit does not imply its descendants exited.
        # They may still hold the pipes or continue without inherited stdio.
        try:
            os.killpg(process.pid, signal.SIGKILL)
            if error is None:
                error = 'process_descendants_refused'
        except ProcessLookupError:
            pass
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        error = 'process_output_incomplete'
    if overflow.is_set() and error is None:
        error = 'output_limit'
    return ProcessObservation(process.returncode if error is None else None,
                              bytes(output[0]), bytes(output[1]), error)


def read_junit_tar(raw: bytes, *, max_bytes=2 * 1024 * 1024) -> bytes:
    """Accept one regular USTAR junit.xml; no extraction or link following."""
    _need(type(max_bytes) is int and 0 < max_bytes <= 2 * 1024 * 1024, 'invalid_tar_limit')
    _need(type(raw) is bytes and 1536 <= len(raw) <= max_bytes + 65536 and len(raw) % 512 == 0,
          'unsafe_junit_tar')
    header = raw[:512]
    def octal(value):
        text = value.rstrip(b'\0 ').lstrip(b' ')
        _need(re.fullmatch(b'[0-7]+', text) is not None, 'unsafe_junit_tar')
        return int(text, 8)
    checksum = octal(header[148:156])
    _need(checksum == sum(header[:148]) + 8 * 32 + sum(header[156:]), 'unsafe_junit_tar')
    _need(header[:100] == b'junit.xml' + b'\0' * 91 and header[156:157] in (b'0', b'\0')
          and not header[157:257].strip(b'\0') and not header[345:500].strip(b'\0')
          and header[257:265] in (b'ustar\x0000', b'ustar  \x00'), 'unsafe_junit_tar')
    size = octal(header[124:136])
    mode = octal(header[100:108])
    _need(size <= max_bytes and not mode & 0o7000, 'unsafe_junit_tar')
    end = 512 + size
    padded_end = 512 + ((size + 511) // 512) * 512
    _need(padded_end + 1024 <= len(raw) and not raw[end:].strip(b'\0'), 'unsafe_junit_tar')
    return raw[512:end]


def _test_path(value, inventory):
    _need(type(value) is str and len(value) <= 240 and
          re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*', value) is not None and
          all(segment not in {'', '.', '..'} and not segment.startswith('-') for segment in value.split('/')),
          'invalid_test_path')
    _need(any(item['path'] == value or item['path'].startswith(value + '/') for item in inventory['files']),
          'test_path_absent')


def _runtime(runtime):
    _need(type(runtime) is DockerRuntime and os.name == 'posix', 'unsupported_producer_runtime')
    _need(runtime.executable.is_absolute() and runtime.config_directory.is_absolute(), 'invalid_docker_runtime')
    _regular(runtime.executable)
    _need(re.fullmatch(r'[0-9a-f]{64}', runtime.executable_sha256) is not None and
          hashlib.sha256(runtime.executable.read_bytes()).hexdigest() == runtime.executable_sha256,
          'docker_binary_mismatch')
    _need(re.fullmatch(r'unix:///[A-Za-z0-9_./-]+', runtime.host) is not None and
          '..' not in runtime.host.split('/'), 'invalid_docker_runtime')
    _need(runtime.config_directory.is_dir() and not runtime.config_directory.is_symlink() and
          not list(runtime.config_directory.iterdir()), 'docker_config_not_empty')
    return [str(runtime.executable), '--config', str(runtime.config_directory), '--host', runtime.host]


def _mount(source, destination):
    _need(source.is_absolute() and ',' not in str(source) and '\n' not in str(source), 'unsafe_mount_path')
    return f'type=bind,src={source},dst={destination},readonly,bind-recursive=disabled'


def _seccomp_bytes():
    path = HERE / 'seccomp' / 'moby-default.json'
    _need(_regular(path).st_size <= 65536, 'seccomp_profile_mismatch')
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == SECCOMP_SHA256, 'seccomp_profile_mismatch')
    return raw


def _security_options(value, profile):
    # Docker CLI sends the profile's JSON, not the local filename, to the daemon.
    # Compare parsed exact content after Docker's insignificant whitespace rewrite.
    _need(type(value) is list and len(value) == 2 and value.count('no-new-privileges') == 1,
          'container_configuration_mismatch')
    options = [item for item in value if type(item) is str and item.startswith('seccomp=')]
    _need(len(options) == 1 and loads_strict(options[0][8:].encode()) == loads_strict(profile),
          'container_configuration_mismatch')


def _hardening(name, volume, image, limits, *, seccomp_profile, keeper=False):
    return ['create', '--name', name, '--pull=never', '--user', '65532:65532', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--security-opt', 'seccomp=' + str(seccomp_profile),
            '--pids-limit', str(limits.pids), '--memory', str(limits.memory_bytes),
            '--memory-swap', str(limits.memory_bytes), '--cpus', str(limits.cpus),
            '--ulimit', f'fsize={limits.file_bytes}:{limits.file_bytes}', '--ulimit', 'nofile=256:256',
            '--log-driver', 'none', '--ipc', 'none', '--init', '--env', 'HOME=/work/home',
            '--env', 'TMPDIR=/work/tmp', '--mount',
            f'type=volume,src={volume},dst=/work,volume-nocopy' + (',readonly' if keeper else '')]


def _inspect_config(document, image, volume, limits, *, seccomp_bytes, keeper):
    _need(type(document) is list and len(document) == 1, 'container_configuration_mismatch')
    value = document[0]
    host, config = value['HostConfig'], value['Config']
    mounts = value['Mounts']
    _security_options(host['SecurityOpt'], seccomp_bytes)
    _need(value['Image'] == image and config['User'] == '65532:65532' and host['ReadonlyRootfs'] is True
          and host['NetworkMode'] == 'none' and host['Privileged'] is False and host['PidMode'] == ''
          and host['IpcMode'] == 'none' and host['CapDrop'] == ['ALL'] and not host['CapAdd']
          and host['PidsLimit'] == limits.pids
          and host['Memory'] == limits.memory_bytes and host['MemorySwap'] == limits.memory_bytes
          and host['NanoCpus'] == limits.cpus * 1000000000 and host['LogConfig']['Type'] == 'none'
          and config['Entrypoint'] in (None, []) and config['Cmd'] == list(IDLE),
          'container_configuration_mismatch')
    work = [item for item in mounts if item['Destination'] == '/work']
    _need(len(work) == 1 and work[0]['Type'] == 'volume' and work[0]['Name'] == volume and
          work[0]['RW'] is not keeper, 'container_configuration_mismatch')
    _need(all(item['Destination'] == '/work' or item['Type'] == 'bind' and item['RW'] is False for item in mounts),
          'container_configuration_mismatch')


def execute_pytest(snapshot: SourceSnapshot, *, runtime: DockerRuntime, image_digest: str,
                   wheelhouse: Path, test_path: str, limits=ProducerLimits(), source_mode='pr_head',
                   pr_head_sha=None) -> ProducerExecution:
    """Execute the fixed profile; failures retain observations, never readiness."""
    _need(type(limits) is ProducerLimits, 'invalid_producer_limits')
    for item in fields(limits):
        value = getattr(limits, item.name)
        _need(type(value) is int and 0 < value <= getattr(ProducerLimits(), item.name), 'invalid_producer_limits')
    _need(type(image_digest) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', image_digest) is not None,
          'invalid_image_digest')
    checked_out_commit_bytes = read_commit(snapshot, snapshot.head_sha)
    inventory = snapshot.inventory
    measured_source(inventory=inventory, source_mode=source_mode, pr_head_sha=pr_head_sha,
                    checked_out_commit_bytes=checked_out_commit_bytes)
    _test_path(test_path, inventory)
    base = _runtime(runtime)
    seccomp_bytes = _seccomp_bytes()
    wheels = json.loads((HERE / 'wheels.json').read_bytes())['wheels']
    wheelhouse = Path(wheelhouse).absolute()
    _need(wheelhouse.is_dir() and not wheelhouse.is_symlink() and
          {item.name for item in wheelhouse.iterdir()} == {item['filename'] for item in wheels}, 'wheelhouse_inventory_mismatch')
    for wheel in wheels:
        path = wheelhouse / wheel['filename']
        _need(_regular(path).st_size == wheel['size_bytes'] and
              hashlib.sha256(path.read_bytes()).hexdigest() == wheel['sha256'], 'wheelhouse_inventory_mismatch')
    setup = test = junit = observation = None
    errors = []
    stopped = cleaned = False
    identifier = 'delivery-review-' + uuid.uuid4().hex
    container, keeper, volume = identifier + '-test', identifier + '-keeper', identifier + '-work'
    command = ('/work/venv/bin/python', '-I', '-m', 'pytest', '-c', '/trusted/pytest.ini',
               '--rootdir=/work/source', '--junitxml=/work/output/junit.xml', '--strict-markers', test_path)

    def docker(args, *, timeout=None, max_bytes=262144):
        return _run(base + args, timeout=timeout or limits.control_seconds, max_bytes=max_bytes)

    def control(args):
        result = docker(args)
        _need(result.error is None and result.exit_code == 0, 'docker_control_failed')
        return result.stdout

    # Separate, trusted configuration directory, never a writable mount.
    with tempfile.TemporaryDirectory(prefix='delivery-review-producer-') as temporary:
        data = Path(temporary)
        data.chmod(0o755)
        # SourceSnapshot files are deliberately 0600. Stage only their bounded,
        # verified bytes with read-only permissions for the container UID; never
        # change the source snapshot's permissions or mount its .git database.
        staged_source = data / 'source'
        staged_source.mkdir(mode=0o755)
        for item in inventory['files']:
            with (snapshot.root / item['path']).open('rb') as stream:
                raw = stream.read(item['size_bytes'] + 1)
            _need(len(raw) == item['size_bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'],
                  'source_inventory_mismatch')
            target = staged_source / item['path']
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            target.write_bytes(raw)
            target.chmod(0o444)
        (data / 'config.json').write_bytes(canonical_bytes({'files': inventory['files'], 'wheels': wheels,
            'test_path': test_path, 'limits': {item.name: getattr(limits, item.name) for item in fields(limits)}}))
        (data / 'requirements.txt').write_bytes((HERE / 'requirements.txt').read_bytes())
        (data / 'seccomp.json').write_bytes(seccomp_bytes)
        (data / 'config.json').chmod(0o444)
        (data / 'requirements.txt').chmod(0o444)
        (data / 'seccomp.json').chmod(0o444)
        try:
            control(['volume', 'create', '--driver', 'local', '--opt', 'type=tmpfs', '--opt', 'device=tmpfs',
                     '--opt', f'o=size={limits.volume_bytes},uid=65532,gid=65532,mode=0700', volume])
            volume_info = json.loads(control(['volume', 'inspect', volume]))
            _need(len(volume_info) == 1 and volume_info[0]['Driver'] == 'local' and
                  volume_info[0]['Options'] == {'type': 'tmpfs', 'device': 'tmpfs',
                  'o': f'size={limits.volume_bytes},uid=65532,gid=65532,mode=0700'}, 'volume_configuration_mismatch')
            for name, is_keeper in ((keeper, True), (container, False)):
                args = _hardening(name, volume, image_digest, limits,
                                  seccomp_profile=data / 'seccomp.json', keeper=is_keeper)
                if not is_keeper:
                    for source, target in ((staged_source, '/source'), (HERE, '/trusted'),
                                           (wheelhouse, '/wheels'), (data, '/trusted-data')):
                        args += ['--mount', _mount(source, target)]
                control(args + [image_digest, *IDLE])
                _inspect_config(json.loads(control(['inspect', name])), image_digest, volume, limits,
                                seccomp_bytes=seccomp_bytes, keeper=is_keeper)
                control(['start', name])
            setup = docker(['exec', container, '/usr/local/bin/python3', '-I', '/trusted/guest.py', 'prepare'],
                           timeout=limits.setup_seconds, max_bytes=limits.output_bytes)
            if setup.error:
                errors.append('setup_' + setup.error)
            elif setup.exit_code != 0:
                errors.append('setup_failed')
            else:
                test = docker(['exec', container, '/work/venv/bin/python', '-I', '/trusted/guest.py', 'test'],
                              timeout=limits.test_seconds, max_bytes=limits.output_bytes)
                if test.error:
                    errors.append('test_' + test.error)
                elif test.exit_code not in {0, 1, 2, 3, 4, 5}:
                    errors.append('unexpected_test_exit')
        except (ProducerError, OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(exc.code if isinstance(exc, ProducerError) else 'producer_control_invalid')
        finally:
            # A stopped guest cannot keep a background writer racing the copy.
            docker(['kill', container])
            state_result = docker(['inspect', '--format', '{{json .State}}', container])
            try:
                state = json.loads(state_result.stdout)
                stopped = (state_result.error is None and state_result.exit_code == 0 and state['Running'] is False
                           and state['Pid'] == 0 and state['Paused'] is False and state['Restarting'] is False)
                if state.get('OOMKilled') is True:
                    errors.append('guest_oom_killed')
            except (ValueError, KeyError, TypeError):
                stopped = False
            if not stopped:
                errors.append('guest_stop_unconfirmed')
            elif test is not None:
                copied = docker(['cp', keeper + ':/work/output/junit.xml', '-'], max_bytes=limits.junit_bytes + 65536)
                if copied.error is not None or copied.exit_code != 0:
                    errors.append('junit_copy_unavailable')
                else:
                    try:
                        junit = read_junit_tar(copied.stdout, max_bytes=limits.junit_bytes)
                        parsed = parse_pytest_junit(junit)
                        observation = canonical_bytes(parsed)
                        if not parsed['valid']:
                            errors.append('invalid_junit_observation')
                    except (ProducerError, JUnitError) as exc:
                        errors.append(exc.code)
            removed = [docker(['rm', '--force', name]) for name in (container, keeper)]
            removed.append(docker(['volume', 'rm', volume]))
            cleaned = all(item.exit_code == 0 and item.error is None for item in removed)
            if not cleaned:
                errors.append('producer_cleanup_unconfirmed')
    try:
        verify_snapshot(snapshot)
        _need(_seccomp_bytes() == seccomp_bytes, 'seccomp_changed_during_execution')
    except ContractError:
        errors.append('source_changed_during_execution')
    return ProducerExecution(snapshot.inventory_bytes, image_digest, test_path, command, setup, test, junit,
                             observation, tuple(errors), stopped, cleaned, source_mode, pr_head_sha, checked_out_commit_bytes)
