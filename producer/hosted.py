"""Fixed GitHub-hosted producer entry points; never run on a developer's PR tree."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from delivery_review.adapters.artifact_zip import read_artifact_zip
from delivery_review.contracts import canonical_bytes, loads_strict, validate_receipt
from delivery_review.evidence.store import _read
from delivery_review.providers.github_artifact_http import GitHubArtifactTransport
from delivery_review.providers.github_http import GitHubTransport
from delivery_review.providers.github_source import acquire_source
from delivery_review.source_snapshot import verify_snapshot
from producer.bootstrap import NoRedirect, request_claims, require, number, producer_path
from producer.host import DockerRuntime, ProducerError, execute_pytest, _run
from producer.platform_identity import artifact_name, collect_identity
from producer.receipt import build_receipt
from producer.source_identity import read_commit, commit_parents

DOCKER_ARCHIVE = 'https://download.docker.com/linux/static/stable/x86_64/docker-29.9.3.tgz'
DOCKER_ARCHIVE_SHA256 = '3fb5d1f13014c18cc54945bc177c58a9056a482dada031e17d12cf2e6922c973'
DOCKER_SHA256 = '3ac6daa3aff1afd1c0c673fa216fb592743c75ea406daa4ade413d933bc06f92'
BASE_IMAGE = 'python@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b'
PREDICATE_TYPE = 'https://github.com/taipei49314/delivery-review/attestation/pytest/v1'
STAGES = frozenset({'identity-token', 'trusted-checkout', 'platform-identity', 'trusted-dependencies',
    'source-acquisition', 'source-verification', 'isolated-tests', 'receipt',
    'signing-identity', 'uploaded-artifact', 'predicate'})
PRODUCER_FAILURE_CODES = frozenset({'invalid_producer_limits', 'invalid_image_digest',
    'unsupported_producer_runtime', 'invalid_docker_runtime', 'unsafe_producer_file',
    'docker_binary_mismatch', 'docker_config_not_empty', 'seccomp_profile_mismatch',
    'wheelhouse_inventory_mismatch', 'invalid_test_path', 'test_path_absent',
    'unsafe_mount_path', 'seccomp_changed_during_execution'})


def _stage(name):
    # Only literal controller phases reach the Actions log. Neither exceptions,
    # credentials, platform bodies nor guest output are diagnostic messages.
    require(type(name) is str and name in STAGES, 'invalid_producer_stage')
    print('delivery-review: producer stage ' + name, flush=True)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _save(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
    path.chmod(0o600)


def _output(values):
    require(all(re.fullmatch(r'[a-z_]+', key) and type(value) is str and 0 < len(value) <= 2048
                and not any(ord(c) < 32 or ord(c) == 127 for c in value) for key, value in values.items()),
            'invalid_host_output')
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as stream:
        stream.write(''.join(key + '=' + value + '\n' for key, value in values.items()))


def _download(url, *, hosts, limit):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == 'https' and parsed.netloc in hosts and not parsed.fragment
            and not parsed.username and not parsed.password, 'unapproved_dependency_url')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'delivery-review-producer/1'})
    with opener.open(request, timeout=30) as response:
        require(response.status == 200 and response.geturl() == url, 'dependency_transport_failed')
        raw = response.read(limit + 1)
    require(len(raw) <= limit, 'dependency_download_limit')
    return raw


def prepare_dependencies(root):
    """Download approved inert bytes before any project data is available."""
    archive = _download(DOCKER_ARCHIVE, hosts={'download.docker.com'}, limit=90 * 1024 * 1024)
    require(_sha(archive) == DOCKER_ARCHIVE_SHA256, 'docker_distribution_mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        member = bundle.getmember('docker/docker')
        require(member.isfile() and member.size <= 64 * 1024 * 1024, 'docker_binary_invalid')
        binary = bundle.extractfile(member).read(64 * 1024 * 1024 + 1)
    require(_sha(binary) == DOCKER_SHA256, 'docker_binary_mismatch')
    docker = root / 'docker'
    _save(docker, binary)
    docker.chmod(0o700)
    build_config = root / 'docker-build-config'
    build_config.mkdir(mode=0o700)
    wheelhouse = root / 'wheelhouse'
    wheelhouse.mkdir(mode=0o755)
    for wheel in loads_strict((ROOT / 'producer/wheels.json').read_bytes())['wheels']:
        metadata = loads_strict(_download(wheel['metadata_url'], hosts={'pypi.org'}, limit=1024 * 1024))
        selected = [item for item in metadata['urls'] if item.get('filename') == wheel['filename']
                    and item.get('digests', {}).get('sha256') == wheel['sha256'] and item.get('size') == wheel['size_bytes']]
        require(len(selected) == 1, 'producer_wheel_locator_unconfirmed')
        raw = _download(selected[0]['url'], hosts={'files.pythonhosted.org'}, limit=wheel['size_bytes'])
        require(len(raw) == wheel['size_bytes'] and _sha(raw) == wheel['sha256'], 'producer_wheel_mismatch')
        _save(wheelhouse / wheel['filename'], raw)
        (wheelhouse / wheel['filename']).chmod(0o444)
    host = 'unix:///var/run/docker.sock'
    base = [str(docker), '--config', str(build_config), '--host', host]
    context = root / 'trusted-build'
    context.mkdir(mode=0o700)
    _save(context / 'Dockerfile', (ROOT / 'producer/Dockerfile').read_bytes())
    for name, args in [('image-pull', ['pull', BASE_IMAGE]), ('image-build', ['build', '--network=none',
            '--iidfile', str(root / 'image-id'), str(context)])]:
        process = _run(base + args, timeout=240, max_bytes=1024 * 1024)
        _save(root / (name + '.stdout'), process.stdout)
        _save(root / (name + '.stderr'), process.stderr)
        require(process.error is None and process.exit_code == 0, 'producer_image_setup_failed')
    image = (root / 'image-id').read_text().strip()
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'invalid_built_image')
    # Buildx writes its own state below --config even without a config.json.
    # Preserve that build state; the execution boundary still requires a new,
    # empty configuration directory and independently checks the pinned CLI.
    execution_config = root / 'docker-execution-config'
    execution_config.mkdir(mode=0o700)
    runtime = DockerRuntime(docker, DOCKER_SHA256, host, execution_config)
    return runtime, image, wheelhouse


def verify_event_source(snapshot, identity):
    sha = identity['event_source_sha']
    raw = read_commit(snapshot, sha)
    parents = commit_parents(raw, sha)
    require(parents == [identity['base_tip_sha'], identity['head_sha']], 'event_source_parent_mismatch')
    return raw


def tested_source_claim(identity):
    mode = identity['source_mode']
    require(identity['producer_path'] == producer_path(mode), 'producer_source_mode_mismatch')
    return {'mode': mode, 'base_tip_sha': identity['base_tip_sha'], 'head_sha': identity['head_sha'],
        'checked_out_sha': identity['head_sha'] if mode == 'pr_head' else identity['event_source_sha'],
        'merge_parents': [] if mode == 'pr_head' else [identity['base_tip_sha'], identity['head_sha']]}


def select_test_snapshot(head_snapshot, event_snapshot, identity):
    """The two entry points choose fixed modes; caller inputs cannot select code."""
    expected = tested_source_claim(identity)
    verify_snapshot(head_snapshot)
    verify_event_source(event_snapshot, identity)
    require(head_snapshot.repository == event_snapshot.repository == identity['repository_full_name']
            and head_snapshot.head_sha == identity['head_sha']
            and event_snapshot.head_sha == identity['event_source_sha']
            and head_snapshot.base_tip_sha == event_snapshot.base_tip_sha == identity['base_tip_sha'],
            'measured_snapshot_identity_mismatch')
    selected = head_snapshot if identity['source_mode'] == 'pr_head' else event_snapshot
    require(selected.head_sha == expected['checked_out_sha'], 'selected_checkout_mismatch')
    return selected


def prepare(source_mode='pr_head'):
    producer_path(source_mode)
    require(sys.version_info[:2] == (3, 11) and platform.machine() == 'x86_64' and sys.platform == 'linux',
            'unsupported_hosted_platform')
    _stage('identity-token')
    claims = request_claims(os.environ, source_mode=source_mode)
    _stage('trusted-checkout')
    require((ROOT / '.git').is_dir() and not (ROOT / '.git').is_symlink()
            and _read(ROOT / '.git/HEAD', 1024).strip() == claims['job_workflow_sha'].encode(),
            'producer_checkout_mismatch')
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(parent.is_dir() and not parent.is_symlink(), 'invalid_runner_temporary_directory')
    root = Path(tempfile.mkdtemp(prefix='delivery-review-producer-', dir=parent))
    # Only generated host paths/IDs are exported. No project strings enter a shell.
    _output({'producer_root': str(root)})
    transport = GitHubTransport(token=os.environ.get('GH_TOKEN'))
    _stage('platform-identity')
    identity = collect_identity(claims, transport=transport, destination=root / 'platform', source_mode=source_mode)
    _save(root / 'identity.json', canonical_bytes(identity))
    _stage('trusted-dependencies')
    runtime, image, wheels = prepare_dependencies(root)
    _stage('source-acquisition')
    source = acquire_source(root / 'source', repository={'id': identity['repository_id'],
        'full_name': identity['repository_full_name']}, head_sha=identity['head_sha'], base_tip_sha=identity['base_tip_sha'],
        git_executable='/usr/bin/git', token=os.environ.get('GH_TOKEN'))
    event_source = acquire_source(root / 'event-source', repository={'id': identity['repository_id'],
        'full_name': identity['repository_full_name']}, head_sha=identity['event_source_sha'],
        base_tip_sha=identity['base_tip_sha'], git_executable='/usr/bin/git', token=os.environ.get('GH_TOKEN'))
    _stage('source-verification')
    _save(root / 'event-source.commit', verify_event_source(event_source.snapshot, identity))
    snapshot = select_test_snapshot(source.snapshot, event_source.snapshot, identity)
    _stage('isolated-tests')
    execution = execute_pytest(snapshot, runtime=runtime, image_digest=image, wheelhouse=wheels, test_path='tests',
                               source_mode=source_mode, pr_head_sha=identity['head_sha'])
    _save(root / 'tested-source.commit', execution.checked_out_commit_bytes)
    _save(root / 'tested-source-inventory.json', execution.source_inventory_bytes)
    for phase in ('setup', 'test'):
        value = getattr(execution, phase)
        if value is not None:
            _save(root / (phase + '.stdout'), value.stdout)
            _save(root / (phase + '.stderr'), value.stderr)
    _save(root / 'execution.json', canonical_bytes({'image': image, 'command': list(execution.command),
        'setup_exit': None if execution.setup is None else execution.setup.exit_code,
        'test_exit': None if execution.test is None else execution.test.exit_code,
        'errors': list(execution.error_codes), 'stopped': execution.stopped, 'cleaned': execution.cleaned,
        'source_inventory_sha256': _sha(execution.source_inventory_bytes), 'source_mode': execution.source_mode,
        'checked_out_commit_sha256': _sha(execution.checked_out_commit_bytes)}))
    source_claim = {key: identity[key] for key in ('repository_id', 'head_repository_id', 'pr_number')}
    source_claim['source'] = tested_source_claim(identity)
    producer = {'repository_id': identity['producer_repository_id'], 'workflow_path': identity['producer_path'],
                'workflow_sha': identity['producer_sha'], 'run_id': identity['run_id'],
                'attempt': identity['run_attempt'], 'job_id': identity['job_id']}
    _stage('receipt')
    receipt = build_receipt(execution, source_claim=source_claim, producer_identity=producer,
        recorded_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), receipt_id=artifact_name(identity))
    upload = root / 'upload'
    upload.mkdir(mode=0o700)
    _save(upload / 'receipt.json', canonical_bytes(receipt) + b'\n')
    _save(upload / 'junit.xml', execution.junit_bytes)
    _save(root / 'upload-digests.json', canonical_bytes({name: _sha((upload / name).read_bytes())
                                                       for name in ('receipt.json', 'junit.xml')}))
    _output({'artifact_directory': str(upload), 'artifact_name': artifact_name(identity),
             'test_exit': str(execution.test.exit_code)})
    return 0


def _saved_root():
    root = Path(os.environ['DELIVERY_REVIEW_PRODUCER_ROOT'])
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_absolute() and root.parent == parent and root.name.startswith('delivery-review-producer-')
            and root.resolve(strict=True) == root and root.is_dir() and not root.is_symlink(), 'invalid_producer_root')
    return root


def build_predicate(identity, *, artifact_id, artifact_sha256, receipt_bytes, junit_bytes):
    require(type(artifact_id) is int and 0 < artifact_id <= 2**53 - 1
            and type(artifact_sha256) is str and re.fullmatch(r'[0-9a-f]{64}', artifact_sha256),
            'invalid_upload_output')
    receipt = loads_strict(receipt_bytes)
    validate_receipt(receipt)
    require(all(receipt[key] == identity[key] for key in ('repository_id', 'head_repository_id', 'pr_number'))
        and receipt['source'] == tested_source_claim(identity)
        and receipt['execution']['command'] == ['/work/venv/bin/python', '-I', '-m', 'pytest', '-c', '/trusted/pytest.ini',
            '--rootdir=/work/source', '--junitxml=/work/output/junit.xml', '--strict-markers', 'tests']
        and receipt['producer'] == {'repository_id': identity['producer_repository_id'],
        'workflow_path': identity['producer_path'], 'workflow_sha': identity['producer_sha'],
        'run_id': identity['run_id'], 'attempt': identity['run_attempt'], 'job_id': identity['job_id']}
        and receipt['execution']['junit']['sha256'] == _sha(junit_bytes)
        and receipt['execution']['junit']['size_bytes'] == len(junit_bytes), 'upload_receipt_mismatch')
    return {**{key: identity[key] for key in ('repository_id', 'run_id', 'run_attempt', 'job_id', 'check_run_id')},
            'artifact_id': artifact_id, 'artifact_sha256': artifact_sha256,
            'receipt_sha256': _sha(receipt_bytes), 'junit_sha256': _sha(junit_bytes),
            'predicate_version': 1, 'producer_profile': 'pytest-isolated-container-v1'}


def attest():
    root = _saved_root()
    identity = loads_strict(_read(root / 'identity.json', 65536))
    _stage('signing-identity')
    claims = request_claims(os.environ, source_mode=identity['source_mode'])
    require(all(claims[key] == str(identity[other]) for key, other in [('repository_id', 'repository_id'),
        ('run_id', 'run_id'), ('run_attempt', 'run_attempt'), ('check_run_id', 'check_run_id'),
        ('job_workflow_sha', 'producer_sha'), ('sha', 'event_source_sha')]), 'signing_execution_changed')
    current = collect_identity(claims, transport=GitHubTransport(token=os.environ.get('GH_TOKEN')),
                               destination=root / 'platform-before-signing', source_mode=identity['source_mode'])
    require(current == identity, 'platform_identity_changed_before_signing')
    _stage('uploaded-artifact')
    artifact_id = number(os.environ.get('DELIVERY_REVIEW_ARTIFACT_ID'))
    digest = os.environ.get('DELIVERY_REVIEW_ARTIFACT_DIGEST')
    require(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest), 'invalid_upload_digest')
    files = {name: _read(root / 'upload' / name, 2 * 1024 * 1024) for name in ('receipt.json', 'junit.xml')}
    require({name: _sha(raw) for name, raw in files.items()} == loads_strict(_read(root / 'upload-digests.json', 4096)),
            'upload_inputs_changed')
    transport = GitHubArtifactTransport(token=os.environ.get('GH_TOKEN'))
    responses = {}
    for operation in ('artifact', 'archive'):
        captured = transport.get(operation, identity['repository_full_name'], artifact_id,
            max_bytes=25 * 1024 * 1024 if operation == 'archive' else 1024 * 1024, timeout_seconds=30)
        response = captured['response']
        require(response['status_code'] == 200 and response['complete'] is True and response['error'] is None
                and type(response['body']) is bytes, 'uploaded_artifact_unavailable')
        responses[operation] = response['body']
        _save(root / ('uploaded.' + ('zip' if operation == 'archive' else 'json')), response['body'])
    metadata = loads_strict(responses['artifact'])
    require(metadata.get('id') == artifact_id and metadata.get('name') == artifact_name(identity)
            and metadata.get('digest') == 'sha256:' + digest and metadata.get('expired') is False
            and metadata.get('workflow_run', {}).get('id') == identity['run_id']
            and metadata.get('workflow_run', {}).get('head_sha') == identity['head_sha']
            and _sha(responses['archive']) == digest, 'uploaded_artifact_mismatch')
    archive = read_artifact_zip(responses['archive'], policy_limits={'artifact_bytes': 25 * 1024 * 1024,
        'expanded_bytes': 4 * 1024 * 1024, 'archive_members': 2})
    require(dict(archive.files) == files, 'uploaded_member_mismatch')
    _stage('predicate')
    predicate = build_predicate(identity, artifact_id=artifact_id, artifact_sha256=digest,
                                receipt_bytes=files['receipt.json'], junit_bytes=files['junit.xml'])
    _save(root / 'predicate.json', canonical_bytes(predicate) + b'\n')
    _output({'predicate_path': str(root / 'predicate.json'), 'subject_digest': 'sha256:' + digest,
        'subject_name': f'https://github.com/{identity["repository_full_name"]}/actions/runs/{identity["run_id"]}/artifacts/{artifact_id}'})
    return 0


def finish():
    root = _saved_root()
    receipt = loads_strict(_read(root / 'upload/receipt.json', 2 * 1024 * 1024))
    validate_receipt(receipt)
    return 0 if receipt['execution']['test_exit'] == 0 else 1


def main():
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in {'prepare', 'prepare-merge', 'attest', 'finish'}, 'invalid_producer_command')
        return {'prepare': prepare, 'prepare-merge': lambda: prepare('github_merge'),
                'attest': attest, 'finish': finish}[sys.argv[1]]()
    except ProducerError as error:
        code = error.code if type(error.code) is str and error.code in PRODUCER_FAILURE_CODES else 'producer_control_refused'
        print('delivery-review: producer measurement or provenance step refused code=' + code, flush=True)
        return 2
    except Exception:
        # Never emit untrusted source/log/exception text into Actions commands.
        print('delivery-review: producer measurement or provenance step refused')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
