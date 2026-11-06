"""Construct or check a policy identity; neither operation grants approval."""
import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import ContractError, MAX_JSON_BYTES, loads_strict
from .evidence.store import _directories, _read, _write
from .inspection_profile import load_inspection_profile, seal_inspection_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="check complete profile format and selector-bound identity")
    verify.add_argument("profile", type=Path)
    seal = commands.add_parser("seal", help="construct identity in a new file for independent operator review")
    seal.add_argument("profile", type=Path)
    seal.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        source = Path(os.path.abspath(args.profile))
        raw = _read(source, MAX_JSON_BYTES)
        if args.command == "seal":
            raw = seal_inspection_profile(loads_strict(raw))
            destination = Path(os.path.abspath(args.out))
            _directories(destination.parent)
            _write(destination, raw)
        profile = load_inspection_profile(raw)
        print(json.dumps({"operation": args.command, "status": "valid_identity", "approval": "not_established",
                          "policy_id": profile["policy"]["policy_id"]}, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError):
        print(json.dumps({"operation": args.command, "status": "error", "reason": "invalid_profile_or_output",
                          "approval": "not_established"}), file=sys.stderr)
        return 2
