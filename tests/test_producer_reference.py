"""Authored public reference/profile wiring, without publication or execution."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from delivery_review.inspection_profile import load_inspection_profile
from examples.reference import render, prepare_profile
from producer.bootstrap import producer_path


class ProducerReferenceTests(unittest.TestCase):
    def test_reference_and_external_policy_bind_the_same_fixed_mode_without_runtime_inputs(self):
        identities = set()
        for mode in ('pr_head', 'github_merge'):
            with self.subTest(mode=mode):
                files = render.files('d' * 40, 'base', mode)
                workflow = files['.github/workflows/delivery-review.yml'].decode()
                self.assertIn('taipei49314/delivery-review/' + producer_path(mode) + '@' + 'd' * 40, workflow)
                self.assertNotIn('with:', workflow)
                self.assertNotIn('inputs.', workflow)
                manifest = json.loads(files['fixture-manifest.json'])
                self.assertEqual('NOT_RUN', manifest['product_integration'])
                self.assertEqual(mode, manifest['source_mode'])
                self.assertEqual(producer_path(mode), manifest['producer_path'])
                for item in manifest['files']:
                    self.assertEqual(hashlib.sha256(files[item['path']]).hexdigest(), item['sha256'])
                raw = prepare_profile.prepare(repository='authored/reference', repository_id=9001,
                    workflow_id=1001, job_name='caller / observed-' + mode, producer_sha='d' * 40, source_mode=mode)
                profile = load_inspection_profile(raw)
                identities.add(profile['policy']['policy_id'])
                self.assertEqual([mode], profile['policy']['allowed_source_modes'])
                test = next(item for item in profile['policy']['requirements'] if item['kind'] == 'test_report')
                self.assertEqual(producer_path(mode), test['selector']['producer_path'])
                self.assertEqual('d' * 40, test['selector']['producer_sha'])
        self.assertEqual(2, len(identities))

    def test_unrecognized_mode_cannot_create_a_dynamic_workflow_or_policy(self):
        for mode in ('auto', '../untrusted.yml', '${{ inputs.mode }}', [], None):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError): render.files('d' * 40, 'base', mode)
                with self.assertRaises(ValueError):
                    prepare_profile.prepare(repository='authored/reference', repository_id=9001,
                        workflow_id=1001, job_name='observed', producer_sha='d' * 40, source_mode=mode)


if __name__ == '__main__':
    unittest.main()
