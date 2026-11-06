"""Select a workflow run from bounded, saved GitHub listing responses.

This is not report-v1 evaluation. A selected run's listing attempt is only an
observation: a future provider must confirm the current run detail and fetch its
specific attempt. Metadata association establishes neither checkout nor producer
trust. This module performs no IO and never reads a saved normalized report.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..contracts import ContractError, MAX_JSON_BYTES, canonical_digest, loads_strict

API_VERSION = "2026-03-10"
STATUSES = {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out",
               "action_required", "stale", "startup_failure"}
FIELDS = ("id", "run_number", "run_attempt", "status", "conclusion", "workflow_id", "path", "head_sha")


class _Invalid(ValueError):
    pass


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise _Invalid(reason)


def _positive(value) -> bool:
    return type(value) is int and 0 < value <= 2**53 - 1


def _sha(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _id(value):
    identifier = value.get("id") if isinstance(value, dict) else None
    return identifier if _positive(identifier) else None


def _page_number(value: str, endpoint: str, head: str, per_page: int, *, link=False) -> int:
    _need(isinstance(value, str), "invalid_request_path")
    _need(not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value), "invalid_url_characters")
    parsed = urlsplit(value)
    if link:
        _need(parsed.scheme == "https" and parsed.netloc == "api.github.com", "unsafe_pagination_link")
    else:
        _need(not parsed.scheme and not parsed.netloc, "invalid_request_path")
    _need(parsed.path == endpoint and not parsed.fragment, "unexpected_listing_endpoint")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    _need(len(pairs) == len(query) == 5
          and set(query) == {"event", "head_sha", "per_page", "page", "exclude_pull_requests"},
          "unexpected_listing_query")
    _need(query["event"] == "pull_request" and query["head_sha"] == head
          and query["per_page"] == str(per_page) and query["exclude_pull_requests"] == "false", "unexpected_listing_query")
    if not link:
        _need(value == endpoint + "?" + urlencode(sorted(query.items())), "noncanonical_request_path")
    _need(re.fullmatch(r"[1-9][0-9]{0,8}", query["page"]) is not None, "invalid_page_number")
    return int(query["page"])


def _next_page(headers: dict, endpoint: str, head: str, per_page: int):
    _need(isinstance(headers, dict) and all(isinstance(k, str) and k == k.lower()
          and isinstance(v, str) for k, v in headers.items()), "invalid_response_headers")
    value = headers.get("link", "")
    if not value:
        return None
    relations = {}
    for part in value.split(","):
        match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="(next|prev|first|last)"\s*', part)
        _need(match is not None, "invalid_pagination_link")
        url, relation = match.groups()
        _need(relation not in relations, "duplicate_pagination_relation")
        relations[relation] = _page_number(url, endpoint, head, per_page, link=True)
    return relations.get("next")


def _run_observation(run, repository: dict, pr: dict, workflow: dict, path: str):
    """Retain native diagnostic facts before deciding if a row is usable."""
    document = run if isinstance(run, dict) else {}
    invalid = []
    if not isinstance(run, dict) or not all(_positive(document.get(k)) for k in
                                           ("id", "run_number", "run_attempt", "workflow_id")):
        invalid.append("invalid_run_identity")
    if not (_positive(_id(document.get("repository"))) and _sha(document.get("head_sha"))
            and isinstance(document.get("event"), str) and bool(document["event"].strip())):
        invalid.append("invalid_run_eligibility")
    if not all(document.get(k) is None or isinstance(document[k], str) for k in
               ("status", "conclusion", "path", "head_sha", "event")):
        invalid.append("invalid_run_metadata")
    reasons = list(invalid)
    if not invalid:
        for field, actual, expected in (("repository", _id(document.get("repository")), repository["id"]),
                                       ("workflow", document["workflow_id"], workflow["id"]),
                                       ("event", document["event"], "pull_request"),
                                       ("head_sha", document["head_sha"], pr["head"]["sha"])):
            if actual != expected:
                reasons.append(f"{field}_mismatch")
    observation = {key: document.get(key) for key in FIELDS}
    observation.update(eligible=not reasons, reasons=sorted(reasons), raw_ref=path)
    return observation, invalid


def _association(run: dict, pr: dict, workflow: dict) -> tuple[str, list[str]]:
    reasons, mismatch = [], False
    path = run.get("path")
    if isinstance(path, str) and path.count("@") == 1 and path.split("@", 1)[1]:
        path = path.split("@", 1)[0]
    for key, observed, expected in (("workflow_path", path, workflow["path"]),
                                  ("head_repository", _id(run.get("head_repository")), _id(pr["head"]["repo"]))):
        if observed is None:
            reasons.append(f"{key}_missing")
        elif observed != expected:
            reasons.append(f"{key}_mismatch")
            mismatch = True
    associations = run.get("pull_requests")
    if not isinstance(associations, list) or not associations:
        reasons.append("pr_association_unconfirmed")
    else:
        malformed = any(not isinstance(item, dict) or not _positive(item.get("number")) for item in associations)
        matches = [item for item in associations if isinstance(item, dict)
                   and _positive(item.get("number")) and item["number"] == pr["number"]]
        if not matches:
            reasons.append("pr_association_unconfirmed" if malformed else "pr_association_mismatch")
            mismatch = mismatch or not malformed
        elif len(matches) != 1:
            reasons.append("pr_association_ambiguous")
        else:
            if malformed:
                reasons.append("pr_association_unconfirmed")
            item = matches[0]
            head, base = item.get("head"), item.get("base")
            if not isinstance(head, dict) or not isinstance(base, dict):
                reasons.append("pr_association_unconfirmed")
            else:
                values = ((_id(head.get("repo")), _id(pr["head"]["repo"])),
                          (_id(base.get("repo")), _id(pr["base"]["repo"])),
                          (head.get("sha") if _sha(head.get("sha")) else None, pr["head"]["sha"]))
                if any(observed is not None and observed != expected for observed, expected in values):
                    reasons.append("pr_association_mismatch")
                    mismatch = True
                elif any(observed is None for observed, _ in values):
                    reasons.append("pr_association_unconfirmed")
    return ("mismatch" if mismatch else "unconfirmed" if reasons else "confirmed"), reasons


def select_workflow_runs(*, repository: dict, pr: dict, workflow: dict,
                         pages: list[dict], bodies: Mapping[str, bytes],
                         per_page: int = 100, max_pages: int = 10) -> dict:
    """Return run selection observations; never a readiness or binding decision.

    Only exact repository/workflow/event/head candidates participate. Choose the
    greatest run_number, then check path/head-repository/PR association without
    falling back. Equal run numbers or repeated run IDs are ambiguous. Empty PR
    associations are unconfirmed, not evidence that a run is unrelated.
    Diagnostic history retains parsed native facts and row rejection reasons
    even when another row/page prevents authoritative selection.
    """
    result = {"selector_version": 1, "status": "unconfirmed", "selected": None,
              "association": "unconfirmed", "checkout_source": "unconfirmed",
              "attempt_status": "unconfirmed", "listing_complete": False,
              "reasons": [], "raw_refs": [], "history": []}
    try:
        _need(isinstance(repository, dict) and _positive(repository.get("id"))
              and isinstance(repository.get("full_name"), str)
              and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository["full_name"]), "invalid_repository")
        _need(all(part not in {".", ".."} for part in repository["full_name"].split("/")), "invalid_repository")
        _need(isinstance(pr, dict) and _positive(pr.get("number")), "invalid_pr")
        for side in ("head", "base"):
            _need(isinstance(pr.get(side), dict) and _sha(pr[side].get("sha"))
                  and _positive(_id(pr[side].get("repo"))), "invalid_pr_source")
        _need(_id(pr["base"]["repo"]) == repository["id"], "pr_repository_mismatch")
        if _id(pr["head"]["repo"]) != repository["id"]:
            result["reasons"] = ["unsupported_fork"]
            return result
        _need(isinstance(workflow, dict) and _positive(workflow.get("id"))
              and isinstance(workflow.get("path"), str) and workflow.get("event") == "pull_request", "invalid_workflow")
        _need(type(per_page) is int and 1 <= per_page <= 100 and type(max_pages) is int
              and 1 <= max_pages <= 10, "invalid_pagination_limits")
        _need(isinstance(pages, list) and isinstance(bodies, Mapping), "invalid_capture")
        if not pages or len(pages) > max_pages:
            result["reasons"] = ["pagination_incomplete" if not pages else "page_limit_exceeded"]
            return result
        endpoint = f"/repos/{repository['full_name']}/actions/workflows/{workflow['id']}/runs"
        captured, paths = {}, set()
        for page in pages:
            _need(isinstance(page, dict) and isinstance(page.get("request"), dict), "invalid_request")
            request = page["request"]
            _need(request.get("method") == "GET" and request.get("api_version") == API_VERSION, "unsupported_request")
            number = _page_number(request.get("path"), endpoint, pr["head"]["sha"], per_page)
            _need(number not in captured, "duplicate_page")
            captured[number] = page
        rows, totals, next_pages = [], set(), {}
        incomplete = set()
        if max(captured) > max_pages:
            result["reasons"] = ["page_limit_exceeded"]
            return result
        if sorted(captured) != list(range(1, max(captured) + 1)):
            incomplete.add("pagination_incomplete")
        for number, page in sorted(captured.items()):
            status_code = page.get("status_code")
            _need(status_code is None or (type(status_code) is int and 100 <= status_code <= 599), "invalid_http_status")
            if page.get("error") is not None or status_code != 200:
                incomplete.add("http_observation_unavailable")
                continue
            body = page.get("body")
            _need(isinstance(body, dict), "invalid_body_record")
            path = body.get("path")
            _need(isinstance(path, str) and path not in paths, "duplicate_or_invalid_body_path")
            paths.add(path)
            if body.get("complete") is not True:
                incomplete.add("body_incomplete")
                continue
            raw = bodies.get(path)
            _need(isinstance(raw, bytes), "body_missing")
            _need(len(raw) <= MAX_JSON_BYTES, "body_limit_exceeded")
            _need(type(body.get("size_bytes")) is int and body["size_bytes"] == len(raw)
                  and body.get("sha256") == hashlib.sha256(raw).hexdigest(), "body_integrity_mismatch")
            result["raw_refs"].append({"path": path, "size_bytes": len(raw), "sha256": body["sha256"]})
            try:
                payload = loads_strict(raw)
            except ContractError as exc:
                raise _Invalid("invalid_response_json") from exc
            _need(isinstance(payload, dict) and type(payload.get("total_count")) is int
                  and payload["total_count"] >= 0 and isinstance(payload.get("workflow_runs"), list), "invalid_listing_shape")
            _need(len(payload["workflow_runs"]) <= per_page, "page_size_exceeded")
            totals.add(payload["total_count"])
            observations = [_run_observation(run, repository, pr, workflow, path) for run in payload["workflow_runs"]]
            result["history"].extend(observation for observation, _ in observations)
            invalid = sorted({reason for _, errors in observations for reason in errors})
            _need(not invalid, invalid[0] if invalid else "invalid_run_identity")
            rows.extend(payload["workflow_runs"])
            next_pages[number] = _next_page(page.get("response_headers"), endpoint, pr["head"]["sha"], per_page)
        eligible = lambda run: (_id(run.get("repository")) == repository["id"]
                                and run["workflow_id"] == workflow["id"]
                                and run.get("event") == "pull_request" and run.get("head_sha") == pr["head"]["sha"])
        ordered = sorted(rows, key=lambda r: (r["run_number"], r["id"], canonical_digest(r)), reverse=True)
        if len(totals) != 1 or (totals and len({r["id"] for r in rows}) != next(iter(totals))):
            incomplete.add("listing_count_mismatch")
        if totals and max(totals) >= 1000:
            incomplete.add("filtered_result_limit_reached")
        for number, next_number in next_pages.items():
            _need(next_number is None or next_number == number + 1, "invalid_next_page")
            if next_number is not None and next_number not in captured:
                incomplete.add("pagination_incomplete")
            if next_number is None and number != max(captured):
                incomplete.add("pagination_chain_broken")
        if len(rows) != len({r["id"] for r in rows}):
            incomplete.add("duplicate_run_id")
        candidates = [run for run in ordered if eligible(run)]
        if len({r["run_number"] for r in candidates}) != len(candidates):
            incomplete.add("ambiguous_run_sequence")
        if incomplete:
            result["reasons"] = sorted(incomplete)
            return result
        result["listing_complete"] = True
        if not candidates:
            result.update(status="missing", reasons=["no_applicable_run"])
            return result
        selected = candidates[0]
        result["selected"] = {key: selected.get(key) for key in FIELDS}
        association, reasons = _association(selected, pr, workflow)
        result["association"] = association
        status, conclusion = selected.get("status"), selected.get("conclusion")
        if status not in STATUSES or (conclusion is not None and conclusion not in CONCLUSIONS):
            reasons.append("unsupported_native_status")
        if (status == "completed") != (conclusion is not None):
            reasons.append("invalid_native_state")
        result.update(status="unconfirmed" if reasons else "selected", reasons=sorted(set(reasons)))
    except (_Invalid, ValueError) as exc:
        result.update(status="error", listing_complete=False, reasons=[str(exc) if isinstance(exc, _Invalid) else "invalid_url"])
    finally:
        result["history"].sort(key=lambda row: (row["run_number"] if _positive(row["run_number"]) else -1,
                                               row["id"] if _positive(row["id"]) else -1,
                                               canonical_digest(row)), reverse=True)
    return result
