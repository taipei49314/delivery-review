"""Explicit local qualification using only built-in authored source fixtures.

This does not accept a project path and is not run by unit-test discovery.
Call ``qualify`` with an operator-approved local runtime/image/wheelhouse.
Raw test output is saved, never printed. No network acquisition occurs here.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
import tempfile

from delivery_review.contracts import canonical_bytes
from delivery_review.source_snapshot import materialize_snapshot
from .host import DockerRuntime, ProducerError, ProducerLimits, execute_pytest


FIXTURES = {
    'happy': b'def test_pass():\n    assert 2 + 2 == 4\n',
    'failure': b'def test_fail():\n    assert False, "authored native failure"\n',
    'all_skipped': b'import pytest\n@pytest.mark.skip(reason="authored")\ndef test_skip():\n    pass\n',
    'zero': b'# Authored empty test module\n',
    'timeout': b'import time\ndef test_timeout():\n    time.sleep(60)\n',
    'output_limit': b'import os\ndef test_loud():\n    os.write(1, b"x" * 2000000)\n    assert False\n',
    'abrupt_exit': b'''import os, pathlib
def test_abrupt_exit():
    pathlib.Path('/work/output/junit.xml').write_text('<testsuites name="pytest tests"><testsuite name="delivery-review" tests="1" failures="0" errors="0" skipped="0" time="0"><testcase classname="authored" name="fake" time="0"/></testsuite></testsuites>')
    os._exit(137)
''',
    'boundary': b'''import os, pathlib, socket, errno, ctypes
def test_boundary():
    assert os.getuid() == 65532
    assert not any(k.startswith(('GH_', 'GITHUB_', 'ACTIONS_', 'DOCKER_')) for k in os.environ)
    assert not pathlib.Path('/var/run/docker.sock').exists()
    for p in ('/source/attempt.txt', '/trusted/attempt.txt', '/wheels/attempt.txt', '/etc/attempt.txt'):
        try:
            pathlib.Path(p).write_text('forbidden')
        except OSError as e:
            assert e.errno in (errno.EROFS, errno.EACCES)
        else:
            raise AssertionError('writable outside private tmpfs')
    with socket.socket() as s:
        s.settimeout(.2)
        assert s.connect_ex(('1.1.1.1', 443)) != 0
    status = pathlib.Path('/proc/self/status').read_text()
    assert status.split('NoNewPrivs:')[1].splitlines()[0].strip() == '1'
    assert status.split('CapEff:')[1].splitlines()[0].strip() == '0000000000000000'
    assert status.split('Seccomp:')[1].splitlines()[0].strip() == '2'
    # Linux amd64 io_uring_setup is intentionally absent from the pinned allowlist.
    # Invalid pointers cannot be reached: the syscall must be rejected with EPERM.
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.syscall(425, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
    assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip() == '536870912'
    assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
    assert pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().strip() == '100000 100000'
    stats = os.statvfs('/work')
    assert stats.f_blocks * stats.f_frsize == 256 * 1024 * 1024
''',
    'disk_limit': b'''import errno, pathlib
def test_volume_has_kernel_cap():
    files = []
    try:
        for n in range(128):
            path = pathlib.Path('/work/tmp') / str(n)
            files.append(path)
            path.write_bytes(b'x' * 4 * 1024 * 1024)
    except OSError as e:
        assert e.errno == errno.ENOSPC
    else:
        raise AssertionError('tmpfs exceeded declared cap')
    finally:
        for path in files:
            path.unlink(missing_ok=True)
''',
    'symlink_report': b'''import os, atexit
def replace_report():
    path = '/work/output/junit.xml'
    if os.path.lexists(path): os.unlink(path)
    os.symlink('/etc/passwd', path)
atexit.register(replace_report)
def test_pass(): pass
''',
    'teardown_error': b'''import pytest
@pytest.fixture
def resource():
    yield
    assert False, 'authored teardown error'
def test_pass(resource): pass
''',
    'duplicate_phase': b'''import pytest
@pytest.fixture
def resource():
    yield
    assert False, 'authored teardown error'
def test_fail(resource): assert False, 'authored call failure'
''',
}


def _objects(source):
    objects = {}
    def add(kind, payload):
        raw = kind.encode('ascii') + b' ' + str(len(payload)).encode('ascii') + b'\0' + payload
        oid = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        objects[oid] = raw
        return oid
    base_tree = add('tree', b'100644 base.txt\0' + bytes.fromhex(add('blob', b'authored base\n')))
    def commit(tree, parent=b''):
        return add('commit', b'tree ' + tree.encode('ascii') + b'\n' + parent +
                   b'author Fixture <fixture@example.invalid> 1 +0000\n'
                   b'committer Fixture <fixture@example.invalid> 1 +0000\n\nauthored qualification\n')
    base = commit(base_tree)
    head_tree = add('tree', b'100644 test_subject.py\0' + bytes.fromhex(add('blob', source)))
    head = commit(head_tree, b'parent ' + base.encode('ascii') + b'\n')
    return objects, base, head


def qualify(destination: Path, *, runtime: DockerRuntime, image_digest: str,
            wheelhouse: Path, git_executable: str, variants: tuple[str, ...] | None = None) -> list[dict]:
    """Create a new local record directory; execute only the authored fixtures."""
    variants = tuple(FIXTURES) if variants is None else variants
    if type(variants) is not tuple or not variants or len(set(variants)) != len(variants) or any(name not in FIXTURES for name in variants):
        raise ProducerError('invalid_qualification_variants')
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    records = []
    for name in variants:
        started_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        objects, base, head = _objects(FIXTURES[name])
        record = destination / name
        record.mkdir()
        with tempfile.TemporaryDirectory(prefix='delivery-review-authored-producer-') as directory:
            snapshot = materialize_snapshot(Path(directory) / 'snapshot', repository='authored/producer-fixture',
                                            head_sha=head, base_tip_sha=base, objects=objects, git_executable=git_executable)
            limits = ProducerLimits()
            if name == 'timeout':
                limits = replace(limits, test_seconds=1)
            elif name == 'output_limit':
                limits = replace(limits, output_bytes=16384)
            elif name == 'disk_limit':
                limits = replace(limits, volume_bytes=64 * 1024 * 1024)
            result = execute_pytest(snapshot, runtime=runtime, image_digest=image_digest,
                                    wheelhouse=wheelhouse, test_path='test_subject.py', limits=limits)
        for phase in ('setup', 'test'):
            captured = getattr(result, phase)
            if captured is not None:
                for channel in ('stdout', 'stderr'):
                    (record / f'{phase}.{channel}.bin').write_bytes(getattr(captured, channel))
        if result.junit_bytes is not None:
            (record / 'junit.xml').write_bytes(result.junit_bytes)
        (record / 'source-inventory.json').write_bytes(result.source_inventory_bytes)
        observation = {'fixture': name, 'source_inventory_sha256': hashlib.sha256(result.source_inventory_bytes).hexdigest(),
                       'started_at': started_at, 'completed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       'command': list(result.command),
                       'image': image_digest, 'setup_exit': None if result.setup is None else result.setup.exit_code,
                       'test_exit': None if result.test is None else result.test.exit_code,
                       'errors': list(result.error_codes), 'stopped': result.stopped, 'cleaned': result.cleaned,
                       'junit': result.junit_observation,
                       'raw_files': {item.name: {'sha256': hashlib.sha256(item.read_bytes()).hexdigest(),
                            'size_bytes': item.stat().st_size} for item in sorted(record.iterdir())}}
        (record / 'observation.json').write_bytes(canonical_bytes(observation))
        records.append(observation)
    (destination / 'qualification.json').write_bytes(canonical_bytes({'kind': 'authored-local-producer-qualification',
        'hosted_qualification': False, 'records': records, 'docker_sha256': runtime.executable_sha256,
        'python': platform.python_version(), 'platform': platform.platform(), 'machine': platform.machine(),
        'producer_files': {item.relative_to(Path(__file__).parent).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                           for item in sorted(Path(__file__).parent.rglob('*'))
                           if item.is_file() and '__pycache__' not in item.parts}}))
    return records
