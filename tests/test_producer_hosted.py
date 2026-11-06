"""Authored host dependency controls, not a hosted execution qualification."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from producer.bootstrap import BootstrapError
from producer.host import DockerRuntime, ProcessObservation, ProducerError, _runtime
from producer.hosted import prepare_dependencies


class ProducerHostedTests(unittest.TestCase):
    def dependency_fixture(self, root, stack, *, build_exit=0):
        binary, wheel = b'authored Docker executable bytes', b'authored wheel bytes'
        binary_sha = hashlib.sha256(binary).hexdigest()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
            member = tarfile.TarInfo('docker/docker')
            member.size = len(binary)
            bundle.addfile(member, io.BytesIO(binary))
        archive = archive.getvalue()
        trusted = root / 'trusted' / 'producer'
        trusted.mkdir(parents=True)
        (trusted / 'Dockerfile').write_bytes(b'FROM authored@example\n')
        metadata_url = 'https://pypi.org/pypi/authored/1/json'
        wheel_url = 'https://files.pythonhosted.org/authored.whl'
        wheel_sha = hashlib.sha256(wheel).hexdigest()
        (trusted / 'wheels.json').write_text(json.dumps({'wheels': [{'filename': 'authored.whl',
            'metadata_url': metadata_url, 'sha256': wheel_sha, 'size_bytes': len(wheel)}]}))
        metadata = json.dumps({'urls': [{'filename': 'authored.whl', 'url': wheel_url,
            'digests': {'sha256': wheel_sha}, 'size': len(wheel)}]}).encode()
        downloads = {'https://download.docker.com/authored.tgz': archive,
                     metadata_url: metadata, wheel_url: wheel}
        commands = []

        def run(argv, **kwargs):
            commands.append(argv)
            config = Path(argv[argv.index('--config') + 1])
            self.assertEqual('docker-build-config', config.name)
            if 'build' in argv:
                self.assertEqual([], list(config.iterdir()))
                (config / 'buildx').mkdir()
                (config / 'buildx/.lock').write_bytes(b'authored build state')
                Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + 'a' * 64)
            return ProcessObservation(build_exit if 'build' in argv else 0,
                                      b'authored build output', b'', None)

        for target, value in [('ROOT', trusted.parent), ('DOCKER_SHA256', binary_sha),
                              ('DOCKER_ARCHIVE', 'https://download.docker.com/authored.tgz'),
                              ('DOCKER_ARCHIVE_SHA256', hashlib.sha256(archive).hexdigest())]:
            stack.enter_context(patch('producer.hosted.' + target, value))
        stack.enter_context(patch('producer.hosted._download', side_effect=lambda url, **kwargs: downloads[url]))
        stack.enter_context(patch('producer.hosted._run', side_effect=run))
        execution = root / 'execution'
        execution.mkdir()
        return execution, commands, binary_sha

    def test_build_state_is_preserved_and_execution_config_starts_empty(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root, commands, binary_sha = self.dependency_fixture(Path(directory), stack)
            runtime, image, wheels = prepare_dependencies(root)
            self.assertEqual('sha256:' + 'a' * 64, image)
            self.assertEqual(root / 'wheelhouse', wheels)
            self.assertEqual(binary_sha, runtime.executable_sha256)
            self.assertEqual(2, len(commands))
            self.assertEqual(root / 'docker-execution-config', runtime.config_directory)
            self.assertEqual([], list(runtime.config_directory.iterdir()))
            self.assertEqual(b'authored build state', (root / 'docker-build-config/buildx/.lock').read_bytes())
            if os.name == 'posix':
                self.assertEqual(str(runtime.config_directory), _runtime(runtime)[2])
                # The existing check must still refuse either reused build state
                # or a newly contaminated execution directory.
                with self.assertRaisesRegex(ProducerError, 'docker_config_not_empty'):
                    _runtime(DockerRuntime(runtime.executable, binary_sha, runtime.host, root / 'docker-build-config'))
                (runtime.config_directory / 'config.json').write_text('{}')
                with self.assertRaisesRegex(ProducerError, 'docker_config_not_empty'):
                    _runtime(runtime)
            else:
                with self.assertRaisesRegex(ProducerError, 'unsupported_producer_runtime'):
                    _runtime(runtime)

    def test_failed_build_keeps_original_output_without_execution_runtime(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root, _, _ = self.dependency_fixture(Path(directory), stack, build_exit=3)
            with self.assertRaisesRegex(BootstrapError, 'producer_image_setup_failed'):
                prepare_dependencies(root)
            self.assertEqual(b'authored build output', (root / 'image-build.stdout').read_bytes())
            self.assertTrue((root / 'docker-build-config/buildx/.lock').is_file())
            self.assertFalse((root / 'docker-execution-config').exists())


if __name__ == '__main__':
    unittest.main()
