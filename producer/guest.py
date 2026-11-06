"""Fixed guest entry points. This file is mounted read-only by the host.

This is producer code, not a verifier API. Never invoke it against a project on
the host. Preparation executes only the approved wheel set; project code starts
only at the final isolated pytest exec.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
import venv


def environment():
    return {"HOME": "/work/home", "TMPDIR": "/work/tmp", "PATH": "/work/venv/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def check_boundary(config):
    """Refuse a missing kernel boundary before any source is imported/executed."""
    if platform.machine() != 'x86_64' or os.getuid() != 65532 or os.getgid() != 65532:
        raise ValueError('unsupported_guest_identity')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    for key, expected in {'NoNewPrivs': '1', 'Seccomp': '2', 'CapEff': '0000000000000000',
                          'CapPrm': '0000000000000000', 'CapInh': '0000000000000000',
                          'CapAmb': '0000000000000000'}.items():
        if status[key].strip() != expected:
            raise ValueError('guest_boundary_mismatch')
    limits = config['limits']
    for name, expected in [('memory.max', str(limits['memory_bytes'])), ('memory.swap.max', '0'),
                            ('pids.max', str(limits['pids'])),
                            ('cpu.max', str(limits['cpus'] * 100000) + ' 100000')]:
        if Path('/sys/fs/cgroup', name).read_text().strip() != expected:
            raise ValueError('guest_resource_limit_mismatch')
    stats = os.statvfs('/work')
    work_mounts = [line.split(' - ', 1) for line in Path('/proc/self/mountinfo').read_text().splitlines()
                   if len(line.split()) > 4 and line.split()[4] == '/work']
    if (stats.f_blocks * stats.f_frsize != limits['volume_bytes'] or len(work_mounts) != 1
            or work_mounts[0][1].split()[0] != 'tmpfs'
            or resource.getrlimit(resource.RLIMIT_FSIZE) != (limits['file_bytes'], limits['file_bytes'])
            or resource.getrlimit(resource.RLIMIT_NOFILE) != (256, 256)):
        raise ValueError('guest_filesystem_limit_mismatch')


def prepare():
    config = json.loads(Path('/trusted-data/config.json').read_bytes())
    check_boundary(config)
    for name in ('source', 'output', 'home', 'tmp'):
        Path('/work', name).mkdir(mode=0o700)
    for item in config['files']:
        # Names and inventory are validated on the host; no project directory
        # traversal or .git copy is performed in the guest.
        raw = Path('/source', item['path']).read_bytes()
        if len(raw) != item['size_bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('source_inventory_mismatch')
        target = Path('/work/source', item['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    for wheel in config['wheels']:
        raw = Path('/wheels', wheel['filename']).read_bytes()
        if len(raw) != wheel['size_bytes'] or hashlib.sha256(raw).hexdigest() != wheel['sha256']:
            raise ValueError('wheel_inventory_mismatch')
    venv.EnvBuilder(with_pip=True, symlinks=True).create('/work/venv')
    result = subprocess.run(['/work/venv/bin/python', '-I', '-m', 'pip', 'install',
                             '--no-index', '--no-deps', '--require-hashes', '--only-binary=:all:',
                             '--disable-pip-version-check', '--no-cache-dir', '--no-compile',
                             '--find-links=/wheels', '-r', '/trusted-data/requirements.txt'],
                            stdin=subprocess.DEVNULL, env=environment(), check=False)
    return result.returncode


def run_tests():
    import pytest
    if pytest.__version__ != '8.4.2':
        return 70
    config = json.loads(Path('/trusted-data/config.json').read_bytes())
    check_boundary(config)
    os.chdir('/work/source')
    command = ['/work/venv/bin/python', '-I', '-m', 'pytest', '-c', '/trusted/pytest.ini',
               '--rootdir=/work/source', '--junitxml=/work/output/junit.xml', '--strict-markers',
               config['test_path']]
    os.execve(command[0], command, environment())


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {'prepare', 'test'}:
        return 70
    # No raw exception (which could contain project data) is forwarded by this
    # helper; all native pytest output is still captured separately by the host.
    try:
        return prepare() if sys.argv[1] == 'prepare' else run_tests()
    except Exception:
        return 70


if __name__ == '__main__':
    raise SystemExit(main())
