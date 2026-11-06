"""Authored declaration/static-source controls, not Ledgerwash integration qualification."""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, loads_strict, validators
from delivery_review.ledger_input import apply_declaration, observe_input, validate_declaration
from delivery_review.source_snapshot import materialize_snapshot, verify_snapshot
from test_source_snapshot import Objects
from test_engine_adapters import control, normalize


class LedgerInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        objects = Objects()
        self.raw = b'unknown ledger format\r\n$(do not execute)\x00\xff'
        base = objects.commit(objects.files({"README": b"base"}))
        head = objects.commit(objects.files({"ledger.custom": self.raw, "README": b"head"}), [base])
        self.snapshot = materialize_snapshot(Path(temporary.name) / "source", repository="fixture-owner/fixture-repo",
            head_sha=head, base_tip_sha=base, objects=objects.objects, git_executable=shutil.which("git"))
        verify_snapshot(self.snapshot)
        self.declaration = {"format": "authored-custom-v7", "paths": ["ledger.custom"]}

    def observation(self, *, block=False, malformed=False):
        args, native, sidecar = control("ledgerwash", block=block)
        return normalize(args, b"invalid native JSON" if malformed else native, sidecar)

    def test_unknown_present_format_retains_original_inventory_and_native_observation(self):
        original = self.observation()
        before = deepcopy(original)
        result, raw = apply_declaration(original, self.declaration, self.snapshot)
        receipt = loads_strict(raw)
        self.assertEqual(before, original)
        self.assertEqual(("unsupported", "not_judged", "unknown"),
            (result["status"], result["verdict"], result["coverage"]["status"]))
        self.assertIn("ledger_format_unsupported", result["reason_codes"])
        self.assertEqual(before["parsed_native"], result["parsed_native"])
        self.assertEqual(before["raw_inventory"], result["raw_inventory"])
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), receipt["files"][0]["source"]["sha256"])
        self.assertEqual(self.raw, (self.snapshot.root / "ledger.custom").read_bytes())
        validator = validators()["common"].evolve(schema={"$ref": "urn:delivery-review:contract:1:common#/$defs/engine"})
        self.assertEqual([], list(validator.iter_errors(result["engine"])))

    def test_absent_input_is_not_mislabeled_unsupported(self):
        declaration = dict(self.declaration, paths=["missing.custom"])
        result, raw = apply_declaration(self.observation(), declaration, self.snapshot)
        self.assertEqual("not_provided", result["status"])
        self.assertEqual("not_provided", result["coverage"]["status"])
        self.assertNotIn("ledger_format_unsupported", result["reason_codes"])
        self.assertEqual([{"path": "missing.custom", "present": False, "source": None}], loads_strict(raw)["files"])

    def test_supported_fixed_ec_scope_preserves_native_coverage(self):
        original = self.observation()
        result, raw = apply_declaration(original, {"format": "ec-ledger-v1", "paths": []}, self.snapshot)
        self.assertEqual("supported", loads_strict(raw)["status"])
        result.pop("ledger_input")
        self.assertEqual(original, result)

    def test_unknown_format_cannot_hide_native_errors_or_blocking_findings(self):
        for options in ({"malformed": True}, {"block": True}):
            with self.subTest(options=options):
                original = self.observation(**options)
                result, _ = apply_declaration(original, self.declaration, self.snapshot)
                self.assertEqual(original["parsed_native"], result["parsed_native"])
                self.assertEqual(original["engine"]["findings"], result["engine"]["findings"])
                if options.get("malformed"):
                    self.assertEqual("error", result["status"])
                    self.assertEqual(original["reason_codes"], result["reason_codes"])
                else:
                    self.assertTrue(result["known_block"])

    def test_partial_declared_inventory_keeps_missing_and_present_facts(self):
        receipt = observe_input(dict(self.declaration, paths=["missing.custom", "ledger.custom"]), self.snapshot)
        self.assertEqual("unsupported", receipt["status"])
        self.assertEqual([False, True], [row["present"] for row in receipt["files"]])

    def test_unsafe_duplicate_dynamic_or_ambiguous_declarations_are_refused(self):
        cases = [dict(self.declaration, paths=[path]) for path in
            ("../outside", "/absolute", "C:/absolute", ".git/config", "a\\b", "a//b", "a/../b", "CON", "bad\npath")]
        cases += [dict(self.declaration, paths=[]), dict(self.declaration, paths=["a", "A"]),
            dict(self.declaration, paths=["x" * 241]), dict(self.declaration, paths=[str(i) for i in range(17)]),
            dict(self.declaration, format="$(command)"), dict(self.declaration, future=True),
            {"format": "ec-ledger-v1", "paths": ["alternate-ec-root"]}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_declaration(value)


if __name__ == "__main__":
    unittest.main()
