# GitHub observations: source-checkout preview

This implements the narrow [SPEC-001](specs/SPEC-001-github-observations.md)
platform-observation flow. It reads GitHub.com metadata for a same-repository PR,
retains original response bodies, derives observations, and replays them offline.
It does not establish the test checkout, producer authenticity, executed pytest
counts, scanner results or delivery readiness. The broader product and E01-E18
remain unqualified.

## Run the preview

Use the pinned source checkout and Python environment:

```sh
uv sync --locked
uv run --locked python tools/observations.py inspect https://github.com/OWNER/REPO/pull/NUMBER --profile APPROVED_PROFILE.json --out work/inspection
uv run --locked python tools/observations.py verify work/inspection/capture
uv run --locked python tools/observations.py replay work/inspection/capture --out work/replayed
uv run --locked python tools/observations.py refresh work/inspection/capture --out work/refreshed
```

Every output directory must be new, with an existing plain parent directory.
Use ignored `work/` or a directory outside the product checkout. Replay and
refresh refuse a destination inside the source capture. Neither updates old
observations or source files. A failed/interrupted output path is not reused.

Copy and edit the [authored profile](../examples/github-observations/profile.json)
outside the reviewed PR. Independently select the numeric repository/workflow/App
IDs, initiating workflow path and exact expected job names. The example's IDs and
repository are fictional. The first profile accepts only same-repository head/base
and `pull_request` runs; a branch name or job label cannot expand that support.
No profile is read from a reviewed checkout.

Authentication comes from `GH_TOKEN`, then `GITHUB_TOKEN`; `--anonymous` disables
both for public repositories. The tool does not obtain tokens through another
application. It only makes the documented GET requests and requires no write
permission. A private-repository credential must allow reading that repository's
PR, Actions and Checks resources; unavailable resources remain diagnostic results.
401/403/404 do not establish that evidence never existed. No credential is stored
in the capture or passed on a worker command line.

Live inspect/refresh require a clean Git source checkout so `collector.source_sha`
identifies the actual implementation. This is development tooling, not an
installed release. Offline verify/replay do not run Git or the network worker.

## Files and commands

An inspection/refresh creates:

```text
inspection/
  capture/
    profile.json
    raw/r000001.body
    ...
    capture.json
  observations.json
  observations.md
```

The profile and bodies retain their original bytes. `capture.json` is written
last and records requests, permitted response headers, identities, hashes,
timestamps and termination. A stopped capture can preserve useful observations;
a directory without a valid capture manifest is not replayable. Exact inventory,
digest, path, link/reparse and hardlink checks apply before mapping.

JSON and Markdown are derived outputs outside the capture. Replay requires no
saved normalized report. It regenerates results through the same mapper as live
inspection; the original observation time and historical `captured_freshness`
remain, while applicability now (`freshness`) is always `unknown` offline.
Refresh obtains new raw bytes under the saved profile into a distinct directory.

`verify` exits 0 when local integrity verifies. Inspect/replay/refresh exit 0 for
a complete observation computation, and 2 for partial/error. A complete result
can contain native failures, cancellation, skips or identity mismatches. Exit 0
is operational success; no observation command produces `ready_for_review` or a
contract-v1 policy decision. The older `tools/evidence.py` normalized-report
replay retains its separate product exit semantics.

Useful diagnostics include `output_already_exists`, `input_or_parent_not_found`,
`collector_source_must_be_clean`, `invalid_or_unreadable_capture`, `rate_limited`,
`resource_unavailable`, `pagination_incomplete` and `request_timeout`. Detailed
collection reasons and raw references belong to the derived JSON. CLI failures
do not echo arbitrary input content or transport exceptions.

## What is checked

- Run lists are complete within their bounds, and selection uses `run_number`,
  never success or update time. Invalid/ambiguous newer candidates do not allow
  fallback to an older green run.
- Current run detail selects the latest observed attempt. Its specific attempt
  and jobs are kept separate from other attempts.
- Workflow metadata, PR associations and independent check ID/App/head/suite
  relationships are checked. Every native job stays visible. Missing associations
  remain unconfirmed; well-typed conflicting identities remain mismatches.
- Native outcome interpretation is separate from identity association.
  `native_state: unconfirmed` marks unknown/contradictory native state pairs;
  a matched identity does not make such an outcome valid.
- Final PR/list/current-run requests must follow initial acquisition. Head, base
  and selected-run/attempt changes produce stale observations; known drift is
  retained when another recheck is unavailable.
- Known failures survive unrelated missing/error observations. Markdown escapes
  untrusted text; JSON retains native values and per-record references.

## Boundaries and current evidence

The HTTP boundary permits fixed HTTPS GET routes to `api.github.com`, disables
proxy discovery, and refuses redirects and credential-bearing links. A first-party
worker is killed and reaped at the whole-request deadline, including DNS/connect
and slow reads. This worker never executes reviewed PR code or dependencies.
Responses and error bodies are bounded. Headers use an allowlist; known token
echoes are refused instead of altering bytes and claiming them as originals.

Profile limits may lower 10 pages/listing, 2 MiB/body, 100 MiB total bodies,
200 requests or 30 seconds/request. Profile and manifest have separate 2 MiB
ceilings. Each retained header value is limited to 8,192 characters and all
retained headers to 32,768; derived JSON/Markdown each have a 16 MiB output ceiling.
Bounded refusal does not establish complete evidence. There is no automatic
retry or rate-limit sleep; rerun creates a new capture.

Hashes verify local byte consistency. A local owner can rewrite bytes and their
manifest; they are not signatures. Use exclusive ownership during filesystem IO.
Private captures remain local; do not publish raw data without reviewing its
provenance and disclosure scope.

Tests use explicitly authored responses, an injected GET boundary, hostile-input
controls and real worker timeout termination. These establish implementation
behavior, not a controlled producer run, Linux qualification, independent external
adoption, or the full E01-E18 release gate. Real API smoke receipts, when recorded,
must identify their exact product source and remain separate from these controls.

Artifact transport, approved producer/source proof, pytest/JUnit, scanner adapters,
clean install qualification and external reproduction remain subsequent gates.
