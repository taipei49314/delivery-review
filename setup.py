"""Build-time resource copying and source provenance; never run reviewed code.

The generated inventory is a consistency record, not a signature. Distribution
authenticity comes from separately verified release hashes/provenance.
"""
import json
import importlib.util
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("delivery_review_build_identity", ROOT / "build_identity.py")
_identity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_identity)
expected_package_files = _identity.expected_package_files
source_identity = _identity.source_identity
verify_build_package = _identity.verify_build_package


class Build(build_py):
    def run(self):
        identity = source_identity(ROOT)
        expected = expected_package_files(ROOT)
        package = Path(self.build_lib) / "delivery_review"
        verify_build_package(package, expected, complete=False)
        super().run()
        for name, source in expected.items():
            if name.startswith("data/"):
                destination = package / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
        inventory = verify_build_package(package, expected, complete=True)
        if source_identity(ROOT) != identity:
            identity = {**identity, "source_state": "unverified", "source_sha": None}
        result = {"kind": "delivery-review-build", "version": 1,
                  "package_version": self.distribution.get_version(),
                  "source_sha": identity["source_sha"], "source_state": identity["source_state"],
                  "files": inventory}
        (package / "_build_info.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")


class Source(sdist):
    def make_release_tree(self, base_dir, files):
        identity = source_identity(ROOT)
        super().make_release_tree(base_dir, files)
        (Path(base_dir) / "_source_provenance.json").write_text(
            json.dumps(identity, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


setup(cmdclass={"build_py": Build, "sdist": Source})
