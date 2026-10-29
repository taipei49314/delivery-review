"""Synthetic wheel-byte checks; no engine installation or network access."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "verify_engine_artifacts.py"
SPEC = importlib.util.spec_from_file_location("engine_artifact_verifier", MODULE_PATH)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def wheel_bytes(*, name="sample", version="1.0", replacement=None, extra=None,
                omit_record=False, duplicate=False, omit_hash=False):
    prefix = "sample-1.0.dist-info"
    files = {"sample/__init__.py": b"raise RuntimeError('must never import')\n",
             f"{prefix}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n".encode(),
             f"{prefix}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n"}
    if extra:
        files.update(extra)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for path, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([path, "" if omit_hash else f"sha256={digest}", len(data)])
    writer.writerow([f"{prefix}/RECORD", "", ""])
    if not omit_record:
        files[f"{prefix}/RECORD"] = output.getvalue().encode()
    if replacement:
        files.update(replacement)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            entry = zipfile.ZipInfo(path)
            # Preserve hostile spelling on Windows, where ZipInfo normally
            # rewrites backslashes before a verifier can observe them.
            entry.filename = path
            archive.writestr(entry, data)
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("sample/__init__.py", files["sample/__init__.py"])
    return result.getvalue()


class WheelVerificationTests(unittest.TestCase):
    def verify(self, data, **overrides):
        args = dict(expected_name="sample", expected_version="1.0",
                    expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data))
        args.update(overrides)
        return verifier.verify_wheel_bytes(data, **args)

    def test_valid_inventory_and_record_without_importing_subject(self):
        result = self.verify(wheel_bytes())
        self.assertEqual(result["status"], "candidate_byte_verified")
        self.assertEqual(result["metadata"], {"name": "sample", "version": "1.0"})
        self.assertEqual(result["wheel"]["member_count"], 4)
        self.assertEqual(result["wheel"]["record_verified_files"], 3)
        self.assertEqual(len(result["wheel"]["inventory_sha256"]), 64)

    def test_archive_digest_and_byte_length_are_independent_gates(self):
        data = wheel_bytes()
        with self.assertRaisesRegex(verifier.VerificationError, "SHA-256 differs"):
            self.verify(data, expected_sha256="0" * 64)
        with self.assertRaisesRegex(verifier.VerificationError, "byte length"):
            self.verify(data, expected_size=len(data) + 1)

    def test_member_mutation_is_rejected_even_if_archive_hash_matches(self):
        data = wheel_bytes(replacement={"sample/__init__.py": b"changed\n"})
        with self.assertRaisesRegex(verifier.VerificationError, "RECORD digest"):
            self.verify(data)

    def test_metadata_identity_must_match_separate_candidate(self):
        for data in (wheel_bytes(name="impostor"), wheel_bytes(version="2.0")):
            with self.subTest(data_digest=hashlib.sha256(data).hexdigest()):
                with self.assertRaisesRegex(verifier.VerificationError, "METADATA identity"):
                    self.verify(data)

    def test_unsafe_and_duplicate_archive_members_are_rejected(self):
        for data in (wheel_bytes(extra={"../outside": b"x"}),
                     wheel_bytes(extra={"/absolute": b"x"}),
                     wheel_bytes(extra={"sample\\escape": b"x"}), wheel_bytes(duplicate=True)):
            with self.subTest(data_digest=hashlib.sha256(data).hexdigest()):
                with self.assertRaisesRegex(verifier.VerificationError, "unsafe or duplicate"):
                    self.verify(data)

    def test_missing_record_unlisted_file_and_hashless_member_are_rejected(self):
        for data in (wheel_bytes(omit_record=True),
                     wheel_bytes(replacement={"unrecorded": b"x"}), wheel_bytes(omit_hash=True)):
            with self.subTest(data_digest=hashlib.sha256(data).hexdigest()):
                with self.assertRaises(verifier.VerificationError):
                    self.verify(data)

    def test_expanded_archive_limit_is_enforced_before_reading(self):
        with patch.object(verifier, "MAX_EXPANDED", 1):
            with self.assertRaisesRegex(verifier.VerificationError, "expanded byte"):
                self.verify(wheel_bytes())

    def test_urls_must_use_the_expected_public_https_host(self):
        for url in ("http://pypi.org/pypi/x/json", "https://pypi.org.evil/x",
                    "https://user:secret@pypi.org/x", "https://pypi.org:444/x"):
            with self.subTest(url=url):
                with self.assertRaises(verifier.VerificationError):
                    verifier._approved_url(url, "pypi.org")

    def test_network_reader_rejects_oversized_body_without_content_length(self):
        class Response(io.BytesIO):
            url = "https://pypi.org/pypi/sample/1.0/json"
            headers = {}
        response = Response(b"12345")
        opener = SimpleNamespace(open=lambda request, timeout: response)
        with patch.object(verifier.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(verifier.VerificationError, "byte bound"):
                verifier._fetch(response.url, host="pypi.org", limit=4)

    def test_network_reader_enforces_transfer_deadline(self):
        class Response(io.BytesIO):
            url = "https://pypi.org/pypi/sample/1.0/json"
            headers = {}
        response = Response(b"12345")
        opener = SimpleNamespace(open=lambda request, timeout: response)
        with patch.object(verifier.urllib.request, "build_opener", return_value=opener):
            with patch.object(verifier.time, "monotonic", side_effect=[0, 121]):
                with self.assertRaisesRegex(verifier.VerificationError, "transfer deadline"):
                    verifier._fetch(response.url, host="pypi.org", limit=10)

    def test_offline_cli_writes_candidate_receipt_and_never_fetches(self):
        data = wheel_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / "sample-1.0-py3-none-any.whl"
            wheel.write_bytes(data)
            lock = root / "input.json"
            lock.write_text(json.dumps({"schema_version": 1, "lock_kind": "engine_candidates", "engines": [
                {"name": "sample", "version": "1.0", "artifact": {
                    "filename": wheel.name, "url": "https://files.pythonhosted.org/sample.whl",
                    "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}}]}), encoding="utf-8")
            receipt = root / "receipt.json"
            with patch.object(verifier, "_fetch", side_effect=AssertionError("network is forbidden")):
                with patch("sys.stdout", new_callable=io.StringIO):
                    code = verifier.main(["--lock", str(lock), "--wheel-dir", str(root), "--receipt", str(receipt)])
            self.assertEqual(code, 0)
            engine = json.loads(receipt.read_text())["engines"][0]
            self.assertEqual(engine["integration_status"], "NOT_RUN")
            self.assertEqual(engine["verification"]["status"], "candidate_byte_verified")


if __name__ == "__main__":
    unittest.main()
