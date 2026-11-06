"""Pure SPEC-001 mapping of verified captures; no IO or delivery verdicts."""
from __future__ import annotations

import hashlib

from ..contracts import ContractError, canonical_digest, loads_strict
from .github_routes import check_run_id_from_url, next_page, positive, request_path, sha
from .github_selection import CONCLUSIONS, STATUSES, _association, select_workflow_runs


class _Trace:
    def __init__(self, parent=None):
        self.parent, self.severity, self.reasons, self.refs = parent, 0, set(), set()

    def add(self, reason, severity=1):
        self.reasons.add(reason)
        self.severity = max(self.severity, severity)
        if self.parent is not None:
            self.parent.add(reason, severity)

    def use(self, record):
        self.refs.add(record["id"])
        if self.parent is not None:
            self.parent.use(record)


def _nested(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _combine(*associations):
    return "mismatch" if "mismatch" in associations else "unconfirmed" if "unconfirmed" in associations else "matched"


def _compare(value, expected, label, trace, validator=positive):
    if not validator(value):
        trace.add(label + "_unconfirmed")
        if value is not None:
            trace.add("invalid_response", 2)
        return "unconfirmed"
    if expected is None:
        trace.add(label + "_unconfirmed")
        return "unconfirmed"
    if value != expected:
        trace.add(label + "_mismatch", 0)
        return "mismatch"
    return "matched"


def _native(payload, trace):
    status, conclusion = payload.get("status"), payload.get("conclusion")
    confirmed = True
    if any(value is not None and not isinstance(value, str) for value in (status, conclusion)):
        trace.add("invalid_response", 2)
        confirmed = False
    elif status not in STATUSES or (conclusion is not None and conclusion not in CONCLUSIONS):
        trace.add("unsupported_native_status")
        confirmed = False
    if (status == "completed") != (conclusion is not None):
        trace.add("invalid_native_state")
        confirmed = False
    return {"status": status, "conclusion": conclusion, "native_state": "observed" if confirmed else "unconfirmed"}


class _Mapper:
    def __init__(self, capture):
        self.manifest, self.profile, self.bodies = capture.manifest, capture.profile, capture.bodies
        self.records = self.manifest["records"]
        self.trace = _Trace()
        self.positions = {record["id"]: index for index, record in enumerate(self.records)}
        initial = [index for index, record in enumerate(self.records) if record["phase"] == "initial"]
        final = [index for index, record in enumerate(self.records) if record["phase"] == "final"]
        self.recheck_order_valid = not initial or not final or max(initial) < min(final)
        if not self.recheck_order_valid:
            self.trace.add("recheck_order_invalid", 2)

    def records_for(self, role, phase="initial", **identity):
        return [record for record in self.records if record["role"] == role and record["phase"] == phase
                and all(record[key] == value for key, value in identity.items())]

    def payload(self, record, trace):
        trace.use(record)
        code, error = record["status_code"], record["error"]
        if error is not None:
            trace.add(error["code"])
        if code != 200:
            headers = record["response_headers"]
            if code is None:
                trace.add(error["code"] if error else "transport_error")
            elif code == 401:
                trace.add("authentication_unavailable")
            elif code == 429 or (code == 403 and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers)):
                trace.add("rate_limited")
            elif code in {403, 404}:
                trace.add("resource_unavailable")
            else:
                trace.add("http_error")
            return None
        body = record["body"]
        if body is None or body["path"] not in self.bodies:
            trace.add("observation_missing")
            return None
        if not body["complete"]:
            trace.add("response_limit")
            return None
        if error is not None:
            return None
        try:
            payload = loads_strict(self.bodies[body["path"]])
        except ContractError:
            trace.add("invalid_response", 2)
            return None
        if not isinstance(payload, dict):
            trace.add("invalid_response", 2)
            return None
        return payload

    def one(self, role, trace, phase="initial", **identity):
        records = self.records_for(role, phase, **identity)
        if len(records) != 1:
            trace.add("observation_missing" if not records else "capture_record_ambiguous", 1 if not records else 2)
            return None, None
        return records[0], self.payload(records[0], trace)

    def snapshot(self, record, payload, trace):
        if payload is None:
            return None
        fields = {"repository_id": _nested(payload, "base", "repo", "id"),
                  "head_repository_id": _nested(payload, "head", "repo", "id"),
                  "pr_number": payload.get("number"), "head_sha": _nested(payload, "head", "sha"),
                  "base_tip_sha": _nested(payload, "base", "sha"),
                  "full_name": self.profile["repository"]["full_name"]}
        if not (all(positive(fields[key]) for key in ("repository_id", "head_repository_id", "pr_number"))
                and all(sha(fields[key]) for key in ("head_sha", "base_tip_sha"))):
            trace.add("invalid_response", 2)
            return None
        if record["request"]["path"] != request_path(self.profile, "pull_request", pr_number=fields["pr_number"]):
            trace.add("pr_number_mismatch", 2)
            return None
        return fields

    def selection(self, workflow, pr, trace, phase):
        pages = self.records_for("workflow_runs", phase, workflow_id=workflow["id"])
        for page in pages:
            # Record HTTP/parse failures separately; the kernel still guards the
            # entire listing and never chooses an older prefix as authoritative.
            self.payload(page, trace)
        if not pages:
            trace.add("observation_missing")
        kernel = select_workflow_runs(repository=self.profile["repository"], pr=pr, workflow=workflow,
                                      pages=pages, bodies=self.bodies, per_page=self.profile["limits"]["per_page"],
                                      max_pages=self.profile["limits"]["max_pages"])
        for reason in kernel["reasons"]:
            severity = 0 if reason in {"workflow_path_mismatch", "head_repository_mismatch", "pr_association_mismatch", "no_applicable_run"} else 1
            if kernel["status"] == "error":
                severity = 2
            trace.add(reason, severity)
        selected = kernel["selected"]
        if selected is not None:
            status = "selected"
        elif kernel["status"] == "missing":
            status = "none"
        elif kernel["status"] == "error":
            status = "error"
        elif {"duplicate_run_id", "ambiguous_run_sequence"} & set(kernel["reasons"]):
            status = "ambiguous"
        else:
            status = "partial"
        if status in {"partial", "ambiguous"}:
            trace.add("selection_unconfirmed")
        elif status == "error":
            trace.add("invalid_response", 2)
        page_refs = {page["body"]["path"]: page["id"] for page in pages if page["body"] is not None}
        candidates = []
        for row in kernel["history"]:
            candidate = {key: value for key, value in row.items() if key != "raw_ref"}
            candidate["raw_refs"] = [page_refs[row["raw_ref"]]]
            candidates.append(candidate)
        selection = {"status": status, "run_id": selected["id"] if selected else None,
                     "run_number": selected["run_number"] if selected else None,
                     "listing_attempt": selected["run_attempt"] if selected else None, "attempt": None,
                     "reasons": sorted(kernel["reasons"]), "raw_refs": sorted(page["id"] for page in pages)}
        association = {"confirmed": "matched", "unconfirmed": "unconfirmed", "mismatch": "mismatch"}[kernel["association"]]
        return selection, candidates, association

    def resolve_listing(self, selection, association, trace, *, workflow_association,
                        run, run_valid, attempt, attempt_valid, phase):
        # Mapper 1 deliberately retains the original listing's association.
        return association

    def run_detail(self, workflow, pr, selection, trace, phase="initial", *, attempt=None):
        trace = _Trace(trace)
        role = "run" if attempt is None else "attempt"
        identity = {"workflow_id": workflow["id"], "run_id": selection["run_id"]}
        if attempt is not None:
            identity["attempt"] = attempt
        record, payload = self.one(role, trace, phase, **identity)
        if payload is None:
            return None, "unconfirmed", False
        predecessors = self.records_for("workflow_runs", phase, workflow_id=workflow["id"]) if attempt is None else \
            self.records_for("run", phase, workflow_id=workflow["id"], run_id=selection["run_id"])
        order_valid = bool(predecessors) and all(self.positions[parent["id"]] < self.positions[record["id"]] for parent in predecessors)
        if not order_valid:
            trace.add("run_observation_order_invalid", 2)
        summary = {key: payload.get(key) for key in ("id", "run_number", "run_attempt", "workflow_id", "path", "event", "head_sha", "check_suite_id")}
        summary.update(repository_id=_nested(payload, "repository", "id"), head_repository_id=_nested(payload, "head_repository", "id"),
                       raw_refs=[record["id"]], **_native(payload, trace))
        relations = [_compare(summary[key], expected, label, trace, validator) for key, expected, label, validator in (
            ("id", selection["run_id"], "run_id", positive),
            ("run_number", selection["run_number"], "run_number", positive),
            ("repository_id", self.profile["repository"]["id"], "run_repository", positive),
            ("workflow_id", workflow["id"], "run_workflow", positive),
            ("event", workflow["event"], "run_event", lambda value: isinstance(value, str) and bool(value)),
            ("head_sha", pr["head"]["sha"], "run_head", sha))]
        valid_attempt = positive(summary["run_attempt"])
        if not valid_attempt:
            trace.add("run_attempt_unconfirmed")
            if summary["run_attempt"] is not None:
                trace.add("invalid_response", 2)
        elif attempt is not None:
            relations.append(_compare(summary["run_attempt"], attempt, "run_attempt", trace))
        elif summary["run_attempt"] < selection["listing_attempt"]:
            trace.add("run_attempt_mismatch", 0)
            relations.append("mismatch")
        elif summary["run_attempt"] > selection["listing_attempt"]:
            trace.add("attempt_advanced", 0)
        critical = order_valid and valid_attempt and all(relation == "matched" for relation in relations)
        sanitized = dict(payload)
        if payload.get("path") is not None and not isinstance(payload["path"], str):
            sanitized["path"] = None
            trace.add("invalid_response", 2)
        association, reasons = _association(sanitized, pr, workflow)
        for reason in reasons:
            trace.add(reason, 0 if reason.endswith("_mismatch") else 1)
        association = "matched" if association == "confirmed" else association
        if not positive(summary["check_suite_id"]):
            trace.add("check_suite_unconfirmed")
            if summary["check_suite_id"] is not None:
                trace.add("invalid_response", 2)
        association = _combine(association, *relations, "matched" if valid_attempt else "unconfirmed")
        summary.update(association=association, reasons=sorted(trace.reasons))
        return summary, association, critical

    def job_rows(self, workflow, selection, trace):
        pages = self.records_for("jobs", workflow_id=workflow["id"], run_id=selection["run_id"], attempt=selection["attempt"])
        if not pages:
            trace.add("observation_missing")
            return []
        rows, totals, seen_pages = [], set(), {page["page"] for page in pages}
        if seen_pages != set(range(1, max(seen_pages) + 1)):
            trace.add("pagination_incomplete")
        for page in sorted(pages, key=lambda record: record["page"]):
            payload = self.payload(page, trace)
            if payload is None:
                continue
            if not isinstance(payload.get("jobs"), list) or type(payload.get("total_count")) is not int or payload["total_count"] < 0:
                trace.add("invalid_response", 2)
                continue
            totals.add(payload["total_count"])
            if len(payload["jobs"]) > self.profile["limits"]["per_page"]:
                trace.add("pagination_incomplete")
            for job in payload["jobs"]:
                if isinstance(job, dict):
                    rows.append((job, page))
                else:
                    trace.add("invalid_response", 2)
            try:
                following = next_page(page["response_headers"], page["request"]["path"], max_pages=self.profile["limits"]["max_pages"])
                if following is not None and following not in seen_pages:
                    trace.add("pagination_incomplete")
                if following is None and page["page"] != max(seen_pages):
                    trace.add("pagination_incomplete")
            except ContractError:
                trace.add("pagination_incomplete")
        ids = [job.get("id") for job, _ in rows]
        valid_ids = [identifier for identifier in ids if positive(identifier)]
        if len(valid_ids) != len(ids):
            trace.add("invalid_response", 2)
        if len(set(valid_ids)) != len(valid_ids):
            trace.add("duplicate_job_id")
        if len(totals) != 1 or (totals and len(set(valid_ids)) != next(iter(totals))):
            trace.add("pagination_incomplete")
        return rows

    def job(self, job, record, workflow, pr, selection, suite_id, parent_association, parent_trace):
        trace = _Trace(parent_trace)
        trace.use(record)
        summary = {key: job.get(key) for key in ("id", "name", "run_id", "run_attempt", "head_sha")}
        summary.update(**_native(job, trace), check=None)
        relations = [parent_association]
        if not positive(job.get("id")) or not isinstance(job.get("name"), str) or not job["name"]:
            trace.add("invalid_response", 2)
            relations.append("unconfirmed")
        for key, expected, validator in (("run_id", selection["run_id"], positive),
                                         ("run_attempt", selection["attempt"], positive), ("head_sha", pr["head"]["sha"], sha)):
            if key in job:
                relations.append(_compare(job[key], expected, "job_" + key, trace, validator))
        try:
            check_id = check_run_id_from_url(job.get("check_run_url"), self.profile)
        except ContractError:
            trace.add("check_url_unconfirmed")
            relations.append("unconfirmed")
            check_id = None
        if check_id is not None and positive(job.get("id")):
            check_record, check = self.one("check_run", trace, workflow_id=workflow["id"], run_id=selection["run_id"],
                                           attempt=selection["attempt"], job_id=job["id"], check_run_id=check_id)
            if check is None:
                relations.append("unconfirmed")
            else:
                value = {"id": check.get("id"), "app_id": _nested(check, "app", "id"), "head_sha": check.get("head_sha"),
                         "check_suite_id": _nested(check, "check_suite", "id"), "raw_refs": [check_record["id"]], **_native(check, trace)}
                summary["check"] = value
                for key, expected, validator in (("id", check_id, positive), ("app_id", workflow["app_id"], positive),
                                                 ("head_sha", pr["head"]["sha"], sha), ("check_suite_id", suite_id, positive)):
                    relations.append(_compare(value[key], expected, "check_" + key, trace, validator))
        summary.update(association=_combine(*relations), reasons=sorted(trace.reasons), raw_refs=sorted(trace.refs))
        return summary

    def workflow(self, workflow, pr):
        trace = _Trace(self.trace)
        _, metadata = self.one("workflow", trace, workflow_id=workflow["id"])
        workflow_association = "unconfirmed"
        if metadata is not None:
            workflow_association = _combine(_compare(metadata.get("id"), workflow["id"], "workflow_id", trace),
                _compare(metadata.get("path"), workflow["path"], "workflow_path", trace, lambda value: isinstance(value, str) and bool(value)))
        selection, candidates, listed_association = self.selection(workflow, pr, trace, "initial")
        run, attempt, run_association, attempt_association = None, None, "unconfirmed", "unconfirmed"
        critical, attempt_valid = False, False
        jobs = []
        if selection["run_id"] is not None:
            run, run_association, critical = self.run_detail(workflow, pr, selection, trace)
            if critical:
                selection["attempt"] = run["run_attempt"]
                attempt, attempt_association, attempt_valid = self.run_detail(workflow, pr, selection, trace, attempt=selection["attempt"])
                if attempt is not None:
                    suite_relation = _compare(attempt["check_suite_id"], run["check_suite_id"], "attempt_check_suite", trace)
                    attempt_association = _combine(attempt_association, suite_relation)
                    attempt["association"] = attempt_association
                    if suite_relation != "matched":
                        attempt["reasons"] = sorted(set(attempt["reasons"]) | {"attempt_check_suite_" + suite_relation})
            else:
                trace.add("selected_attempt_unconfirmed")
        listed_association = self.resolve_listing(selection, listed_association, trace,
            workflow_association=workflow_association, run=run, run_valid=critical,
            attempt=attempt, attempt_valid=attempt_valid, phase="initial")
        if critical:
            parent = _combine(workflow_association, listed_association, run_association, attempt_association)
            jobs = [self.job(job, record, workflow, pr, selection, run["check_suite_id"], parent, trace)
                    for job, record in self.job_rows(workflow, selection, trace)]
        association = _combine(workflow_association, listed_association, run_association, attempt_association,
                               *(job["association"] for job in jobs))
        expected_jobs = []
        for name in workflow["job_names"]:
            matches = [job for job in jobs if job["name"] == name]
            if not matches:
                status = "missing" if selection["status"] == "none" or selection["attempt"] is not None else "unconfirmed"
                trace.add("job_missing" if status == "missing" else "job_unconfirmed")
            elif len(matches) > 1:
                status = "ambiguous"
                trace.add("job_name_ambiguous")
            else:
                status = matches[0]["association"]
            expected_jobs.append({"name": name, "job_ids": sorted(job["id"] for job in matches if positive(job["id"])), "status": status})
        final_trace = _Trace(trace)
        final_selection, _, final_association = self.selection(workflow, pr, final_trace, "final")
        final_run, final_valid = None, False
        if final_selection["run_id"] is not None:
            final_run, _, final_valid = self.run_detail(workflow, pr, final_selection, final_trace, "final")
            if final_valid:
                final_selection["attempt"] = final_run["run_attempt"]
        self.resolve_listing(final_selection, final_association, final_trace,
            workflow_association=workflow_association, run=final_run, run_valid=final_valid,
            attempt=attempt, attempt_valid=attempt_valid, phase="final")
        before = self.selection_identity(selection)
        after = self.selection_identity(final_selection)
        changed = before is not None and after is not None and before != after
        # A different selected run is drift even if its final detail is unavailable.
        if selection["status"] in {"selected", "none"} and final_selection["status"] in {"selected", "none"}:
            changed = changed or selection["run_id"] != final_selection["run_id"]
        changed = changed and self.recheck_order_valid
        if changed:
            trace.add("selected_execution_changed", 0)
        result = {"workflow_id": workflow["id"], "path": workflow["path"], "selection": selection,
                  "candidates": candidates, "association": association, "run": run, "attempt": attempt,
                  "jobs": sorted(jobs, key=lambda job: (job["id"] if positive(job["id"]) else -1, canonical_digest(job))),
                  "expected_jobs": expected_jobs,
                  "recheck": {"selection": final_selection, "run": final_run, "reasons": sorted(final_trace.reasons), "raw_refs": sorted(final_trace.refs)},
                  "reasons": sorted(trace.reasons), "raw_refs": sorted(trace.refs)}
        return result, changed, self.recheck_order_valid and before is not None and after is not None

    @staticmethod
    def selection_identity(selection):
        if selection["status"] == "none":
            return (None, None)
        if selection["status"] == "selected" and selection["attempt"] is not None:
            return (selection["run_id"], selection["attempt"])
        return None


class _ListingTrace:
    """Defer only the listing PR gap; unrelated diagnostics keep their severity."""
    def __init__(self, trace):
        self.trace = trace

    def add(self, reason, severity=1):
        self.trace.add(reason, 0 if reason == "pr_association_unconfirmed" and severity == 1 else severity)

    def use(self, record):
        self.trace.use(record)


class _MapperV2(_Mapper):
    """ADR-002: matching detail evidence can resolve an absent listing relation."""
    def __init__(self, capture):
        super().__init__(capture)
        self.resolvable_listings = set()

    def selection(self, workflow, pr, trace, phase):
        selection, candidates, association = super().selection(workflow, pr, _ListingTrace(trace), phase)
        reasons, refs, raw = selection["reasons"], [], None
        if selection["run_id"] is not None:
            candidate = next(row for row in candidates if row["id"] == selection["run_id"])
            refs = candidate["raw_refs"]
            record = next(record for record in self.records if record["id"] == refs[0])
            payload = loads_strict(self.bodies[record["body"]["path"]])
            raw = next(row for row in payload["workflow_runs"] if row["id"] == selection["run_id"])
            _, reasons = _association(raw, pr, workflow)
        omitted = raw is not None and ("pull_requests" not in raw or raw["pull_requests"] == [])
        eligible = omitted and set(reasons) == {"pr_association_unconfirmed"}
        selection["listing_association"] = {
            "status": association, "reasons": sorted(reasons), "raw_refs": refs,
            "resolution": "unresolved" if association == "unconfirmed" else "not_applicable", "resolved_by": []}
        if eligible:
            self.resolvable_listings.add(id(selection))
        elif "pr_association_unconfirmed" in selection["reasons"]:
            trace.add("pr_association_unconfirmed")
        return selection, candidates, association

    def resolve_listing(self, selection, association, trace, *, workflow_association,
                        run, run_valid, attempt, attempt_valid, phase):
        if id(selection) not in self.resolvable_listings:
            return association
        self.resolvable_listings.remove(id(selection))
        matched = (workflow_association == "matched" and run_valid and attempt_valid
                   and run is not None and attempt is not None
                   and run["association"] == attempt["association"] == "matched"
                   and run["id"] == attempt["id"] == selection["run_id"]
                   and run["run_number"] == attempt["run_number"] == selection["run_number"]
                   and run["run_attempt"] == attempt["run_attempt"] == selection["attempt"]
                   and positive(run["check_suite_id"]) and run["check_suite_id"] == attempt["check_suite_id"]
                   and (phase == "initial" or self.recheck_order_valid))
        if not matched:
            trace.add("pr_association_unconfirmed")
            return association
        resolved_by = sorted(set(run["raw_refs"] + attempt["raw_refs"]))
        selection["listing_association"].update(resolution="resolved", resolved_by=resolved_by)
        for record in self.records:
            if record["id"] in resolved_by:
                trace.use(record)
        trace.add("listing_pr_association_resolved", 0)
        return "matched"


def map_capture(capture, *, mode="offline_replay") -> dict:
    """Derive observations only from a VerifiedCapture and its original profile.

    Validation/storage belongs to evidence.capture. No clock, file, subprocess,
    network or saved normalized report is consulted by this pure mapper.
    """
    if mode not in {"live", "offline_replay"}:
        raise ValueError("unsupported observation mode")
    version = capture.manifest["mapper_version"]
    if type(version) is not int or version not in {1, 2}:
        raise ContractError("unsupported observation mapper version")
    mapper = (_Mapper if version == 1 else _MapperV2)(capture)
    trace, profile = mapper.trace, mapper.profile
    result = {"kind": "delivery-review-github-observations", "observations_version": 1, "mapper_version": version,
              "scope": "github_platform_observations", "mode": mode, "observed_at": mapper.manifest["observed_at"],
              "profile_sha256": hashlib.sha256(capture.profile_bytes).hexdigest(),
              "capture_sha256": hashlib.sha256(capture.manifest_bytes).hexdigest(),
              "tested_source": "not_established", "subject": None, "workflows": [], "reasons": [], "raw_refs": [],
              "collection_status": "error", "captured_freshness": "unknown", "freshness": "unknown"}
    _, repository = mapper.one("repository", trace)
    pr_record, pr = mapper.one("pull_request", trace)
    subject = mapper.snapshot(pr_record, pr, trace)
    repository_ok = repository is not None and _compare(repository.get("id"), profile["repository"]["id"], "repository", trace) == "matched"
    if subject is None or not repository_ok:
        trace.add("subject_unconfirmed", 2)
    elif subject["repository_id"] != profile["repository"]["id"]:
        trace.add("repository_mismatch", 2)
    else:
        result["subject"] = subject
        if subject["head_repository_id"] != subject["repository_id"]:
            trace.add("unsupported_fork")
        else:
            final_record, final_pr = mapper.one("pull_request", trace, "final")
            final_subject = mapper.snapshot(final_record, final_pr, trace)
            changed = False
            checked = final_subject is not None and mapper.recheck_order_valid
            if final_subject is not None:
                if final_subject["pr_number"] != subject["pr_number"]:
                    trace.add("pr_number_mismatch", 2)
                    checked = False
            # An independently located, matching PR can establish a changed
            # well-typed field even when another final field failed parsing.
            if (mapper.recheck_order_valid and final_pr is not None and positive(final_pr.get("number"))
                    and final_pr["number"] == subject["pr_number"] and final_record["request"]["path"] ==
                    request_path(profile, "pull_request", pr_number=subject["pr_number"])):
                values = {"repository_id": _nested(final_pr, "base", "repo", "id"),
                          "head_repository_id": _nested(final_pr, "head", "repo", "id"),
                          "head_sha": _nested(final_pr, "head", "sha"), "base_tip_sha": _nested(final_pr, "base", "sha")}
                for field, value in values.items():
                    valid = sha(value) if field.endswith("sha") else positive(value)
                    if valid and value != subject[field]:
                        trace.add(field + "_changed", 0)
                        changed = True
            for workflow in sorted(profile["workflows"], key=lambda item: item["id"]):
                observation, drift, complete_recheck = mapper.workflow(workflow, pr)
                result["workflows"].append(observation)
                changed = changed or drift
                checked = checked and complete_recheck
            result["captured_freshness"] = "stale" if changed else "current" if checked else "unknown"
    if mapper.manifest["termination"]["status"] == "stopped":
        for reason in mapper.manifest["termination"]["reason_codes"]:
            trace.add(reason)
    result.update(collection_status=("complete", "partial", "error")[trace.severity],
                  reasons=sorted(trace.reasons), raw_refs=sorted(trace.refs))
    if mode == "live":
        result["freshness"] = result["captured_freshness"]
    return result
