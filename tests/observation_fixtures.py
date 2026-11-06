"""Fictional, authored SPEC-001 responses; not recordings or live receipts.

IDs, SHAs, times and collector identity are synthetic. Digests are calculated
from the actual fixture bytes. Helpers never write files or contact GitHub.
"""
from copy import deepcopy
import hashlib
import json

from delivery_review.contracts import canonical_bytes
from delivery_review.evidence.capture import parse_capture
from delivery_review.providers.github_routes import request_path

HEAD, BASE, NOW = "b" * 40, "a" * 40, "2026-11-06T00:00:00Z"
REPOSITORY_ID, WORKFLOW_ID, RUN_ID, JOB_ID, CHECK_ID, SUITE_ID = 9001, 101, 1001, 2001, 4001, 5001


def profile_document():
    return {"kind": "delivery-review-github-observation-profile", "profile_version": 1,
            "profile_id": "authored-synthetic-observations", "api_version": "2026-03-10",
            "repository": {"id": REPOSITORY_ID, "full_name": "fixture-owner/fixture-repo"},
            "source_mode": "pr_head", "selection": "latest_applicable_run_number_v1",
            "workflows": [{"id": WORKFLOW_ID, "path": ".github/workflows/ci.yml", "event": "pull_request",
                           "app_id": 15368, "job_names": ["pytest-linux"]}],
            "limits": {"per_page": 100, "max_pages": 10, "max_response_bytes": 2097152,
                       "max_total_bytes": 104857600, "max_requests": 200, "timeout_seconds": 30}}


def pr_document():
    return {"number": 7, "state": "open", "head": {"sha": HEAD, "repo": {"id": REPOSITORY_ID}},
            "base": {"sha": BASE, "repo": {"id": REPOSITORY_ID}}, "merge_commit_sha": "c" * 40}


def run_document(*, run_id=RUN_ID, number=8, attempt=1, status="completed", conclusion="success"):
    return {"id": run_id, "run_number": number, "run_attempt": attempt, "workflow_id": WORKFLOW_ID,
            "path": ".github/workflows/ci.yml@refs/pull/7/merge", "event": "pull_request", "head_sha": HEAD,
            "repository": {"id": REPOSITORY_ID}, "head_repository": {"id": REPOSITORY_ID},
            "pull_requests": [pr_document()], "check_suite_id": SUITE_ID,
            "status": status, "conclusion": conclusion}


def job_document(*, job_id=JOB_ID, attempt=1, status="completed", conclusion="success", name="pytest-linux", check_id=CHECK_ID):
    return {"id": job_id, "name": name, "run_id": RUN_ID, "run_attempt": attempt, "head_sha": HEAD,
            "status": status, "conclusion": conclusion,
            "check_run_url": f"https://api.github.com/repos/fixture-owner/fixture-repo/check-runs/{check_id}"}


def check_document(*, check_id=CHECK_ID, status="completed", conclusion="success"):
    return {"id": check_id, "name": "pytest-linux", "head_sha": HEAD, "app": {"id": 15368},
            "check_suite": {"id": SUITE_ID}, "status": status, "conclusion": conclusion}


class CaptureFixture:
    """Mutable builder; seal() always uses the real capture validator.

    find(role, phase='initial', **identity) returns the mutable record.
    set_payload(record, dict_or_bytes) updates actual bytes and their digests.
    data() returns (manifest, profile_bytes, bodies), reindexing removed records.
    """
    def __init__(self, *, empty=False, attempt=1, listing_attempt=None, per_page=100, mapper_version=1):
        self.profile, self.records, self.bodies = profile_document(), [], {}
        self.mapper_version = mapper_version
        self.profile["limits"]["per_page"] = per_page
        self.termination = {"status": "completed", "reason_codes": []}
        if empty:
            return
        listed = run_document(attempt=attempt if listing_attempt is None else listing_attempt)
        current = run_document(attempt=attempt)
        self.add("repository", deepcopy(self.profile["repository"]))
        self.add("pull_request", pr_document())
        self.add("workflow", {"id": WORKFLOW_ID, "path": ".github/workflows/ci.yml", "state": "active"})
        self.add("workflow_runs", {"total_count": 1, "workflow_runs": [listed]})
        self.add("run", current)
        self.add("attempt", current, attempt=attempt)
        self.add("jobs", {"total_count": 1, "jobs": [job_document(attempt=attempt)]}, attempt=attempt)
        self.add("check_run", check_document(), attempt=attempt)
        self.add("pull_request", pr_document(), phase="final")
        self.add("workflow_runs", {"total_count": 1, "workflow_runs": [current]}, phase="final")
        self.add("run", current, phase="final")

    def add(self, role, payload, *, phase="initial", workflow_id=None, run_id=None,
            attempt=None, job_id=None, check_run_id=None, page=None):
        workflow_id = WORKFLOW_ID if workflow_id is None and role not in {"repository", "pull_request"} else workflow_id
        run_id = RUN_ID if run_id is None and role in {"run", "attempt", "jobs", "check_run"} else run_id
        attempt = 1 if attempt is None and role in {"attempt", "jobs", "check_run"} else attempt
        job_id = JOB_ID if job_id is None and role == "check_run" else job_id
        check_run_id = CHECK_ID if check_run_id is None and role == "check_run" else check_run_id
        page = 1 if page is None and role in {"workflow_runs", "jobs"} else page
        number = max((int(record["id"][1:]) for record in self.records), default=0) + 1
        record = {"id": f"r{number:06d}", "phase": phase, "role": role,
                  "workflow_id": workflow_id, "run_id": run_id, "attempt": attempt,
                  "job_id": job_id, "check_run_id": check_run_id, "page": page,
                  "request": {"method": "GET", "api_version": "2026-03-10",
                              "path": request_path(self.profile, role, pr_number=7, head_sha=HEAD, workflow_id=workflow_id,
                                                   run_id=run_id, attempt=attempt, check_run_id=check_run_id, page=page)},
                  "observed_at": NOW, "status_code": 200, "response_headers": {}, "body": None, "error": None}
        first_final = next((index for index, item in enumerate(self.records) if item["phase"] == "final"), len(self.records))
        self.records.insert(first_final if phase == "initial" else len(self.records), record)
        self.set_payload(record, payload)
        return record

    def find(self, role, phase="initial", **identity):
        return next(record for record in self.records if record["role"] == role and record["phase"] == phase
                    and all(record[key] == value for key, value in identity.items()))

    def payload(self, record):
        return json.loads(self.bodies[record["body"]["path"]])

    def set_payload(self, record, payload):
        raw = payload if isinstance(payload, bytes) else canonical_bytes(payload) + b"\n"
        path = "raw/" + record["id"] + ".body"
        self.bodies[path] = raw
        record["body"] = {"path": path, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "complete": True}

    def unavailable(self, record, *, status_code=404, code="resource_unavailable"):
        record["status_code"], record["error"] = status_code, {"code": code}
        if status_code is None:
            record["body"], record["response_headers"] = None, {}
        else:
            self.set_payload(record, {"message": "Synthetic unavailable observation"})
        self.termination = {"status": "stopped", "reason_codes": ["collection_incomplete"]}

    def data(self):
        profile_bytes = canonical_bytes(self.profile) + b"\n"
        records, bodies = deepcopy(self.records), {}
        for number, record in enumerate(records, 1):
            record["id"] = f"r{number:06d}"
            if record["body"] is not None:
                raw = self.bodies[record["body"]["path"]]
                path = "raw/" + record["id"] + ".body"
                record["body"].update(path=path, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                bodies[path] = raw
        manifest = {"kind": "delivery-review-github-observation-capture", "capture_version": 1, "mapper_version": self.mapper_version,
                    "observed_at": NOW, "collector": {"version": "synthetic-test-fixture", "source_sha": "1" * 40},
                    "profile": {"path": "profile.json", "size_bytes": len(profile_bytes), "sha256": hashlib.sha256(profile_bytes).hexdigest()},
                    "termination": deepcopy(self.termination), "records": records}
        return manifest, profile_bytes, bodies

    def seal(self):
        manifest, profile_bytes, bodies = self.data()
        return parse_capture(canonical_bytes(manifest) + b"\n", profile_bytes, bodies)


def mutable_data(**options):
    return CaptureFixture(**options).data()


def make_capture(**options):
    return CaptureFixture(**options).seal()
