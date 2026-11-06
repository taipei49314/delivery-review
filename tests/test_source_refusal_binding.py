"""Authored original Git/ref/ZIP controls; no actual GitHub or crypto claim."""
from dataclasses import fields, replace
from pathlib import Path
import tempfile
import unittest

from test_test_evidence import AuthoredInspection, fact
from test_composition import authored_engine
from delivery_review.composition import compose_report
from delivery_review.contracts import ContractError, loads_strict
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.test_evidence import EventSourceFact, TestEvidenceInputs, bind_test_evidence

NEW_BASIS = 'scoped_original_refusals_v1'
OLD_BASIS = 'legacy_missing_v1'


class SourceRefusalBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AuthoredInspection(Path(temporary.name))
        self.profile(2)

    def profile(self, version):
        self.fixture.profile['profile_version'] = version
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)

    def event(self, parents):
        value = self.fixture.objects
        merge = value.commit(value.files({'README.txt': b'authored source refusal control'}), parents)
        reference = fact('/repos/fixture-owner/fixture-repo/git/ref/pull/7/merge', {
            'ref': 'refs/pull/7/merge', 'object': {'type': 'commit', 'sha': merge,
            'url': 'https://api.github.com/repos/fixture-owner/fixture-repo/git/commits/' + merge}})
        return EventSourceFact(value.objects[merge], reference)

    def bind(self, event, basis=NEW_BASIS, **changes):
        return self.fixture.bound(event_source=event, event_source_basis='github_merge_reference_v1',
            evidence_selection_basis=basis, **changes)

    def test_two_wrong_ordered_parents_are_a_scoped_contradiction_only_in_new_v2(self):
        snapshot = self.fixture.snapshot
        for parents in ([snapshot.head_sha, snapshot.base_tip_sha], ['e' * 40, snapshot.head_sha],
                [snapshot.base_tip_sha, 'e' * 40]):
            event = self.event(parents)
            for version, basis, expected in ((1, OLD_BASIS, ('error', 'clear')), (1, NEW_BASIS, ('error', 'clear')),
                    (2, OLD_BASIS, ('error', 'clear')), (2, NEW_BASIS, ('mismatch', 'block'))):
                with self.subTest(parents=parents, profile=version, basis=basis):
                    self.profile(version)
                    result = self.bind(event, basis)
                    self.assertEqual(expected, (result.obligation['outcome'], result.obligation['effect']))
                    self.assertEqual(expected[0] == 'mismatch', 'merge_parents_mismatch' in result.obligation['reason_codes'])
                    self.assertIn('event_merge_parent_mismatch', result.obligation['reason_codes'])
                    self.assertIsNone(result.diagnostics['attestation_expected'])
                    self.assertIn(event.commit_bytes, result.raw_files.values())
                    self.assertIn(event.reference.body_bytes, result.raw_files.values())
                    self.assertNotEqual('workflow_bound', result.evidence['binding_level'])

    def test_missing_parent_proof_is_untrusted_and_octopus_is_unsupported_without_block(self):
        snapshot = self.fixture.snapshot
        for parents, expected, reason in (([], 'untrusted', 'event_merge_parent_proof_missing'),
                ([snapshot.base_tip_sha], 'untrusted', 'event_merge_parent_proof_missing'),
                ([snapshot.base_tip_sha, snapshot.head_sha, 'e' * 40], 'unsupported', 'tested_source_mode_unsupported')):
            event = self.event(parents)
            for basis in (OLD_BASIS, NEW_BASIS):
                with self.subTest(parents=parents, basis=basis):
                    result = self.bind(event, basis)
                    self.assertEqual('error' if basis == OLD_BASIS else expected, result.obligation['outcome'])
                    self.assertEqual('clear', result.obligation['effect'])
                    self.assertNotIn('merge_parents_mismatch', result.obligation['reason_codes'])
                    if basis == NEW_BASIS:
                        self.assertIn(reason, result.obligation['reason_codes'])
                    self.assertIsNone(result.diagnostics['attestation_expected'])

    def test_missing_ref_or_commit_does_not_supply_a_parent_contradiction(self):
        event = self.event([self.fixture.snapshot.head_sha, self.fixture.snapshot.base_tip_sha])
        for value in (None, replace(event, reference=None)):
            with self.subTest(event=value):
                result = self.bind(value)
                self.assertEqual(('untrusted', 'clear'), (result.obligation['outcome'], result.obligation['effect']))
                self.assertIn('event_merge_reference_missing', result.obligation['reason_codes'])
                self.assertIsNone(result.diagnostics['attestation_expected'])

    def test_partial_wrong_scope_wrong_hash_or_custom_ref_cannot_establish_contradiction(self):
        event = self.event([self.fixture.snapshot.head_sha, self.fixture.snapshot.base_tip_sha])
        values = [replace(event, reference=replace(event.reference, **change)) for change in (
            {'complete': False}, {'status_code': 404},
            {'request_path': '/repos/fixture-owner/fixture-repo/git/ref/heads/gh-readonly-queue/main'},
            {'request_path': '/repos/fixture-owner/fixture-repo/git/ref/pull/8/merge'},
            {'body_bytes': b'{"ref":"refs/heads/custom","object":{}}'})]
        values += [replace(event, commit_bytes=event.commit_bytes[:-1] + b'!'), replace(event, commit_bytes=b'not a Git object'),
                   self.event([self.fixture.snapshot.base_tip_sha, self.fixture.snapshot.base_tip_sha])]
        for value in values:
            with self.subTest(event=value):
                result = self.bind(value)
                self.assertNotEqual('satisfied', result.obligation['outcome'])
                self.assertEqual('clear', result.obligation['effect'])
                self.assertNotIn('merge_parents_mismatch', result.obligation['reason_codes'])
                self.assertIsNone(result.diagnostics['attestation_expected'])

    def test_queue_and_custom_receipt_modes_are_not_expanded_into_accepted_claims(self):
        snapshot = self.fixture.snapshot
        event = self.event([snapshot.base_tip_sha, snapshot.head_sha])
        for mode in ('merge_queue', 'custom_merge', 'octopus_merge'):
            with self.subTest(mode=mode):
                self.fixture.receipt['source']['mode'] = mode
                self.fixture.rebuild()
                result = self.bind(event)
                self.assertNotEqual('satisfied', result.obligation['outcome'])
                self.assertEqual('clear', result.obligation['effect'])
                self.assertNotIn('merge_parents_mismatch', result.obligation['reason_codes'])
                self.assertNotEqual('workflow_bound', result.evidence['binding_level'])
                self.assertIsNone(result.diagnostics['attestation_expected'])

    def test_correct_original_ref_and_parents_still_require_the_full_authentication_chain(self):
        snapshot = self.fixture.snapshot
        event = self.event([snapshot.base_tip_sha, snapshot.head_sha])
        preflight = bind_test_evidence(**self.fixture.args(event_source=event,
            event_source_basis='github_merge_reference_v1', evidence_selection_basis=NEW_BASIS))
        self.assertEqual('untrusted', preflight.obligation['outcome'])
        self.assertIn('attestation_missing', preflight.obligation['reason_codes'])
        self.assertEqual('satisfied', self.bind(event).obligation['outcome'])
        for basis in ('future', None, True):
            with self.subTest(basis=basis), self.assertRaisesRegex(ContractError, 'unsupported_evidence_selection_basis'):
                self.bind(event, basis)

    def test_legacy_event_protocol_never_gains_new_parent_mismatch_credit(self):
        snapshot = self.fixture.snapshot
        event = self.event([snapshot.head_sha, snapshot.base_tip_sha])
        result = self.fixture.bound(event_source=replace(event, reference=None), evidence_selection_basis=NEW_BASIS)
        self.assertEqual(('error', 'clear'), (result.obligation['outcome'], result.obligation['effect']))
        self.assertNotIn('merge_parents_mismatch', result.obligation['reason_codes'])

    def test_raw_composition_matches_frozen_wrong_parent_and_unsupported_dispositions(self):
        snapshot = self.fixture.snapshot
        root = Path(__file__).resolve().parents[1]
        for parents, case in (([snapshot.head_sha, snapshot.base_tip_sha], 'E03-wrong-merge-parents'),
                ([snapshot.base_tip_sha, snapshot.head_sha, 'e' * 40], 'E18-unsupported-source')):
            with self.subTest(case=case):
                event = self.event(parents)
                args = self.fixture.args(event_source=event, event_source_basis='github_merge_reference_v1', evidence_selection_basis=NEW_BASIS)
                values = TestEvidenceInputs(**{field.name: args[field.name] for field in fields(TestEvidenceInputs) if field.name in args})
                result = compose_report(profile_bytes=self.fixture.profile_bytes, capture=self.fixture.capture_fixture.seal(),
                    snapshot=snapshot, engine_executions={name: authored_engine(self.fixture, name) for name in
                    ('checkwash', 'pinwash', 'ledgerwash')}, test_inputs={'pytest-linux': values},
                    report_id='authored-source-refusal', mode='live', evidence_selection_basis=NEW_BASIS)
                expected = loads_strict((root / 'fixtures/cases' / (case + '.json')).read_bytes())['expected']
                self.assertEqual(expected['state'], result.report['state'])
                self.assertEqual(expected['exit_code'], result.report['exit_code'])
                self.assertFalse(result.report['ready_for_review'])
                self.assertTrue(set(expected['reason_codes']) <= set(result.report['obligations'][0]['reason_codes']))
                self.assertEqual([], result.report['subject']['merge_parents'])

    def test_unsupported_or_missing_source_keeps_an_independently_observed_ci_block(self):
        for role in ('jobs', 'check_run'):
            row = self.fixture.capture_fixture.find(role)
            payload = self.fixture.capture_fixture.payload(row)
            (payload['jobs'][0] if role == 'jobs' else payload)['conclusion'] = 'failure'
            self.fixture.capture_fixture.set_payload(row, payload)
        snapshot = self.fixture.snapshot
        for event in (None, self.event([snapshot.base_tip_sha]), self.event([snapshot.base_tip_sha, snapshot.head_sha, 'e' * 40])):
            result = self.bind(event)
            self.assertEqual('block', result.obligation['effect'])
            self.assertNotIn('merge_parents_mismatch', result.obligation['reason_codes'])


if __name__ == '__main__':
    unittest.main()
