# SPEC-009 — full inspection CLI and original-byte replay

Status: development implementation, `1.0.0.dev0`. The envelope's format version
is 1; that version is not a stable product release or qualification claim.
Hosted producer qualification, E01–E18 product acceptance and independent external
reproduction remain separate release gates. Authored orchestration/CLI tests do
not close those gates.

## User operations

The installed `delivery-review` command exposes the complete inspection family:

```text
delivery-review inspect https://github.com/OWNER/REPO/pull/NUMBER \
  --policy /trusted/inspection-profile.json --tools /trusted/tools.json \
  --out /evidence/new-inspection
delivery-review verify /evidence/new-inspection
delivery-review replay /evidence/new-inspection \
  --tools /trusted/offline-tools.json --out /evidence/new-replay
delivery-review refresh /evidence/new-inspection \
  --tools /trusted/tools.json --out /evidence/new-refresh
```

Each output directory must be new. Replay and refresh outputs must be outside
the saved inspection. Refresh uses the original complete inspection profile and
PR URL, obtains a fresh collector identity, and acquires new observations; it
does not rewrite the old result or change its approved policy. Inspect/refresh
may use `GH_TOKEN`, then `GITHUB_TOKEN`; `--anonymous` explicitly ignores both.
Replay does not use either token, discover a GitHub connection or fetch source.

`verify` checks local bytes and the exact file inventory. It requires no toolchain
or current source-checkout identity. Its output explicitly states
`authentication: not_established`: local digest equality does not authenticate a
producer or publisher and is not a readiness decision.

Inspect/replay/refresh return the derived report's exit: 0 means ready for human
review, 1 means complete/current evidence with a review or block policy result,
and 2 covers incomplete/error evidence or non-current freshness. A known native
block remains visible alongside exit 2. Malformed CLI input and controller
failure also return 2 with a sanitized diagnostic, never raw exception/source
text that could contain credentials or hostile content.

The older `observations`, `bundle` and `policy` command families remain distinct.
Observation captures describe platform metadata; saved-normalized bundles replay
their prior format. Neither is silently upgraded to a full original-evidence
inspection or a newly authenticated producer claim.

## Operator-owned toolchain

This first implementation requires explicit local tools and already obtained
engine wheels. It does not install arbitrary dependencies or download executables
selected by the reviewed PR. Paths in the toolchain file are absolute or relative
to the toolchain file's directory, not the current working directory. Exactly
these keys are accepted:

```json
{
  "git_executable": "/usr/bin/git",
  "python_executable": "/opt/delivery-review/python3.11",
  "engine_wheels": {
    "checkwash": "wheels/checkwash-0.10.0-py3-none-any.whl",
    "pinwash": "wheels/pinwash-0.1.1-py3-none-any.whl",
    "ledgerwash": "wheels/ledgerwash-0.5.0-py3-none-any.whl"
  },
  "gh_executable": "tools/gh",
  "trusted_root": "tools/trusted_root.jsonl"
}
```

This is a path example, not a download source or a qualification receipt. Engine
wheel identities come from the frozen engine pins, including SHA-256; official
`gh` and saved trust-root identities come from the pinned attestation adapter.
Every engine is installed into its own bounded static-reader environment and
uses the public CLI. The selected Python/Git runtime is an operator prerequisite;
the supported installed runtime still requires release qualification. No PR
dependency manifest, checkout hook, test command or project script runs here.

For offline replay, `engine_wheels` may be `{}` because native scanner streams
are reparsed rather than rerun. Git rebuilds the inert static snapshot from saved
Git objects. The pinned official `gh` executable performs fresh cryptographic
verification using the original artifact, signed bundle and saved trust root.
No stored successful process exit can replace that execution.

## Collection sequence

The orchestrator reserves a new inspection tree and saves the approved profile
and trust-root bytes. It then collects the initial GitHub PR, selected workflows,
latest runs/attempts, complete job pages and independent checks. A collector stage
hook receives an immutable copy of those initial bytes before the final recheck.
The hook performs the following work:

1. Acquire and independently materialize exact static head/base Git objects.
   Keep original acquisition observations and exact compressed object bytes.
2. Obtain the original exact PR merge-reference HTTP response and then its
   event-source Git commit by immutable SHA; establish its canonical hash and
   ordered parents separately from the tested head. The API 2026-03-10 response
   no longer supplies `PR.merge_commit_sha`; missing reference proof is retained
   as a gap, with no guessed head source or synthetic PR field.
3. Run each declared pinned static engine with full source SHAs and the bound
   native policy. The policy evaluation date comes from the original observation
   time, not the replay machine's clock.
4. Acquire independent producer-repository metadata and complete artifact
   listings for the selected execution. Enforce the exact artifact locator,
   unique candidate, actual ID and archive digest. Retain bounded original ZIP
   and signed bundle bytes, with explicit credential-bearing locator omissions.
5. Derive attestation expectations from those original facts, run the official
   verifier, and bind the result to the approved producer qualification.

Only after these stages finish does the collector obtain final PR and selected
run/attempt observations. A head/base/attempt race cannot be hidden by a recheck
that occurred before source/artifact/engine work. Every requirement is then
composed under the unchanged three-axis contract and rendered as JSON, Markdown
and self-contained HTML.

The collection reserves space for final platform observations before other
stages consume the outer budget. The complete envelope has a cumulative 512 MiB
byte limit and 25,000-file limit; artifact collection receives the remaining
allowance. Request counts, pagination and deadlines are bounded within each
platform/artifact/source stage, not by one shared HTTP-request counter or one
whole-inspection deadline. Per-request/per-format limits remain in force.
One failed engine or unavailable attestation cannot erase another independently
observed block. If the fundamental subject/source cannot be established, an
explicit inspection-error record and available raw capture remain; no fictional
SHA or empty successful report is created.

## Full envelope format

`inspection.json` is the last file written when sealing succeeds. It contains
`kind: delivery-review-full-inspection`, `inspection_version: 1`,
`replay_basis: raw_evidence_v1`, original collector/time/report identities, optional
parent-manifest digest and a sorted exact inventory of every stored file.
Mandatory entries include the complete inspection profile, GitHub capture and
observation profile, source inventory, execution plan, and the three rendered
report files. Original inputs and controller observations live under `raw/`.

The frozen report's smaller `raw_inventory` lists directly referenced evidence
and acquisition manifests. It does not repeat every Git object and setup-log
entry. Those bytes remain covered by the complete envelope inventory and the
source/acquisition inventories linked from the report. A repository with more
than a thousand source objects therefore does not require widening the frozen
report schema or dropping original objects.

The envelope bounds files, bytes and manifest size; it rejects path traversal,
Windows aliases, case collisions, duplicate names, links/reparse points, hard
links and unexpected files/directories. It hashes bytes after acquiring them and
checks unchanged regular-file identity during reads. A changed digest is an
error, never repaired by blessing a new hash. The directory is exclusively owned
by this operation; a digest inventory is not protection against someone able to
replace both local data and its manifest.

The source inventory contains the original compressed and uncompressed object
identities. Replay decompresses under bounds, rederives Git hashes and full DAG/
tree closure, rejects unsupported objects/paths, and recomputes the unique merge
base. It preserves original compressed bytes so zlib-version differences cannot
silently change source identity. Source inventory equality is checked after
reconstruction. There is no `git checkout`, filter, project hook or remote fetch.

The execution plan selects original engine inputs; it is not a saved decision.
Replay reparses native output, validates pinned invocation/source/policy and
retains original verdicts, findings and coverage. It does not read `report.json`
as evidence and does not trust a saved adapter-success field.

[ADR-004](../amendments/ADR-004-github-merge-reference.md) versions event-source
interpretation inside this unchanged outer envelope. Version 2 plans contain exactly
`engine_names`, `stage_errors`, `pr_url`, `plan_version: 2` and
`event_source_basis: github_merge_reference_v1`. Original reference request facts
and body bytes are saved under `raw/event-source/reference/request.json` and
`body.bin`, independently of the original PR body and canonical
`raw/event-source/commit.bin`. Both reference and commit proof are mandatory for
that basis; an unavailable one cannot trigger legacy or PR-head fallback.

The historical exact three-key plan has no `plan_version` and dispatches to
`legacy_pr_field_v1`. It retains its old interpretation and ignores stray
reference bytes. New readers reject unknown versions, bases and extra plan keys;
old readers reject the new five-key shape. Replay never fetches a current ref or
changes the saved basis. Refresh makes a new observation and explicit plan, while
retaining the originally approved inspection profile and leaving the old envelope
untouched. No original PR JSON is augmented to resemble an older API response.

[ADR-005](../amendments/ADR-005-artifact-ambiguity.md) adds plan version 3 with
the same event-source basis and one additional exact field,
`evidence_selection_basis: scoped_original_refusals_v1`. New live observations
use this six-key plan. It lets profile v2 retain an independently established
artifact-name ambiguity and a scoped two-parent source contradiction in the
outer report. A complete fixed PR merge ref and matching canonical Git object
with two wrong ordered parents yield mismatch/block; fewer parents leave
untrusted missing proof and more than two leave unsupported source. Raw bytes
remain retained, and unknown receipt source modes remain schema-refused.
Historical three-key and version 2 plans retain their prior artifact/source
interpretation, including saved v2 profiles. The binder's explicit evidence
selection basis defaults to `legacy_missing_v1`; unknown fields, versions or
selection bases are rejected.

The same profile-v2/plan3 basis classifies a fully validated exact receipt-only
ZIP as `missing` / `junit_missing`. This bounded absence check reuses strict
archive validation and validates the receipt schema, but never accepts a partial
artifact or constructs signature expectations. Other inventory mismatches and
unsafe/corrupt archives remain errors. No acquired archive still means
`artifact_missing`; legacy plans keep their prior inventory-mismatch error.

## Replay audit and qualification

Replays preserve original observation time, raw evidence and the same policy.
Freshness is always unknown. Repeating a successful replay of the same saved
inputs produces the same canonical report; a live report's original current
freshness is not rewritten.

Fresh official signature verification naturally records new runtime paths and
process timing. These new audits are stored separately under `raw/audit/` and
covered by the full envelope inventory, outside the canonical report's historical
evidence inventory. The binder retains the original audit only after checking
its raw inventory and matching artifact/bundle/trust-root/expectation bytes
against the fresh execution. Both the original successful claim chain and the
fresh verifier result are required. A later successful verification cannot
upgrade an originally failed or unavailable verification; refresh creates a new
observation when new proof is available. The entire
new envelope, including its fresh audit, is not promised to be byte-identical.

Producer approval comes from release-bundled qualification resources. Registry
entries bind exact producer repository/path/full SHA and qualification-receipt
digest; duplicate identities, altered receipts and invalid/empty source modes
are refused. Neither an environment variable, policy pin, signed claim alone,
nor a receipt inside the reviewed artifact adds a qualified producer.

Replay requires the exact originally retained qualification receipt still to be
approved by the current verifier. Newly available approval cannot silently upgrade
an old unqualified inspection; removed approval remains an explicit trust gap.
Use refresh for a new observation/approval claim. The development registry now
admits the separately reviewed head/merge workflow paths at fixed `3d3e545`;
their digest-bound original public evidence and limits are published in
[qualification/README.md](../../qualification/README.md). This admission does
not establish complete-product or stable-release qualification.

## Evidence classification

Authored CLI controls exercise immutable refresh inputs, offline delegation,
ambient-token isolation, strict toolchain parsing, local tamper refusal, truthful
verification output and sanitized failures. Authored orchestration controls use
real local Git objects and storage/replay, with explicitly mocked HTTP, scanner
and signature outputs. These establish implementation behavior, not real
transport, hosted workflow, cryptographic execution or E01 release acceptance.
Those claims require separately recorded actual executions and immutable sources.
