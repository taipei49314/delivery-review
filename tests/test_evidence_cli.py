"""Exercise the offline command boundary, including its intentional exit 2."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_evidence_store import create_synthetic, tree_bytes

ROOT = Path(__file__).resolve().parents[1]


class EvidenceCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        create_synthetic(self.source)

    def command(self, *arguments):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(ROOT / "tools/evidence.py"), *map(str, arguments)],
            cwd=self.root, stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        )

    def test_verify_exit_zero_is_integrity_only(self):
        result = self.command("verify", self.source)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual("verified", metadata["integrity"])
        self.assertEqual("not_established", metadata["authentication"])
        self.assertNotIn("ready_for_review", metadata)

    def test_successful_replay_writes_output_but_exits_two(self):
        before = tree_bytes(self.source)
        output = self.root / "replay"
        result = self.command("replay", self.source, output)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual("written", metadata["status"])
        self.assertEqual("unknown", metadata["state"]["freshness"])
        self.assertFalse(metadata["ready_for_review"])
        report = json.loads((output / "report.json").read_bytes())
        self.assertEqual(report["state"], metadata["state"])
        self.assertEqual(before, tree_bytes(self.source))

    def test_tampering_is_an_error_without_stdout_or_output_bundle(self):
        (self.source / "raw/junit.xml").write_bytes(b"tampered")
        output = self.root / "replay"
        result = self.command("replay", self.source, output)
        self.assertEqual(2, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertEqual("error", json.loads(result.stderr)["status"])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
