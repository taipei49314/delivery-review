"""Closed artifact/attestation routes, separate from historical capture-v1 routes."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..contracts import require
from .github_routes import _locator, positive, validate_response_headers

PREDICATES = frozenset({"https://github.com/taipei49314/delivery-review/attestation/pytest/v1",
                        "https://slsa.dev/provenance/v1"})


def artifact_path(operation, repository, identity, page=None, per_page=None):
    _locator(repository)
    require(positive(identity), "invalid_artifact_identity")
    base = "/repos/" + repository + "/actions/"
    if operation == "run_artifacts":
        require(positive(page) and type(per_page) is int and 1 <= per_page <= 100,
                "invalid_artifact_pagination")
        return base + f"runs/{identity}/artifacts?page={page}&per_page={per_page}"
    require(operation in {"artifact", "archive"} and page is None and per_page is None,
            "invalid_artifact_request")
    return base + f"artifacts/{identity}" + ("/zip" if operation == "archive" else "")


def attestation_path(repository, digest, predicate_type, per_page, cursor=None):
    _locator(repository)
    require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid_subject_digest")
    require(type(predicate_type) is str and predicate_type in PREDICATES, "unsupported_predicate_type")
    require(type(per_page) is int and 1 <= per_page <= 10, "invalid_attestation_page_size")
    query = {"per_page": str(per_page), "predicate_type": predicate_type}
    if cursor is not None:
        require(type(cursor) is dict and len(cursor) == 1 and set(cursor) <= {"before", "after"},
                "invalid_attestation_cursor")
        value = next(iter(cursor.values()))
        require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_+/:=-]{1,512}", value),
                "invalid_attestation_cursor")
        query.update(cursor)
    return "/repos/" + repository + "/attestations/sha256:" + digest + "?" + urlencode(sorted(query.items()))


def split_evidence_path(path):
    require(type(path) is str and len(path) <= 4096, "invalid_evidence_route")
    part = urlsplit(path)
    require(not part.scheme and not part.netloc and not part.fragment, "invalid_evidence_route")
    pairs = parse_qsl(part.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    require(len(query) == len(pairs), "duplicate_evidence_query")
    require(not any(ord(c) <= 32 or ord(c) >= 127 for c in path), "invalid_evidence_route")
    artifact = re.fullmatch(r"/repos/([^/]+/[^/]+)/actions/runs/([1-9][0-9]{0,15})/artifacts", part.path)
    attest = re.fullmatch(r"/repos/([^/]+/[^/]+)/attestations/sha256:([0-9a-f]{64})", part.path)
    if artifact:
        require(set(query) == {"page", "per_page"} and all(re.fullmatch(r"[1-9][0-9]{0,15}", value)
                for value in query.values()), "invalid_artifact_query")
        artifact_path("run_artifacts", artifact[1], int(artifact[2]), int(query["page"]), int(query["per_page"]))
    elif attest:
        require({"per_page", "predicate_type"} <= query.keys() and
                set(query) <= {"per_page", "predicate_type", "before", "after"} and
                re.fullmatch(r"[1-9][0-9]?", query["per_page"]), "invalid_attestation_query")
        cursor = {key: value for key, value in query.items() if key in {"before", "after"}}
        attestation_path(attest[1], attest[2], query["predicate_type"], int(query["per_page"]), cursor or None)
    else:
        require(False, "unsupported_evidence_route")
    return part.path, query


def evidence_links(value):
    if not value:
        return {}
    result = {}
    for part in value.split(","):
        match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="(next|prev|first|last)"\s*', part)
        require(match is not None and match[2] not in result, "invalid_evidence_link")
        url = urlsplit(match[1])
        require(url.scheme == "https" and url.netloc == "api.github.com" and not url.fragment,
                "unsafe_evidence_link")
        path = url.path + ("?" + url.query if url.query else "")
        split_evidence_path(path)
        result[match[2]] = path
    return result


def evidence_headers(headers):
    require(type(headers) is dict, "unsafe_response_headers")
    validate_response_headers({key: value for key, value in headers.items() if key != "link"})
    link = headers.get("link", "")
    require(type(link) is str and len(link) <= 8192 and
            not any(ord(c) < 32 or ord(c) == 127 for c in link), "unsafe_response_headers")
    require(sum(len(key) + len(value) for key, value in headers.items()) <= 32768, "response_headers_limit")
    evidence_links(link)
    return dict(headers)


def next_evidence_page(headers, expected_path):
    """Return next integer page or cursor; every relation must stay in scope."""
    evidence_headers(headers)
    route, query = split_evidence_path(expected_path)
    kind = "page" if "page" in query else "cursor"
    changing = {"page"} if kind == "page" else {"before", "after"}
    fixed = {k: v for k, v in query.items() if k not in changing}
    links = evidence_links(headers.get("link", ""))
    for path in links.values():
        linked_route, linked_query = split_evidence_path(path)
        require(route == linked_route and {k: v for k, v in linked_query.items() if k not in changing} == fixed,
                "evidence_pagination_scope_mismatch")
    if "next" not in links:
        return None
    next_query = split_evidence_path(links["next"])[1]
    if kind == "page":
        page = int(next_query["page"])
        require(page == int(query["page"]) + 1, "invalid_next_artifact_page")
        return page
    cursor = {k: v for k, v in next_query.items() if k in changing}
    require(len(cursor) == 1 and cursor != {k: v for k, v in query.items() if k in changing},
            "invalid_next_attestation_cursor")
    return cursor
