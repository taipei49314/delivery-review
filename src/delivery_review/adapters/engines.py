"""Pure adapters for three exact public CLI contracts, never detector imports.

The caller owns isolated execution and immutable source snapshots. This module
checks their recorded inputs and native outputs; it neither runs a process nor
authenticates an execution, producer, or test checkout. Raw findings and gaps
remain available even when a normalized contract-v1 engine cannot be completed.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re

from ..contracts import ContractError

ADAPTER_VERSION = 1
PINS = {
    "checkwash": ("0.10.0", "3522d641d589ff9b75ad5e2f510d0884ca2520c32265d8397f0fcef226bc1171", 2),
    "pinwash": ("0.1.1", "d002160a73f5affbd4cf3446f676aa2809ef006a12feff77cab5edb4a8c62e03", 1),
    "ledgerwash": ("0.5.0", "5a38efe79c38f8d4e2ea25dae231d174e597ffbc5ee2e3df4d312fdb7bc128e9", 2),
}
STREAM_LIMITS = {"stdout": 2 * 1024 * 1024, "stderr": 1024 * 1024, "coverage": 2 * 1024 * 1024}
SEVERITIES = ("info", "warn", "high", "critical")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_RULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_ABSENT_LEDGER = {("governance/tasks", "no task records found at epoch"),
                  ("evidence/task-operations", "no operation receipts found at epoch")}
_LEDGER_SCOPE = "supported EC task/ordinary-repair receipt inputs, records, history and source bytes"


class EngineAdapterError(ContractError):
    """Malformed controller input, distinct from a recorded native engine error."""


def _need(condition, code):
    if not condition:
        raise EngineAdapterError(code)


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53 - 1


def _strings(value):
    return type(value) is list and all(type(item) is str for item in value)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate native JSON key")
        result[key] = value
    return result


def _json(raw):
    """Bounded strict UTF-8 JSON, retaining finite native floating-point data."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("native JSON BOM")
    def no_constant(_):
        raise ValueError("non-finite native number")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=no_constant)
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100000:
            raise ValueError("native JSON resource limit")
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
            pending.extend((key, depth + 1) for key in item)
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            item.encode("utf-8", errors="strict")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("non-finite native number")
    if type(value) is not dict:
        raise ValueError("native document is not an object")
    return value


def _parse(raw, reasons, label):
    try:
        return _json(raw)
    except (ValueError, UnicodeError, RecursionError):
        reasons.add(label + "_invalid_json")
        return None


def _arguments(name, process, subject, reasons):
    argv = process["argv"]
    try:
        marker = argv.index("-m")
        prefix = argv[1:marker]
        fresh_cache = (len(prefix) == 6 and prefix[:4] == ["-I", "-X", "utf8", "-B"]
                       and prefix[4] == "-X" and prefix[5].startswith("pycache_prefix=")
                       and len(prefix[5]) > len("pycache_prefix="))
        if not (prefix == ["-I", "-X", "utf8"] or fresh_cache) or argv[marker + 1] != name:
            raise ValueError
        tail = argv[marker + 2:]
        if name == "pinwash":
            if tail != ["check", subject["merge_base_sha"] + ".." + subject["head_sha"]]:
                raise ValueError
            return False
        if len(tail) < 2 or tail[0] != ("scan" if name == "ledgerwash" else "check"):
            raise ValueError
        if name == "checkwash" and tail[1] != subject["merge_base_sha"] + ".." + subject["head_sha"]:
            raise ValueError
        options, index, strict = {}, 2, False
        while index < len(tail):
            key = tail[index]
            if key == "--require-complete" and name == "ledgerwash" and not strict:
                strict = True
                index += 1
                continue
            if key in options or index + 1 >= len(tail):
                raise ValueError
            options[key] = tail[index + 1]
            index += 2
        allowed = ({"--repo", "--format", "--coverage-report", "--fail-on"} if name == "checkwash" else
                   {"--at", "--adapter", "--fail-on"})
        if not set(options) <= allowed or any(not value for value in options.values()):
            raise ValueError
        if name == "checkwash":
            if not {"--repo", "--format", "--coverage-report"} <= options.keys() or options["--format"] != "json":
                raise ValueError
            if options.get("--fail-on") != "high":
                reasons.add("native_threshold_unconfirmed")
        elif options.get("--at") != subject["head_sha"] or options.get("--adapter", "ec-ledger") != "ec-ledger":
            raise ValueError
        elif options.get("--fail-on", "high") != "high":
            reasons.add("native_threshold_unconfirmed")
        return strict
    except (ValueError, IndexError):
        reasons.add("engine_arguments_mismatch")
        return False


def _ref(value, expected, *, abbreviated=False):
    return (type(value) is str and re.fullmatch(r"[0-9a-f]{7,40}" if abbreviated else r"[0-9a-f]{40}", value)
            is not None and (expected.startswith(value) if abbreviated else value == expected))


def _run(native, name, version, subject, strict, reasons, target=None):
    run = native.get("run")
    if type(run) is not dict or run.get(name + "_version") != version:
        reasons.add("engine_run_identity_mismatch")
        return
    if name == "ledgerwash":
        if (run.get("adapter") != "ec-ledger" or type(run.get("spec_version")) is not int or run["spec_version"] != 5
                or type(run.get("require_complete")) is not bool or run["require_complete"] != strict
                or type(run.get("target")) is not str or run["target"] != target
                or not _ref(run.get("epoch"), subject["head_sha"]) or not _ref(run.get("ref"), subject["head_sha"])):
            reasons.add("engine_run_identity_mismatch")
    else:
        if not all(_ref(run.get(field), subject[target], abbreviated=name == "checkwash")
                   for field, target in (("base", "merge_base_sha"), ("head", "head_sha"))):
            reasons.add("engine_run_identity_mismatch")
        if name == "pinwash" and (type(run.get("spec_version")) is not int or run["spec_version"] != 29):
            reasons.add("unsupported_native_spec")


def _findings(native, name, reasons):
    rows = native.get("findings")
    if type(rows) is not list:
        reasons.add("native_findings_invalid")
        return [], []
    mapped, wrappers, seen = [], [], set()
    for row in rows:
        valid = (type(row) is dict and type(row.get("rule")) is str and _RULE.fullmatch(row["rule"]) is not None
                 and type(row.get("fingerprint")) is str and 0 < len(row["fingerprint"]) <= 256
                 and type(row.get("severity")) is str and row["severity"] in SEVERITIES
                 and type(row.get("message")) is str and type(row.get("path")) is str)
        if name == "checkwash" and valid:
            valid = (type(row.get("allowlisted")) is bool and _strings(row.get("escalators"))
                     and _strings(row.get("deescalators")))
        if not valid:
            reasons.add("native_finding_incompatible")
            continue
        identity = (row["rule"], row["fingerprint"])
        if identity in seen:
            reasons.add("duplicate_native_finding")
        seen.add(identity)
        disposition = ("added_exemption" if row["rule"] == "EXEMPTION_ADDED" else
                       "allowlisted" if name == "checkwash" and row["allowlisted"] else "original")
        normalized = {key: row[key] for key in ("fingerprint", "rule", "severity")}
        normalized["disposition"] = disposition
        mapped.append(normalized)
        wrappers.append({"normalized": deepcopy(normalized), "native": deepcopy(row)})
    if len(rows) > 1000:
        reasons.add("report_finding_limit")
        mapped = []  # Original rows remain in parsed_native; do not truncate a report.
    summary = native.get("summary")
    if (type(summary) is not dict or set(summary) != set(SEVERITIES)
            or any(not _integer(value) for value in summary.values())
            or any(summary.get(severity) != sum(type(row) is dict and row.get("severity") == severity for row in rows)
                   for severity in SEVERITIES)):
        reasons.add("native_summary_mismatch")
    return mapped, wrappers


def _rows(value, keys):
    return type(value) is list and all(type(row) is dict and all(type(row.get(key)) is str for key in keys) for row in value)


def _coverage(native, sidecar, name, version, subject, reasons):
    details, gaps, unknown, absent = [], False, False, False
    if name in {"checkwash", "pinwash"}:
        errors = native.get("config_errors")
        if not _strings(errors):
            reasons.add("native_config_errors_invalid")
            unknown = True
        elif errors:
            reasons.add("native_configuration_error")
            gaps = True
            details.extend("config: " + error for error in errors)
    if name == "checkwash":
        skipped = native.get("skipped_files")
        unread = native.get("unread", [])
        if not _strings(skipped) or not _rows(unread, ("path", "side")):
            reasons.add("native_coverage_invalid")
            unknown = True
        else:
            gaps |= bool(skipped or unread)
            details.extend("skipped: " + path for path in skipped)
            details.extend("unread: " + row["path"] + " (" + row["side"] + ")" for row in unread)
            if any(not _integer(row.get("size")) or not _integer(row.get("limit"), 1)
                   or row["side"] not in {"base", "head", "worktree"} for row in unread):
                reasons.add("native_coverage_invalid")
        valid = (type(sidecar) is dict and type(sidecar.get("checkwash_coverage_version")) is int
                 and sidecar["checkwash_coverage_version"] == 1
                 and sidecar.get("scope") == "javascript_assertion_candidates"
                 and type(sidecar.get("status")) is str and sidecar["status"] in {"incomplete", "no_known_gaps"}
                 and _rows(sidecar.get("files"), ("path", "side"))
                 and _rows(sidecar.get("gaps"), ("path", "side", "callee", "reason")))
        if not valid:
            reasons.add("coverage_sidecar_missing_or_invalid")
            unknown = True
        else:
            run = sidecar.get("run")
            if type(run) is not dict or run != native.get("run"):
                reasons.add("coverage_run_identity_mismatch")
                unknown = True
            if any(row["side"] not in {"before", "after"} for row in sidecar["files"] + sidecar["gaps"]):
                reasons.add("native_coverage_invalid")
            if any(not _integer(row.get(key), 1) for row in sidecar["gaps"] for key in ("line", "column")):
                reasons.add("native_coverage_invalid")
            if (sidecar["status"] == "incomplete") != bool(sidecar["gaps"]):
                reasons.add("coverage_state_mismatch")
            gaps |= bool(sidecar["gaps"])
            details.extend("JS: " + row["path"] + ": " + row["reason"] for row in sidecar["gaps"])
            details.append("JS assertion candidate coverage only; no whole-program coverage claim")
        findings = native.get("findings")
        if type(findings) is list and any(type(row) is dict and row.get("rule") == "TEST_FILE_UNPARSEABLE" for row in findings):
            gaps = True
            details.append("native TEST_FILE_UNPARSEABLE finding")
    elif name == "pinwash":
        rows = native.get("unknown_coverage")
        if not _rows(rows, ("path", "reason")):
            reasons.add("native_coverage_invalid")
            unknown = True
        else:
            gaps |= bool(rows)
            details.extend(row["path"] + ": " + row["reason"] for row in rows)
        details.append("fixed diff and native surface set only; not a whole-session review")
    else:
        coverage, residuals = native.get("coverage"), native.get("residuals")
        valid = (type(coverage) is dict and type(coverage.get("version")) is int and coverage["version"] == 1
                 and type(coverage.get("complete")) is bool and coverage.get("scope") == _LEDGER_SCOPE
                 and _strings(coverage.get("limitations")) and _rows(coverage.get("checks"), ("path", "locator", "check", "status", "reason"))
                 and _rows(residuals, ("path", "reason")))
        if not valid:
            reasons.add("native_coverage_invalid")
            unknown = True
        else:
            counts = coverage.get("counts")
            checks = coverage["checks"]
            expected = {status: sum(row["status"] == status for row in checks) for status in ("verified", "unverified", "mismatch")}
            if (type(counts) is not dict or set(counts) != set(expected) or any(not _integer(v) for v in counts.values())
                    or counts != expected or sum(expected.values()) != len(checks)
                    or not {"nonempty_tasks", "nonempty_receipts"} <= {row["check"] for row in checks}
                    or (coverage["complete"] and bool(residuals))
                    or coverage["complete"] != (expected["unverified"] == expected["mismatch"] == 0)):
                reasons.add("coverage_state_mismatch")
            gaps = not coverage["complete"] or bool(residuals)
            details.extend(row["path"] + ": " + row["reason"] for row in checks if row["status"] != "verified")
            details.extend(coverage["limitations"])
            absent = (not native.get("findings") and {(row["path"], row["reason"]) for row in residuals} == _ABSENT_LEDGER
                      and all(row["check"] in {"adapter_residual", "nonempty_tasks", "nonempty_receipts"} for row in checks))
    if len(details) > 1000 or any(len(text) > 4096 or not text for text in details):
        reasons.add("report_coverage_limit")
        details = ["Full native coverage retained in parsed_native/parsed_coverage and original bytes"]
    status = "not_provided" if absent else "unknown" if unknown else "gaps" if gaps else "sufficient"
    return {"status": status, "details": details}, absent


def normalize_engine_result(*, engine_name, engine_version, wheel_sha256, subject: dict,
                            process: dict, raw_files: Mapping[str, bytes], native_policy: dict) -> dict:
    """Validate a captured public invocation and preserve native errors and gaps.

Controller shape/resource errors raise EngineAdapterError. Native failures return
an error observation with original byte digests and all safely parsed native data.
The ``engine`` projection fits frozen report-v1 after a composer adds evidence_id.
"""
    _need(type(engine_name) is str and engine_name in PINS, "unsupported_engine")
    _need(type(engine_version) is str and 0 < len(engine_version) <= 64, "invalid_engine_version")
    _need(type(wheel_sha256) is str and _DIGEST.fullmatch(wheel_sha256) is not None, "invalid_engine_digest")
    _need(type(subject) is dict and set(subject) == {"merge_base_sha", "head_sha", "base_tip_sha"}
          and all(type(value) is str and _SHA.fullmatch(value) is not None for value in subject.values()), "invalid_engine_subject")
    _need(type(process) is dict and set(process) == {"exit_code", "termination", "argv", "duration_ms"}, "invalid_engine_process")
    _need(process["exit_code"] is None or (type(process["exit_code"]) is int and -255 <= process["exit_code"] <= 0xFFFFFFFF), "invalid_engine_exit")
    _need(type(process["termination"]) is str and process["termination"] in {"exited", "timeout", "output_limit", "spawn_error"}, "invalid_engine_termination")
    _need(_strings(process["argv"]) and 1 <= len(process["argv"]) <= 64
          and all(0 < len(item) <= 4096 and "\x00" not in item for item in process["argv"]), "invalid_engine_arguments")
    _need(_integer(process["duration_ms"]), "invalid_engine_duration")
    _need(type(native_policy) is dict and native_policy.get("threshold") == "high", "unsupported_native_threshold")
    try:
        policy_bytes = json.dumps(native_policy, allow_nan=False).encode("utf-8", errors="strict")
        _need(len(policy_bytes) <= 65536, "native_policy_limit")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise EngineAdapterError("invalid_native_policy") from exc
    _need(isinstance(raw_files, Mapping) and {"stdout", "stderr"} <= raw_files.keys()
          and set(raw_files) <= STREAM_LIMITS.keys(), "invalid_engine_stream_inventory")
    _need(all(type(raw) is bytes and len(raw) <= STREAM_LIMITS[name] for name, raw in raw_files.items()), "engine_stream_limit")
    reasons = set()
    version, digest, contract = PINS[engine_name]
    if engine_version != version or wheel_sha256 != digest:
        reasons.add("engine_pin_mismatch")
    strict = _arguments(engine_name, process, subject, reasons)
    inventory = {key: {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for key, raw in sorted(raw_files.items())}
    native = _parse(raw_files["stdout"], reasons, "stdout")
    sidecar = _parse(raw_files["coverage"], reasons, "coverage") if "coverage" in raw_files else None
    mapped, wrappers, coverage, absent = [], [], {"status": "unknown", "details": []}, False
    known_block, review_required = False, False
    if native is not None:
        field = engine_name + "_findings_version"
        if type(native.get(field)) is not int or native[field] != contract:
            reasons.add("unsupported_findings_contract")
        else:
            argv = process["argv"]
            target = (argv[argv.index("-m") + 3] if "-m" in argv and len(argv) > argv.index("-m") + 3
                      and engine_name == "ledgerwash" else None)
            _run(native, engine_name, engine_version, subject, strict, reasons, target)
            mapped, wrappers = _findings(native, engine_name, reasons)
            coverage, absent = _coverage(native, sidecar, engine_name, engine_version, subject, reasons)
            verdict = native.get("verdict")
            emitted_block = any(row["severity"] in {"high", "critical"} and row["disposition"] != "allowlisted" for row in mapped)
            known_block = verdict == "block" or emitted_block
            review_required = any(row["disposition"] == "added_exemption" for row in mapped)
            if type(verdict) is not str or verdict not in {"pass", "block"}:
                reasons.add("unsupported_native_verdict")
            elif (process["exit_code"], verdict) not in {(0, "pass"), (1, "block")}:
                reasons.add("native_exit_verdict_mismatch")
            if not {"native_finding_incompatible", "report_finding_limit", "native_findings_invalid"} & reasons:
                expected_findings = "block" if emitted_block else "pass"
                if native.get("finding_verdict" if engine_name == "ledgerwash" else "verdict") != expected_findings:
                    reasons.add("native_verdict_mismatch")
            if engine_name == "ledgerwash":
                finding_verdict = native.get("finding_verdict")
                if type(finding_verdict) is not str or finding_verdict not in {"pass", "block"}:
                    reasons.add("unsupported_finding_verdict")
                elif (type(native.get("coverage")) is dict and type(native["coverage"].get("complete")) is bool
                      and verdict != ("block" if finding_verdict == "block" or (strict and not native["coverage"]["complete"]) else "pass")):
                    reasons.add("native_verdict_mismatch")
                if type(native.get("observations")) is not list or any(type(row) is not dict for row in native["observations"]):
                    reasons.add("native_observations_invalid")
    if process["termination"] != "exited":
        reasons.add("engine_" + process["termination"])
    elif process["exit_code"] not in {0, 1}:
        reasons.add("native_engine_error")
    if process["exit_code"] is not None and process["exit_code"] > 255:
        reasons.add("report_exit_limit")
    status = "error" if reasons else "not_provided" if absent else "completed"
    verdict = native["verdict"] if status == "completed" else "not_judged"
    exit_code = process["exit_code"] if process["exit_code"] is not None and 0 <= process["exit_code"] <= 255 else None
    engine = {"name": engine_name, "version": engine_version, "artifact_sha256": wheel_sha256,
              "status": status, "verdict": verdict, "exit_code": exit_code,
              "raw_report_sha256": inventory["stdout"]["sha256"], "evidence_id": None,
              "findings": mapped, "coverage": coverage}
    return {"kind": "delivery-review-engine-observation", "adapter_version": ADAPTER_VERSION,
            "status": status, "verdict": verdict, "coverage": deepcopy(coverage), "reason_codes": sorted(reasons),
            "known_block": known_block, "review_required": review_required, "engine": engine,
            "findings": wrappers, "parsed_native": native, "parsed_coverage": sidecar,
            "raw_inventory": inventory, "subject": deepcopy(subject), "process": deepcopy(process),
            "native_policy": deepcopy(native_policy)}
