"""Inspect GitHub metadata or replay a raw capture; no delivery readiness decision."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import ContractError, MAX_JSON_BYTES
from delivery_review.evidence.capture import CaptureError, verify_capture
from delivery_review.evidence.store import _read
from delivery_review.observation_profile import load_observation_profile
from delivery_review.observations import new_output_directory, replay_observations, write_observations
from delivery_review.providers.github_observations import map_capture
from delivery_review.providers.github_routes import parse_pr_url


def collector_identity() -> dict:
    """Live provenance requires an exact clean source checkout, including new files."""
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL,
                                         timeout=10).decode("ascii").strip()
        status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"],
                                         cwd=ROOT, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ContractError("collector_source_unavailable") from exc
    if status or len(commit) != 40:
        raise ContractError("collector_source_must_be_clean")
    return {"version": "0.0.0", "source_sha": commit}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="capture read-only platform metadata into a new directory")
    inspect.add_argument("pr_url")
    inspect.add_argument("--profile", required=True, type=Path)
    inspect.add_argument("--out", required=True, type=Path)
    inspect.add_argument("--anonymous", action="store_true", help="do not use GH_TOKEN or GITHUB_TOKEN")
    for command in ("replay", "refresh"):
        item = commands.add_parser(command, help="create new observations from saved bytes" if command == "replay"
                                   else "acquire a new capture using the saved profile and PR")
        item.add_argument("capture", type=Path)
        item.add_argument("--out", required=True, type=Path)
        if command == "refresh":
            item.add_argument("--anonymous", action="store_true")
    verify = commands.add_parser("verify", help="check local raw capture integrity; operational status only")
    verify.add_argument("capture", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            capture = verify_capture(args.capture)
            print(json.dumps({"operation": "verify", "status": "verified", "authentication": "not_established",
                              "scope": "github_platform_observations", "records": len(capture.manifest["records"])}))
            return 0
        if args.command == "replay":
            result = replay_observations(args.capture, args.out)
        else:
            if args.command == "inspect":
                raw_profile = _read(Path(os.path.abspath(args.profile)), MAX_JSON_BYTES)
                pr_url = args.pr_url
            else:
                saved = verify_capture(args.capture)
                raw_profile = saved.profile_bytes
                initial_pr = [record for record in saved.manifest["records"]
                              if record["phase"] == "initial" and record["role"] == "pull_request"]
                if len(initial_pr) != 1:
                    raise ContractError("saved_pr_identity_unavailable")
                number = initial_pr[0]["request"]["path"].rsplit("/", 1)[1]
                pr_url = f"https://github.com/{saved.profile['repository']['full_name']}/pull/{number}"
            profile = load_observation_profile(raw_profile)
            parse_pr_url(pr_url, profile)
            identity = collector_identity()
            from delivery_review.providers.github_collect import collect_observations
            from delivery_review.providers.github_http import GitHubTransport
            token = None if args.anonymous else os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            transport = GitHubTransport(token=token)
            directory = new_output_directory(args.out, source=args.capture if args.command == "refresh" else None)
            capture = collect_observations(pr_url, raw_profile, directory / "capture", transport=transport,
                                           collector=identity)
            result = map_capture(capture, mode="live")
            write_observations(result, directory)
        print(json.dumps({"operation": args.command, "status": "written", "scope": result["scope"],
                          "collection_status": result["collection_status"], "freshness": result["freshness"],
                          "tested_source": result["tested_source"], "output": str(args.out)}, sort_keys=True))
        return 0 if result["collection_status"] == "complete" else 2
    except (ContractError, OSError, ValueError) as exc:
        # Error messages can contain untrusted paths/JSON; never emit them or credentials.
        if str(exc) == "collector_source_must_be_clean":
            code = "collector_source_must_be_clean"
        elif isinstance(exc, FileExistsError):
            code = "output_already_exists"
        elif isinstance(exc, FileNotFoundError):
            code = "input_or_parent_not_found"
        elif isinstance(exc, PermissionError):
            code = "filesystem_permission_denied"
        elif isinstance(exc, CaptureError):
            code = "invalid_or_unreadable_capture"
        else:
            code = "invalid_input_or_operation_failed"
        print(json.dumps({"operation": args.command, "status": "error", "reason": code,
                          "scope": "github_platform_observations"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
