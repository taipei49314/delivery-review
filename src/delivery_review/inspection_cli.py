"""Inspect a fixed PR, replay original evidence, or refresh into a new directory."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import ContractError, MAX_JSON_BYTES, loads_strict, require
from .evidence.inspection import verify_inspection
from .evidence.store import _read
from .inspection_profile import load_inspection_profile
from .inspection_runner import InspectionTools, inspect_pr, replay_inspection
from .provenance import collector_identity


def load_tools(path, *, live):
    path = Path(os.path.abspath(path))
    value = loads_strict(_read(path, MAX_JSON_BYTES))
    require(type(value) is dict and set(value) == {"git_executable", "python_executable", "engine_wheels", "gh_executable", "trusted_root"},
        "invalid_inspection_toolchain")
    def local(name):
        require(type(name) is str and 0 < len(name) <= 4096 and not any(ord(c) < 32 for c in name), "invalid_toolchain_path")
        supplied = Path(name)
        return (supplied if supplied.is_absolute() else path.parent / supplied).resolve(strict=True)
    wheels = value["engine_wheels"]
    require(type(wheels) is dict and set(wheels) <= {"checkwash", "pinwash", "ledgerwash"}, "invalid_toolchain_wheels")
    require(not live or set(wheels) == {"checkwash", "pinwash", "ledgerwash"}, "missing_toolchain_wheels")
    return InspectionTools(local(value["git_executable"]), local(value["python_executable"]),
        {name: local(wheel) for name, wheel in wheels.items()}, local(value["gh_executable"]),
        _read(local(value["trusted_root"]), 1024 * 1024))


def main(argv=None, *, identity_provider=None):
    parser = argparse.ArgumentParser(prog="delivery-review", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="collect and evaluate evidence under an independently approved profile")
    inspect.add_argument("pr_url")
    inspect.add_argument("--policy", required=True, type=Path, help="complete sealed inspection profile approved outside the PR")
    for name, command in (("inspect", inspect), ("replay", commands.add_parser("replay", help="offline raw replay with fresh offline signature verification")),
                           ("refresh", commands.add_parser("refresh", help="collect new evidence using a saved inspection policy and PR"))):
        if name != "inspect":
            command.add_argument("inspection", type=Path)
        command.add_argument("--out", required=True, type=Path)
        command.add_argument("--tools", required=True, type=Path, help="operator-supplied paths to pinned tools and offline wheels")
        if name != "replay":
            command.add_argument("--anonymous", action="store_true", help="ignore GH_TOKEN and GITHUB_TOKEN")
    verify = commands.add_parser("verify", help="verify the exact local file inventory; no authentication claim")
    verify.add_argument("inspection", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            saved = verify_inspection(args.inspection)
            print(json.dumps({"operation": "verify", "status": "verified", "files": len(saved.files),
                "authentication": "not_established", "scope": "local_file_integrity"}))
            return 0
        tools = load_tools(args.tools, live=args.command != "replay")
        if args.command == "replay":
            report = replay_inspection(args.inspection, args.out, tools=tools)
        else:
            if args.command == "inspect":
                profile = _read(Path(os.path.abspath(args.policy)), MAX_JSON_BYTES)
                pr_url = args.pr_url
            else:
                source = Path(os.path.abspath(args.inspection))
                require(not Path(os.path.abspath(args.out)).is_relative_to(source), "output_must_be_outside_inspection")
                saved = verify_inspection(source)
                profile = saved.files["raw/inspection-profile.json"]
                pr_url = loads_strict(saved.files["raw/execution-plan.json"])["pr_url"]
            load_inspection_profile(profile)
            identity = (identity_provider or collector_identity)()
            token = None if args.anonymous else os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            report = inspect_pr(pr_url, profile, args.out, tools=tools, collector=identity, token=token)
        print(json.dumps({"operation": args.command, "status": "written", "state": report["state"],
            "ready_for_review": report["ready_for_review"], "exit_code": report["exit_code"], "output": str(args.out)}, sort_keys=True))
        return report["exit_code"]
    except (ContractError, OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, FileExistsError): reason = "output_already_exists"
        elif isinstance(exc, FileNotFoundError): reason = "input_or_parent_not_found"
        elif isinstance(exc, PermissionError): reason = "filesystem_permission_denied"
        elif str(exc) == "collector_source_must_be_clean": reason = "collector_source_must_be_clean"
        elif str(exc) == "inspection_source_unavailable": reason = "source_unavailable_raw_capture_retained"
        else: reason = "invalid_input_or_operation_failed"
        print(json.dumps({"operation": args.command, "status": "error", "reason": reason}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
