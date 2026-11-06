"""Authored original API/ZIP controls, not observed GitHub or crypto execution."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.contracts import loads_strict
from delivery_review.execution_history import execution_reason_aliases
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.providers.github_observations import map_capture
from delivery_review.test_evidence import TestEvidenceInputs
from observation_fixtures import CaptureFixture
from test_composition import authored_engine
from test_test_evidence import AuthoredInspection, digest

NEW = 'scoped_original_refusals_v1'
OLD = 'legacy_missing_v1'


def native(fixture, status, conclusion):
    for record in fixture.records:
        if record['role'] not in {'workflow_runs', 'run', 'attempt', 'jobs', 'check_run'}:
            continue
        payload = fixture.payload(record)
        rows = payload['workflow_runs'] if record['role'] == 'workflow_runs' else \
            payload['jobs'] if record['role'] == 'jobs' else [payload]
        for row in rows:
            row.update(status=status, conclusion=conclusion)
        fixture.set_payload(record, payload)


def history(fixture, *, old_head=False, only=False):
    for phase in ('initial', 'final'):
        record = fixture.find('workflow_runs', phase)
        payload = fixture.payload(record)
        row = deepcopy(payload['workflow_runs'][0])
        row.update(id=1000, run_number=row['run_number'] - 1, status='completed', conclusion='success')
        if old_head:
            row['head_sha'] = 'e' * 40
            row['pull_requests'][0]['head']['sha'] = 'e' * 40
        payload['workflow_runs'] = [row] if only else [row, *payload['workflow_runs']]
        payload['total_count'] = len(payload['workflow_runs'])
        fixture.set_payload(record, payload)


def partial(fixture, record):
    record['body']['complete'] = False
    record['error'] = {'code': 'response_limit'}
    fixture.termination = {'status': 'stopped', 'reason_codes': ['collection_incomplete']}


class ExecutionHistoryTests(unittest.TestCase):
    def aliases(self, fixture, outcome='pending', effect='clear'):
        return execution_reason_aliases(fixture.seal(), workflow_id=101, outcome=outcome, effect=effect)

    def test_latest_pending_does_not_require_or_claim_older_success(self):
        for older in (False, True):
            with self.subTest(older=older):
                fixture = CaptureFixture(mapper_version=2)
                native(fixture, 'queued', None)
                if older: history(fixture)
                self.assertEqual(['latest_run_pending'], self.aliases(fixture))
                selection = map_capture(fixture.seal())['workflows'][0]['selection']
                self.assertEqual((1001, 1), (selection['run_id'], selection['attempt']))

    def test_latest_failure_alias_cannot_make_a_missing_test_observation_complete(self):
        fixture = CaptureFixture(mapper_version=2)
        native(fixture, 'completed', 'failure')
        history(fixture)
        for outcome, effect, expected in (('failed', 'block', ['latest_run_failed']),
                ('missing', 'block', []), ('untrusted', 'block', []), ('satisfied', 'clear', []), ('failed', 'clear', [])):
            with self.subTest(outcome=outcome, effect=effect):
                self.assertEqual(expected, self.aliases(fixture, outcome, effect))

    def test_partial_duplicate_ambiguous_or_count_mismatched_pages_have_no_alias(self):
        for problem in ('partial', 'http', 'duplicate', 'equal_sequence', 'count', 'next_page', 'workflow_metadata'):
            with self.subTest(problem=problem):
                fixture = CaptureFixture(mapper_version=2)
                native(fixture, 'queued', None)
                record = fixture.find('workflow_runs')
                payload = fixture.payload(record)
                if problem == 'partial': partial(fixture, record)
                elif problem == 'http': fixture.unavailable(record)
                elif problem == 'workflow_metadata':
                    row = fixture.find('workflow')
                    value = fixture.payload(row)
                    value['path'] = '.github/workflows/unrelated.yml'
                    fixture.set_payload(row, value)
                elif problem == 'next_page':
                    record['response_headers']['link'] = '<https://api.github.com' + record['request']['path'].replace('page=1&', 'page=2&') + '>; rel="next"'
                else:
                    if problem in {'duplicate', 'equal_sequence'}:
                        second = deepcopy(payload['workflow_runs'][0])
                        if problem == 'equal_sequence': second['id'] += 1
                        payload['workflow_runs'].append(second)
                    payload['total_count'] = 2
                    fixture.set_payload(record, payload)
                self.assertEqual([], self.aliases(fixture))

    def test_missing_conflicting_or_unrelated_selected_details_have_no_alias(self):
        for role, problem in ((role, problem) for role in ('run', 'attempt')
                for problem in ('missing', 'partial', 'http', 'id', 'attempt', 'event', 'head', 'repository', 'status', 'association')):
            with self.subTest(role=role, problem=problem):
                fixture = CaptureFixture(mapper_version=2)
                native(fixture, 'queued', None)
                record = fixture.find(role)
                value = fixture.payload(record)
                if problem == 'missing': fixture.records.remove(record)
                elif problem == 'partial': partial(fixture, record)
                elif problem == 'http': fixture.unavailable(record)
                else:
                    if problem == 'id': value['id'] += 1
                    elif problem == 'attempt': value['run_attempt'] += 1
                    elif problem == 'event': value['event'] = 'merge_group'
                    elif problem == 'head': value['head_sha'] = 'e' * 40
                    elif problem == 'repository': value['repository']['id'] += 1
                    elif problem == 'status': value.update(status='completed', conclusion='success')
                    elif problem == 'association': value['pull_requests'] = []
                    fixture.set_payload(record, value)
                self.assertEqual([], self.aliases(fixture))

    def test_omitted_listing_relation_requires_independent_v2_resolution(self):
        for mapper in (1, 2):
            with self.subTest(mapper=mapper):
                fixture = CaptureFixture(mapper_version=mapper)
                native(fixture, 'in_progress', None)
                record = fixture.find('workflow_runs')
                value = fixture.payload(record)
                value['workflow_runs'][0]['pull_requests'] = []
                fixture.set_payload(record, value)
                self.assertEqual(['latest_run_pending'] if mapper == 2 else [], self.aliases(fixture))

    def test_old_head_alias_needs_complete_explicit_same_pr_evidence(self):
        fixture = CaptureFixture(mapper_version=2)
        history(fixture, old_head=True, only=True)
        self.assertEqual(['evidence_for_old_head'], self.aliases(fixture, 'missing'))
        self.assertIsNone(map_capture(fixture.seal())['workflows'][0]['selection']['run_id'])
        original = deepcopy(fixture)
        for problem in ('empty', 'current', 'partial', 'wrong_repo', 'wrong_workflow', 'wrong_event',
                'wrong_path', 'wrong_pr', 'wrong_head_repo', 'missing_pr', 'wrong_associated_head'):
            fixture = deepcopy(original)
            with self.subTest(problem=problem):
                record = fixture.find('workflow_runs')
                value = fixture.payload(record)
                row = value['workflow_runs'][0]
                if problem == 'empty': value = {'total_count': 0, 'workflow_runs': []}
                elif problem == 'current':
                    row['head_sha'] = 'b' * 40
                    row['pull_requests'][0]['head']['sha'] = 'b' * 40
                elif problem == 'partial': partial(fixture, record)
                elif problem == 'wrong_repo': row['repository']['id'] += 1
                elif problem == 'wrong_workflow': row['workflow_id'] += 1
                elif problem == 'wrong_event': row['event'] = 'push'
                elif problem == 'wrong_path': row['path'] = '.github/workflows/other.yml'
                elif problem == 'wrong_pr': row['pull_requests'][0]['number'] += 1
                elif problem == 'wrong_head_repo': row['head_repository']['id'] += 1
                elif problem == 'missing_pr': row['pull_requests'] = []
                elif problem == 'wrong_associated_head': row['pull_requests'][0]['head']['sha'] = 'f' * 40
                if problem != 'partial': fixture.set_payload(record, value)
                self.assertEqual([], self.aliases(fixture, 'missing'))


class ExecutionAliasCompositionTests(unittest.TestCase):
    def fixture(self, version=2):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        value = AuthoredInspection(Path(temporary.name))
        value.profile['profile_version'] = version
        # Include a CI obligation to exercise both projection paths.
        requirement = deepcopy(value.profile['policy']['requirements'][0])
        requirement.update(id='ci-job', kind='ci_job', minimum_binding='platform_bound')
        requirement['selector'] = {key: requirement['selector'][key] for key in ('workflow_id', 'job_name', 'app_id')}
        value.profile['policy']['requirements'].insert(0, requirement)
        value.profile_bytes = seal_inspection_profile(value.profile)
        return value

    def compose(self, fixture, basis, inputs, *, block=False):
        with patch.object(attest, 'TRUSTED_ROOT_PINS', {digest(fixture.trusted_root)}):
            return compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
                snapshot=fixture.snapshot, engine_executions={name: authored_engine(fixture, name,
                    block=block and name == 'pinwash') for name in ('checkwash', 'pinwash', 'ledgerwash')},
                test_inputs={'pytest-linux': inputs}, report_id='authored-execution-alias', mode='live',
                evidence_selection_basis=basis)

    def test_pending_alias_keeps_original_outcomes_selection_and_independent_block(self):
        fixture = self.fixture()
        native(fixture.capture_fixture, 'queued', None)
        history(fixture.capture_fixture)
        before = self.compose(fixture, OLD, TestEvidenceInputs(), block=True)
        after = self.compose(fixture, NEW, TestEvidenceInputs(), block=True)
        self.assertEqual(before.report['state'], after.report['state'])
        self.assertEqual('block', after.report['state']['policy'])
        self.assertEqual(before.report['selected_runs'], after.report['selected_runs'])
        for left, right in zip(before.report['obligations'][:2], after.report['obligations'][:2]):
            self.assertEqual((left['outcome'], left['effect']), (right['outcome'], right['effect']))
            self.assertIn('latest_run_pending', right['reason_codes'])
            self.assertNotIn('latest_run_pending', left['reason_codes'])
        self.assertEqual(1001, after.report['selected_runs'][0]['run_id'])

    def test_authenticated_failure_matches_frozen_state_without_selecting_older_success(self):
        fixture = self.fixture()
        fixture.junit = fixture.xml(passed=1, failed=1)
        fixture.receipt['execution']['test_exit'] = 1
        fixture.rebuild()
        native(fixture.capture_fixture, 'completed', 'failure')
        history(fixture.capture_fixture)
        inputs = fixture.inputs()
        before, after = self.compose(fixture, OLD, inputs), self.compose(fixture, NEW, inputs)
        expected = loads_strict((ROOT / 'fixtures/cases/E09-latest-failed.json').read_bytes())['expected']
        self.assertEqual(expected['state'], after.report['state'])
        self.assertEqual(expected['exit_code'], after.report['exit_code'])
        self.assertEqual(before.report['selected_runs'], after.report['selected_runs'])
        for row in after.report['obligations'][:2]: self.assertIn('latest_run_failed', row['reason_codes'])
        test = next(row for row in after.report['evidence'] if row['kind'] == 'test_report')
        self.assertEqual(('workflow_bound', 1001), (test['binding_level'], test['run_id']))
        self.assertFalse(any(row.get('run_id') == 1000 for row in after.report['evidence']))

    def test_old_head_is_diagnostic_without_selecting_or_crediting_it(self):
        fixture = self.fixture()
        history(fixture.capture_fixture, old_head=True, only=True)
        before, after = (self.compose(fixture, basis, TestEvidenceInputs()) for basis in (OLD, NEW))
        expected = loads_strict((ROOT / 'fixtures/cases/E02-old-head-ci.json').read_bytes())['expected']
        self.assertEqual(expected['state'], after.report['state'])
        self.assertEqual([], after.report['selected_runs'])
        for left, right in zip(before.report['obligations'][:2], after.report['obligations'][:2]):
            self.assertEqual((left['outcome'], left['effect']), (right['outcome'], right['effect']))
            self.assertIn('evidence_for_old_head', right['reason_codes'])
        for row in after.report['evidence'][:2]: self.assertIsNone(row['run_id'])

    def test_profile_v1_remains_byte_identical_across_bases(self):
        fixture = self.fixture(1)
        native(fixture.capture_fixture, 'queued', None)
        history(fixture.capture_fixture)
        before, after = (self.compose(fixture, basis, TestEvidenceInputs()) for basis in (OLD, NEW))
        self.assertEqual(before.report, after.report)
        self.assertEqual(before.raw_files, after.raw_files)


if __name__ == '__main__':
    unittest.main()
