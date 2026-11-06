"""Controls for regression-label fidelity; no upstream code or scanner runs."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.qualification.engine_regression import (
    compare_case_expectation, expectation_match, ledger_controls, parse_case, preserve_native,
)


class EngineRegressionTests(unittest.TestCase):
    def test_declared_snapshots_preserve_changes_deletions_and_unchanged_context(self):
        raw = (b'=== meta ===\nnote: authored parser control\n=== before: old.txt ===\nold\n'
               b'=== after: new.txt ===\nnew\n=== head: shared.txt ===\nshared\n=== expect ===\n[]\n')
        before, after, expected, metadata = parse_case(raw)
        self.assertEqual({'old.txt': b'old', 'shared.txt': b'shared'}, before)
        self.assertEqual({'new.txt': b'new', 'shared.txt': b'shared'}, after)
        self.assertEqual([], expected)
        self.assertEqual('note: authored parser control', metadata)

    def test_unrepresented_overrides_and_path_or_section_ambiguity_are_refused(self):
        for raw in (b'=== task ===\ncontract', b'=== env ===\nmodule',
                    b'=== before: ../escape ===\nx\n=== expect ===\n[]',
                    b'=== before: x ===\na\n=== before: x ===\nb\n=== expect ===\n[]',
                    b'=== head: x ===\na\n=== after: x ===\nb\n=== expect ===\n[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_case(raw)

    def test_expected_subset_fields_require_distinct_rows_and_exact_count(self):
        actual = [{'rule': 'R', 'severity': 'high', 'escalators': ['A', 'B']}, {'rule': 'R', 'severity': 'warn'}]
        self.assertTrue(expectation_match([{'rule': 'R'}, {'rule': 'R', 'severity': 'high', 'escalators': ['A']}], actual))
        self.assertFalse(expectation_match([{'rule': 'R'}], actual))
        self.assertFalse(expectation_match([{'rule': 'R', 'severity': 'high'}] * 2, actual))
        self.assertFalse(expectation_match([{'rule': 'R'}, {'rule': 'R', 'escalators': ['C']}], actual))

    def test_preservation_check_detects_native_or_normalized_label_mutation(self):
        finding = {'fingerprint': 'authored/identity', 'rule': 'R', 'severity': 'warn', 'allowlisted': True}
        native = {'findings': [finding], 'verdict': 'pass'}
        observation = {'parsed_native': deepcopy(native), 'findings': [{'native': deepcopy(finding),
            'normalized': {key: finding[key] for key in ('fingerprint', 'rule', 'severity')}}],
            'status': 'completed', 'verdict': 'pass'}
        streams = {'stdout': json.dumps(native).encode()}
        self.assertTrue(preserve_native(observation, streams))
        for change in (lambda value: value['parsed_native'].update(verdict='block'),
                       lambda value: value['findings'][0]['normalized'].update(severity='high'),
                       lambda value: value['findings'][0]['native'].update(allowlisted=False),
                       lambda value: value.update(verdict='block'),
                       lambda value: value.update(status='error'),
                       lambda value: value.update(findings=[])):
            changed = deepcopy(observation); change(changed)
            self.assertFalse(preserve_native(changed, streams))

    def test_matching_public_findings_cannot_hide_an_adapter_error(self):
        expectation = {'upstream_findings': [{'rule': 'R', 'severity': 'high'}]}
        native = {'findings': [{'rule': 'R', 'severity': 'high'}]}
        self.assertTrue(compare_case_expectation(expectation, {'adapter_status': 'completed'}, native))
        self.assertFalse(compare_case_expectation(expectation, {'adapter_status': 'error'}, native))

    def test_absence_amendment_changes_only_contract_defined_coverage_expectation(self):
        controls = []
        def collect(case_id, engine, before, after, expected, **kwargs):
            controls.append((case_id, before, after, expected, kwargs))
        ledger_controls(collect, {'app.py': b'unchanged\n'}, absence_only=True)
        amendment = json.loads((ROOT / 'tools/qualification/engine_regression_amendment_v2.json').read_bytes())
        changes = {row['case_id']: row for row in amendment['changes']}
        self.assertEqual(set(changes), {row[0] for row in controls})
        for case_id, before, after, expectation, policy in controls:
            change = changes[case_id]
            self.assertEqual(before, after)
            self.assertEqual(change['new_expectation'], expectation)
            self.assertEqual({**change['old_expectation'], 'coverage': 'not_provided'}, expectation)
            self.assertEqual(expectation['native_exit'] == 1, policy['require_complete'])


if __name__ == '__main__':
    unittest.main()
