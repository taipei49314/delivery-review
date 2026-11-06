# SPEC-001: GitHub Actions observations

Status: normative target for implementation; this document is not a qualification
receipt. Date: 2026-11-06. Specification identifier: `SPEC-001`.

This specification defines a portable, read-only observation profile and raw
capture format. A conforming result describes GitHub's recorded associations and
their gaps. It does not decide delivery readiness, authenticate a producer or
establish the source actually checked out by a test command.

The words MUST, MUST NOT and SHOULD express implementation requirements. The
decision and compatibility rationale is in
[ADR-001](../amendments/ADR-001-observation-profile.md).

Implementation status: the profile validator, raw capture/reader, bounded GET
transport, pure mapper and inspect/verify/replay/refresh development commands are
implemented. See [usage and boundaries](../GITHUB-OBSERVATIONS.md). Synthetic
controls cover mapping and storage behavior; controlled live and independent
qualification remain separate gates. The standalone selection kernel still
leaves its listing attempt unconfirmed; the full mapper requires current-run and
attempt records. Checkout and producer identity remain unestablished.
See [field feasibility](SPEC-001-feasibility.md) for separately recorded public
API research. None of these files is a release receipt.

The machine-readable profile is
[`formats/github-observation-profile-v1.schema.json`](../../formats/github-observation-profile-v1.schema.json),
with an [authored example](../../examples/github-observations/README.md). The
selection kernel's internal result uses `status`, `association`, `raw_refs` and
`history` plus explicit `attempt_status`/`checkout_source: unconfirmed`; it is an
intermediate value, not the complete observations envelope in section 6.

## 1. Scope and format separation

The first profile supports GitHub.com, a PR whose head and base repositories are
the same repository, and Actions runs in that repository triggered by
`pull_request`. It observes association with the captured PR head SHA. The
`pr_head` profile value MUST NOT be rendered as proof of the runner's checkout.

Fork PRs, `pull_request_target`, push/workflow-dispatch attribution, merge or queue
source proof, cross-repository execution, general matrix inference, artifact
download/extraction, producer qualification, JUnit and engine execution are outside
this profile. Unsupported input remains visible with a reason; an implementation
MUST NOT silently select a more permissive profile.

There are three new document kinds, each with its own integer version:

| Kind | Version field | Responsibility |
| --- | --- | --- |
| `delivery-review-github-observation-profile` | `profile_version: 1` | Operator-approved selectors, algorithm and collection bounds. |
| `delivery-review-github-observation-capture` | `capture_version: 1` | Original profile and HTTP bodies, request metadata and acquisition termination. |
| `delivery-review-github-observations` | `observations_version: 1` | Deterministically derived platform observations. |

These are not contract-v1 policy/report documents or evidence bundle-v1
documents. Observation output MUST NOT contain `ready_for_review`, a contract-v1
`state` object, `minimum_binding`, `workflow_bound` or a product `exit_code`.
It MUST explicitly state `scope: github_platform_observations` and
`tested_source: not_established`. A green native job is an observation, not an
assertion that required tests ran or that a delivery is ready.

## 2. Trusted local profile

The operator supplies a UTF-8 JSON file outside the reviewed checkout. The
collector MUST preserve its original bytes and SHA-256 before fetching GitHub.
It MUST NOT discover or replace this profile from files in the PR.

The profile has exactly the following fields. This illustrative identity is
fictional and does not constitute a live fixture:

```json
{
  "kind": "delivery-review-github-observation-profile",
  "profile_version": 1,
  "profile_id": "example-ci-observations",
  "api_version": "2026-03-10",
  "repository": {"id": 9001, "full_name": "example-owner/example-repo"},
  "source_mode": "pr_head",
  "selection": "latest_applicable_run_number_v1",
  "workflows": [
    {
      "id": 101,
      "path": ".github/workflows/tests.yml",
      "event": "pull_request",
      "app_id": 202,
      "job_names": ["pytest"]
    }
  ],
  "limits": {
    "per_page": 100,
    "max_pages": 10,
    "max_response_bytes": 2097152,
    "max_total_bytes": 104857600,
    "max_requests": 200,
    "timeout_seconds": 30
  }
}
```

All listed fields are required; additional fields and unknown versions are
rejected. IDs are integers in `[1, 2^53-1]`, excluding booleans. `profile_id` is a
1-128 character ASCII identifier beginning with a letter or digit and otherwise
using letters, digits, `_`, `-`, `.`, or `:`. `full_name` is a single `owner/repo`
locator; numeric repository identity is authoritative. The PR URL must name the
same locator, compared case-insensitively, and a positive PR number.

There MUST be 1-10 workflows with distinct numeric IDs and distinct paths. Paths are exact, portable
repository-relative `.github/workflows/*.yml` or `*.yaml` paths, with no `.` or
`..` path components, backslash, query, fragment or `@` suffix. The filename begins
with an ASCII letter, digit or underscore; the rest uses letters, digits,
underscores, dots or hyphens. `event` is exactly `pull_request`.
`app_id` is explicitly approved, never inferred from a familiar check name.
`job_names` contains 1-100 distinct, exact, nonempty names of at most 256
characters. It identifies expected display labels only after independent
run/attempt/job/check linkage; it is not a general matrix specification.

The explicit `api_version` is fixed to `2026-03-10`; it is part of mapper version
1, not an ambient transport default. `source_mode` and `selection` have only the
values shown above. Limits are positive integers. `per_page` is at most 100 and
is fixed to the profile's value throughout a capture. The other hard ceilings
are 10 pages per listing, 2 MiB per body, 100 MiB total retained bodies,
200 requests and 30 seconds per request. The profile may lower the ceilings.
A ceiling is a refusal boundary, never permission to declare a partial list
complete. Changes to selectors or bounds create new profile bytes/digest.

## 3. Request and raw capture contract

Only HTTPS GET requests to `api.github.com` are permitted. Routes are constructed
from the validated locator, numeric IDs and fixed queries. API-provided URLs
are data until validated against the expected route. Redirects, user-info,
fragments, alternate hosts, credential query parameters and arbitrary URLs MUST
be refused. No endpoint under this specification executes or modifies a PR.

The planned request roles are:

| Role | Route after `/repos/{owner}/{repo}` | Phase |
| --- | --- | --- |
| `repository` | The repository root itself | initial |
| `pull_request` | `/pulls/{pr_number}` | initial and final |
| `workflow` | `/actions/workflows/{workflow_id}` | initial |
| `workflow_runs` | `/actions/workflows/{workflow_id}/runs` | initial and final, paginated |
| `run` | `/actions/runs/{run_id}` | initial and final selected run |
| `attempt` | `/actions/runs/{run_id}/attempts/{attempt}` | initial |
| `jobs` | `/actions/runs/{run_id}/attempts/{attempt}/jobs` | initial, paginated |
| `check_run` | `/check-runs/{check_run_id}` | initial, for selected-attempt jobs |

Workflow-run lists use `event=pull_request`, the original captured `head_sha`,
`exclude_pull_requests=false`, the profile's `per_page` and the page number. They MUST NOT
filter by success, conclusion, actor, branch name or a convenient date window.
Jobs use the attempt-specific route; an all-attempt jobs list is not equivalent.
Query keys are serialized in lexicographic order with percent-encoded values.
The supported API routes and attempt-specific job listing are documented by
GitHub's [workflow-run](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2026-03-10)
and [workflow-job](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2026-03-10)
APIs.

Each new capture directory contains `profile.json`, zero or more
`raw/r000001.body` files and a final `capture.json`. Body files retain original
response bytes; JSON normalization MUST NOT rewrite them. Record IDs use `r`
followed by six decimal digits, monotonically increasing in request order.

The capture manifest has exactly these top-level fields:

```text
kind: "delivery-review-github-observation-capture"
capture_version: 1
mapper_version: 1
observed_at: UTC timestamp for collection start
collector: {version: nonempty string, source_sha: full 40-character commit SHA}
profile: {path: "profile.json", size_bytes: integer, sha256: lowercase SHA-256}
termination: {status: "completed" | "stopped", reason_codes: [identifier, ...]}
records: [record, ...]
```

Every record has exactly these fields:

```text
id: "r000001"
phase: "initial" | "final"
role: one of the roles in the route table
workflow_id, run_id, attempt, job_id, check_run_id: positive integer or null
page: positive integer for a paginated role, otherwise null
request: {method: "GET", path: canonical relative API path including query,
          api_version: "2026-03-10"}
observed_at: UTC timestamp for receipt or terminal request failure
status_code: HTTP integer status or null when no HTTP response was obtained
response_headers: object of permitted lowercase header names and string values
body: {path: "raw/r000001.body", size_bytes, sha256, complete: boolean} or null
error: {code: identifier} or null
```

Timestamps use `YYYY-MM-DDTHH:MM:SSZ`. Nullable identity fields are populated only
where relevant to the requested route; metadata never overrides response body
identity. Workflow/PR names, run IDs, attempt numbers and page numbers in record
metadata MUST agree with the request and the relevant payload before use.
Duplicated logical request roles/pages in one phase, unknown roles and
non-monotonic request records are rejected; version 1 makes one attempt per
request and does not silently retry with different data.

Profile and capture JSON each have a separate 2 MiB ceiling and a maximum nesting
depth of 32. They use strict UTF-8, reject duplicate keys/non-finite numbers, and
do not coerce strings, booleans or floats to integer identities/counts. The record
count cannot exceed `max_requests`. Capture metadata limits apply independently
of the total-body budget. Live capture `collector.source_sha` identifies the
actual clean product source used; an uncommitted implementation cannot be labeled
as execution of its unchanged base commit. Synthetic test documents must retain
their explicit fixture provenance outside any claimed live execution receipt.

Permitted response headers are `link`, `content-type`, `date`, `etag`,
`last-modified`, `retry-after`, `x-github-request-id`, `x-ratelimit-limit`,
`x-ratelimit-remaining`, `x-ratelimit-reset` and `x-ratelimit-resource`. Request
authorization headers, cookies, token values, signed URLs, process environment
and raw transport stderr MUST NOT be stored or printed. Authentication is supplied
outside the capture and is used only by the HTTP boundary. Private capture data
stays local; publishing fixtures requires separate provenance and disclosure review.

A body limit may leave an explicitly incomplete bounded prefix with
`complete: false` and `response_limit`; such bytes are diagnostic only and MUST
NOT be parsed as a complete response. `body: null` means no body was retained.
Total bytes and request counts are bounded independently of metadata claims.

The capture directory is created exclusively. Raw responses and their hashes are
saved before mapping. `capture.json` is written last, after all referenced bytes
exist. An interrupted directory without a valid manifest is not replayable.
`termination: stopped` seals a diagnostic capture; it does not pretend the planned
acquisition completed. Readers verify exact files, sizes and digests, reject
links/reparse points, unsafe paths, extra files and unsupported versions, and
apply independent local bounds no larger than this specification's ceilings.
A local owner able to rewrite both bytes and manifest can forge this consistency;
the capture is not a signature or producer attestation.

## 4. Snapshot, complete listings and deterministic selection

### 4.1 Subject

The initial repository and PR records must establish the approved numeric
repository ID, PR number, head/base repository IDs and full head/base-tip SHAs.
For this profile all repository IDs must equal the approved repository ID.
Missing or malformed identity leaves `subject: null` and a collection error;
confirmed fork identity yields `unsupported_fork`. No placeholder identity is
manufactured. The observation subject does not need a merge-base: it performs
no static diff analysis or checkout proof and is not the contract-v1 subject.

### 4.2 Listing completion

Each listing begins at page 1. All pages must have HTTP 200, complete valid JSON,
the expected collection array and an integral `total_count`. Every `next` link
must resolve to the same validated route and fixed query, with exactly the next
page. Missing intermediate pages, duplicate IDs, contradictory counts, repeated
pages, malformed links or different query constraints invalidate completeness.
Stopping requires no `next` relation and unique observed item count equal to the
consistent `total_count`. Absence of a `Link` header alone is not enough.
The paging mechanism is specified in
[GitHub's pagination documentation](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).

Filtered workflow-run searches are capped by GitHub. The mapper MUST conservatively
refuse completeness when `total_count >= 1000`, when the configured page limit is
exhausted with remaining items, or when the transport reports truncation. It MUST
NOT claim an older run is latest from the visible prefix. The documented search
limit is in the [workflow-run API](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2026-03-10).

### 4.3 Candidate selection before association checks

For each configured workflow, an applicable candidate has matching repository ID,
workflow ID, event and captured head SHA. Each used field must be present and
well-typed. Malformed candidates whose eligibility/order cannot be determined
make selection incomplete; they are not silently discarded.

PR association, head-repository identity, workflow path, App ID, job matching,
native status and conclusion are deliberately **not** eligibility filters. They
are checked after selecting the latest candidate. An empty or wrong PR association
in a newer run therefore cannot cause a fallback to an older green run.

Order retained candidates by numeric `(run_number, id)` descending for stable
display. A distinct run ID with the same `run_number` means
`ambiguous_run_sequence`: no authoritative selection is established, and the ID
ordering does not claim chronological priority. With distinct sequence numbers,
select the highest run number. Do not rank by update time, response order, success
time or conclusion. GitHub defines the run sequence separately from the attempt
counter; re-running keeps the sequence number while increasing the attempt.
[GitHub variables reference](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)

An empty, complete listing establishes `no_applicable_run`; a partial listing
does not. Preserve all candidate IDs and rejection reasons in deterministic order.
After candidate selection, read the current `run` endpoint. Its positive
`run_attempt` selects the attempt to collect. The detail must agree on run ID,
run number, repository, workflow, event and head SHA. A greater attempt than the
earlier list records is allowed and retained as observed movement before child
collection; a lower/contradictory attempt is inconsistent. Fetch the selected
attempt and its own jobs. Never combine children from two attempts or retry an
older attempt when the selected attempt is inaccessible.

## 5. Association checks and native outcomes

Selected run and attempt details must independently match the approved workflow
path, head repository and a PR association containing the captured PR number and
repository/head identities. A run `path` may have one nonempty `@ref` suffix;
only its path portion is compared with the configured path, while the full native
value is retained. A ref suffix is not a qualified workflow source revision.
Current workflow metadata must match the configured numeric ID and exact path.

Missing or empty `pull_requests` is `pr_association_unconfirmed`, not proof that
the run was unrelated to any PR. Explicit conflicting identity is a mismatch.
Neither changes which candidate was selected, and neither grants tested-source
confirmation. `referenced_workflows` can be retained in raw responses but does not
alone establish a qualified producer contract in this profile.

Every job must belong to the selected run/attempt endpoint and agree with any
explicit `run_id`, `run_attempt` and `head_sha` supplied by that payload. Missing
fields cannot be filled from an arbitrary job name. The attempt-specific route
is retained as part of the platform association. The collector validates the
job's `check_run_url` as an exact same-repository check-run route and independently
reads it. Its ID, `app.id`, `head_sha` and `check_suite.id` must match the URL,
profile, captured head and selected run's check-suite identity. A check with the
same display name is never a substitute. Check-run platform fields are documented
in the [Checks API](https://docs.github.com/en/rest/checks/runs?apiVersion=2026-03-10).

Expected `job_names` are matched within the selected attempt after the identity
checks. Zero matches yields `job_missing`; several matches yield
`job_name_ambiguous`. All native jobs remain visible. Names MUST NOT be parsed into
matrix dimensions or used to assemble success from multiple executions.

Retain native `status` and `conclusion`, including null, and record each supporting
raw record ID. Known completed conclusions are `success`, `failure`, `neutral`,
`cancelled`, `skipped`, `timed_out`, `action_required`, `stale` and
`startup_failure`; none is coerced to another. Non-completed statuses, including
`queued`, `in_progress`, `waiting`, `pending` and `requested`, remain non-completed.
Unknown values are preserved with `unsupported_native_status`; contradictory
status/conclusion pairs remain unconfirmed. Completion or upload success never
becomes a pytest verdict, a count of executed tests or source proof.

This first profile neither downloads nor binds artifacts. An artifact's run ID,
name or embedded receipt cannot by itself establish its attempt/job/checkout
association. Qualification of that relationship belongs to a later producer and
artifact specification, with its own actual transport controls.

## 6. Final recheck, failures and observation output

Before finalizing a live observation, re-read the PR, repeat complete candidate
selection for each workflow against the original head, and read each final
selected run's current attempt. Compare repository identity, head, base tip and
the selected `(workflow_id, run_id, attempt)` tuples. Any observed change is
`captured_freshness: stale`. If no change is observed but a required recheck is
missing/incomplete, it is `unknown`. Only a complete matching recheck is `current`.
A known change remains stale even if another recheck failed.

The final pass does not overwrite initial observations or splice final jobs into
the original attempt. Native status changes within an unchanged attempt remain
recorded observations; freshness here concerns source/selected-execution identity,
not a guarantee that a running job has stopped changing.

Output has kind/version as defined above, `mapper_version: 1`, scope, mode
(`live` or `offline_replay`), original `observed_at`, profile and capture digests,
`tested_source: not_established`, nullable subject, per-workflow selections,
association results, native job/check observations, ordered reason codes and
raw-record references. Workflows sort by ID; candidates sort by the ranking rule;
jobs sort by job ID; diagnostic codes sort lexicographically without duplicates.
Selections distinguish `selected`, `none`, `partial`, `ambiguous` and `error`.
Association results distinguish `matched`, `unconfirmed` and `mismatch`, and mean
only the explicitly checked platform relationship.

`collection_status` is `complete`, `partial` or `error`. Complete means all planned
in-scope observations were obtained and interpreted, not that native jobs passed
or delivery evidence is sufficient. A complete observation may show a native
failure or an explicit association mismatch. Missing/unsupported observations
yield partial. Invalid capture/profile or inability to establish the initial
subject yields error. Preserve known native failure and mismatch details alongside
partial/error outcomes.

At minimum, the mapper/collector recognizes these diagnostic classes:

| Condition | Reason and handling |
| --- | --- |
| No HTTP response | `transport_error` or `request_timeout`; retain sanitized request metadata. |
| HTTP 401 | `authentication_unavailable`; do not reveal credential state. |
| HTTP 403 or 404 | `resource_unavailable`; do not claim nonexistent rather than inaccessible. |
| HTTP 429, or 403 with rate-limit evidence | `rate_limited`; preserve permitted retry/rate headers; no silent retry. |
| Other non-200 HTTP | `http_error`; retain bounded original body privately. |
| Tool/connector refuses an otherwise valid route | `connector_unsupported`; not a GitHub 404 or missing platform evidence. |
| Missing required raw record/body | `observation_missing`; never synthesize an empty successful response. |
| Invalid JSON or used field | `invalid_response`; retain original bytes. |
| Count/link/page/budget failure | `pagination_incomplete`, `response_limit`, `capture_limit` or `request_limit`. |
| Unsupported subject/event/source | Specific unsupported code; no alternate-profile fallback. |
| Contradictory identity | Specific `*_mismatch` or `*_unconfirmed`; keep selected candidate. |

An error before a full subject exists uses this same sealed diagnostic capture and
an observations result with `subject: null`; it MUST NOT fabricate contract-v1
SHAs to satisfy report validation. Record acquisition `error.code` values are
limited to transport/HTTP/tool/budget failures; mapper-derived identity and JSON
diagnostics belong in derived observations.

Any command exposing these observations uses operational exit 0 only for a
complete observation computation and exit 2 for partial/error. A recorded native
test failure does not cause product exit 1: this command is not a readiness
command. CLI text and JSON MUST label this distinction. The existing
`tools/evidence.py` contract-report replay keeps its separate exit semantics.

## 7. Raw replay and compatibility

Raw replay verifies the capture inventory and profile bytes, then passes the
saved response bodies/metadata through the **same versioned mapper** used by live
collection. It MUST NOT read a saved normalized result as an input, contact GitHub,
execute code from the subject, repair digests or renew observation timestamps.
Unknown profile/capture/mapper versions are rejected, not guessed.

For the same capture/profile/mapper, selections, association results, native
outcomes and reasons are byte-deterministic. Replay keeps `captured_freshness` as
the historical recheck finding but always reports current applicability as
`freshness: unknown`; live output uses the captured value for `freshness`.
Replaying a stale historical capture therefore does not hide its observed drift.
Any replay output is written to a new destination outside the source capture;
source bytes and prior outputs are never overwritten. A later refresh is a new
capture, not mutation of the old one.

Capture version 1 does not pretend to be evidence bundle version 1. The existing
bundle reader continues to reject it. New capture readers do not convert saved
normalized reports into raw provider evidence. Mapping from this observation
format to a future full readiness profile requires a separate reviewed contract
change and the producer/parser/scanner evidence demanded by that profile.

## 8. Acceptance gates

Implementation and qualification are separate. A conforming implementation must
have recorded controls for all of the following; this document does not mark them
passed:

1. Byte preservation, inventory tampering, profile substitution, hostile paths,
   partial/interrupted captures and independent reader resource limits; rejected
   alternate-host/credential-bearing URLs and absence of secrets from diagnostics.
2. Same-repository head positive control; wrong numeric repo, fork, missing subject,
   unsupported event and no fabricated source identity.
3. Multiple run pages; missing/duplicate/out-of-order pages; count disagreement;
   malformed next links; configured limits and saturated 1,000-result searches.
4. Old success/new pending; old success/new failure; duplicate run sequence;
   malformed candidate; deterministic order independent of response order.
5. Newest run with absent/wrong PR association or path; selected candidate remains
   visible and no older green fallback occurs.
6. Current latest attempt, inaccessible latest attempt, contradictory attempt
   metadata and complementary successes in different attempts.
7. Correct job/check/App relationship; wrong App/check suite/head, duplicate job
   names, missing job and an arbitrary same-named check.
8. Native failures, skips, cancellation, timeouts, unknown status and HTTP/API/tool
   failures remain distinct; acquisition success is never readiness.
9. Head, base and selected run/attempt drift; missing final page/recheck; unchanged
   positive control; known drift retained alongside another recheck failure.
10. Raw replay regenerates observations without a normalized result present, retains
    timestamp and historical drift, has freshness unknown, produces the same
    semantic results and performs no network/subprocess operation.
11. At least one controlled live same-repository `pull_request` run exercises the
    supported path with source-pinned product/fixture records. Independent external
    reproduction is required before publicly qualifying the profile.

Synthetic, public-recorded, private-recorded and controlled live sources must be
identified separately. Public fixtures need provenance and a redistribution basis;
recording a public API response alone is not a live controlled execution test.
Field-feasibility probes are supporting evidence and may discover unsupported
cases; their receipts are recorded separately and do not close these gates or
E01-E18. No EC pool access or private corpus is a runtime prerequisite.
