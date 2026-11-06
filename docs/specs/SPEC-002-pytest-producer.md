# SPEC-002 — approved pytest producer and JUnit observations

Status: proposed producer contract; bounded parser implemented and locally
qualified against authored pytest 8.4.2 examples. Hosted producer, independent
artifact-to-job proof, source-mode qualification and E01–E18 product integration
remain **NOT_RUN**. This document does not amend contract v1, its schemas or
frozen acceptance expectations. It does not approve a producer workflow SHA.

The normative boundaries are [CONTRACT](../CONTRACT.md), especially sections
3–5, [ACCEPTANCE](../ACCEPTANCE.md), especially E03/E10/E14/E16, and
[ARCHITECTURE](../ARCHITECTURE.md). The producer is permitted to execute tests in
its approved CI environment. The verifier only reads saved bytes and platform
observations; it never executes reviewed PR tests, installs project dependencies
or runs commands from a receipt.

## 1. Producer identity and execution contract

An operator approves an external producer repository by numeric repository ID,
`.github/workflows/...` path and full commit SHA in the trusted policy. The
caller must invoke that immutable reusable workflow by full SHA. A same-name
workflow copied into the reviewed repository is not that producer. The approved
revision includes its own driver, test configuration, runtime/dependency pins,
source-selection code and fixed setup/test/receipt/upload steps. All referenced
actions and nested reusable workflows require reviewed full-SHA pins. No branch,
tag, caller-supplied shell command, caller-supplied Python module or producer code
loaded from the PR is an approved executable entry point.

GitHub reusable workflows declare `workflow_call`; callers can reference a
repository workflow at a commit SHA. Our stronger full-SHA and external-policy
requirements are product rules. [GitHub reusable workflow documentation](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)

The first qualified producer contract must use one fixed producer job, without
matrix expansion or caller-selectable job steps. Its display name and artifact
name are locators, not authentication. The fixed job resolves its numeric job ID
using platform metadata and records it in the receipt. The verifier independently
checks that ID against the selected run's exact attempt and approved job/check
identity; ambiguous resolution is incomplete. Caller jobs do not inherit trust
merely by using the same names or an Actions App check.

The producer must keep these outcomes separate:

1. Setup: exact runtime identity and dependency installation result. A failed or
   absent setup cannot be silently replaced with a passing test summary.
2. Test invocation: actual argv, native exit, timeout/termination and retained raw
   JUnit bytes. The approved driver creates the report destination itself and
   checks that a new regular report was produced by this invocation. Reusing an
   earlier run's report is forbidden.
3. Receipt: contract-v1 JSON written before upload, with exact source/producer
   claims and the internal JUnit path, size and SHA-256. Failure to parse a report
   is not permission to invent zero successful tests.
4. Upload: fixed pinned upload action, exact receipt/report paths, missing files
   treated as error and `overwrite: false`. Upload may be attempted after test
   failure to preserve evidence. Upload failure is an acquisition gap, not an
   observed test failure that could make a test obligation complete.

The producer must retain nullable exits honestly. A single pytest invocation
does not provide a separate collection-process exit: `collection_exit` is null
unless a separately recorded approved process supplied it. A report containing
a collection error remains an error regardless of that field. Native pytest
exit 0, 1, 2, 3, 4 and 5 mean distinct outcomes; 0 alone does not prove sufficient
execution, and 5 does not mean success. [pytest exit-code reference](https://docs.pytest.org/en/8.4.x/reference/exit-codes.html)

The approved test configuration is outside the subject's writable checkout.
For the initial dialect it fixes `junit_family=xunit2`,
`junit_suite_name=delivery-review`, `junit_logging=no`,
`junit_log_passing_tests=false`, `xfail_strict=true`, and empty configured
`addopts`. The driver passes an absolute trusted `-c` path and a fixed report
destination. It clears ambient `PYTEST_*` options before setting
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; it records the actual Python, pytest and
dependency versions. Optional plugins, xdist, rerun plugins, xunit1, custom
JUnit postprocessors and runtime replacement of the pinned pytest installation
are outside this qualifier. Explicit test/conftest code is still untrusted code
executed by the producer; plugin autoload isolation is not a sandbox or a proof
of test honesty.

Production approval additionally requires bounded producer execution, an
ephemeral approved runner, separation of publication credentials from test
execution, pinned dependency artifacts and runner/platform qualification. The
local experiment below establishes none of those hosted properties. No caller
input may weaken source modes, collection requirements, timeouts or policy.

## 2. Source selection and independent confirmation

The inspection snapshot freezes numeric base/head repository IDs, PR number,
base tip, PR head and static-analysis merge base. Source mode is allowed only
when the external policy permits it. The fixed producer records the actual
checked-out Git object after checkout, independently of caller-provided labels.
The verifier checks the receipt against the saved snapshot, selected platform
run/attempt metadata and independently obtained source proof:

| Mode | Required relationship |
| --- | --- |
| `pr_head` | Actual checkout is the snapshot's full PR-head SHA in the expected head repository; receipt `merge_parents` is empty. |
| `github_merge` | Actual checkout is the supported GitHub PR merge object with exactly two ordered parents: observed base-tip SHA, then PR-head SHA. Retain the platform Git commit response containing that object and parents. |

An equal tree, branch name, receipt claim or run `head_sha` alone does not prove
the checkout relationship. Head and merge SHAs are distinct by design. Missing
parent proof, changed base/head, wrong parent order, third parent, custom merge,
merge queue and unsupported fork relationships leave source proof unconfirmed.
Never synthesize a merge in the verifier to make evidence match. The approved
source contract must also prevent a caller from selecting an unrelated commit.

Select the latest applicable run and its latest attempt before selecting child
jobs/artifacts. Missing evidence on that attempt cannot be filled from an older
passing attempt. Recheck the relevant PR/source and run-attempt state before
any current-readiness conclusion. Replay retains the old observation time and
cannot become fresh merely because it was parsed again.

## 3. Independent workflow and artifact association

Capture raw run and exact-attempt API responses before normalization. Confirm
the actual `referenced_workflows` entry's repository/path and full `sha` against
the approved producer, rather than trusting the caller's `uses` text or the
receipt. Preserve the API response digest used as producer proof. The workflow
run API exposes referenced workflow path/SHA records. Its presence does not by
itself identify which artifact a particular job uploaded. [GitHub workflow-run REST API](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2026-03-10)

The job API supports an exact run-attempt jobs listing and individual numeric
job retrieval. Capture complete pages and match the selected job, run, head,
check/App identity, native steps and outcomes. Do not treat a step's display name
as executable-code identity. [GitHub workflow-job REST API](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2026-03-10)

Artifact REST metadata provides an artifact ID, digest, size, expiry and a
workflow-run relation. The documented response does not provide a native exact
attempt or job ID. Thus it can support the outer artifact/run relation but
cannot alone establish the missing artifact/job/attempt relation. This is a
product limitation inferred from the documented fields, not a claim that
GitHub authenticates no other artifact metadata. [GitHub artifact REST API](https://docs.github.com/en/rest/actions/artifacts?apiVersion=2026-03-10)

The following candidate conjunction is **insufficient** for `workflow_bound`:
same run; run/attempt embedded in a unique-looking artifact name; creation time
inside the approved job's start/end window; matching receipt job ID; referenced
workflow SHA confirmed; and a successful upload step in that job.

Concrete counterexample: the trusted upload succeeds with `overwrite: false`.
A caller-controlled concurrent job then deletes/recreates that artifact using
`overwrite: true`, before the trusted job finishes post steps or within timestamp
granularity. The replacement has the same name and run and can contain copied
identity claims. The trusted upload remains successful, yet the final artifact
has a different ID and bytes. Immutable archive contents do not prevent this
replacement. The upload action documents replacement with a new ID and its
pinned v4.6.2 source performs deletion when overwrite is enabled.
[Upload action behavior](https://github.com/actions/upload-artifact/blob/v4.6.2/README.md#overwriting-an-artifact),
[upload implementation](https://raw.githubusercontent.com/actions/upload-artifact/v4.6.2/src/upload/upload-artifact.ts)

Therefore the release gate requires **independent proof of the actual artifact
ID and digest emitted by the trusted upload**, bound to that exact approved job
and attempt. `overwrite: false`, names and time windows remain useful consistency
checks but are never sufficient proof. Possible proof routes require separate
qualification before implementation can award `workflow_bound`:

- A complete platform-held job/step log obtained by numeric job ID, with a
  demonstrably unambiguous trusted upload step boundary and action-generated
  artifact ID/digest. Unstructured whole-job stdout is insufficient: tests can
  print lookalike lines, and collection must reject duplicate or ambiguous
  candidate records, truncated logs and step-boundary spoofing. The approved
  upload step itself must be shown to be the pinned action. A receipt or another
  artifact containing a copied output is still self-report.
- A verified artifact attestation binding the exact digest, source, run/attempt
  and approved reusable-workflow identity, with the required job association.
  Use a separately qualified official verifier and validated identity policy,
  not handwritten cryptography or a JSON object that merely resembles an
  attestation. A digest-only signature or a claim missing the attempt/job
  relationship is insufficient.

GitHub OIDC for reusable workflows can carry caller run/attempt claims and the
called `job_workflow_ref`. A raw token claim is not an artifact association:
signature/issuer/audience/time validation and a binding to the actual artifact
digest are still necessary, and a numeric job-ID relationship must be
established. This is a potential building block, not the implemented solution.
[GitHub OIDC reusable workflow documentation](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows)

Until a proof route is independently qualified, preserve the available
platform observations and receipt as claims; do not promote them to
`workflow_bound`. The contract's limited trust in test contents does not waive
its job/artifact linkage requirement. Failure of that proof leaves the required
confirmed-test-source obligation unmet, even when the JUnit report is valid.

## 4. Raw artifact → receipt → JUnit chain

The collector keeps these byte identities separate:

1. Raw API metadata responses, including their recorded observation times and
   full SHA-256 values.
2. The downloaded archive bytes, outer numeric `artifact_id`, independent
   archive SHA-256 and the platform transport digest. Compare the obtained bytes
   to the platform digest; retain both observations. Never substitute a receipt's
   digest for either value.
3. The extracted receipt's exact JSON bytes and SHA-256, validated by frozen
   contract-v1 receipt semantics. Receipt provenance remains `producer_declared`.
4. The extracted JUnit's exact bytes, byte size and SHA-256, compared with
   `receipt.execution.junit`. This digest refers to the internal file, not the
   enclosing ZIP. The parser observes those exact retained bytes.

The receipt is written before upload and has no enclosing artifact ID/digest.
No self-referential archive hash is added to the frozen schema. Outer evidence
holds `artifact_id`/`artifact_sha256`; `raw_sha256` identifies the particular
selected extracted bytes. Recorded paths are manifest selectors, never commands.

Artifact download is a new bounded capability: the existing SPEC-001 JSON GET
transport refuses redirects. It must not be silently relaxed to follow arbitrary
artifact URLs. Qualify the API-to-storage redirect separately, with an explicit
approved destination policy, HTTPS, no authorization forwarding, no credential
URL retention, bounded total deadline and response bytes, and failure records.
Archive processing checks outer/expanded/member limits before use; rejects
traversal, absolute/drive paths, case collisions, duplicate entries, symlinks,
encrypted/unsupported members and file-type spoofing; selects only expected
receipt/JUnit members; and never extracts executable project content.

Expired, missing, inaccessible, partial or oversized artifacts are unavailable
or erroneous observations. They do not imply test failure or passing tests.
Replay verifies the stored chain without downloads and writes a new result
without overwriting the source bundle. A refresh captures new evidence with new
timestamps and explicit source provenance.

## 5. Qualified JUnit byte parser

Public API:

```python
from delivery_review.adapters.pytest_junit import (
    DIALECT, JUnitError, JUnitLimits, parse_pytest_junit,
)

observation = parse_pytest_junit(
    raw_bytes, dialect="pytest-8.4.2-xunit2", limits=JUnitLimits()
)
```

`JUnitError` subclasses `ContractError` and exposes a fixed `.code`. It is raised
for unsafe/malformed XML, unsupported dialect shape or exceeded resource bounds;
error strings do not echo untrusted input. Raw input remains the caller's saved
evidence. The parser opens no files, resolves no external identifiers, runs no
subprocesses and makes no network requests.

The supported subset is UTF-8 XML 1.0 from pinned pytest 8.4.2's builtin,
single-process xunit2 reporter with the configuration in section 1: one
`testsuites` named `pytest tests`, one `testsuite` named `delivery-review`, and
testcases with `classname`, `name` and decimal `time`. Each testcase has at most
one failure/error/skipped child. The native empty classname on a collection
error is allowed. `pytest.skip` and `pytest.xfail` skipped types remain distinct.
Properties, captured-output elements, namespaces, nested suites, extra
attributes, custom reporters and other versions are unsupported. Format metadata
does not prove which executable actually generated the bytes.

DTD declarations, entity declarations, external entities and processing
instructions are rejected. XML predefined escapes and numeric character
references are supported. Nonnegative count strings are bounded interoperable
integers without float/sign/exponent coercion. Native three-place decimal
durations are retained as strings; NaN/infinity are not accepted. Timestamp and
hostname attributes are retained untrusted metadata, never freshness proof.

| `JUnitLimits` field | Hard/default ceiling |
| --- | ---: |
| `max_bytes` | 2 MiB raw bytes |
| `max_depth` | 8 elements |
| `max_elements` | 30,002 |
| `max_cases` | 10,000 testcase records |
| `max_attributes` | 8 per element |
| `max_attribute_chars` | 8,192 per name/value |
| `max_text_chars` | 256 Ki characters per element |
| `max_total_text_chars` | 1 Mi characters |

Callers can lower limits, never raise these ceilings. The raw byte cap also
bounds comments and token text before XML parsing. No resource refusal is
reported as an empty successful suite.

Output fields are `kind`, `observation_version`, `dialect`, `raw_sha256`,
`size_bytes`, `valid`, `reason_codes`, `suite_attributes`, `native_counts`,
`case_counts`, and `cases`. All original suite and testcase attributes and
failure/error/skip text are retained as parsed XML data. XML-defined newline and
entity normalization affects parsed text; the raw bytes and digest retain the
original representation. Renderers must escape all strings.

`native_counts` retains the suite's `tests`, `failures`, `errors`, `skipped`.
`case_counts` independently counts serialized testcase records with `tests`,
`passed`, `failures`, `errors`, `skipped`. Case identity is exactly the decoded
`(classname, name)` pair. Duplicate identities or disagreement between declared
and record counts return **`valid: false`**, with `duplicate_case_id` and/or
`inconsistent_counts`. Neither deduplication nor count repair is allowed. All
original cases, native counts, failures and errors remain visible.

Well-formed zero tests and all-skipped tests return `valid: true` with
`zero_tests` or `all_tests_skipped`; that means parseable, never sufficient.
Partial skips add `partial_skips`; observed native or record failures/errors add
`test_failures`/`test_errors`, even for invalid reports. The parser never emits
readiness, binding, source authentication, receipt verdict or a product exit.

A later adapter must require `valid`, independently bound producer/source and
artifact evidence, consistent real setup/test exits, nonzero permitted execution,
policy `min_tests`, and explicit `allow_skips` before satisfying a test obligation.
For valid reports, case `failures` maps to receipt count `failed`; do not silently
rename or overwrite the preserved native XML counts. A valid authenticated
failure/error can support failed/block under the contract. A malformed or
contradictory report remains rejected while its observed failure/error is shown.

## 6. Local qualification record, 2026-11-06

The experiment used only newly authored fixture modules under ignored
`work/pytest-8.4.2-qualification/`; it did not execute any reviewed PR or project
tests. Platform: Windows 11, Python 3.12.10. Isolated pins: pytest 8.4.2,
iniconfig 2.1.0, packaging 25.0, pluggy 1.6.0, Pygments 2.19.2, colorama 0.4.6.
Existing project dependency locks were not changed. This is a local pinned
package experiment, not a production dependency-hash or runner qualification.

Each variant used the trusted configuration above and the equivalent fixed
command `python -I -m pytest -c <absolute trusted pytest.ini> --rootdir=.
--junitxml=report.xml --strict-markers test_authored.py`, with plugin autoload
disabled. The local record retains source digests, argv, native exits, stdout,
stderr, original XML/digests and parser observations. Builtin pytest output was
also checked against the pinned reporter source. [pytest 8.4.2 JUnit implementation](https://raw.githubusercontent.com/pytest-dev/pytest/8.4.2/src/_pytest/junitxml.py)

| Authored variant | Native exit | Native tests/failures/errors/skipped | XML case count | Parser result |
| --- | ---: | --- | ---: | --- |
| One pass | 0 | 1/0/0/0 | 1 | Valid; one observed pass |
| One assertion failure | 1 | 1/1/0/0 | 1 | Valid; `test_failures` |
| All skipped | 0 | 1/0/0/1 | 1 | Valid; `all_tests_skipped` |
| Setup fixture error | 1 | 1/0/1/0 | 1 | Valid; `test_errors` |
| Passing call plus teardown error | 1 | 2/0/1/0 | 1 | Invalid; `inconsistent_counts`, error retained |
| Failing call plus teardown error | 1 | 1/1/1/0 | 2, same identity | Invalid; duplicate and counts, failure/error retained |
| Collection error | 2 | 1/0/1/0 | 1 | Valid; `test_errors` |
| Empty module | 5 | 0/0/0/0 | 0 | Valid; `zero_tests` |
| Expected failure | 0 | 1/0/0/1 | 1 | Valid; pytest.xfail retained as skipped |
| Strict unexpected pass | 1 | 1/1/0/0 | 1 | Valid; `test_failures` |
| One pass plus one skip | 0 | 2/0/0/1 | 2 | Valid; `partial_skips` |

The two teardown variants demonstrate a real native reporter limitation for
this strict contract. They are intentionally rejected, not reclassified as
successful qualified input. A future dialect may add explicit phase semantics
only after a separately reviewed specification and new qualification; it must
not alter the frozen duplicate/count expectations to make this experiment pass.

Twenty authored unittest methods cover the parser's byte/resource boundaries,
unsafe XML, unsupported shapes, strict counts, all outcomes, duplicate/count
contradictions, actual teardown shapes, native text preservation and absence of
readiness claims. These synthetic controls and eleven local producer variants
do not constitute hosted E14/E16 or end-to-end E01 product acceptance.

## 7. Remaining release gates and negative controls

Before approval of a concrete producer SHA, preserve independently reproducible
source/version/environment/command/raw-result records for:

- A real controlled same-repository PR and approved producer with both supported
  source modes; mismatched/changed/unsupported source controls; latest run and
  latest attempt; no historical-success fallback.
- The artifact proof route in section 3, including a same-name malicious caller
  job, pre-upload collision, post-upload delete/recreate, overlapping timestamp
  windows, copied receipt identity, forged log lines, missing proof, duplicate
  matching proof and a different workflow/App. An upload collision is not a
  complete failed-test observation.
- Outer metadata/archive mismatch, wrong archive ID, expired archive, altered
  extracted receipt/JUnit bytes, swapped attempt/job, missing referenced-workflow
  SHA, missing JUnit, oversized/traversing/duplicate/symlink archive members and
  all XML/resource controls from E14/E16.
- Hosted pinned pytest pass/failure/setup/collection/zero/skip/duplicate/count
  cases, with native outcomes preserved. Show a complete successful flow only
  when every required obligation independently qualifies.
- Fresh inspection, stale source, force-push/rebase, refresh and offline replay,
  retaining all original byte digests and times without source overwrite.

The missing artifact-to-job proof is a release blocker, not permission to lower
the default `workflow_bound` requirement. Static schema validation, local parser
tests and documentation research do not change the frozen NOT_RUN statuses.
