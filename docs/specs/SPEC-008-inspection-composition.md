# SPEC-008 — original evidence binding and report composition

Status: implementation with authored semantic controls. These controls are not
signatures, hosted producer qualification, or E01–E18 product acceptance. The
frozen contract-v1 schemas and forty expected cases remain unchanged.

## Inputs and authority

`test_evidence.bind_test_evidence` consumes the original approved inspection
profile, verified raw GitHub capture, independently hashed static source snapshot
and one policy test requirement. It receives optional original artifact bytes,
artifact-detail and producer-repository HTTP facts, canonical event-source Git
commit bytes, an actual `AttestationExecution`, and an approved producer
qualification. It returns evidence, obligation, retained bytes and diagnostics.
`archive_bytes` is the normal collector input; alternatively `artifact` accepts
an existing parsed archive object, whose original bytes are reparsed anyway.
They are mutually exclusive. A malformed obtained ZIP is retained as an error,
not relabeled as missing evidence.

`RawApiFact` includes the exact request path, HTTP status, body bytes, observation
time, completeness and safe response headers. The binder checks the fixed route
and derives body hashes itself. It does not accept a caller-supplied body digest
or a normalized metadata success flag. The collector separately proves complete
artifact pagination and unique candidate selection before passing an artifact.
Neither a partial candidate list nor a convenient older run may be used.

`ProducerQualification` comes only from the operator-approved registry, never
from the reviewed PR or an artifact. It binds numeric producer repository ID,
workflow path, full producer Git SHA, execution profile, fixed `tests` selector,
qualified source modes, and the SHA-256 of the original qualification receipt.
The receipt must repeat those identities and a qualified result. That digest is
content identity; its approval and underlying real hosted receipts are a separate
registry/release obligation. A self-consistent JSON document is not a producer
qualification. The default implementation has no automatically qualified entry.
`pr_head` qualification cannot silently qualify `github_merge` execution.

The current workflow profile fixes the entire pytest command, including literal
`tests`, the trusted config, root directory and JUnit location. A caller-selected
subdirectory, command, plugin or test option cannot satisfy the requirement.
Changing this selection contract requires a versioned profile and qualification.

## Independent relationships

The binder reparses the capture using its saved mapper version and verifies its
profile matches the approved inspection profile. It verifies the static source
snapshot and matches repository locator, full head and base-tip SHAs. It selects
the required workflow, latest selected run/attempt and unique job from complete
initial job pages. The job and independently obtained check must match App,
suite/source association and compatible native execution state. Missing pages
cannot prove that an apparent single match is unique.

The artifact is reparsed from its original ZIP bytes with the approved byte and
member limits; supplied extracted-file maps are not trusted. The artifact-detail
route and payload must agree on actual artifact ID, run and repository identities,
head SHA, exact byte size and independently computed SHA-256. Its name is only
the fixed candidate locator. Receipt and JUnit digests remain separate from the
enclosing archive digest.

The receipt must match repository/PR, source, producer and selected run/attempt/
job identities. Its internal JUnit reference must match the actual extracted XML
bytes. The qualified parser independently computes case counts and retains native
XML observations; the receipt cannot substitute its own counts. The independently
read attempt response must identify exactly one approved reusable workflow at its
full SHA, and the producer repository response must match the approved numeric ID.

For `EventSourceFact`, the verifier hashes canonical Git commit-object bytes and
parses actual parents. A merge event source must equal the independently observed
PR merge SHA and have exactly `[base_tip, head]` as its parents. Its certificate
source SHA can differ from the tested `pr_head`; those are separate facts. A
tested merge mode also needs explicit policy permission and producer qualification
for that mode. Trees that merely look similar do not establish source equivalence.

The binder exposes `diagnostics.attestation_expected` once the original platform,
artifact and receipt facts suffice. The orchestrator uses these exact expectations
to run `verify_github_attestation`, then calls the binder again with the resulting
execution. No cryptographic success is inferred from this preflight result.

The binder rechecks the verifier tool/trusted-root pins, native process exit and
fixed invocation, raw-file inventory, artifact bytes, expected identities,
certificate, signed subject/predicate and correspondence to the original DSSE
bundle. It ignores saved `observation.json` success flags. On offline replay the
orchestrator must actually rerun the official verifier on saved bundle/root bytes;
constructing an execution object from a saved JSON result is not sufficient.
The Python execution object is an internal controller boundary, not an
unforgeable capability or an externally accepted proof format.

For deterministic replay inventories, optional `attestation_retention` carries
the original historical execution. Its raw inventory and tool pin are checked,
and its artifact, bundle, trust root and expectation bytes must equal the fresh
verification's inputs. It supplies retained bytes only; only the fresh execution
determines trust. The orchestrator saves the new process audit outside the
canonical report inventory. A historical successful exit cannot override a fresh
verification failure, and changed signed inputs cannot reuse an old audit.

Transport/run digest consistency alone leaves the job-bearing test evidence at
`reported`. Only the full signed job assertion, independently cross-checked
platform relationships, approved workflow version and qualified producer contract
permit `workflow_bound`. A receipt or metadata-only record never awards itself
that binding.

## Outcomes

Authenticated native failures with actual failing/error cases produce
`failed/block`. Zero tests, all skips, disallowed partial skips and insufficient
passed tests remain incomplete; permitted partial skips follow the bound policy.
Unknown native exits, contradictory counts, malformed XML, digest mismatches and
unqualified commands remain explicit errors. Native pending, skipped, neutral,
cancelled and timeout states remain distinguishable. Missing trust is not converted
into observed failure merely to manufacture completeness.

`composition.compose_report` accepts original inputs and calls the binder itself.
It does not accept pre-normalized test success, obligations or state axes. It
reparses each engine's original streams using the pinned adapter, verifies source
inventory and execution inputs, and requires the exact native policy reconstructed
from approved engine options plus the original observation's UTC date. The ambient
clock cannot change BASE exemption evaluation during replay.

Native block/findings and exemption review survive another engine error or a
post-execution integrity failure. Required coverage gaps leave evidence incomplete
while preserving policy block/review. A qualified `not_provided` ledger is
`not_applicable/clear` when optional, even if a strict native invocation blocked
solely on absence; that original native block remains in retained stdout. Required
absence remains unmet. Findings and native errors do not use this absence exception.

Every declared requirement appears exactly once. The existing contract reducer
derives evidence completeness, freshness and policy independently and determines
readiness/exit. The result is validated against the unchanged report/policy
contract. JSON, Markdown and script-free self-contained HTML use those same
derived states and raw references; untrusted text is escaped by the report renderer.

## Freshness and storage

The live orchestrator obtains final PR/run observations after source, engines,
artifacts and signature verification. A selected head/base/attempt change remains
stale. Offline mode always produces unknown freshness and preserves the original
observation time and evidence/policy decisions.

Contract v1 requires a complete recheck tuple. If raw observations demonstrate a
change but lack another mandatory final field, the composer does not invent that
field: it leaves freshness unknown and records
`observed_drift_with_incomplete_recheck`. This cannot produce readiness. A future
partial-recheck representation requires an explicit versioned contract amendment.

The composer returns an exact raw inventory and byte mapping for the create-once
inspection store. It keeps original capture/profile, source inventory, native
engine manifests/streams/policy, artifact/receipt/JUnit, API facts, event-source
commit, qualification receipt and attestation execution inputs. Raw Git source
objects and controller setup logs also belong in the full inspection store;
the source/execution acquisition readers own their inventories. The composer
does not overwrite a prior report or repair an altered digest.

An unavailable basic subject or source cannot be represented by fictitious SHAs
in the frozen report schema. The orchestrator retains an explicit inspection-error
record and available raw observations. It must not construct a successful empty
report when these prerequisites are missing.

## Development validation

Authored controls cover the full semantic chain and its independent missing/
conflicting facts, source merge parents, selected attempts, incomplete job pages,
receipt/JUnit mismatch, fixed test selection, qualification digest/modes, fake
saved verifier success versus native failure, test failure/zero/all-skip/partial
skip, scanner errors alongside known blocks, native exemption review, optional/
required ledger absence, drift, deterministic offline recomposition and safe HTML.
Synthetic signature envelopes are explicitly labeled and never counted as real
cryptographic verification or hosted integration receipts.
