"""Bounded EC workload for M0 checks; never a product integration receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    started = time.monotonic()
    if (os.environ.get("EC_WORKLOAD_REPOSITORY") != "taipei49314/delivery-review"
            or os.environ.get("EC_WORKLOAD_NAME") != "m0-contracts"
            or os.environ.get("EC_WORKLOAD_MODE") != "single"):
        raise RuntimeError("run only through the admitted EC m0-contracts workload")
    source = os.environ["EC_WORKLOAD_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise RuntimeError("exact product commit is required")
    if Path(os.environ["EC_WORKLOAD_SOURCE"]).resolve() != ROOT:
        raise RuntimeError("EC source directory differs")
    if json.loads(Path(os.environ["EC_WORKLOAD_PARAMETERS"]).read_bytes()) != {}:
        raise RuntimeError("this workload accepts no parameters")
    if platform.system() != "Windows" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("this lane qualifies Windows / Python 3.12 only")

    out = Path(os.environ["EC_WORKLOAD_OUT"]).resolve()
    work = Path(os.environ["EC_WORKLOAD_WORK"]).resolve()
    if not out.is_dir() or not work.is_dir() or out == work or out.is_relative_to(ROOT) or work.is_relative_to(ROOT):
        raise RuntimeError("EC must supply distinct job-local work/output directories outside source")
    venv = work / "m0-venv"
    if venv.exists() or (out / "m0-result.json").exists():
        raise RuntimeError("fresh execution directories are required")
    record = {
        "schema_version": 1, "scope": "M0_CONTRACT", "product_integration": "NOT_RUN",
        "repository": "taipei49314/delivery-review", "workload": "m0-contracts",
        "source_sha": source, "host": platform.node(),
        "platform": platform.platform(), "python_version": platform.python_version(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "status": "failed", "steps": [], "files": [],
    }
    inputs = ("requirements-contract.txt", "uv.lock", "ec-workloads.json", "acceptance/contract-freeze.json")
    before = {name: digest(ROOT / name) for name in inputs}
    record["input_sha256"] = before
    deadline = started + 540  # Leave EC's ten-minute outer bound time for receipt publication.

    def run(name, command, limit=120):
        step = {"name": name, "command": command, "exit_code": None}
        record["steps"].append(step)
        clock = time.monotonic()
        remaining = min(limit, deadline - clock)
        if remaining <= 0:
            raise TimeoutError("workload deadline exhausted")
        stdout = out / f"{name}.stdout.log"
        stderr = out / f"{name}.stderr.log"
        try:
            with stdout.open("xb") as output, stderr.open("xb") as errors:
                process = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=errors, timeout=remaining)
            step["exit_code"] = process.returncode
        except subprocess.TimeoutExpired:
            step["timed_out"] = True
            raise
        finally:
            step["seconds"] = round(time.monotonic() - clock, 3)
        if step["exit_code"] != 0:
            raise RuntimeError(f"{name} failed with native exit {step['exit_code']}")
        return stdout

    try:
        head = run("source", ["git", "rev-parse", "HEAD"], 20)
        if head.read_text(encoding="ascii").strip() != source:
            raise RuntimeError("checkout does not match the reviewed commit")
        run("venv", [sys.executable, "-I", "-m", "venv", str(venv)], 90)
        python = str(venv / "Scripts/python.exe")
        run("dependencies", [python, "-I", "-m", "pip", "--isolated", "install",
                             "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
                             "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "--no-compile",
                             "-r", "requirements-contract.txt"], 300)
        run("contracts", [python, "-I", "-B", "tools/validate_contracts.py"], 60)
        # The complete suite now includes bounded subprocess and package checks.
        # Keep it inside the existing shared 540-second deadline and EC's
        # admitted ten-minute outer limit, while allowing more than the old
        # 51-test baseline's ninety-second per-step allowance.
        run("unittest", [python, "-I", "-B", "tools/run_contract_tests.py", "--report", str(out / "unittest.json")], 480)
        record["unittest"] = json.loads((out / "unittest.json").read_bytes())
        status = run("source-status", ["git", "status", "--porcelain", "--untracked-files=no"], 20)
        if status.read_bytes().strip() or {name: digest(ROOT / name) for name in inputs} != before:
            raise RuntimeError("validation changed tracked source or input locks")
        record["status"] = "passed"
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        for path in sorted(out.glob("*")):
            if path.is_file():
                record["files"].append({"path": path.name, "size_bytes": path.stat().st_size, "sha256": digest(path)})
        write_json(out / "m0-result.json", record)
        counts = record.get("unittest", {})
        summary = (
            "# delivery-review / m0-contracts\n\n"
            f"Status: **{record['status'].upper()}**\n\n"
            f"Source: `{source}`\n\n"
            f"Host: `{record['host']}`; Windows / Python `{record['python_version']}`.\n\n"
            f"Tests run: {counts.get('tests_run', 'NOT_RUN')}; failures: {counts.get('failures', 'unknown')}; "
            f"errors: {counts.get('errors', 'unknown')}; skipped: {counts.get('skipped', 'unknown')}.\n\n"
            "This checks M0 schemas, authored fixtures and contract invariants. "
            "Collector/scanner/product integrations remain **NOT_RUN**; Linux is not qualified by this run.\n"
        )
        if "error" in record:
            summary += f"\nFailure: {record['error']}\n"
        with (out / "SUMMARY.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(summary)
    print(json.dumps({"status": record["status"], "source_sha": source, "scope": "M0_CONTRACT"}))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
