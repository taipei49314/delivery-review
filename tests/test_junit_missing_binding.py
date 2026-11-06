"""Authored partial-archive controls; absence never authenticates a producer."""
from dataclasses import fields
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from test_test_evidence import AuthoredInspection, digest, fact
from test_artifact_zip import archive
from test_composition import authored_engine
from delivery_review.composition import compose_report
from delivery_review.contracts import canonical_bytes, loads_strict
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.test_evidence import TestEvidenceInputs, bind_test_evidence

NEW_BASIS = 'scoped_original_refusals_v1'


class MissingJUnitBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AuthoredInspection(Path(temporary.name))
        self.receipt = canonical_bytes(self.fixture.receipt)
        self.raw = archive([('receipt.json', self.receipt)])

    def args(self, raw, *, version=2, basis=NEW_BASIS):
        self.fixture.profile['profile_version'] = version
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        metadata = dict(self.fixture.metadata, digest='sha256:' + digest(raw), size_in_bytes=len(raw))
        return self.fixture.args(artifact=None, archive_bytes=raw, artifact_metadata=fact(
            '/repos/fixture-owner/fixture-repo/actions/artifacts/7001', metadata), evidence_selection_basis=basis)

    def test_only_new_v2_safe_exact_receipt_singleton_establishes_missing_junit(self):
        for version, basis in ((1, 'legacy_missing_v1'), (1, NEW_BASIS), (2, 'legacy_missing_v1'), (2, NEW_BASIS)):
            with self.subTest(version=version, basis=basis):
                result = bind_test_evidence(**self.args(self.raw, version=version, basis=basis))
                new = version == 2 and basis == NEW_BASIS
                self.assertEqual(('missing' if new else 'error', 'clear'),
                    (result.obligation['outcome'], result.obligation['effect']))
                self.assertEqual(['junit_missing' if new else 'zip_member_inventory_mismatch'], result.obligation['reason_codes'])
                self.assertIn(self.raw, result.raw_files.values())
                self.assertIsNone(result.diagnostics['attestation_expected'])
                self.assertNotEqual('workflow_bound', result.evidence['binding_level'])
                if new:
                    self.assertIn(self.receipt, result.raw_files.values())
                    self.assertEqual(['junit.xml'], result.diagnostics['artifact_inventory']['missing_members'])

    def test_unsafe_extra_wrong_duplicate_damaged_or_invalid_receipt_is_not_absence(self):
        stored = bytearray(archive([('receipt.json', self.receipt)], method=zipfile.ZIP_STORED))
        stored[30 + len('receipt.json')] ^= 1
        cases = {'extra': archive([('receipt.json', self.receipt), ('unexpected.txt', b'authored')]),
            'junit-only': archive([('junit.xml', self.fixture.junit)]),
            'empty': archive([]), 'duplicate': archive([('receipt.json', self.receipt), ('receipt.json', self.receipt)]),
            'symlink': archive([('receipt.json', self.receipt)], mode=stat.S_IFLNK | 0o777),
            'traversal': archive([('../receipt.json', self.receipt)]),
            'crc': bytes(stored), 'invalid-receipt': archive([('receipt.json', b'{}')])}
        for name, raw in cases.items():
            with self.subTest(name=name):
                result = bind_test_evidence(**self.args(raw))
                self.assertEqual('error', result.obligation['outcome'])
                self.assertNotIn('junit_missing', result.obligation['reason_codes'])
                self.assertIsNone(result.diagnostics['attestation_expected'])
                self.assertIn(raw, result.raw_files.values())

    def test_size_limit_and_known_digest_contradiction_are_not_downgraded(self):
        self.fixture.profile['policy']['limits']['artifact_bytes'] = len(self.raw) - 1
        result = bind_test_evidence(**self.args(self.raw))
        self.assertEqual('error', result.obligation['outcome'])
        self.assertNotIn('junit_missing', result.obligation['reason_codes'])
        args = self.args(self.raw)
        metadata = dict(self.fixture.metadata, digest='sha256:' + 'f' * 64, size_in_bytes=len(self.raw))
        args['artifact_metadata'] = fact('/repos/fixture-owner/fixture-repo/actions/artifacts/7001', metadata)
        result = bind_test_evidence(**args)
        self.assertEqual(('error', 'block'), (result.obligation['outcome'], result.obligation['effect']))
        self.assertIn('artifact_digest_mismatch', result.obligation['reason_codes'])
        self.assertNotIn('junit_missing', result.obligation['reason_codes'])

    def test_no_archive_is_only_artifact_missing_and_ci_failure_is_preserved(self):
        args = self.args(self.raw)
        args.update(archive_bytes=None, artifact_metadata=None)
        result = bind_test_evidence(**args)
        self.assertIn('artifact_missing', result.obligation['reason_codes'])
        self.assertNotIn('junit_missing', result.obligation['reason_codes'])
        for role in ('jobs', 'check_run'):
            row = self.fixture.capture_fixture.find(role)
            payload = self.fixture.capture_fixture.payload(row)
            (payload['jobs'][0] if role == 'jobs' else payload)['conclusion'] = 'failure'
            self.fixture.capture_fixture.set_payload(row, payload)
        result = bind_test_evidence(**self.args(self.raw))
        self.assertEqual(('missing', 'block'), (result.obligation['outcome'], result.obligation['effect']))
        self.assertIn('junit_missing', result.obligation['reason_codes'])

    def test_original_singleton_composition_matches_frozen_e14(self):
        args = self.args(self.raw)
        values = TestEvidenceInputs(**{field.name: args[field.name] for field in fields(TestEvidenceInputs) if field.name in args})
        result = compose_report(profile_bytes=self.fixture.profile_bytes, capture=self.fixture.capture_fixture.seal(),
            snapshot=self.fixture.snapshot, engine_executions={name: authored_engine(self.fixture, name) for name in
            ('checkwash', 'pinwash', 'ledgerwash')}, test_inputs={'pytest-linux': values},
            report_id='authored-junit-missing', mode='live', evidence_selection_basis=NEW_BASIS)
        root = Path(__file__).resolve().parents[1]
        expected = loads_strict((root / 'fixtures/cases/E14-missing-junit.json').read_bytes())['expected']
        self.assertEqual(expected['state'], result.report['state'])
        self.assertEqual(expected['exit_code'], result.report['exit_code'])
        self.assertIn('junit_missing', result.report['obligations'][0]['reason_codes'])
        self.assertFalse(result.report['ready_for_review'])


if __name__ == '__main__':
    unittest.main()
