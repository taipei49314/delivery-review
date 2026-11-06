"""Construct and validate the closed set of SPEC-001 read-only GitHub routes."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..contracts import ContractError, require

API_VERSION = "2026-03-10"
ALLOWED_RESPONSE_HEADERS = frozenset({
    "link", "content-type", "date", "etag", "last-modified", "retry-after",
    "x-github-request-id", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "x-ratelimit-resource",
})
ACQUISITION_ERRORS = frozenset({
    "transport_error", "request_timeout", "response_limit", "capture_limit", "request_limit",
    "authentication_unavailable", "resource_unavailable", "rate_limited", "http_error",
    "connector_unsupported", "redirect_refused", "unsafe_response_headers", "sensitive_response_refused",
})
_NUMBER = r"[1-9][0-9]{0,15}"
_LOCATOR = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
_IDENTITIES = ("workflow_id", "run_id", "attempt", "job_id", "check_run_id", "page")
_REQUIRED = {
    "repository": set(), "pull_request": set(), "workflow": {"workflow_id"},
    "workflow_runs": {"workflow_id", "page"}, "run": {"workflow_id", "run_id"},
    "attempt": {"workflow_id", "run_id", "attempt"},
    "jobs": {"workflow_id", "run_id", "attempt", "page"},
    "check_run": {"workflow_id", "run_id", "attempt", "job_id", "check_run_id"},
}


def positive(value) -> bool:
    return type(value) is int and 0 < value <= 2**53 - 1


def sha(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _locator(value) -> str:
    require(isinstance(value, str) and len(value) <= 256
            and re.fullmatch(_LOCATOR, value) is not None
            and all(part not in {".", ".."} for part in value.split("/")), "invalid_repository_locator")
    return value


def _split(path: str):
    require(isinstance(path, str) and 0 < len(path) <= 2048
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in path), "invalid_api_path")
    try:
        parsed = urlsplit(path)
    except ValueError as exc:
        raise ContractError("invalid_api_path") from exc
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment, "invalid_api_path")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    require(len(pairs) == len(query), "duplicate_api_query")
    require(path == parsed.path + ("?" + urlencode(sorted(query.items())) if query else ""),
            "noncanonical_api_path")
    return parsed.path, query


def validate_api_path(path: str) -> None:
    """Refuse paths outside the fixed route set, including unsafe query options."""
    route, query = _split(path)
    match = re.fullmatch(r"/repos/(" + _LOCATOR + r")(?P<suffix>/.*)?", route)
    require(match is not None, "unsupported_api_route")
    _locator(match.group(1))
    suffix = match.group("suffix") or ""
    simple = (not suffix or re.fullmatch(r"/pulls/" + _NUMBER, suffix)
              or re.fullmatch(r"/actions/workflows/" + _NUMBER, suffix)
              or re.fullmatch(r"/actions/runs/" + _NUMBER + r"(?:/attempts/" + _NUMBER + r")?", suffix)
              or re.fullmatch(r"/check-runs/" + _NUMBER, suffix))
    if simple:
        require(not query, "unexpected_api_query")
    else:
        runs = re.fullmatch(r"/actions/workflows/" + _NUMBER + r"/runs", suffix)
        jobs = re.fullmatch(r"/actions/runs/" + _NUMBER + r"/attempts/" + _NUMBER + r"/jobs", suffix)
        require(runs is not None or jobs is not None, "unsupported_api_route")
        expected = {"page", "per_page"} | ({"event", "head_sha", "exclude_pull_requests"} if runs else set())
        require(set(query) == expected, "unexpected_api_query")
        for name, maximum in (("page", 2**53 - 1), ("per_page", 100)):
            require(re.fullmatch(_NUMBER, query[name]) is not None and int(query[name]) <= maximum,
                    "invalid_api_pagination")
        if runs:
            require(query["event"] == "pull_request" and query["exclude_pull_requests"] == "false"
                    and sha(query["head_sha"]), "unexpected_api_query")
    for value in re.findall(r"/(\d+)(?=/|$)", suffix):
        require(positive(int(value)), "invalid_api_identity")


def request_path(profile: dict, role: str, *, pr_number=None, head_sha=None, workflow_id=None,
                 run_id=None, attempt=None, check_run_id=None, page=None) -> str:
    """Build canonical paths from trusted selectors and validated native IDs."""
    base = "/repos/" + _locator(profile["repository"]["full_name"])
    require(role in _REQUIRED, "unsupported_request_role")
    values = {"workflow_id": workflow_id, "run_id": run_id, "attempt": attempt,
              "check_run_id": check_run_id, "page": page}
    for name in _REQUIRED[role] - {"job_id"}:
        require(positive(values[name]), "invalid_request_identity")
    if workflow_id is not None:
        require(workflow_id in [w["id"] for w in profile["workflows"]], "unapproved_workflow")
    suffix, query = "", {}
    if role == "pull_request":
        require(positive(pr_number), "invalid_pr_number")
        suffix = f"/pulls/{pr_number}"
    elif role in {"workflow", "workflow_runs"}:
        suffix = f"/actions/workflows/{workflow_id}"
        if role == "workflow_runs":
            require(sha(head_sha), "invalid_head_sha")
            suffix += "/runs"
            query = {"event": "pull_request", "exclude_pull_requests": "false", "head_sha": head_sha}
    elif role in {"run", "attempt", "jobs"}:
        suffix = f"/actions/runs/{run_id}"
        if role in {"attempt", "jobs"}:
            suffix += f"/attempts/{attempt}"
        if role == "jobs":
            suffix += "/jobs"
    elif role == "check_run":
        suffix = f"/check-runs/{check_run_id}"
    if role in {"workflow_runs", "jobs"}:
        require(page <= profile["limits"]["max_pages"], "page_limit_exceeded")
        query.update(page=str(page), per_page=str(profile["limits"]["per_page"]))
    result = base + suffix + ("?" + urlencode(sorted(query.items())) if query else "")
    validate_api_path(result)
    return result


def parse_pr_url(url: str, profile: dict) -> int:
    require(isinstance(url, str), "invalid_pr_url")
    match = re.fullmatch(r"https://github\.com/(" + _LOCATOR + r")/pull/(" + _NUMBER + r")/?", url)
    require(match is not None, "invalid_pr_url")
    require(_locator(match.group(1)).casefold() == profile["repository"]["full_name"].casefold(),
            "pr_repository_mismatch")
    number = int(match.group(2))
    require(positive(number), "invalid_pr_number")
    return number


def _link_relations(value: str) -> dict[str, str]:
    if not value:
        return {}
    relations = {}
    for part in value.split(","):
        match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="(next|prev|first|last)"\s*', part)
        require(match is not None, "invalid_pagination_link")
        url, relation = match.groups()
        require(relation not in relations and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url),
                "invalid_pagination_link")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ContractError("unsafe_pagination_link") from exc
        require(parsed.scheme == "https" and parsed.netloc == "api.github.com" and not parsed.fragment,
                "unsafe_pagination_link")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        require(len(pairs) == len(dict(pairs)), "invalid_pagination_link")
        path = parsed.path + ("?" + urlencode(sorted(pairs)) if pairs else "")
        validate_api_path(path)
        relations[relation] = path
    return relations


def validate_response_headers(headers: dict) -> dict:
    require(type(headers) is dict and set(headers) <= ALLOWED_RESPONSE_HEADERS, "unsafe_response_headers")
    require(all(type(value) is str and len(value) <= 8192
                and not any(ord(c) < 32 or ord(c) == 127 for c in value) for value in headers.values()),
            "unsafe_response_headers")
    require(sum(len(key) + len(value) for key, value in headers.items()) <= 32768, "response_headers_limit")
    _link_relations(headers.get("link", ""))
    return dict(headers)


def next_page(headers: dict, expected_path: str, *, max_pages: int) -> int | None:
    """Validate every Link relation against the current fixed query and route."""
    validate_response_headers(headers)
    route, query = _split(expected_path)
    require("page" in query and positive(max_pages) and max_pages <= 10, "invalid_pagination_request")
    current = int(query["page"])
    expected = {key: value for key, value in query.items() if key != "page"}
    relations = _link_relations(headers.get("link", ""))
    for path in relations.values():
        linked_route, linked_query = _split(path)
        require(route == linked_route and "page" in linked_query
                and {k: v for k, v in linked_query.items() if k != "page"} == expected,
                "pagination_scope_mismatch")
    if "next" not in relations:
        return None
    number = int(_split(relations["next"])[1]["page"])
    require(number == current + 1, "invalid_next_page")
    require(number <= max_pages, "page_limit_exceeded")
    return number


def check_run_id_from_url(url: str, profile: dict) -> int:
    require(isinstance(url, str), "invalid_check_url")
    expected = "https://api.github.com/repos/" + _locator(profile["repository"]["full_name"])
    match = re.fullmatch(re.escape(expected) + r"/check-runs/(" + _NUMBER + r")", url)
    require(match is not None and positive(int(match.group(1))), "invalid_check_url")
    return int(match.group(1))


def validate_record_route(record: dict, profile: dict) -> None:
    role, phase = record.get("role"), record.get("phase")
    require(role in _REQUIRED and phase in {"initial", "final"}, "invalid_record_role")
    require(phase == "initial" or role in {"pull_request", "workflow_runs", "run"}, "invalid_record_phase")
    for field in _IDENTITIES:
        require(field in record and (positive(record[field]) if field in _REQUIRED[role] else record[field] is None),
                "invalid_record_identity")
    request = record.get("request")
    require(type(request) is dict and set(request) == {"method", "path", "api_version"}
            and request["method"] == "GET" and request["api_version"] == API_VERSION, "invalid_request")
    validate_api_path(request["path"])
    route, query = _split(request["path"])
    pr_number = None
    if role == "pull_request":
        match = re.fullmatch(r"/repos/" + _LOCATOR + r"/pulls/(" + _NUMBER + r")", route)
        require(match is not None, "record_route_mismatch")
        pr_number = int(match.group(1))
    expected = request_path(profile, role, pr_number=pr_number, head_sha=query.get("head_sha"),
                            **{key: record[key] for key in _IDENTITIES if key != "job_id"})
    require(request["path"] == expected, "record_route_mismatch")
