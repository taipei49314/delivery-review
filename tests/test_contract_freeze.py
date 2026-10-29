from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, load_document
from delivery_review.freeze import verify_freeze


class FreezeTests(unittest.TestCase):
    def test_frozen_contract_inventory_and_bytes(self):
        self.assertEqual(20, verify_freeze(ROOT))

    def test_changed_contract_is_detected_without_rewriting_baseline(self):
        manifest = load_document(ROOT / "acceptance/contract-freeze.json")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for relative in [item["path"] for item in manifest["files"]] + ["acceptance/contract-freeze.json"]:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            with (target / "schemas/report.schema.json").open("ab") as stream:
                stream.write(b" ")
            with self.assertRaisesRegex(ContractError, "frozen bytes changed"):
                verify_freeze(target)


if __name__ == "__main__":
    unittest.main()
