# SPEC-010: complete inspection profile v2

Status: development implementation; actual qualification remains separate.
The frozen report/policy/receipt schemas and E01–E18 expectations do not change.
Profile v1 retains its exact field set and historical report interpretation.
There is no automatic upgrade of saved profiles.

## Version and identity

`profile_version: 2` retains every v1 base field. It permits three optional fields,
`job_matrix`, `ledger_input` and `policy_input`; a profile with none is valid. Unknown fields
and versions are rejected. All declarations, the profile version and every base
field participate in the existing complete-profile identity calculation. Changing
one requires a new approved identity. Sealing constructs that identity; it does
not approve the profile or accept a policy delivered by the reviewed PR.

V2 also selects these corrections required by the frozen acceptance cases:

- Native `timed_out` is missing/block with `required_job_timeout`; skipped,
  neutral and cancelled remain clear.
- Independently observed numeric App contradiction is mismatch/block with
  `check_app_mismatch`; ambiguous names remain untrusted/clear.
- Raw artifact digest/size contradiction is error/block; cross-attempt receipt
  contradiction is mismatch/block. Native reason codes remain alongside frozen
  aliases. Missing/unconfirmed referenced producer remains untrusted/clear.
- Unsafe ZIP paths and XML entities are error/block, retaining native rejection
  codes and the frozen traversal/entity aliases.
- Independently authenticated and qualified zero-test/all-skipped reports remain
  missing/block. Exit 2 alone does not establish a collection error: that alias
  additionally requires validated pytest collection-failure error records, with
  no passed or failed testcases. Original execution/counts remain retained.

These keep their separate evidence-completeness outcomes. Unknown parser errors,
ordinary absence and ambiguity are not globally blocked. Historical v1 captures
keep their prior interpretation for reproducible replay.

For a known artifact digest/size contradiction, v2 can pass the original complete
HTTP-200 ZIP to the binder even though acquisition correctly refused to select
it. The binder first independently validates metadata route, artifact/run/source
and job/check scope, then compares the original bytes before ZIP parsing. A
damaged ZIP cannot hide that established contradiction behind a later parser
error. Partial bodies, transport errors and ambiguous selection do not establish
this proof. Historical artifact mapping and v1 interpretation stay unchanged.

The hosted precreation control established that different jobs can upload the
same artifact name in one run. A complete original listing with multiple exact
name matches remains untrusted with `artifact_ambiguous`; no candidate is chosen
by upload time, ordering or contents. The v2 binder independently remaps the raw
capture and checks its repository/run/attempt/check scope before reporting that
specific refusal. Incomplete listings cannot establish this complete ambiguity
proof. Original listing bytes remain linked to the report. Historical v1 keeps
its generic missing-artifact interpretation. This correction additionally requires
the explicit execution-plan version 3 selection basis in [ADR-005](../amendments/ADR-005-artifact-ambiguity.md);
saved version 2 plans retain their original interpretation even with profile v2.

The same explicit plan3 `evidence_selection_basis: scoped_original_refusals_v1`
enables the E03 source-refusal correction. Only a complete fixed PR merge-ref
response whose full SHA matches a canonical Git object can establish the
relationship. Two distinct but incorrect ordered parents yield mismatch/block
with `merge_parents_mismatch`, retaining the native rejection code. Zero or one
parent leaves an untrusted parent-proof gap; more than two parents yields
unsupported/clear with `tested_source_mode_unsupported`. Incomplete, unrelated
or malformed inputs cannot establish this contradiction. Missing reference or
object proof never falls back to head. Unknown custom/queue receipt source modes
continue to fail the unchanged receipt schema; no unsupported claim is accepted.
Profile v1 and legacy/version2 plans keep their previous dispositions, and all
independently known CI/scanner blocks remain present.

With this same explicit plan3 basis, a complete safe ZIP containing exactly one
schema-valid `receipt.json` and no JUnit is missing with `junit_missing`. A second
strict singleton validation verifies headers, paths/types, physical layout,
expansion/resource bounds and CRC before establishing this narrow absence.
There is no positive partial-artifact path. Extra members, symlinks, duplicates,
unsafe paths, invalid receipts and corrupt/oversized archives remain errors, and
a missing acquisition remains `artifact_missing`. The earlier scoped digest
contradiction check and independently known native blocks take precedence.

The same basis gives completed native scanner/ledger coverage gaps an
`unsupported` outcome, preserving native verdicts and independent effects. Closed
aliases identify deadlines, unknown schemas, coverage gaps and exemption review.
Known Checkwash weakening additionally requires an actual blocking high/critical
`ASSERT_REMOVED` or `ASSERT_WEAKENED` finding. Native pass results, including the
documented #419 escape, remain pass. Details and historical dispatch are in
[ADR-005](../amendments/ADR-005-artifact-ambiguity.md).

## Exact matrix selection

For example, an approved declaration may contain:

```json
{
  "job_matrix": {
    "mapping_version": 1,
    "cells": [
      {
        "workflow_id": 101,
        "job_name": "pytest-linux",
        "matrix": {"python": "3.11"},
        "platform_job_name": "tests (3.11) / measure"
      },
      {
        "workflow_id": 101,
        "job_name": "pytest-linux",
        "matrix": {"python": "3.12"},
        "platform_job_name": "tests (3.12) / measure"
      }
    ]
  }
}
```

These IDs/names are examples, not observed platform identities. The corresponding
test requirements use the exact logical job/matrix tuple. The observation profile
contains the exact platform names. The mapping must cover all and only declared
matrix cells, with unique logical tuples and no reuse of a platform name within a
workflow. A CI-only matrix cell is insufficient: a paired test requirement must
provide independently bound runtime proof. At most 100 cells are accepted.
The initial supported axis is `python`, with a bounded major.minor string.

Selection uses the approved physical name and all existing workflow/run/attempt/
job/check/App constraints. Complete inventory with no such job yields
`required_matrix_cell_missing`; incomplete pagination cannot establish absence
or success. A physical job's display text is never parsed as runtime evidence.
Raw API records keep the original name even after an authenticated report uses
the logical name. Absent/untrusted proof leaves the report matrix empty.

## Runtime proof

Only after the original artifact/receipt/JUnit, actual official cryptography,
exact producer revision and release-owned qualification agree can a test record
receive matrix values. For `pytest-isolated-container-v1`, the qualification
receipt must additionally contain exactly this reviewed runtime assertion:

```json
{
  "runtime": {
    "python": "3.11.16",
    "implementation": "cpython",
    "platform": "linux",
    "architecture": "x86_64"
  }
}
```

This is an optional extension of the digest-pinned qualification receipt v1,
not a PR-supplied claim and not a change to the signed producer predicate. The
registry must approve the exact receipt about the actual qualified workflow SHA.
The measured minor `3.11` derives from that fixed full runtime. A selector asking
for `3.12` cannot be satisfied by it, regardless of names or caller matrix labels.
Missing runtime proof yields `matrix_runtime_unconfirmed`; an incompatible
requested cell yields `matrix_runtime_mismatch`. Older qualification receipts
remain usable for ordinary non-matrix jobs only. No qualified registry entry is
created merely by implementing this parser.

CI matrix evidence can borrow runtime identity only from a workflow-bound test
proof with the same actual workflow/run/attempt/job/App and approved logical
job/matrix identity. It cannot borrow from another attempt, another cell or an
unqualified artifact. Other runtimes require separate fixed-producer support and
actual qualification. The reusable producer accepts no new runtime inputs.

## Ledger declaration

`ledger_input` has exactly `format` and `paths`. The format is a lowercase
identifier of at most 64 characters. `ec-ledger-v1` requires `paths: []` and keeps
the existing fixed EC adapter scope. Other identifiers require 1–16 unique safe
repository-relative file paths. The verifier records presence and original Git
blob/size/SHA identity from the verified source inventory; it does not execute or
parse an arbitrary declared format.

An unknown format with at least one present declared input is `unsupported`;
all declared inputs absent is `not_provided`. Malformed recognized EC input
retains its native error/gap classification. `ledger/input.json` links the exact
declaration to retained source objects. Native errors, verdicts, findings and raw
streams remain observable; a genuine finding cannot disappear behind this
declaration. Optional absence alone does not become a block. No declaration is
inferred from native absence or files in the reviewed PR.

## Proposed outer policy

An explicit `policy_input` declaration has exactly
`{"format":"delivery-review-policy-v1","path":"policy.json"}` with an
operator-selected safe repository-relative path. It is paired with exactly one
`policy_change` requirement, empty selector and `minimum_binding: reported`.
The path, format and requirement are part of the independently approved profile.
The verifier does not search other JSON files or execute configuration code.

The observer traverses the verified canonical Git objects for that exact path at
the **merge base and HEAD**, preserving original blob bytes, object IDs, hashes,
file presence and the source inventory identity. The target base tip is not the
comparison base. Each present document must be at most 1 MiB, strict duplicate-free
JSON and valid frozen contract-v1 policy. Source reads also retain the snapshot's
bounded object, depth and total-size limits. Oversized/malformed original bytes
remain linked to the source and retained; they produce an explicit error.

Canonically identical valid documents are satisfied/clear. Any valid change,
addition or deletion is satisfied/review. Known relaxation classes include
removing/making optional a required obligation, lowering minimum tests/binding,
newly permitting skips, expanding source modes and increasing resource limits.
Those use `unapproved_policy_relaxation`; other changes require review without
being called weakening. Both absent is missing, never a fabricated empty policy
pass. A bounded hash-based change index links to the full originals and marks
truncation if needed. Malformed proposals cannot clear another observed block.

`policy-change/observation.json`, `base.json`, `head.json` (when present) and
`approved-policy.json` are included in report inventory. The approved policy is
always the sole authority used for existing obligations, engine options and
readiness. The proposed policy is never loaded as a replacement inspection
profile. Native scanner configuration/exemption semantics remain separate;
this initial declaration does not accept arbitrary configuration dialects.
