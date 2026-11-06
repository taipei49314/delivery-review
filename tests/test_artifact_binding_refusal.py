"""Original-listing ambiguity controls; authored HTTP is not hosted evidence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from test_test_evidence import AuthoredInspection
from test_artifact_acquisition import ArtifactTransport, AttestationTransport, response, COLLECTOR
from observation_fixtures import NOW
from delivery_review.contracts import ContractError, canonical_bytes
from delivery_review.evidence.artifacts import collect_artifacts
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.test_evidence import bind_test_evidence


class ArtifactBindingRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = AuthoredInspection(self.root)
        profile = deepcopy(self.fixture.profile)
        profile['profile_version'] = 2
        self.profile = seal_inspection_profile(profile)

    def collect(self, *, complete=True, attempt=1, check=4001):
        metadata = self.fixture.metadata
        values = response({'total_count': 2 if complete else 3,
            'artifacts': [metadata, dict(metadata, id=7002)]})
        return collect_artifacts(repository={'id': 9001, 'full_name': self.fixture.snapshot.repository},
            run_id=1001, run_attempt=attempt, check_run_id=check, destination=self.root / 'acquisition',
            transport=ArtifactTransport([values]), attestation_transport=AttestationTransport([]),
            collector=COLLECTOR, clock=lambda: NOW)

    def bind(self, acquisition):
        return bind_test_evidence(**self.fixture.args(profile_bytes=self.profile, artifact=None,
            artifact_metadata=None, artifact_capture=acquisition,
            evidence_selection_basis='scoped_original_refusals_v1'))

    def test_legacy_basis_does_not_reinterpret_even_an_explicit_original_capture(self):
        result = bind_test_evidence(**self.fixture.args(profile_bytes=self.profile, artifact=None,
            artifact_metadata=None, artifact_capture=self.collect()))
        self.assertEqual('missing', result.obligation['outcome'])
        self.assertEqual(['artifact_missing'], result.obligation['reason_codes'])

    def test_complete_duplicate_names_are_untrusted_and_all_originals_retained(self):
        acquisition = self.collect()
        result = self.bind(acquisition)
        self.assertEqual('untrusted', result.obligation['outcome'])
        self.assertEqual(['artifact_ambiguous'], result.obligation['reason_codes'])
        self.assertIsNone(result.diagnostics['attestation_expected'])
        self.assertIsNone(result.evidence['artifact_id'])
        self.assertIn(acquisition.manifest_bytes, result.raw_files.values())
        for raw in acquisition.raw_files.values():
            self.assertIn(raw, result.raw_files.values())

    def test_incomplete_listing_does_not_become_complete_ambiguity_proof(self):
        result = self.bind(self.collect(complete=False))
        self.assertEqual('missing', result.obligation['outcome'])
        self.assertEqual(['artifact_missing'], result.obligation['reason_codes'])

    def test_other_attempt_or_check_cannot_supply_ambiguity(self):
        acquisition = self.collect()
        for key, value in (('run_attempt', 2), ('check_run_id', 4999), ('run_id', 1002)):
            manifest = acquisition.manifest
            manifest['inputs'][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.bind(replace(acquisition, manifest_bytes=canonical_bytes(manifest)))

    def test_original_capture_tamper_is_not_trusted_as_an_ambiguity_flag(self):
        acquisition = self.collect()
        raw = dict(acquisition.raw_files)
        key = next(iter(raw))
        raw[key] += b' '
        with self.assertRaises(ContractError):
            self.bind(replace(acquisition, raw_files=raw))


if __name__ == '__main__':
    unittest.main()
