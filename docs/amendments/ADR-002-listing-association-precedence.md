# ADR-002: Versioned precedence for absent run-list PR associations

Date: 2026-11-06. Status: accepted for implementation; qualification remains
separate. Applies to observation mapper 2. Mapper 1 remains a supported historical
interpretation of [SPEC-001](../specs/SPEC-001-github-observations.md).

## Evidence and problem

The [source-pinned public smoke](../specs/SPEC-001-live-smoke.md) captured 42 HTTP
200 responses at product source `8747e161b441910aac839eafe484d0d12ae5db0f`.
Both filtered workflow-run listings had `pull_requests: []`, despite an explicit
`exclude_pull_requests=false` query. Independently obtained current-run and
attempt-detail responses supplied the matching PR association. Mapper 1 correctly
reported its then-defined aggregate as partial. This observation does not explain
why GitHub's endpoints differed, and its recorded result must not be rewritten.

Mapper 2 defines a narrow evidence-precedence rule so a missing optional listing
association need not outweigh matching independent detail observations. This is
not permission to ignore contradictory data or to turn metadata into checkout,
producer, test, scanner or delivery-readiness proof.

## Version and API decision

- Capture format 1 remains the same byte-storage envelope. Its explicit integer
  `mapper_version` selects either 1 or 2; all other values, including booleans,
  are rejected. `map_capture(capture, mode=...)` dispatches on that stored value.
  There is no replay-time mapper override or silent historical upgrade.
- The observation profile remains version 1: its operator-approved repository,
  workflow, event, App, job, source and resource selectors are unchanged. The
  capture's recorded collector source and mapper version identify the chosen
  observation algorithm. New writers default to mapper 2 and can explicitly
  request mapper 1. Changing a mapper declaration creates different manifest
  bytes; an existing capture is never relabeled or overwritten.
- The observations envelope remains version 1, with `mapper_version: 2` explicitly
  identifying the new interpretation and additive resolution records. Consumers
  must interpret kind, format and mapper version together. Mapper-1 output gains
  no new fields and retains its exact canonical replay bytes.
- Synthetic fixture helpers continue to default to mapper 1. Mapper-2 tests must
  opt in explicitly. The M0 contract, frozen expected outputs, readiness axes and
  existing saved-normalized report replay are unchanged.

## The only resolvable listing gap

First select the latest candidate using the existing complete-listing and numeric
run-sequence rules. Never choose a different run because of native outcome or
association quality. The selected listing row is eligible for this rule only if:

1. Its `pull_requests` key is absent, or its value is exactly an empty JSON array.
2. All other applicable listing identity and association fields match the captured
   subject and approved workflow. There is no missing path/head-repository proof,
   explicit conflicting identity, incomplete page, duplicate candidate or other
   selection ambiguity.
3. Independent current-run and exact selected-attempt responses match the run ID,
   run number, repository/head-repository IDs, workflow ID/path/event, captured
   head SHA, PR number and PR repository/head identities. Their positive check
   suite IDs agree. The current-run attempt selects the attempt detail; no old
   attempt or another run may supply the missing relationship.
4. The workflow metadata is matched, the current-run observation follows the run
   listing, and the attempt observation follows the current-run observation.

Null, a non-array, a nonempty malformed association, duplicate/ambiguous PR
associations and well-typed conflicts are not omissions under this rule. A newer
run with one of these inputs remains selected and retains its original gap or
mismatch. Matching details cannot erase that contradictory or malformed listing.
Malformed, mismatched, unavailable or out-of-order details cannot resolve a gap.

Native status and conclusion remain an independent observation. Resolving the PR
association does not resolve unknown or contradictory native state. Likewise,
job/check mismatches, unavailable children, pagination failures and every other
diagnostic retain their original effect on collection status.

## Final recheck

The final listing may use the final current-run response together with the
already independently collected attempt detail only when that detail names the
exact same final `(run_id, attempt)` and all identities above still match. Both
references are recorded. This avoids inventing a final-attempt request role or
mixing execution attempts.

A new final run or attempt without its matching independent attempt detail cannot
resolve an empty final listing association. Its known drift remains stale and its
unresolved observation remains partial. Final observations preceding initial
collection cannot resolve a recheck gap. No old result is substituted.

## Resolution record and retained diagnostics

Mapper-2 selections include `listing_association`:

```text
status: original listing association (matched | unconfirmed | mismatch)
reasons: original listing association diagnostic codes
raw_refs: original selected-row listing record references
resolution: resolved | unresolved | not_applicable
resolved_by: independent current-run and exact-attempt record references
```

The initial selection and the final `recheck.selection` each retain this record.
The original `pr_association_unconfirmed` code remains in historical selection and
aggregate reason lists. When, and only when, this rule resolves that particular
listing gap, it no longer contributes partial severity and the mapper also records
`listing_pr_association_resolved`. The effective aggregate relationship may then
be matched. Consumers must read the structured resolution and collection status;
the presence of a historical reason string alone is not an active failure flag.

All native bytes, original listing facts, selected IDs, timestamps, current-run
and attempt results remain unchanged. `resolved_by` identifies evidence, not a
signature or permission to claim tested checkout. Replay still reports current
applicability as unknown and retains historical captured freshness.

## Required controls

Tests must demonstrate absent and empty-array resolution, malformed/null/non-array
and explicit-conflict refusal, current/attempt identity and chronology controls,
current-attempt advancement without cross-attempt reuse, and final same-tuple
resolution versus changed-tuple refusal. Unknown native state and unrelated
job/check errors must survive resolution. Failed native jobs remain visible.

Mapper-1 canonical-output hashes are recorded from the pre-amendment implementation
for complete, empty-listing partial and stale captures. They must remain exactly
equal after adding dispatch. The original public smoke remains mapper 1 and
partial; any mapper-2 live evidence is a new source-pinned acquisition, not an
edited historical receipt. These controls do not close E01-E18 or qualify 1.0.
