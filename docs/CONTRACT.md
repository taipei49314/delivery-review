# Delivery Review contract v1

Status: **M0 contract**, for the planned v0.1 product. M0 defines data shapes,
decision semantics and synthetic acceptance expectations. It does not implement
GitHub collection, scanner execution, producer execution or end-to-end product
acceptance. Those integrations remain **NOT_RUN** until separately recorded.

The product reviews evidence for one fixed GitHub pull request under a declared
policy. A result may be ready for **human review**. It never declares that the
task is complete, that requirements are correct or that the product has no bugs.
It does not accept, merge, repair, comment on or publish checks for a PR.

## 1. Versioned documents

The normative JSON shapes are the following files under `schemas/`. This
document defines their meaning and cross-document invariants. A schema-valid
document can still contain unverified claims; schema validation is not source
authentication or product acceptance.

| Schema | Responsibility |
| --- | --- |
| `common.schema.json` | Shared identities, references and state definitions. |
| `policy.schema.json` | Operator-approved requirements, source rules, engine requirements and limits. |
| `receipt.schema.json` | Producer identity, declared source/execution and the internal JUnit file digest; written before artifact upload. |
| `report.schema.json` | Snapshot, observations, obligations, original scanner results and decisions. |
| `acceptance-case.schema.json` | Declared scenario, expected outcomes and the distinction between synthetic checks and integration evidence. |
| `engine-lock.schema.json` | Exact engine candidates and the status of artifact and adapter verification. |

Documents use `schema_version: 1`. Producers and consumers explicitly select the
applicable schema type and check the version; a filename or package version is
not sufficient. The schemas do not require a separate top-level kind field.
Unsupported versions, malformed documents and inconsistent references
must not be interpreted as an empty successful result. The schemas determine
accepted fields and types; no parser may coerce an unknown state to a success.
Breaking changes to field meaning, required structure or decision semantics
require a contract version change and migration notes. Historical raw documents
retain their original bytes and version.

JSON is UTF-8. Store the original bytes of policy, receipts and scanner reports
before normalization, and bind those bytes by SHA-256. A metadata-provided hash
is a claimed digest until the artifact has been obtained and independently
hashed. A candidate in the engine lock is not an accepted installation merely
because its filename, version and published digest are known.

The report's `policy.canonical_sha256` identifies the normalized policy object:
SHA-256 of sorted-key, compact UTF-8 JSON without a trailing newline. This M0
serialization is not RFC 8785, a signature or producer authentication. It is
distinct from the digest of a stored raw policy file; retain both meanings
without substituting one for the other.

## 2. Subject and policy identity

The subject contains the numeric repository and head-repository identities, PR
number, complete base-tip and head commit SHAs, and merge-base SHA. Store a
candidate merge-commit SHA separately when available. Names and abbreviated SHAs
are display labels, not authoritative identities.

Static analysis compares `merge_base..head`. The base tip is the target branch
observed at collection time; it is not interchangeable with the merge base.
Scanner and ledger evidence bind to that fixed PR head. The CI checkout policy's
`allowed_source_modes` does not change the static comparison basis: merge-only
CI can coexist with a head-bound static scan.
Keep each scanner's native BASE policy behavior. If an external requirement
demands a newer base-tip policy that the scanner cannot enforce, record the
policy-basis gap instead of claiming that requirement is met.

A policy is approved outside the PR under review: a trusted local file or an
explicitly approved commit/blob. Save its raw bytes, identity and digest. HEAD
changes to requirements, thresholds or exemptions do not weaken that policy for
the same report. They remain visible for review. A PR without declared
requirements may have observations, but cannot acquire evidence completeness
by treating the jobs that happened to appear as the full requirement set.

Policy fields and selectors identify the permitted repository, source modes,
workflow IDs, producer versions, jobs/matrix cells, engine versions and resource
limits. The approved producer contract also fixes event, checkout and report
format behavior; the compact M0 schema does not encode a generic workflow DSL.
Whether a ledger is required is an
explicit policy choice. The policy also determines whether known partial skips
are permitted; that does not turn a skipped required execution into success.

## 3. CI source binding and run selection

Retain platform run metadata, check SHA, producer-declared checkout SHA and the
independent observations that connect them. A platform `head_sha` alone is not
proof of what a test command checked out.

The supported source modes are:

1. The exact PR head, when allowed by policy.
2. A two-parent PR merge commit whose actual Git object has the observed base
   tip and PR head as its parents, under the approved producer's source contract.

Custom merges, merge queues, octopus merges and unresolvable source relationships
remain unsupported or unconfirmed. Similar trees or branch names do not establish
equivalence.

For each workflow and snapshot, select the latest applicable run and that run's
latest attempt according to the fixed policy. Selection and trust validation are
separate: a selected run that fails trust checks cannot be skipped in favor of an
older green run. Keep old runs as history. Never assemble one workflow result from
the successful fragments of different attempts. Different required workflows may
have different runs, provided they bind to the same subject and policy.

Match repository, workflow ID/path, event, job/matrix identity, App ID, run ID,
attempt, artifact ID and digest as required. A matching display name is
insufficient. Missing, ambiguous, pending, skipped, neutral, cancelled, timed-out,
inaccessible and expired observations remain distinguishable. Pagination must be
complete within the policy's bounds; a bounded partial result is not a complete
listing.

Completed CI and test-report obligations require explicit workflow, run, attempt,
job and App identities and must refer to a selected execution. A null run cannot
bypass selection. Completed test-report evidence also requires both
`artifact_id` and `artifact_sha256`; these fields remain nullable in the shared
schema for other evidence kinds and incomplete observations.

Re-observe PR head/base and the selected execution attempts before finalizing a
live report. A change makes that report stale. Freshness is an observation at a
recorded time, not a lasting promise about a saved HTML file.

## 4. Evidence binding levels

| Level | Permitted claim |
| --- | --- |
| `reported` | The input says this happened; independent binding is absent. |
| `platform_bound` | Independent platform metadata and obtained-byte digests support the recorded association. |
| `workflow_bound` | Platform binding also matches the approved producer workflow version and its source/collection contract. |

The collector must derive these levels from independent observations. A receipt
cannot award itself a higher level. To establish `workflow_bound`, independently
read run/attempt metadata and verify the actual referenced reusable-workflow path
and full SHA against policy, then verify the approved source-selection contract,
job/artifact linkage and receipt source. Missing platform confirmation of the
workflow version or arbitrary PR-selected commands cannot establish this level.

The default confirmed-test-source obligation requires `workflow_bound`. This
means the evidence follows an approved workflow contract. It does not prove that
the trusted workflow is bug-free, that a runner is uncompromised or that test code
could not fabricate JUnit. Hash equality establishes byte consistency. An unsigned
statement or in-toto-shaped export does not increase the binding level.

The producer writes its receipt before upload. Its `execution.junit` records the
internal JUnit path, byte size and SHA-256; the receipt has no `artifact` field
and does not claim the enclosing archive's identity or digest. The outer report
evidence records `artifact_id` and `artifact_sha256` separately: the collector
reads GitHub artifact metadata, obtains the artifact bytes and independently
hashes them to confirm the transport digest. `raw_sha256` identifies the selected
extracted file bytes. Keep archive, receipt and JUnit digests distinct. A receipt
inside an archive cannot predeclare the final archive hash, and any self-reported
digest alone is insufficient to establish artifact or source trust.

## 5. Scanner and test observations

Use the locked CLI/JSON interfaces of Checkwash, Pinwash and, where applicable,
Ledgerwash. Do not import private engine functions or rewrite their detectors.
Save engine, contract and adapter versions; package digest; arguments; comparison
SHAs; configuration and threshold; exit status; stdout/stderr; original reports
and digests. Keep findings identified by engine, engine version and fingerprint.
Do not merge unrelated fingerprints or infer verdicts by parsing display prose.

Each scanner or ledger requirement must select a pinned engine. Scanner
requirements select Checkwash or Pinwash; ledger requirements select Ledgerwash.
A completed engine must retain a native exit consistent with its status and
verdict. Native error exit 2 cannot support a successful scanner obligation.

Normalization must preserve each original verdict, finding, exemption, residual,
unread/skipped file, configuration error and coverage limitation. Summary counts
cannot replace the original verdict. An engine error, timeout, malformed JSON,
unknown schema or output-limit refusal is not a clean scan. Existing findings
remain visible even when other evidence fails.

Pinwash receives the fixed PR diff; this is not whole-session review. Ledgerwash
is conditional: no ledger is `not_provided`, an unknown format is `unsupported`,
and a supported but partly verified ledger retains its coverage gaps. Optional
absence alone does not block readiness; a required missing/unsupported ledger
leaves its obligation unmet. Do not manufacture an empty ledger to obtain a pass.

The planned pytest adapter accepts only its qualified JUnit dialect. Setup,
test execution and report upload retain separate outcomes. Zero tests, all tests
skipped, collection/setup errors, absent reports, inconsistent counts or duplicate
test identities cannot be treated as required tests passing merely because a
command or upload exited zero. Partial skips follow explicit policy. No test
command, project installation or PR-controlled script executes in the verifier.

## 6. Decision axes and exit status

All three axes are mandatory and independent. The evaluator records reasons and
matched evidence for every declared obligation. Policy requirements and report
obligations must match exactly by ID, `kind`, `required` and `minimum_binding`:
no omitted requirement, undeclared substitute or weakened binding threshold.

An obligation's `outcome` is one of `satisfied`, `failed`, `missing`, `pending`,
`unsupported`, `unavailable`, `untrusted`, `mismatch`, `error` or `not_applicable`.
Required obligations with `satisfied` or `failed` have complete observations;
a required `error` makes evidence `error`; any other outcome of a required
obligation makes evidence `incomplete`. A required obligation cannot be waived
by calling it `not_applicable`. A `failed` obligation must have policy effect
`block`. A new exemption can have observed outcome `satisfied` with effect
`review`: the evidence of the addition is complete while the decision remains
for a person. The `effect` is preserved independently from missing/error status.

| Axis | Values | Meaning |
| --- | --- | --- |
| Evidence | `complete`, `incomplete`, `error` | Whether the declared required evidence was collected and evaluated, is missing/unconfirmed, or failed processing. |
| Freshness | `current`, `stale`, `unknown` | Whether the subject and selected execution still match the last independent observation. |
| Policy | `clear`, `review`, `block` | Whether known observations require no further policy action, human review or a policy block. |

`complete` does not mean the tested code passed: authentic, fully observed
failure evidence can be complete while policy is `block`. Missing required
execution or proof cannot be made complete by adding a policy block. Known
review/block findings are retained alongside `incomplete` or `error`.

A fully observed test failure or setup/collection error can support `failed`
with effect `block`; skipped, cancelled or zero-execution evidence cannot be
relabeled `failed` to manufacture completeness. Every other required obligation
must still have complete observations for the overall evidence axis to be
`complete`.

```text
ready_for_review = (evidence == complete AND freshness == current AND policy == clear)

exit 0: ready_for_review
exit 1: evidence == complete AND freshness == current AND policy in {review, block}
exit 2: every other combination, including malformed input/processing failure
```

The `ready_for_review` value and reported `exit_code` must agree with these
rules. Exit 2 must not hide an independently observed policy block. There is no
confidence score and no state meaning automatic acceptance.

## 7. Saved reports, replay and scope

Each inspection or refresh creates a new evidence directory and report; it does
not rewrite a prior report. Preserve the selected raw files and a digest manifest.
Replay checks the saved data and recomputes under the saved policy. Its result
has freshness `unknown`, preserves the original `observed_at`, and does not
rewrite the historical report's original state. It cannot claim knowledge of
current GitHub state. A new claim of current applicability requires refresh.

Stale individual evidence is distinct from a stale report snapshot. For example,
old-head CI observed against an unchanged current PR leaves current evidence
incomplete while report freshness is `current`. Observed head/base or selected
attempt drift during collection makes the report freshness `stale`.
For live reports, `recheck` compares the observed head/base and `selected_runs`
with their captured values; absent independent recheck leaves freshness unknown.

These files do not resist an actor able to rewrite both local data and its
manifest. A digest mismatch must be reported, never repaired silently by hashing
the altered bytes and calling them the original receipt.

Synthetic M0 examples describe hypothetical normalized observations. Their
expected decisions are independently specified; validating those decisions does
not perform fact extraction from raw GitHub data. Separate raw provider fixtures
do not establish that mapping either until the collector is implemented and
tested. M0 examples do not demonstrate live source binding, scanner accuracy or
actual execution. Passing their schema and state checks does not change any
E01-E18 product integration from `NOT_RUN`.

The first product scope is GitHub.com, Actions and the qualified Python/pytest
flow, initially qualified on Linux/Python 3.11. It makes no complete-session
history claim. Checkwash #419 and other documented engine limitations remain
visible; repairing them is not a prerequisite for this product's v0.1.
