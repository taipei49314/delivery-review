"""Bounded read-only acquisition; all interpretation is reproducible from capture."""
from __future__ import annotations

from datetime import datetime, timezone

from ..contracts import ContractError, loads_strict
from ..evidence.capture import CaptureError, CaptureWriter
from ..observation_profile import load_observation_profile
from .github_routes import (
    API_VERSION, check_run_id_from_url, next_page, parse_pr_url, positive, request_path, sha,
)
from .github_selection import select_workflow_runs


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _StopCollection(Exception):
    pass


def _document(record, bodies):
    if record["error"] is not None or record["status_code"] != 200 or record["body"] is None:
        return None
    if not record["body"]["complete"]:
        return None
    try:
        value = loads_strict(bodies[record["body"]["path"]])
        return value if type(value) is dict else None
    except ContractError:
        return None


def _source(pr, number, repository_id):
    if type(pr) is not dict or type(pr.get("number")) is not int or pr["number"] != number:
        return False
    for side in ("head", "base"):
        value = pr.get(side)
        if not (type(value) is dict and sha(value.get("sha")) and type(value.get("repo")) is dict
                and type(value["repo"].get("id")) is int and value["repo"]["id"] == repository_id):
            return False
    return True


def _run_identity(detail, selected, workflow, profile, head):
    if type(detail) is not dict:
        return False
    repository = detail.get("repository")
    return (type(repository) is dict and type(repository.get("id")) is int
            and repository["id"] == profile["repository"]["id"]
            and all(type(detail.get(key)) is int and detail[key] == expected for key, expected in
                    (("id", selected["id"]), ("run_number", selected["run_number"]), ("workflow_id", workflow["id"])))
            and detail.get("event") == "pull_request" and detail.get("head_sha") == head
            and positive(detail.get("run_attempt")) and detail["run_attempt"] >= selected["run_attempt"])


def collect_observations(pr_url: str, profile_bytes: bytes, destination, *, transport,
                         collector: dict, clock=utc_now, before_recheck=None):
    """Acquire original bytes into a new sealed capture, including diagnostic stops.

    transport.get is the only network boundary. It is never used by raw replay.
    Caller identifies its clean collector source; CLI verifies that provenance.
    before_recheck is a first-party orchestration callback receiving immutable
    initial facts. Static engines/artifact acquisition finish there, before the
    final PR/run rechecks. It never receives a mutable writer or credentials.
    """
    profile = load_observation_profile(profile_bytes)
    number = parse_pr_url(pr_url, profile)
    writer = CaptureWriter(destination, profile_bytes, collector=collector, observed_at=clock())
    limits = profile["limits"]
    records, bodies, stopped = [], {}, set()
    total = 0

    def get(role, *, phase="initial", workflow_id=None, run_id=None, attempt=None,
            job_id=None, check_run_id=None, page=None, head_sha=None):
        nonlocal total
        if len(records) >= limits["max_requests"]:
            stopped.add("request_limit")
            raise _StopCollection
        remaining = limits["max_total_bytes"] - total
        if remaining <= 0:
            stopped.add("capture_limit")
            raise _StopCollection
        path = request_path(profile, role, pr_number=number, head_sha=head_sha,
                            workflow_id=workflow_id, run_id=run_id, attempt=attempt,
                            check_run_id=check_run_id, page=page)
        ceiling = min(limits["max_response_bytes"], remaining)
        try:
            response = transport.get(path, api_version=API_VERSION, max_bytes=ceiling,
                                     timeout_seconds=limits["timeout_seconds"])
        except TimeoutError:
            response = {"status_code": None, "response_headers": {}, "body": None,
                        "complete": False, "error": {"code": "request_timeout"}}
        except OSError:
            response = {"status_code": None, "response_headers": {}, "body": None,
                        "complete": False, "error": {"code": "transport_error"}}
        error = response["error"]
        if error and error["code"] == "response_limit" and ceiling < limits["max_response_bytes"]:
            error = {"code": "capture_limit"}
        metadata = {"phase": phase, "role": role, "workflow_id": workflow_id, "run_id": run_id,
                    "attempt": attempt, "job_id": job_id, "check_run_id": check_run_id, "page": page,
                    "request": {"method": "GET", "path": path, "api_version": API_VERSION},
                    "observed_at": clock(), "status_code": response["status_code"],
                    "response_headers": response["response_headers"], "error": error}
        try:
            record = writer.record(metadata, response["body"], complete=response["complete"])
        except CaptureError as exc:
            if str(exc) != "capture manifest byte limit exceeded":
                raise
            stopped.add("capture_limit")
            raise _StopCollection from exc
        records.append(record)
        if record["body"] is not None:
            bodies[record["body"]["path"]] = response["body"]
            total += len(response["body"])
        if error:
            stopped.add(error["code"])
        return record

    def listing(role, **identities):
        pages, page = [], 1
        while page is not None:
            record = get(role, page=page, **identities)
            pages.append(record)
            if _document(record, bodies) is None:
                stopped.add("collection_incomplete")
                break
            try:
                page = next_page(record["response_headers"], record["request"]["path"],
                                 max_pages=limits["max_pages"])
            except ContractError:
                stopped.add("pagination_incomplete")
                break
        return pages

    def select(workflow, pr, phase):
        pages = listing("workflow_runs", phase=phase, workflow_id=workflow["id"], head_sha=pr["head"]["sha"])
        selection = select_workflow_runs(repository=profile["repository"], pr=pr, workflow=workflow,
                                         pages=pages, bodies=bodies, per_page=limits["per_page"],
                                         max_pages=limits["max_pages"])
        if not selection["listing_complete"]:
            stopped.add("collection_incomplete")
        return selection["selected"]

    try:
        repository = _document(get("repository"), bodies)
        pr = _document(get("pull_request"), bodies)
        if not (type(repository) is dict and type(repository.get("id")) is int
                and repository["id"] == profile["repository"]["id"]
                and _source(pr, number, profile["repository"]["id"])):
            stopped.add("subject_unavailable")
            raise _StopCollection
        for workflow in sorted(profile["workflows"], key=lambda value: value["id"]):
            get("workflow", workflow_id=workflow["id"])
            selected = select(workflow, pr, "initial")
            if selected is None:
                continue
            detail = _document(get("run", workflow_id=workflow["id"], run_id=selected["id"]), bodies)
            if not _run_identity(detail, selected, workflow, profile, pr["head"]["sha"]):
                stopped.add("collection_incomplete")
                continue
            attempt = detail["run_attempt"]
            get("attempt", workflow_id=workflow["id"], run_id=selected["id"], attempt=attempt)
            pages = listing("jobs", workflow_id=workflow["id"], run_id=selected["id"], attempt=attempt)
            seen, totals = set(), set()
            for record in pages:
                payload = _document(record, bodies)
                jobs = payload.get("jobs") if payload else None
                if type(jobs) is not list or len(jobs) > limits["per_page"]:
                    stopped.add("collection_incomplete")
                    continue
                if type(payload.get("total_count")) is not int or payload["total_count"] < 0:
                    stopped.add("collection_incomplete")
                else:
                    totals.add(payload["total_count"])
                for job in jobs:
                    if type(job) is not dict or not positive(job.get("id")) or job["id"] in seen:
                        stopped.add("collection_incomplete")
                        continue
                    seen.add(job["id"])
                    try:
                        check_id = check_run_id_from_url(job.get("check_run_url"), profile)
                    except ContractError:
                        stopped.add("collection_incomplete")
                        continue
                    get("check_run", workflow_id=workflow["id"], run_id=selected["id"], attempt=attempt,
                        job_id=job["id"], check_run_id=check_id)
            if len(totals) != 1 or len(seen) != next(iter(totals), -1):
                stopped.add("pagination_incomplete")
        if before_recheck is not None:
            try:
                before_recheck(writer.snapshot())
            except (ContractError, OSError):
                stopped.add("inspection_stage_failed")
        get("pull_request", phase="final")
        for workflow in sorted(profile["workflows"], key=lambda value: value["id"]):
            selected = select(workflow, pr, "final")
            if selected is not None:
                get("run", phase="final", workflow_id=workflow["id"], run_id=selected["id"])
    except _StopCollection:
        pass
    return writer.seal(status="stopped" if stopped else "completed", reason_codes=sorted(stopped))
