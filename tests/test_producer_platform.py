"""Authored OIDC/API/host controls; no signed or hosted qualification claim."""
import base64
from copy import deepcopy
import hashlib
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from delivery_review.contracts import canonical_bytes
from producer.bootstrap import (AUDIENCE, BootstrapError, PRODUCER_PATH, PRODUCER_REPOSITORY,
    PRODUCER_REPOSITORY_ID, request_claims, validate_claims)
from producer.platform_identity import artifact_name, collect_identity, derive_identity
from producer.hosted import build_predicate, verify_event_source, _stage, main
from delivery_review.source_snapshot import materialize_snapshot
import test_source_snapshot as source_fixtures
import test_producer_receipt as receipt_fixtures
from test_pytest_junit import document


def fixture():
    claims = {'iss': 'https://token.actions.githubusercontent.com', 'aud': AUDIENCE,
              'iat': 1000, 'nbf': 1000, 'exp': 1600, 'repository': 'authored/reference',
              'repository_id': '9001', 'run_id': '1001', 'run_attempt': '2', 'check_run_id': '3001',
              'sha': 'c' * 40, 'ref': 'refs/pull/7/merge', 'job_workflow_sha': 'd' * 40,
              'job_workflow_ref': PRODUCER_REPOSITORY + '/' + PRODUCER_PATH + '@' + 'd' * 40,
              'event_name': 'pull_request', 'runner_environment': 'github-hosted'}
    environment = {'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_API_URL': 'https://api.github.com',
                   'RUNNER_ENVIRONMENT': 'github-hosted', 'GITHUB_EVENT_NAME': 'pull_request'}
    for key, variable in [('repository', 'GITHUB_REPOSITORY'), ('repository_id', 'GITHUB_REPOSITORY_ID'),
            ('run_id', 'GITHUB_RUN_ID'), ('run_attempt', 'GITHUB_RUN_ATTEMPT'), ('sha', 'GITHUB_SHA'), ('ref', 'GITHUB_REF')]:
        environment[variable] = claims[key]
    repository = {'id': 9001, 'full_name': 'authored/reference'}
    run = {'id': 1001, 'run_attempt': 2, 'event': 'pull_request', 'head_sha': 'b' * 40,
           'repository': repository, 'head_repository': repository, 'workflow_id': 101, 'check_suite_id': 401,
           'referenced_workflows': [{'path': claims['job_workflow_ref'], 'sha': claims['job_workflow_sha']}]}
    url = 'https://github.com/authored/reference/actions/runs/1001/job/2001'
    data = {'repository': repository,
        'producer_repository': {'id': PRODUCER_REPOSITORY_ID, 'full_name': PRODUCER_REPOSITORY},
        'pull_request': {'number': 7, 'state': 'open', 'head': {'repo': repository, 'sha': 'b' * 40},
                         'base': {'repo': repository, 'sha': 'a' * 40}},
        'merge_ref': {'ref': 'refs/pull/7/merge', 'url': 'https://api.github.com/repos/authored/reference/git/refs/pull/7/merge',
            'object': {'type': 'commit', 'sha': 'c' * 40,
                       'url': 'https://api.github.com/repos/authored/reference/git/commits/' + 'c' * 40}},
        'run': run, 'attempt': deepcopy(run),
        'jobs': [{'id': 2001, 'run_id': 1001, 'head_sha': 'b' * 40, 'name': 'pytest-linux',
                  'status': 'in_progress', 'conclusion': None, 'html_url': url,
                  'run_url': 'https://api.github.com/repos/authored/reference/actions/runs/1001',
                  'check_run_url': 'https://api.github.com/repos/authored/reference/check-runs/3001'}],
        'check': {'id': 3001, 'head_sha': 'b' * 40, 'name': 'pytest-linux', 'app': {'id': 15368},
                  'check_suite': {'id': 401}, 'status': 'in_progress', 'details_url': url}}
    return claims, environment, deepcopy(data)


class Response:
    def __init__(self, raw, url):
        self.raw, self.url = raw, url
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return 200
    def geturl(self): return self.url
    def read(self, maximum): return self.raw[:maximum]


class ProducerPlatformTests(unittest.TestCase):
    def test_diagnostics_accept_only_fixed_stages_and_never_exception_text(self):
        with patch('builtins.print') as output:
            _stage('platform-identity')
            output.assert_called_once_with('delivery-review: producer stage platform-identity', flush=True)
        for value in ('AUTHORED_SECRET', '::error::forged', 'platform-identity\n::error::forged', [], None):
            with self.subTest(value=value), patch('builtins.print') as output, self.assertRaises(BootstrapError):
                _stage(value)
            output.assert_not_called()
        with patch('producer.hosted.sys.argv', ['hosted.py', 'prepare']), \
                patch('producer.hosted.prepare', side_effect=BootstrapError('AUTHORED_SECRET')), \
                patch('builtins.print') as output:
            self.assertEqual(2, main())
        output.assert_called_once_with('delivery-review: producer measurement or provenance step refused')

    def test_oidc_current_hosted_identity_and_full_sha_pin(self):
        claims, environment, _ = fixture()
        result = validate_claims(claims, environment, now=1100)
        self.assertEqual('d' * 40, result['job_workflow_sha'])
        self.assertNotIn('iss', result)
        for key, value in [('iss', 'https://evil.invalid'), ('aud', 'different'), ('exp', 1000),
                ('iat', 1400), ('nbf', 1400), ('event_name', 'pull_request_target'),
                ('runner_environment', 'self-hosted'), ('job_workflow_ref', PRODUCER_REPOSITORY + '/' + PRODUCER_PATH + '@main'),
                ('job_workflow_sha', 'd' * 7), ('repository_id', 9001), ('check_run_id', True),
                ('run_attempt', '1'), ('sha', 'b' * 40), ('ref', 'refs/heads/main')]:
            changed = deepcopy(claims); changed[key] = value
            with self.subTest(key=key), self.assertRaises((BootstrapError, TypeError)):
                validate_claims(changed, environment, now=1100)

    def test_oidc_fetch_is_fixed_https_no_proxy_and_never_persists_bearers(self):
        claims, environment, _ = fixture()
        environment.update(ACTIONS_ID_TOKEN_REQUEST_URL='https://pipelines.actions.githubusercontent.com/job/idtoken?api-version=2.0',
                           ACTIONS_ID_TOKEN_REQUEST_TOKEN='AUTHORED_SECRET_TOKEN')
        encode = lambda value: base64.urlsafe_b64encode(canonical_bytes(value)).decode().rstrip('=')
        jwt = encode({'alg': 'RS256', 'kid': 'authored'}) + '.' + encode(claims) + '.c2lnbmF0dXJl'
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return Response(canonical_bytes({'value': jwt}), request.full_url)
        with patch('producer.bootstrap.urllib.request.build_opener', return_value=Opener()), \
                patch('pathlib.Path.open', side_effect=AssertionError('no persistence during token acquisition')):
            result = request_claims(environment, now=1100)
        self.assertEqual('3001', result['check_run_id'])
        self.assertEqual(1, len(requests))
        self.assertEqual('Bearer AUTHORED_SECRET_TOKEN', requests[0].get_header('Authorization'))
        self.assertTrue(requests[0].full_url.endswith('&audience=' + AUDIENCE))
        self.assertNotIn(jwt, json.dumps(result))
        for endpoint in ('http://pipelines.actions.githubusercontent.com/job', 'https://evil.invalid/job',
                'https://a.actions.githubusercontent.com.evil.invalid/job', 'https://a.actions.githubusercontent.com:444/job',
                'https://user@a.actions.githubusercontent.com/job', 'https://a.actions.githubusercontent.com/job#fragment',
                'https://a.actions.githubusercontent.com/job?audience=wrong'):
            changed = dict(environment, ACTIONS_ID_TOKEN_REQUEST_URL=endpoint)
            with self.subTest(endpoint=endpoint), patch('producer.bootstrap.urllib.request.build_opener') as network, \
                    self.assertRaises(BootstrapError):
                request_claims(changed, now=1100)
            network.assert_not_called()

    def test_job_and_check_ids_are_bound_by_explicit_platform_relation(self):
        claims, _, data = fixture()
        result = derive_identity(claims, **data)
        self.assertEqual(2001, result['job_id'])
        self.assertEqual(3001, result['check_run_id'])
        self.assertEqual('delivery-review-1001-2-3001', artifact_name(result))
        for mutator in [lambda d: d['jobs'].append(deepcopy(d['jobs'][0])),
                        lambda d: d['jobs'][0].update(check_run_url='https://api.github.com/repos/authored/reference/check-runs/2001'),
                        lambda d: d['jobs'][0].update(run_id=1000),
                        lambda d: d['jobs'][0].update(run_attempt=1),
                        lambda d: d['jobs'][0].update(run_attempt=True),
                        lambda d: d['check'].update(conclusion='success'),
                        lambda d: d['check']['app'].update(id=999),
                        lambda d: d['check']['check_suite'].update(id=999),
                        lambda d: d['check'].update(details_url='https://github.com/authored/reference/actions/runs/1001/job/999')]:
            changed = deepcopy(data); mutator(changed)
            with self.assertRaises(BootstrapError): derive_identity(claims, **changed)

    def test_source_run_attempt_and_producer_substitutions_fail(self):
        claims, _, data = fixture()
        for mutator in [lambda d: d['pull_request']['head']['repo'].update(id=9002),
                        lambda d: d['merge_ref']['object'].update(sha='e' * 40),
                        lambda d: d['pull_request']['head'].update(sha='e' * 40),
                        lambda d: d['attempt'].update(run_attempt=1),
                        lambda d: d['run'].update(event='push'),
                        lambda d: d['producer_repository'].update(id=9002),
                        lambda d: d['run'].update(referenced_workflows=[]),
                        lambda d: d['run']['referenced_workflows'].append({'path': claims['job_workflow_ref'], 'sha': 'e' * 40}),
                        lambda d: d['run']['referenced_workflows'].append({'path': PRODUCER_REPOSITORY + '/' + PRODUCER_PATH + '@' + 'e' * 40, 'sha': 'e' * 40}),
                        lambda d: d['attempt']['referenced_workflows'][0].update(sha='e' * 40)]:
            changed = deepcopy(data); mutator(changed)
            with self.assertRaises(BootstrapError): derive_identity(claims, **changed)

    def test_collector_retains_original_api_bytes_and_refuses_partial_job_listing(self):
        claims, _, data = fixture()
        by_route = {'/repos/authored/reference': data['repository'],
            '/repos/' + PRODUCER_REPOSITORY: data['producer_repository'],
            '/repos/authored/reference/pulls/7': data['pull_request'],
            '/repos/authored/reference/git/ref/pull/7/merge': data['merge_ref'],
            '/repos/authored/reference/actions/runs/1001': data['run'],
            '/repos/authored/reference/actions/runs/1001/attempts/2': data['attempt'],
            '/repos/authored/reference/check-runs/3001': data['check'],
            '/repos/authored/reference/actions/runs/1001/attempts/2/jobs?page=1&per_page=100':
                {'total_count': 1, 'jobs': data['jobs']}}
        class Transport:
            def get(self, path, **kwargs):
                return {'status_code': 200, 'complete': True, 'error': None, 'response_headers': {},
                        'body': json.dumps(by_route[path], indent=1).encode() + b'\r\n'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = collect_identity(claims, transport=Transport(), destination=root / 'first')
            self.assertEqual(2001, result['job_id'])
            self.assertEqual(json.dumps(data['run'], indent=1).encode() + b'\r\n', (root / 'first/run.json').read_bytes())
            self.assertEqual(json.dumps(data['merge_ref'], indent=1).encode() + b'\r\n',
                             (root / 'first/merge_ref.json').read_bytes())
            with patch('producer.platform_identity.derive_identity', side_effect=BootstrapError('AUTHORED_SECRET')), \
                    patch('builtins.print') as output, self.assertRaises(BootstrapError):
                collect_identity(claims, transport=Transport(), destination=root / 'unknown-failure')
            output.assert_called_once_with('delivery-review: platform identity refused code=platform_identity_refused', flush=True)
            self.assertEqual({'code': 'platform_identity_refused'}, json.loads((root / 'unknown-failure/failure.json').read_bytes()))
            by_route[next(key for key in by_route if '/jobs?' in key)]['total_count'] = 2
            with self.assertRaisesRegex(BootstrapError, 'job_pagination_incomplete'):
                collect_identity(claims, transport=Transport(), destination=root / 'partial')

    def test_failed_http_response_keeps_original_bytes_and_only_safe_diagnostics(self):
        claims, _, _ = fixture()
        raw = b'{"message":"AUTHORED_SECRET ::error::not a workflow command"}\r\n'
        class Transport:
            def get(self, path, **kwargs):
                return {'status_code': 403, 'complete': True, 'error': {'code': 'resource_unavailable'},
                        'response_headers': {'content-type': 'application/json'}, 'body': raw}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'failed'
            with patch('builtins.print') as output, self.assertRaisesRegex(BootstrapError, 'platform_response_unavailable'):
                collect_identity(claims, transport=Transport(), destination=root)
            output.assert_called_once_with('delivery-review: platform response refused route=repository status=403 code=resource_unavailable', flush=True)
            self.assertEqual(raw, (root / 'repository.json').read_bytes())
            response = json.loads((root / 'repository.response.json').read_bytes())
            self.assertEqual(403, response['status_code'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), response['sha256'])
            self.assertEqual({'code': 'resource_unavailable'}, response['error'])
            self.assertFalse((root / 'manifest.json').exists())

    def test_predicate_binds_actual_upload_and_exact_measured_members(self):
        claims, _, data = fixture(); identity = derive_identity(claims, **data)
        helper = receipt_fixtures.ProducerReceiptTests(); helper.setUp()
        helper.identity.update(repository_id=PRODUCER_REPOSITORY_ID, workflow_path=PRODUCER_PATH, attempt=2)
        receipt = helper.receipt(helper.execute(document()))
        receipt['execution']['command'] = ['/work/venv/bin/python', '-I', '-m', 'pytest', '-c', '/trusted/pytest.ini',
            '--rootdir=/work/source', '--junitxml=/work/output/junit.xml', '--strict-markers', 'tests']
        raw = canonical_bytes(receipt)
        predicate = build_predicate(identity, artifact_id=4001, artifact_sha256='f' * 64,
                                   receipt_bytes=raw, junit_bytes=document())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), predicate['receipt_sha256'])
        self.assertEqual(4001, predicate['artifact_id'])
        self.assertEqual(2001, predicate['job_id'])
        for mutator in [lambda r: r['source'].update(head_sha='e' * 40, checked_out_sha='e' * 40),
                        lambda r: r['producer'].update(job_id=999),
                        lambda r: r['execution']['command'].__setitem__(-1, 'tests/small_subset'),
                        lambda r: r['execution']['junit'].update(sha256='0' * 64)]:
            changed = deepcopy(receipt); mutator(changed)
            with self.assertRaises(ValueError):
                build_predicate(identity, artifact_id=4001, artifact_sha256='f' * 64,
                                receipt_bytes=canonical_bytes(changed), junit_bytes=document())

    def test_reusable_workflow_bootstrap_matches_reviewed_standalone_bytes(self):
        workflow = (ROOT / '.github/workflows/pytest-producer.yml').read_text()
        embedded = workflow.split("          python3 -I - <<'DELIVERY_REVIEW_BOOTSTRAP'\n", 1)[1].split('          DELIVERY_REVIEW_BOOTSTRAP\n', 1)[0]
        self.assertEqual((ROOT / 'producer/bootstrap.py').read_text(), ''.join(line[10:] + '\n' for line in embedded.splitlines()))
        self.assertNotIn('${{ inputs.', workflow)
        self.assertNotIn('pull_request_target', workflow)
        self.assertNotIn('secrets: inherit', workflow)
        self.assertIn('workflow_call: {}', workflow)
        self.assertIn('compression-level: 0', workflow)

    def test_current_api_without_deprecated_merge_field_requires_exact_platform_ref(self):
        claims, _, data = fixture()
        self.assertNotIn('merge_commit_sha', data['pull_request'])
        self.assertEqual('c' * 40, derive_identity(claims, **data)['event_source_sha'])
        for obsolete in (None, 'e' * 40):
            changed = deepcopy(data); changed['pull_request']['merge_commit_sha'] = obsolete
            self.assertEqual('c' * 40, derive_identity(claims, **changed)['event_source_sha'])
        for mutate in (lambda d: d.update(merge_ref=None),
                       lambda d: d['merge_ref'].update(ref='refs/pull/8/merge'),
                       lambda d: d['merge_ref'].update(ref='refs/pull/7/head'),
                       lambda d: d['merge_ref'].update(url='https://api.github.com/repos/authored/other/git/refs/pull/7/merge'),
                       lambda d: d['merge_ref']['object'].update(type='tag'),
                       lambda d: d['merge_ref']['object'].update(sha='e' * 40),
                       lambda d: d['merge_ref']['object'].update(sha='c' * 7),
                       lambda d: d['merge_ref']['object'].update(url='https://api.github.com/repos/authored/other/git/commits/' + 'c' * 40)):
            changed = deepcopy(data); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(BootstrapError, 'event_source_ref_mismatch'):
                derive_identity(claims, **changed)

    def test_event_merge_identity_requires_original_git_parents_not_tree_similarity(self):
        objects, common, base, head = source_fixtures.fixture()
        merge = objects.commit(objects.files({'README.txt': b'event merge\n'}), [base, head])
        with tempfile.TemporaryDirectory() as directory:
            snapshot = materialize_snapshot(Path(directory) / 'merge', repository='authored/reference',
                head_sha=merge, base_tip_sha=base, objects=objects.objects, git_executable=shutil.which('git'))
            identity = {'event_source_sha': merge, 'base_tip_sha': base, 'head_sha': head}
            self.assertEqual(objects.objects[merge], verify_event_source(snapshot, identity))
            for changed in (dict(identity, head_sha=common), dict(identity, base_tip_sha=head),
                            dict(identity, event_source_sha=head)):
                with self.assertRaises(ValueError): verify_event_source(snapshot, changed)


if __name__ == '__main__':
    unittest.main()
