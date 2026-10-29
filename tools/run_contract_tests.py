"""Run M0 unittests and emit counts without treating skips as success."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
M0_MINIMUM_TESTS = 51  # Existing baseline; a reduced suite needs explicit review.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report destination already exists")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = (result.wasSuccessful() and result.testsRun >= M0_MINIMUM_TESTS and not result.skipped
              and not result.expectedFailures and not result.unexpectedSuccesses)
    report = {
        "scope": "M0_CONTRACT", "product_integration": "NOT_RUN",
        "tests_run": result.testsRun,
        "minimum_tests": M0_MINIMUM_TESTS,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "passed": passed,
    }
    with args.report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
