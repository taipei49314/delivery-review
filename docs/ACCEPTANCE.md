# Acceptance plan

Status: **M0 expectations only**. E01-E18 below are product integration
obligations. Every integration is initially **NOT_RUN**. Synthetic fixture
validation and state-machine tests can validate the M0 contract; they cannot
change these integrations to passed or prove that a collector, engine or
GitHub workflow has executed.

The exact product states and exits are defined in [CONTRACT.md](CONTRACT.md).
Evidence completeness, freshness and policy disposition must be asserted
separately. An observed failing test may produce complete/current/block; an
absent required execution cannot produce complete.

## 1. Evidence required to close an integration

Each execution record must identify the case and variant, immutable product and
fixture sources, policy/engine identities, actual environment, commands or API
operations, retained raw observations/digests, expected and actual states, native
exits, and the outcome. Record deviations, missing observations and failures.
A later success does not overwrite an earlier attempt. A fixture's expected
state is a specification, not an execution receipt.

E01 requires a real dedicated Python repository using the supported GitHub
hosted producer, artifact transport and local report flow. The specialized
Checkwash local-host receipt bridge is not the sole happy path. Other controls
may use recorded provider responses or controlled inputs where that establishes
the claim, but transport, workflow-binding and subprocess claims need their
corresponding real integration evidence. No live integration has been run in M0.

## 2. E01-E18 obligations and required variants

| ID | Scenario and variants | Required behavior | Initial integration status |
| --- | --- | --- | --- |
| E01 | Fixed PR head; all required jobs, approved producer receipt, artifacts and scanners complete. | complete/current/clear, ready, exit 0; trace each requirement to its source. Demonstrate the real hosted Actions-to-artifact-to-report flow. | NOT_RUN |
| E02 | Successful evidence is for a previous head; compare with a matching current-head control. | Never credit the old source to the new one. Expose stale evidence and leave current obligations incomplete unless matching evidence exists. No ready conclusion from old evidence. | NOT_RUN |
| E03 | Allowed direct head; allowed two-parent merge; incorrect parent, missing parent proof and disallowed mode. | Accept only the policy-permitted source with independent source proof. Invalid/unconfirmed relations cannot satisfy the source obligation. Do not compare head and merge SHAs as if they must be equal. | NOT_RUN |
| E04 | Head changes; base tip changes; latest attempt changes between initial and final observation. | Mark the collected snapshot stale, retain its observations and require refresh. No ready result from a raced snapshot. | NOT_RUN |
| E05 | HEAD lowers threshold, removes a required job, edits an allowlist or supplies a different outer policy. | Original approved policy remains authoritative. Preserve the policy difference and native engine exemption rules; no self-exemption. | NOT_RUN |
| E06 | Required workflow absent; job absent; matrix cell absent; pagination incomplete. | Evidence incomplete with the specific missing obligation. An empty or partial list must not establish success. | NOT_RUN |
| E07 | Required execution skipped, neutral, cancelled or timed out; permitted partial testcase skips as a separate positive control. | Preserve each native status. Required non-execution cannot be success. Partial testcase skips satisfy only the explicit policy that permits them. | NOT_RUN |
| E08 | Same check name from another App/workflow; duplicate ambiguous matches; matching trusted identity control. | Match complete required identity, not a name. Wrong or ambiguous source cannot satisfy the requirement. | NOT_RUN |
| E09 | Old run success/new run pending; old attempt success/new attempt failure; complementary successes in different attempts. | Select latest applicable run and latest attempt without cherry-picking. Pending evidence remains incomplete; fully observed failure remains a policy failure. Never combine attempts into success. | NOT_RUN |
| E10 | Alter enclosing artifact bytes/digest, extracted receipt/JUnit digest, run, attempt or source; missing artifact identity; self-consistent receipt lacking independently confirmed producer SHA; fully bound control. | Check outer API artifact identity and independently hashed bytes separately from the receipt's internal JUnit digest. Reject mismatches and retain the reason. Self-reported consistency cannot raise binding to workflow_bound or satisfy an obligation requiring it. | NOT_RUN |
| E11 | Scanner timeout, native exit 2, invalid JSON, unsupported schema or output limit; another scanner has already found a block. | Error/incomplete as appropriate, never clean. Keep the original blocking finding and exit 2 for the overall incomplete/error report. | NOT_RUN |
| E12 | Skipped/unread file, unknown assertion coverage, configuration error; optional versus required coverage policy. | Preserve every gap. Apply the declared requirement without claiming analysis of unread scope. A required unresolved gap prevents completeness. | NOT_RUN |
| E13 | Known weakening, legitimate refactor, valid BASE exemption and new HEAD exemption. | Preserve original engine verdict, finding identity and exemption status. Exemption additions require review under the agreed policy. Do not rewrite labels to force agreement between engines. | NOT_RUN |
| E14 | Exit zero with zero tests, all skipped, collection/setup error, absent JUnit, duplicate case ID or inconsistent counts; valid pytest control. | Upload or process success cannot substitute for required test execution. Preserve counts; authentic observed failure/error can be failed/block, while skipped or zero-execution evidence remains incomplete. Reject malformed or contradictory reports. | NOT_RUN |
| E15 | Ledger absent, unsupported format and supported ledger with gaps; each under optional and required-ledger policies. | Distinguish not_provided/unsupported/gaps. Optional absence alone does not block; required missing or incomplete proof does. No synthetic empty ledger pass. | NOT_RUN |
| E16 | Archive traversal, symlink, duplicate member, oversized archive/XML, DTD/entity input and hostile HTML strings. | Refuse unsupported/dangerous inputs or render escaped text; do not execute content, access external XML entities or escape the evidence directory. Preserve bounded-failure reasons. | NOT_RUN |
| E17 | Saved-byte tampering, offline replay and online refresh; same-input replay control. | Reject digest mismatch. Replay sets freshness unknown and preserves observed_at. Refresh creates a distinct report; old bytes remain. Same saved inputs/policy give the same evidence/policy decision; offline execution cannot renew current freshness. | NOT_RUN |
| E18 | Known Checkwash #419 escape; custom/queue/octopus merge or another unsupported source. | Disclose the existing scanner limitation and retain its real verdict. Unsupported source leaves source proof unconfirmed. Do not repair detector semantics or erase an escape to pass this product's gate. | NOT_RUN |

E02 can expose stale individual evidence while a fresh observation proves that
the report is currently incomplete. E04 concerns the freshness of the report's
own captured snapshot. Fixtures must distinguish these cases rather than map
every missing item to the same state.

E18 is a scope-disclosure control, not a promise to catch the known escape. A
clear scanner verdict means only that engine's bounded analysis found no
blocking issue. It must not be presented as proof that test weakening is absent.

## 3. M0 contract checks versus later gates

M0 checks the six schemas, example documents, declared synthetic acceptance
cases and all combinations of the three decision axes. Synthetic variants pair
a policy with hypothetical normalized observations and independently specified
expected states. M0 checks their shape and decision invariants; it does not
extract the observations from raw API responses. Raw GitHub fixtures remain
separate inputs for future collector tests. M0 rejects invalid versions/states
and contradictory readiness/exit claims. Candidate engine-lock records retain
their unverified status. These are local contract checks only.

Acceptance-case documents use `origin: synthetic_contract`,
`report_role: preregistered_expected_normalized_output` and
`full_integration: NOT_RUN`. Their embedded report is an expected normalized
output, not a collector execution result. The policy must match the report's
canonical policy digest and declared obligations. Each case separately records
expected state, `ready_for_review`, exit and reason codes.

M1 adds evidence acquisition and replay controls: exact source/attempt selection,
complete pagination, artifact boundaries, preserved raw data and final freshness
observations. M2 adds isolated installed-engine adapters, original-result
preservation, conditional Ledgerwash and policy evaluation. M3 supplies the
approved pytest producer, parser and complete user flow, including actual E01
and records for every E01-E18 obligation. M4 qualifies installation, declared
platforms and pilot use before release.

Product release requires all E01-E18 variants to have appropriate recorded
evidence; no deliberately missing/substituted required evidence may be promoted
to complete. Every declared engine artifact must have been independently
downloaded/hashed, isolated and contract-tested. The release must name the
actual qualified platform. Supporting Linux/Python 3.11 does not certify
Windows, macOS or another runtime.

## 4. Data reuse and label discipline

The existing Checkwash data is a regression source, not the runtime database.
Select an initial representative subset of approximately 30-50 cases when the
adapters are implemented. Keep data origin, engine/source pin, raw observation,
current expectation, label method and limitations distinct. Do not vendor
third-party source trees or private transcripts into this repository.

| Asset | Accepted use | Label boundary |
| --- | --- | --- |
| `.gwcase` fixtures and 3,600 historical diffs | Adapter/schema/report regression. | Already used for tuning; not independent accuracy evidence. |
| 80 tamper and 60 refactor cases; stress reproducers | Narrow executable oracle controls and known escapes. | Red-to-green on unchanged buggy production supports that seeded case; catching one bug on both sides does not prove semantic equivalence. |
| Corpus probe FIXED/WEAKENED/FAILED method | Keep original tests, agent production and agent tests separately observable. | Outcome remains relative to the original test set and harness. |
| Harvested PR patches | Format, identity, linking and presentation cases. | Merged PRs are not correctness labels. |
| 630 reconstructed Claude edits | Internal study of interruption and message costs if locally available. | Provisional model labels, reconstruction gaps and private provenance; not the public release benchmark. |

The new delivery cases must cover source/attempt/policy/artifact failures that
the existing test-weakening datasets do not establish. Record constructed
controls separately from real human-adjudicated delivery chains. Retain old
observations when a current regression expectation changes.

After freezing a product candidate, use new external delivery chains with a
predefined review rubric. A case used to change the product becomes regression
data and is no longer held out. The planned pilot of three users, at least three
repositories and 15 PRs is a product-learning target, not a statistical accuracy
claim. Record verification time, irrelevant warnings, missed gaps, traceability
and whether users voluntarily use the report again.
