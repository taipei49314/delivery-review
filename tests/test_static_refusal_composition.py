"""Frozen original-engine disposition controls; not native integration receipts."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from test_composition import authored_engine
from test_test_evidence import AuthoredInspection, digest
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.contracts import canonical_bytes
from delivery_review.inspection_profile import seal_inspection_profile

ROOT = Path(__file__).resolve().parents[1]
BASIS = 'scoped_original_refusals_v1'


def rewrite(execution, mutate):
    native = json.loads(execution.raw_files['stdout'])
    manifest = execution.manifest
    mutate(native, manifest)
    files = dict(execution.raw_files, stdout=canonical_bytes(native))
    manifest['streams']['stdout'].update(size_bytes=len(files['stdout']), sha256=digest(files['stdout']))
    return replace(execution, manifest_bytes=canonical_bytes(manifest), raw_files=MappingProxyType(files))


class StaticRefusalCompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AuthoredInspection(Path(temporary.name))
        self.fixture.profile['profile_version'] = 2
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        trust = patch.object(attest, 'TRUSTED_ROOT_PINS', {digest(self.fixture.trusted_root)})
        trust.start()
        self.addCleanup(trust.stop)

    def engines(self):
        return {name: authored_engine(self.fixture, name) for name in ('checkwash', 'pinwash', 'ledgerwash')}

    def compose(self, engines, *, basis=BASIS):
        return compose_report(profile_bytes=self.fixture.profile_bytes, capture=self.fixture.capture_fixture.seal(),
            snapshot=self.fixture.snapshot, engine_executions=engines,
            test_inputs={'pytest-linux': self.fixture.inputs()}, report_id='authored-static-refusal', mode='live',
            evidence_selection_basis=basis)

    def assert_frozen(self, case, engines):
        expected = json.loads((ROOT / ('fixtures/cases/' + case + '.json')).read_bytes())['expected']
        result = self.compose(engines)
        self.assertEqual(expected['state'], result.report['state'])
        self.assertEqual(expected['exit_code'], result.report['exit_code'])
        reasons = {reason for row in result.report['obligations'] for reason in row['reason_codes']}
        self.assertLessEqual(set(expected['reason_codes']), reasons)
        for name, execution in engines.items():
            self.assertEqual(execution.raw_files['stdout'], result.raw_files['engines/' + name + '/stdout.bin'])
        legacy = self.compose(engines, basis='legacy_missing_v1')
        old_reasons = {reason for row in legacy.report['obligations'] for reason in row['reason_codes']}
        self.assertFalse(set(expected['reason_codes']) & old_reasons)
        return result

    def test_native_timeout_keeps_an_independent_block(self):
        engines = self.engines()
        engines['checkwash'] = authored_engine(self.fixture, 'checkwash', termination='timeout')
        self.assert_frozen('E11-scanner-timeout', engines)
        engines['pinwash'] = authored_engine(self.fixture, 'pinwash', block=True)
        result = self.compose(engines)
        self.assertEqual({'evidence': 'error', 'freshness': 'current', 'policy': 'block'}, result.report['state'])

    def test_unknown_native_schema_is_error_and_does_not_hide_a_block(self):
        engines = self.engines()
        engines['checkwash'] = rewrite(engines['checkwash'], lambda native, manifest: native.update(checkwash_findings_version=999))
        self.assert_frozen('E11-unknown-scanner-schema', engines)
        engines['pinwash'] = authored_engine(self.fixture, 'pinwash', block=True)
        result = self.compose(engines)
        self.assertEqual({'evidence': 'error', 'freshness': 'current', 'policy': 'block'}, result.report['state'])

    def test_valid_unread_scope_keeps_known_native_block_and_becomes_unsupported(self):
        engines = self.engines()
        engines['checkwash'] = rewrite(authored_engine(self.fixture, 'checkwash', block=True),
            lambda native, manifest: native.update(skipped_files=['tests/unread.py']))
        result = self.assert_frozen('E12-unknown-coverage-with-blocker', engines)
        row = next(row for row in result.report['obligations'] if row['id'] == 'checkwash')
        self.assertEqual(('unsupported', 'block'), (row['outcome'], row['effect']))
        self.assertEqual('block', result.engine_observations['checkwash']['parsed_native']['verdict'])

    def test_known_weakening_requires_the_actual_high_original_native_rule(self):
        engines = self.engines()
        engines['checkwash'] = rewrite(authored_engine(self.fixture, 'checkwash', block=True),
            lambda native, manifest: native['findings'][0].update(rule='ASSERT_REMOVED'))
        self.assert_frozen('E13-weakening', engines)
        engines['checkwash'] = authored_engine(self.fixture, 'checkwash', block=True)
        result = self.compose(engines)
        self.assertNotIn('test_verification_weakened', next(row for row in result.report['obligations'] if row['id'] == 'checkwash')['reason_codes'])

    def test_native_allowlisted_finding_stays_pass_and_original_bytes_are_retained(self):
        engines = self.engines()
        def allow(native, manifest):
            native['findings'][0]['allowlisted'] = True
            native['verdict'] = 'pass'
            manifest['process']['exit_code'] = 0
        engines['checkwash'] = rewrite(authored_engine(self.fixture, 'checkwash', block=True), allow)
        result = self.assert_frozen('E13-existing-valid-exemption', engines)
        self.assertTrue(result.engine_observations['checkwash']['parsed_native']['findings'][0]['allowlisted'])

    def test_new_exemption_is_review_without_relabeling_native_pass(self):
        engines = self.engines()
        engines['checkwash'] = authored_engine(self.fixture, 'checkwash', added_exemption=True)
        result = self.assert_frozen('E13-new-exemption', engines)
        self.assertEqual('pass', result.engine_observations['checkwash']['parsed_native']['verdict'])

    def test_supported_ledger_gap_is_distinct_from_absent_and_unknown_format(self):
        self.fixture.profile['engine_options']['ledgerwash']['require_complete'] = False
        ledger = next(row for row in self.fixture.profile['policy']['requirements'] if row['id'] == 'ledgerwash')
        ledger['required'] = True
        self.fixture.profile['ledger_input'] = {'format': 'ec-ledger-v1', 'paths': []}
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        engines = self.engines()
        def gap(native, manifest):
            native['run']['require_complete'] = False
            manifest['process']['argv'].remove('--require-complete')
            native['coverage']['complete'] = False
            native['coverage']['checks'][0]['status'] = 'unverified'
            native['coverage']['counts'] = {'verified': 1, 'unverified': 1, 'mismatch': 0}
        engines['ledgerwash'] = rewrite(engines['ledgerwash'], gap)
        result = self.assert_frozen('E15-required-ledger-coverage-gaps', engines)
        observed = result.engine_observations['ledgerwash']
        self.assertEqual(('completed', 'pass', 'gaps'), (observed['status'], observed['verdict'], observed['coverage']['status']))
        row = next(row for row in result.report['obligations'] if row['id'] == 'ledgerwash')
        self.assertEqual('unsupported', row['outcome'])

    def test_profile_v1_does_not_take_new_plan_static_interpretation(self):
        engines = self.engines()
        engines['checkwash'] = authored_engine(self.fixture, 'checkwash', added_exemption=True)
        self.fixture.profile['profile_version'] = 1
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        old = self.compose(engines, basis='legacy_missing_v1')
        new = self.compose(engines)
        self.assertEqual(old.report, new.report)


if __name__ == '__main__':
    unittest.main()
