"""Authored byte/process controls, not GitHub hosted producer qualification."""
from dataclasses import replace
import io
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from producer.host import (DockerRuntime, ProducerError, ProducerLimits, _environment, _hardening,
                           _run, _runtime, _test_path, read_junit_tar, _seccomp_bytes,
                           _security_options, SECCOMP_SHA256)


def archive(raw=b'<xml/>', *, name='junit.xml', typeflag=tarfile.REGTYPE, linkname='', mode=0o600):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as stream:
        member = tarfile.TarInfo(name)
        member.type = typeflag
        member.mode = mode
        member.linkname = linkname
        member.size = len(raw)
        stream.addfile(member, io.BytesIO(raw))
    return output.getvalue()


class ProducerHostTests(unittest.TestCase):
    def test_exact_raw_report_without_extracting(self):
        raw = b'\xef\xbb\xbf<xml>\r\n</xml>'
        self.assertEqual(raw, read_junit_tar(archive(raw)))

    def test_docker_observed_ustar_shape(self):
        # Docker 29.9.3 cp observed a 2048-byte USTAR stream, not a tarfile
        # 10240-byte record. Both carry the same exact regular-file content.
        self.assertEqual(b'<xml/>', read_junit_tar(archive()[:2048]))

    def test_links_and_other_member_types_refused(self):
        for flag in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE):
            with self.subTest(flag=flag), self.assertRaises(ProducerError):
                read_junit_tar(archive(typeflag=flag, linkname='/etc/passwd'))

    def test_unsafe_member_paths_and_names_refused(self):
        for name in ('other.xml', '../junit.xml', '/junit.xml', 'out/junit.xml', './junit.xml', 'JUNIT.xml', 'junit.xml/evil'):
            with self.subTest(name=name), self.assertRaises(ProducerError):
                read_junit_tar(archive(name=name))

    def test_checksums_truncation_extra_members_and_nonzero_padding_refused(self):
        original = archive()
        changed = bytearray(original)
        changed[1] ^= 1
        for raw in (bytes(changed), original[:1024], original[:-1], original[:1024] + original,
                    original[:-1] + b'x', original[:518] + b'x' + original[519:]):
            with self.subTest(size=len(raw)), self.assertRaises(ProducerError):
                read_junit_tar(raw)

    def test_exact_report_cap_and_privileged_modes(self):
        self.assertEqual(b'x' * 10, read_junit_tar(archive(b'x' * 10), max_bytes=10))
        for raw in (archive(b'x' * 11), archive(mode=0o4600)):
            with self.assertRaises(ProducerError):
                read_junit_tar(raw, max_bytes=10)
        for maximum in (True, 0, 2097153):
            with self.assertRaises(ProducerError):
                read_junit_tar(archive(), max_bytes=maximum)

    def test_test_selector_is_one_existing_relative_path_not_pytest_arguments(self):
        inventory = {'files': [{'path': 'tests/test_one.py'}]}
        _test_path('tests', inventory)
        _test_path('tests/test_one.py', inventory)
        for value in ('-p', '../tests', '/tests', 'tests/../x', 'tests//x', 'tests\\x', 'tests::x', 'tests -p x', 'absent'):
            with self.subTest(value=value), self.assertRaises(ProducerError):
                _test_path(value, inventory)

    def test_capture_never_prints_workflow_commands_and_keeps_native_failure(self):
        with patch('builtins.print', side_effect=AssertionError('must not forward child bytes')):
            result = _run([sys.executable, '-I', '-c', "import sys; print('::error::UNTRUSTED'); sys.exit(3)"],
                          timeout=5, max_bytes=1024)
        self.assertEqual(3, result.exit_code)
        self.assertIsNone(result.error)
        self.assertIn(b'::error::UNTRUSTED', result.stdout)

    def test_output_limit_keeps_only_prefix_and_no_fake_native_exit(self):
        result = _run([sys.executable, '-I', '-c', "import os; os.write(1,b'x'*2000000)"], timeout=5, max_bytes=1024)
        self.assertEqual(1024, len(result.stdout) + len(result.stderr))
        self.assertEqual('output_limit', result.error)
        self.assertIsNone(result.exit_code)

    def test_timeout_does_not_invent_test_exit(self):
        result = _run([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], timeout=.05, max_bytes=1024)
        self.assertEqual('execution_timeout', result.error)
        self.assertIsNone(result.exit_code)

    def test_orphan_descendant_is_stopped_or_platform_execution_is_refused(self):
        if os.name != 'posix':
            # This is an executable production gate, not a skipped Linux test.
            with self.assertRaisesRegex(ProducerError, 'unsupported_producer_runtime'):
                _runtime(DockerRuntime(Path(sys.executable), 'a' * 64, 'unix:///unused', ROOT))
            return
        command = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)']);print(p.pid,flush=True)"
        result = _run([sys.executable, '-I', '-c', command], timeout=5, max_bytes=1024)
        self.assertEqual('process_descendants_refused', result.error)
        self.assertIsNone(result.exit_code)
        pid = int(result.stdout.strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            proc = Path('/proc') / str(pid) / 'stat'
            try:
                state = proc.read_text().split()[2]
            except FileNotFoundError:
                # Reaping may remove /proc/PID/stat between lookup and read.
                # Only actual disappearance is success; a live non-zombie child
                # still has to terminate before the same bounded deadline.
                break
            if state == 'Z':
                break
            time.sleep(.01)
        else:
            self.fail('background descendant still running after capture returned')

    def test_host_environment_has_no_credentials_or_injection(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'secret', 'GITHUB_TOKEN': 'secret', 'DOCKER_HOST': 'bad',
                                    'DOCKER_CONTEXT': 'bad', 'PYTHONPATH': 'bad', 'LD_PRELOAD': 'bad',
                                    'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'secret', 'HTTPS_PROXY': 'bad'}):
            self.assertLessEqual(set(_environment()), {'PATH', 'SystemRoot', 'WINDIR'})

    def test_hardening_and_keeper_read_only_volume(self):
        limits = ProducerLimits()
        for keeper in (False, True):
            args = _hardening('n', 'v', 'unused', limits, seccomp_profile=Path('/trusted/profile.json'), keeper=keeper)
            for required in ('--read-only', '--cap-drop', 'ALL', '--network', 'none', '--security-opt',
                             'no-new-privileges', '--pids-limit', '--memory', '--memory-swap', '--cpus',
                             '--ulimit', '--log-driver', '--ipc', '--init'):
                self.assertIn(required, args)
            self.assertEqual('65532:65532', args[args.index('--user') + 1])
            self.assertEqual(keeper, args[-1].endswith(',readonly'))
            self.assertIn('seccomp=' + str(Path('/trusted/profile.json')), args)

    def test_pinned_seccomp_is_default_deny_and_cannot_be_replaced(self):
        raw = _seccomp_bytes()
        self.assertEqual(SECCOMP_SHA256, hashlib.sha256(raw).hexdigest())
        profile = json.loads(raw)
        self.assertEqual('SCMP_ACT_ERRNO', profile['defaultAction'])
        self.assertEqual(1, profile['defaultErrnoRet'])
        # A daemon profile may be mode 2 while permitting everything. The exact
        # approved profile, including capability/argument constraints, is required.
        _security_options(['no-new-privileges', 'seccomp=' + json.dumps(profile)], raw)
        for candidate in ([], ['no-new-privileges'], ['no-new-privileges', 'seccomp=unconfined'],
                          ['no-new-privileges', 'seccomp=' + json.dumps({'defaultAction': 'SCMP_ACT_ALLOW'})],
                          ['no-new-privileges', 'seccomp=' + json.dumps(profile), 'label=disable']):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                _security_options(candidate, raw)
        with patch('pathlib.Path.read_bytes', return_value=b'{"defaultAction":"SCMP_ACT_ALLOW"}'):
            with self.assertRaisesRegex(ProducerError, 'seccomp_profile_mismatch'):
                _seccomp_bytes()


if __name__ == '__main__':
    unittest.main()
