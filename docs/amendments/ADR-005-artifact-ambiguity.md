# ADR-005: preserve scoped original-evidence refusals

Status: accepted development protocol correction. Frozen contract-v1 schemas,
forty authored cases and twenty baseline files remain unchanged.

## Observation and decision

The actual [reference PR 20](https://github.com/taipei49314/delivery-review-reference/pull/20)
run `42186022093` contained two distinct artifact IDs with the same producer
artifact name. One was created by another caller job before the genuine producer
upload. The platform accepted both. Name uniqueness and upload collision are
therefore not provenance guarantees.

The original collector already refused to select either artifact, recording
`ambiguous` / `artifact_ambiguous` from the complete listing. The installed
`b9c1d2e` outer report collapsed the missing selected ZIP into `artifact_missing`.
It remained incomplete and did not grant readiness, but hid the actionable
identity ambiguity. The original run and report remain retained.

New profile-v2 inspections can pass the original capture to the test binder.
The binder reparses its inventory, independently remaps the original API bytes,
and checks repository, selected run, attempt and check identity. Only a complete
matching ambiguity receives `untrusted` / `artifact_ambiguous`; no selection is
made by time, order or contents. An incomplete listing cannot support this
complete ambiguity proof. All original refusal bytes remain report-linked.

The same explicit decision version corrects E03 parent contradictions. Authored
canonical Git/ref controls reproduced the old `error`/`clear` disposition for a
two-parent commit with reversed or wrong parents. Once the complete fixed PR
merge-ref response and canonical object SHA agree, exactly two parents that do
not equal the ordered observed base/head establish `mismatch`/`block`, retaining
`event_merge_parent_mismatch` and the frozen `merge_parents_mismatch` alias.
Zero or one parent leaves `untrusted`/`clear` missing parent proof; more than two
leaves `unsupported`/`clear` with `tested_source_mode_unsupported`. An unrelated
ref, incomplete response, wrong object hash or malformed object cannot establish
the known two-parent contradiction. These clear dispositions never erase an
independently known CI or scanner block.

This correction does not accept custom or queue receipt source modes. They still
fail the unchanged frozen receipt schema; their bytes do not become accepted
source claims. Missing merge-reference/object proof remains an explicit untrusted
gap, with no head fallback. The parent controls are authored tests, separate from
actual hosted qualification evidence.

For E14, the new basis also distinguishes a fully validated receipt-only ZIP
from an invalid archive. After strict two-member validation reports an inventory
mismatch, a separate exact singleton validation checks the same path/type/header,
layout, expansion, resource and CRC rules, then validates the receipt schema.
Only that complete receipt-only archive yields `missing` / `junit_missing`.
It cannot supply attestation expectations or accepted test claims. Extra,
duplicate, linked, unsafe, oversized or corrupt archives retain errors; absence
of an acquired archive remains `artifact_missing`. Known scoped digest or CI
contradictions are not erased. The default ZIP dialect remains strict.

The same basis preserves completed native engine facts while making their
dispositions explicit. Valid scanner or ledger reports with incomplete coverage
become `unsupported`, retaining independent block/review effects and the native
verdict. Closed reason aliases identify scanner deadlines, unsupported scanner
schemas, coverage gaps, existing allowlisted findings and new-exemption review.
`test_verification_weakened` requires Checkwash's actual blocking high/critical
`ASSERT_REMOVED` or `ASSERT_WEAKENED` finding; unrelated blockers and native pass
results never acquire that label. These changes do not alter native detectors,
raw output, accepted schemas or the documented #419 limitation. Legacy plans
and profile v1 keep their original interpretation.

An actual raw-composer control at `d694175` also exposed a resolved-listing
edge case. Mapper 2 retained the original `pr_association_unconfirmed` diagnostic
after independent run/attempt responses had resolved that omission. The specific
wrong-App classification did not permit this residual reason and returned an
untrusted gap. Under the new basis, that residual diagnostic is permitted only
with explicit mapper-2 resolution, matching selected run/attempt details and the
two original complete successful API records named by that resolution. A known
numeric App contradiction then retains `mismatch`/`block`; unresolved, conflicting
or unrelated associations remain gaps. The failed original control is retained.

Closed execution-history aliases also reparse complete original workflow-run
inventories and scoped run/attempt records. `latest_run_pending` describes the
independently selected pending execution; `latest_run_failed` is attached only
to an already failed/block obligation with matching native failure facts.
Neither implies an older success was observed. `evidence_for_old_head` requires
retained, complete, same-PR successful history at another head and no selectable
current-head run. An empty head-filtered listing cannot prove such history.
These aliases never change selection, outcomes, effects, counts or binding and
are confined to profile v2 with the new explicit execution basis.

## Historical dispatch

This correction is selected by an explicit six-key execution plan:

```json
{
  "engine_names": ["checkwash", "ledgerwash", "pinwash"],
  "stage_errors": [],
  "pr_url": "https://github.com/owner/repository/pull/1",
  "plan_version": 3,
  "event_source_basis": "github_merge_reference_v1",
  "evidence_selection_basis": "scoped_original_refusals_v1"
}
```

The URL is illustrative. The original three-key plan and exact version 2
five-key plan retain their previous artifact and source interpretation, even when their
saved inspection profile is v2. Profile v1 also retains its prior interpretation
under a new plan. Unknown versions, keys and selection bases are refused. The
outer evidence envelope version remains 1; the approved policy identity and
source-proof protocol do not change. The binder's explicit evidence selection
basis defaults to historical behavior; profile v2 alone does not enable these
new refusal classifications.

Replay uses the original plan and cannot upgrade its interpretation. Refresh
creates a new observation and explicit plan without modifying prior evidence.
Old readers reject the new plan shape instead of interpreting it as v2. Actual
hosted refusal, parser/binder controls and saved-byte compatibility are separate
receipts; implementing this dispatch does not itself qualify a producer or 1.0.
