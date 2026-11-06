"""Authored execution records; these identities are not authenticated proof."""
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from delivery_review.adapters.pytest_junit import parse_pytest_junit
from delivery_review.contracts import canonical_bytes
from producer.host import ProcessObservation, ProducerError, ProducerExecution
from producer.receipt import build_receipt
from test_pytest_junit import document, testcase


class ProducerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.source = {'repository_id': 9001, 'head_repository_id': 9001, 'pr_number': 7,
                       'source': {'mode': 'pr_head', 'base_tip_sha': 'a' * 40, 'head_sha': 'b' * 40,
                                  'checked_out_sha': 'b' * 40, 'merge_parents': []}}
        self.identity = {'repository_id': 9002, 'workflow_path': '.github/workflows/pytest.yml',
                         'workflow_sha': 'd' * 40, 'run_id': 1001, 'attempt': 1, 'job_id': 2001}

    def execute(self, raw, exit_code=0):
        return ProducerExecution(canonical_bytes({'head_sha': 'b' * 40, 'base_tip_sha': 'a' * 40}),
                                 'sha256:' + 'e' * 64, 'tests', ('python', '-I', '-m', 'pytest', 'tests'),
                                 ProcessObservation(0, b'', b'', None), ProcessObservation(exit_code, b'', b'', None),
                                 raw, None if raw is None else canonical_bytes(parse_pytest_junit(raw)), (), True, True)

    def receipt(self, execution):
        return build_receipt(execution, source_claim=self.source, producer_identity=self.identity,
                             recorded_at='2026-11-06T00:00:00Z', receipt_id='authored-producer')

    def test_exact_bytes_exit_and_declared_identity_no_invented_collection_exit(self):
        raw = document()
        result = self.receipt(self.execute(raw))
        self.assertEqual('producer_declared', result['provenance'])
        self.assertEqual({'path': 'junit.xml', 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)},
                         result['execution']['junit'])
        self.assertIsNone(result['execution']['collection_exit'])
        self.assertEqual({'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0}, result['execution']['tests'])
        self.assertEqual(0, result['execution']['test_exit'])
        result['producer']['job_id'] = 123
        self.assertEqual(2001, self.identity['job_id'])

    def test_failure_error_zero_and_skip_are_not_normalized_to_pass(self):
        variants = [(document([testcase(outcome='failure')], failures=1), 1, {'failed': 1, 'passed': 0}),
                    (document([testcase(outcome='error')], errors=1), 1, {'errors': 1, 'passed': 0}),
                    (document([testcase(outcome='skipped')], skipped=1), 0, {'skipped': 1, 'passed': 0}),
                    (document([]), 5, {'passed': 0})]
        for raw, exit_code, expected in variants:
            with self.subTest(expected=expected):
                execution = self.execute(raw, exit_code)
                result = self.receipt(execution)
                self.assertEqual(exit_code, result['execution']['test_exit'])
                for key, value in expected.items():
                    self.assertEqual(value, result['execution']['tests'][key])
                self.assertNotIn('ready_for_review', result)

    def test_inconsistent_native_counts_remain_invalid_and_raw_is_not_repaired(self):
        raw = document([testcase(outcome='error')], tests=2, errors=1)
        execution = self.execute(raw, 1)
        with self.assertRaisesRegex(ProducerError, 'invalid_receipt_junit'):
            self.receipt(execution)
        self.assertFalse(execution.junit_observation['valid'])
        self.assertEqual(2, execution.junit_observation['native_counts']['tests'])
        self.assertEqual(1, execution.junit_observation['case_counts']['errors'])
        self.assertEqual(raw, execution.junit_bytes)

    def test_absent_measurements_stay_null(self):
        execution = replace(self.execute(None), setup=ProcessObservation(70, b'', b'', None), test=None,
                            error_codes=('setup_failed',))
        with self.assertRaisesRegex(ProducerError, 'incomplete_producer_execution'):
            self.receipt(execution)
        self.assertEqual(70, execution.setup.exit_code)
        self.assertIsNone(execution.test)
        self.assertIsNone(execution.junit_bytes)

    def test_boundary_errors_cannot_be_laundered_into_an_uploadable_receipt(self):
        original = self.execute(document())
        variants = [replace(original, stopped=False), replace(original, cleaned=False),
                    replace(original, error_codes=('source_changed_during_execution',)),
                    replace(original, junit_observation_bytes=canonical_bytes({'valid': True})),
                    replace(original, test=ProcessObservation(137, b'', b'', None)),
                    replace(original, test=ProcessObservation(None, b'', b'', 'execution_timeout'))]
        for execution in variants:
            with self.subTest(execution=execution), self.assertRaises(ProducerError):
                self.receipt(execution)

    def test_source_claim_must_match_measured_snapshot_and_merge_is_unsupported(self):
        for key, value in (('checked_out_sha', 'c' * 40), ('base_tip_sha', 'c' * 40), ('mode', 'github_merge')):
            with self.subTest(key=key):
                original = self.source['source'][key]
                self.source['source'][key] = value
                with self.assertRaisesRegex(ProducerError, 'receipt_source_mismatch'):
                    self.receipt(self.execute(document()))
                self.source['source'][key] = original


if __name__ == '__main__':
    unittest.main()
