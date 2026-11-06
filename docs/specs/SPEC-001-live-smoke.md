# SPEC-001: source-pinned public API smoke

Date: 2026-11-06. Product source:
`8747e161b441910aac839eafe484d0d12ae5db0f`.
Environment: Windows 11 / Python 3.12.10, locked dependencies.
This is a real read-only product acquisition and replay of public metadata. It
is not a controlled producer execution, Linux qualification, external adoption,
or closure of E01-E18.

## Subject and invocation

The subject was [pytest PR 15339](https://github.com/pytest-dev/pytest/pull/15339),
repository ID 37489525, with head/base in the same repository. The approved local
profile selected workflow 22158026, `.github/workflows/test.yml`, event
`pull_request`, App 15368, and exact expected names `build (ubuntu-py311)` and
`build (ubuntu-py310-xdist)`. It used 100/page, 10 pages, 2 MiB/body, 100 MiB total,
60 requests and 15 seconds/request. No workflow or PR was modified or triggered.

```sh
uv run --locked python tools/observations.py inspect https://github.com/pytest-dev/pytest/pull/15339 --profile work/pytest-observation-profile.json --out work/pytest-live-observations --anonymous
uv run --locked python tools/observations.py verify work/pytest-live-observations/capture
uv run --locked python tools/observations.py replay work/pytest-live-observations/capture --out work/pytest-replay-first
uv run --locked python tools/observations.py replay work/pytest-live-observations/capture --out work/pytest-replay-second
```

Collection began at `2026-11-06T04:23:54Z`. All **42 GET responses were HTTP 200**.
The product retained **317,799 original body bytes**, without reconstructing them
from connector JSON. The manifest records source, request order, allowed headers
and individual byte digests.

- Profile SHA-256: `30572e5a784d46bc58372e694871ba40c4b6d8b36935785bf369c825381a2eeb`.
- Capture manifest SHA-256: `2b75424f216f473e054eba91ca9b398473c88fd9cea8c261abd1f56d443cea57`.
- Selected [run 41996638098](https://github.com/pytest-dev/pytest/actions/runs/41996638098), sequence 11892, attempt 1.
- Observed head: `620cc433033b6d2170ea983e5f90b56d1d577f04`.
- Observed base tip: `35dd0f34b75848c4d2ed759815919becb40cfab4`.

## Observed result, including the gap

The output retained all **32 jobs: 30 success and 2 failure**, plus their
independently fetched checks. Both failures remained visible. Initial and final
source/execution identities matched, so the captured freshness was `current`.
Tested checkout remained `not_established`.

The collection result was **partial**, with `pr_association_unconfirmed`.
Both workflow-specific filtered run lists returned empty `pull_requests`, despite
explicit `exclude_pull_requests=false`. Current-run and attempt-detail responses
did contain the correct PR association. This records an endpoint discrepancy;
it does not establish why GitHub omitted the field from the lists. The implemented
mapper conservatively retains the listing gap in the aggregate association.
This smoke therefore does not qualify a complete observation happy path.

A later refinement must explicitly define how independent detail observations
can resolve an absent listing field while retaining contradictory identities,
historical diagnostics and replay compatibility. The result was not changed to
complete merely to pass this smoke, and the frozen readiness contract was not
weakened.

## Replay and validation evidence

Capture integrity verification exited 0. Both separately invoked raw replays
exited 2 because the observation remained partial. Their JSON output bytes were
identical. Compared with live output, only mode and applicability-now freshness
changed; offline freshness was `unknown`, historical captured freshness remained
`current`, the timestamp stayed unchanged, and both native failures remained.
Hashes of every source-capture file were unchanged after replay.

The initial inspect was invoked through PowerShell; the outer tool reported 1
for its nonzero exit. Its inner process exit was not separately captured. The
verification and two replay exits above were separately measured using Python
subprocess return codes. No exit is presented as a delivery readiness decision.

The source passed **186 local tests**, the **40 synthetic M0 cases**, and the
**20 frozen-file hashes**. Unit controls include no network/subprocess during
replay, whereas this public smoke establishes actual HTTP capture compatibility.
These are distinct evidence categories.

Original public-response bodies remain in ignored local `work/`; they are not
committed or presented as redistributable fixture data. The local handoff retains
machine-readable command logs, hashes and a smoke receipt. A controlled,
appropriately licensed reference repository and independent reproduction remain
subsequent gates.
