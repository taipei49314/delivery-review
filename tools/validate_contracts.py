"""Validate M0 artifacts; never executes a collector, scanner, or PR code."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import load_document, validate_case, validate_engine_lock, validate_receipt, validate_report, validators
from delivery_review.freeze import verify_freeze


def main():
    count = verify_freeze(ROOT)
    validators()
    policy = load_document(ROOT / "examples/policy.json")
    validate_report(load_document(ROOT / "examples/report.json"), policy)
    validate_receipt(load_document(ROOT / "examples/receipt.json"))
    validate_case(load_document(ROOT / "examples/acceptance-template.json"))
    cases = sorted((ROOT / "fixtures/cases").glob("*.json"))
    for path in cases:
        validate_case(load_document(path))
    lock = ROOT / "locks/engine-candidates.json"
    if lock.exists():
        validate_engine_lock(load_document(lock))
    print(f"M0 contract consistency: 6 schemas, 4 examples, {len(cases)} synthetic cases valid.")
    print(f"M0 baseline: {count} frozen files verified.")
    print("Live collector/scanners and E01-E18 product integration: NOT_RUN.")


if __name__ == "__main__":
    main()
