"""Verify or replay local bundles; does not inspect GitHub or authenticate claims."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from delivery_review.evidence import BundleError, replay_bundle, verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="check local byte integrity; operational exit 0 on success")
    verify.add_argument("source", type=Path)
    replay = commands.add_parser("replay", help="save offline replay in a new directory; report exit 2 even on success")
    replay.add_argument("source", type=Path)
    replay.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            saved = verify_bundle(args.source)
            result = {"operation": "verify", "integrity": "verified", "authentication": "not_established",
                      "replay_basis": "saved_normalized_report", "observed_at": saved.report["observed_at"]}
            code = 0  # Operational success, not a product readiness decision.
        else:
            report = replay_bundle(args.source, args.destination)
            result = {"operation": "replay", "status": "written", "report": str(args.destination / "report.json"),
                      "replay_basis": "saved_normalized_report", "authentication": "not_established",
                      "state": report["state"], "ready_for_review": report["ready_for_review"],
                      "exit_code": report["exit_code"]}
            code = report["exit_code"]
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return code
    except BundleError as exc:
        print(json.dumps({"operation": args.command, "status": "error", "error": str(exc)}, ensure_ascii=True),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
