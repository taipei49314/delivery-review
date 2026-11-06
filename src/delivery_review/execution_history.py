"""Diagnostic aliases from complete original execution inventories.

These observations never select a historical run or change obligation outcomes,
binding, counts or effects. "Latest" means the independently selected current-head
run and attempt; it does not imply that an older successful run was observed.
"""
from copy import deepcopy

from .contracts import ContractError, loads_strict
from .evidence.capture import VerifiedCapture, parse_capture
from .providers.github_observations import map_capture
from .providers.github_routes import positive, request_path, sha
from .providers.github_selection import STATUSES, _association, select_workflow_runs


def _document(capture, record):
    body = record["body"]
    if (record["status_code"] != 200 or record["error"] is not None or body is None
            or not body["complete"]):
        return None
    value = loads_strict(capture.bodies[body["path"]])
    return value if type(value) is dict else None


def _one(capture, role, **identity):
    rows = [row for row in capture.manifest["records"] if row["phase"] == "initial" and row["role"] == role
        and all(row[key] == value for key, value in identity.items())]
    if len(rows) != 1:
        return None, None
    return rows[0], _document(capture, rows[0])


def execution_reason_aliases(capture, *, workflow_id, outcome, effect):
    """Reparse raw facts; return only extra reasons, never accepted evidence.

Callers gate this on their versioned interpretation basis. In particular, an
empty head-filtered listing cannot establish the existence of old-head evidence.
"""
    try:
        if type(capture) is not VerifiedCapture:
            return []
        capture = parse_capture(capture.manifest_bytes, capture.profile_bytes, capture.bodies)
        profile = capture.profile
        spec = next((row for row in profile["workflows"] if row["id"] == workflow_id), None)
        observations = map_capture(capture)
        subject = observations["subject"]
        if spec is None or subject is None:
            return []
        pr_record, pr = _one(capture, "pull_request")
        if (pr is None or pr_record["request"]["path"] != request_path(profile, "pull_request", pr_number=subject["pr_number"])
                or pr.get("number") != subject["pr_number"]):
            return []
        metadata_record, metadata = _one(capture, "workflow", workflow_id=workflow_id)
        if (metadata is None or metadata.get("id") != workflow_id or metadata.get("path") != spec["path"]
                or metadata_record["request"]["path"] != request_path(profile, "workflow", workflow_id=workflow_id)):
            return []
        pages = [row for row in capture.manifest["records"] if row["phase"] == "initial"
            and row["role"] == "workflow_runs" and row["workflow_id"] == workflow_id]
        selected = select_workflow_runs(repository=profile["repository"], pr=pr, workflow=spec,
            pages=pages, bodies=capture.bodies, per_page=profile["limits"]["per_page"],
            max_pages=profile["limits"]["max_pages"])
        if not selected["listing_complete"]:
            return []
        if selected["status"] == "missing" and selected["selected"] is None:
            # Only exact same-PR, same-workflow successful history can describe
            # old-head evidence. It remains ineligible and is never selected.
            candidates = {row["id"] for row in selected["history"]
                if row["reasons"] == ["head_sha_mismatch"] and row["status"] == "completed"
                and row["conclusion"] == "success"}
            for page in pages:
                for row in _document(capture, page)["workflow_runs"]:
                    if row["id"] not in candidates or not sha(row.get("head_sha")):
                        continue
                    historical_pr = deepcopy(pr)
                    historical_pr["head"]["sha"] = row["head_sha"]
                    association, reasons = _association(row, historical_pr, spec)
                    if association == "confirmed" and not reasons:
                        return ["evidence_for_old_head"]
            return []
        if selected["selected"] is None:
            return []
        workflow = next(row for row in observations["workflows"] if row["workflow_id"] == workflow_id)
        selection = workflow["selection"]
        current = selected["selected"]
        if (selection["status"] != "selected" or selection["run_id"] != current["id"]
                or selection["run_number"] != current["run_number"] or not positive(selection["attempt"])):
            return []
        # The original list may omit its PR relation. Separate exact run and
        # attempt bodies can establish it, under the mapper's existing rules.
        association = selection.get("listing_association")
        if selected["association"] != "confirmed" and not (
                capture.manifest["mapper_version"] == 2 and type(association) is dict
                and association.get("resolution") == "resolved"
                and association.get("reasons") == ["pr_association_unconfirmed"]):
            return []
        details = []
        for role, mapped in (("run", workflow["run"]), ("attempt", workflow["attempt"])):
            if (type(mapped) is not dict or mapped["association"] != "matched" or mapped["native_state"] != "observed"
                    or set(mapped["reasons"]) - ({"attempt_advanced"} if role == "run" else set())
                    or mapped["id"] != selection["run_id"] or mapped["run_number"] != selection["run_number"]
                    or mapped["run_attempt"] != selection["attempt"] or mapped["head_sha"] != subject["head_sha"]):
                return []
            identity = {"workflow_id": workflow_id, "run_id": selection["run_id"]}
            if role == "attempt":
                identity["attempt"] = selection["attempt"]
            record, raw = _one(capture, role, **identity)
            path = request_path(profile, role, workflow_id=workflow_id, run_id=selection["run_id"],
                attempt=selection["attempt"] if role == "attempt" else None)
            if raw is None or record["request"]["path"] != path or mapped["raw_refs"] != [record["id"]]:
                return []
            details.append(raw)
        if (not positive(details[0].get("check_suite_id"))
                or details[0]["check_suite_id"] != details[1].get("check_suite_id")):
            return []
        if all(row.get("status") in STATUSES - {"completed"} and row.get("conclusion") is None for row in details):
            return ["latest_run_pending"]
        if (outcome == "failed" and effect == "block"
                and all(row.get("status") == "completed" and row.get("conclusion") == "failure" for row in details)):
            return ["latest_run_failed"]
        return []
    except (ContractError, ValueError, TypeError, KeyError, IndexError, StopIteration):
        # Incomplete or inconsistent originals provide no additional diagnostic.
        return []
