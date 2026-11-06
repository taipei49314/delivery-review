"""First-party Git-object policy proposals; no live delivery qualification."""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.inspection_profile import load_inspection_profile, seal_inspection_profile
from delivery_review.policy_change import MAX_POLICY_BYTES, observe_policy_change
from delivery_review.source_snapshot import materialize_snapshot
from test_inspection_profile import profile
from test_source_snapshot import Objects


def policy_profile():
    document = profile()
    document["profile_version"] = 2
    document["policy_input"] = {"format": "delivery-review-policy-v1", "path": "policy.json"}
    document["policy"]["requirements"].append({"id": "policy-diff", "kind": "policy_change", "required": True,
        "minimum_binding": "reported", "selector": {}})
    return load_inspection_profile(seal_inspection_profile(document))


def source(root, before, after, *, base_tip=None):
    value = Objects()
    def tree(raw, marker):
        files = {"README.txt": marker}
        if raw is not None:
            files["policy.json"] = raw
        return value.files(files)
    common = value.commit(tree(before, b"common"))
    base = value.commit(tree(before if base_tip is None else base_tip, b"base tip"), [common])
    head = value.commit(tree(after, b"head"), [common])
    return materialize_snapshot(root, repository="fixture-owner/fixture-repo", head_sha=head,
        base_tip_sha=base, objects=value.objects, git_executable=shutil.which("git"))


class PolicyChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = policy_profile()
        self.original = deepcopy(self.profile["policy"])
        self.count = 0

    def observe(self, before, after, **options):
        self.count += 1
        snapshot = source(self.root / str(self.count), before, after, **options)
        return observe_policy_change(self.profile, snapshot), snapshot

    def test_declared_relaxation_preserves_original_bytes_and_approved_authority(self):
        changed = deepcopy(self.original)
        changed["requirements"][0]["selector"]["min_tests"] = 0
        changed["requirements"] = [row for row in changed["requirements"] if row["id"] != "checkwash"]
        before = canonical_bytes(self.original) + b"\r\n"
        after = canonical_bytes(changed) + b"\n"
        result, snapshot = self.observe(before, after)
        observation = result.observation
        self.assertEqual(("satisfied", "review"), (observation["outcome"], observation["effect"]))
        self.assertEqual(["unapproved_policy_relaxation"], observation["reason_codes"])
        self.assertIn("minimum_tests_lowered", observation["relaxations"])
        self.assertIn("required_obligation_removed_or_optional", observation["relaxations"])
        self.assertEqual(before, result.raw_files["policy-change/base.json"])
        self.assertEqual(after, result.raw_files["policy-change/head.json"])
        self.assertEqual(self.original, loads_strict(result.raw_files["policy-change/approved-policy.json"]))
        for label, raw in (("base", before), ("head", after)):
            document = observation["documents"][label]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), document["sha256"])
            self.assertEqual(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(), document["oid"])
        self.assertEqual(snapshot.merge_base_sha, observation["documents"]["base"]["commit_sha"])
        self.assertNotEqual(snapshot.base_tip_sha, observation["documents"]["base"]["commit_sha"])

    def test_identical_semantics_clear_despite_whitespace_or_different_base_tip(self):
        raw = canonical_bytes(self.original)
        changed_base = deepcopy(self.original)
        changed_base["requirements"][0]["selector"]["min_tests"] = 20
        result, _ = self.observe(raw + b"\r\n", raw + b"\n", base_tip=canonical_bytes(changed_base))
        self.assertEqual(("satisfied", "clear"), (result.observation["outcome"], result.observation["effect"]))
        self.assertFalse(result.observation["changed"])
        self.assertEqual([], result.observation["changes"])

    def test_tightening_requires_review_without_claiming_weakening(self):
        changed = deepcopy(self.original)
        changed["requirements"][0]["selector"]["min_tests"] = 2
        result, _ = self.observe(canonical_bytes(self.original), canonical_bytes(changed))
        self.assertEqual(["policy_change_requires_review"], result.observation["reason_codes"])
        self.assertEqual([], result.observation["relaxations"])
        self.assertEqual("review", result.observation["effect"])

    def test_addition_deletion_and_both_absent_are_distinct(self):
        raw = canonical_bytes(self.original)
        added, _ = self.observe(None, raw)
        removed, _ = self.observe(raw, None)
        absent, _ = self.observe(None, None)
        self.assertEqual(("satisfied", "review"), (added.observation["outcome"], added.observation["effect"]))
        self.assertEqual(["unapproved_policy_relaxation"], removed.observation["reason_codes"])
        self.assertEqual(["policy_removed"], removed.observation["relaxations"])
        self.assertEqual("missing", absent.observation["outcome"])
        self.assertEqual(["policy_proposal_missing"], absent.observation["reason_codes"])
        self.assertNotIn("policy-change/head.json", removed.raw_files)

    def test_malformed_duplicate_deep_and_oversized_input_retained_as_error(self):
        raw = canonical_bytes(self.original)
        malformed = (b'{"secret":"never copy parser content","secret":1}', b'{"schema_version":99}',
                     b'[' * 40 + b'0' + b']' * 40, b' ' * (MAX_POLICY_BYTES + 1))
        for candidate in malformed:
            with self.subTest(size=len(candidate)):
                result, _ = self.observe(raw, candidate)
                self.assertEqual("error", result.observation["outcome"])
                self.assertEqual("clear", result.observation["effect"])
                self.assertEqual(candidate, result.raw_files["policy-change/head.json"])
                self.assertNotIn("secret", str(result.observation["reason_codes"]))
        result, _ = self.observe(b"invalid-base", raw)
        self.assertEqual("error", result.observation["outcome"])
        self.assertEqual(raw, result.raw_files["policy-change/head.json"])

    def test_unapproved_identity_format_path_and_requirement_changes_rejected(self):
        changes = [lambda p: p["policy_input"].update(path="other.json"),
                   lambda p: p["policy_input"].update(format="arbitrary-json")]
        for mutate in changes:
            document = deepcopy(self.profile)
            mutate(document)
            with self.subTest(document=document), self.assertRaises(ContractError):
                load_inspection_profile(canonical_bytes(document))
        for declaration in (None, {"format": "delivery-review-policy-v1", "path": "../policy.json"},
                            {"format": "delivery-review-policy-v1", "path": "policy.json", "script": "ignored"}):
            document = deepcopy(self.profile)
            document["policy_input"] = declaration
            with self.subTest(declaration=declaration), self.assertRaises(ContractError):
                seal_inspection_profile(document)
        for change in ("v1", "no-declaration", "no-requirement", "duplicate-requirement", "selector"):
            document = deepcopy(self.profile)
            if change == "v1":
                document["profile_version"] = 1
            elif change == "no-declaration":
                document.pop("policy_input")
            elif change == "no-requirement":
                document["policy"]["requirements"].pop()
            elif change == "duplicate-requirement":
                document["policy"]["requirements"].append(dict(document["policy"]["requirements"][-1], id="another-policy"))
            else:
                document["policy"]["requirements"][-1]["selector"] = {"job_name": "not supported"}
            with self.subTest(change=change), self.assertRaises(ContractError):
                seal_inspection_profile(document)

    def test_same_static_inputs_are_deterministic(self):
        raw = canonical_bytes(self.original)
        first, snapshot = self.observe(raw, raw)
        second = observe_policy_change(self.profile, snapshot)
        self.assertEqual(first.observation, second.observation)
        self.assertEqual(dict(first.raw_files), dict(second.raw_files))

    def test_other_json_is_not_discovered_as_a_policy(self):
        self.profile["policy_input"]["path"] = "unrelated.json"
        self.profile = load_inspection_profile(seal_inspection_profile(self.profile))
        result, _ = self.observe(canonical_bytes(self.original), b"invalid policy.json")
        self.assertEqual("missing", result.observation["outcome"])
        self.assertNotIn("policy-change/head.json", result.raw_files)


if __name__ == "__main__":
    unittest.main()
