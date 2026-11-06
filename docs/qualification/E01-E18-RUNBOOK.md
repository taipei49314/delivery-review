# E01–E18 execution runbook

This is an execution plan, not a qualification receipt. The frozen expectations
and their `NOT_RUN` labels remain unchanged. Use [ACCEPTANCE](../ACCEPTANCE.md)
for every required variant and [RELEASE-1.0](../RELEASE-1.0.md) for release gates.
The forty normalized examples do not exhaust the variants in that table.

## Common prerequisites and order

1. Freeze a clean product commit and build/install its wheel on the declared
   Linux/Python 3.11 target. Retain source, wheel, runtime, toolchain and frozen
   validation hashes. Run from outside the checkout. Development probes using
   edited source do not qualify that immutable candidate.
2. Qualify each fixed head/merge producer revision independently: hosted runtime
   isolation, platform identity, original artifact upload, official attestation
   verification and receipt/JUnit linkage. Review its exact repository ID,
   workflow path, source SHA and receipt digest before adding a bundled registry
   entry. The current empty registry cannot be bypassed to manufacture E01.
3. Use the public dedicated reference repository and independently approve the
   complete external inspection profile. Record the observed workflow/job/App
   IDs. Generate first-party sources with `examples/reference/render.py`; its
   manifest is a source inventory, not an execution result. Existing variants
   are `base`, `e01`, `test_failure`, `zero_tests`, `all_skipped`, `hostile_html`.
   The renderer selects one fixed head or merge workflow, with no runtime inputs.
4. Establish E01 and the separate allowed-merge E03 control first. Reuse their
   immutable raw captures for clearly identified negative boundary controls;
   preserve the originals. Run actual hosted variants wherever the claim is
   about execution or provenance. Then run races, malformed-input boundaries,
   same-input replay and refresh. Keep failures and retries as separate receipts.

The installed flow is `delivery-review tools install --out NEW`, then
`delivery-review inspect PR_URL --policy APPROVED_PROFILE --tools TOOLCHAIN
--out NEW`. Use `verify SAVED`, `replay SAVED --tools OFFLINE_TOOLS --out NEW`
and `refresh SAVED --tools LIVE_TOOLS --out NEW` for the saved-evidence controls.
Run offline replay in an actual network namespace with networking disabled.

## Case execution

| Case | Concrete setup and observation | Remaining implementation or control work |
| --- | --- | --- |
| E01 | Render `base` then `e01`, run the qualified fixed head producer, and inspect the immutable PR. Trace source, every required job, artifact ID/digest, receipt, JUnit and three actual pinned scanners into complete/current/clear, exit 0. | Actual hosted qualification and approved registry entry are prerequisites. Authored full-flow tests do not substitute. |
| E02 | Save a successful head A run, push head B with a pending/absent run, inspect B, then inspect the matching completed B control. Preserve both source identities. | A controlled provider capture can isolate selection; a genuine hosted pair establishes transport/source behavior. |
| E03 | Run both fixed source workflows. For merge, retain the fixed pull-ref response and canonical Git commit with ordered parents `[base, head]`. Exercise wrong/missing parent proof and a disallowed mode using separately labeled raw-response controls. | Each source mode needs its own qualified workflow revision. API-2026 merge-ref dispatch is explicit; legacy captures keep their original protocol. No custom merge may become a positive control. |
| E04 | Pause a qualification transport controller immediately before the final recheck; independently push a new head, advance the base, or rerun the selected run. Resume and retain initial/final API facts. Refresh into a new directory. | Add a development race controller around the real acquisition transport. A complete changed tuple must produce stale; incomplete recheck proof stays unknown with an explicit gap. Merely editing a saved normalized result is not a race. |
| E05 | Create separate first-party commits lowering scanner threshold, deleting a required caller job, adding an allowlist/exemption and supplying a competing outer policy. Inspect each with the original approved profile. Retain native config/exemption observations and proposed policy bytes. | Use profile-v2 policy_input for one explicit contract-v1 candidate-policy path; the implemented observer compares merge-base/HEAD originals. Native scanner configuration cases remain separate. PR policy bytes never replace the approved profile. |
| E06 | Exercise missing workflow, missing job and truncated pagination against complete and incomplete native inventories. For the matrix case, declare logical `pytest-linux` cells `python=3.11` and `python=3.12`; execute only the independently proven 3.11 cell. Expect incomplete with the missing 3.12 obligation. | Profile v1 rejects matrix selectors. The profile-v2 mapping/runtime implementation has authored controls; actual reviewed runtime qualification and the hosted missing-cell control remain required. A display name alone is not matrix proof. |
| E07 | Literal caller controls skip/cancel executions; record native neutral/timeout responses with their real provider provenance or an explicitly controlled API boundary. Add a producer fixture with one pass plus one skip under allow-skips true/false policies. | Fully observed timeout must preserve the frozen block disposition. Partial testcase skip is distinct from skipped job or all-skipped report. |
| E08 | Create independent same-name jobs from another workflow; retain full workflow/run/attempt/job/check/App identities. Use controlled original API bodies for wrong App and duplicate matches, plus a genuine trusted control. | Wrong App must preserve its frozen block disposition. Do not claim an authored App response establishes actual third-party execution. |
| E09 | Leave a newer run pending after an older success; rerun a failed latest attempt after an older successful attempt. Use two jobs whose successes occur in different attempts to test non-combination. | Retain complete paginated inventories and the final attempt check. No timestamp/name-based cherry-picking. |
| E10 | Start with a fully authenticated control. Independently alter outer ZIP bytes, receipt/JUnit members, run/attempt/source references, or omit artifact identity. Separately use self-consistent unsigned/unqualified evidence and wrong genuine signer/producer controls. | Mutated inputs need an explicit mutation manifest; genuine crypto/process claims require actual official `gh` execution. Digest mismatch and cross-attempt controls must retain the frozen blocking disposition. Never recompute the original capture inventory to disguise tampering. |
| E11 | Run a real bounded pinned engine with a fixture/limit causing timeout or output cap; retain native exit 2 where supported. Feed authored invalid JSON/unknown schema to the adapter as parser controls. Combine each with an independently observed blocking engine result. | Do not replace the pinned executable and call the result a native-engine test. Overall exit 2 must retain the prior block and original raw output. |
| E12 | Use static sources with unsupported/unread assertions, skipped scope and invalid configuration. Retain actual native gaps. Exercise optional-versus-required coverage using the supported Ledger requirement with the same declared non-strict native policy. | Checkwash/Pinwash remain mandatory engines. The optional Ledger control does not claim that arbitrary scanner gaps can be waived; retain the required Pinwash unknown-coverage plus known-block variant separately. |
| E13 | Run the actual pinned engines on known weakening, refactor, BASE exemption and HEAD exemption first-party cases, then inspect the corresponding delivery chains. Keep each engine's original finding IDs/verdict/exemption rules. | Existing 46-case native regression and explicit two-case expectation correction provide native integration evidence only; they are not complete-product receipts. |
| E14 | Run `test_failure`, `zero_tests`, `all_skipped`, a collection/setup error and a valid fixture. Exercise absent JUnit, duplicate IDs and contradictory counts as parser/binder controls with original malformed bytes. | An authentic signed JUnit error can be fully observed failure; absent output cannot. Avoid introducing a new skip/weakening scanner finding when isolating test evidence semantics. Producer refusal and upload failure remain observable gaps. |
| E15 | Under optional and required ledger policies inspect absent EC input, supported input with gaps, and declared unknown format with present original bytes. | Native Ledgerwash absence alone cannot distinguish an unsupported format. Use the implemented profile-v2 format/path declaration plus independently retained source presence; malformed supported input is not an unknown format. |
| E16 | Parse authored archives containing traversal, links, duplicate members and excessive size; XML with DTD/entities and excessive size; render `hostile_html` through a real report. Assert no outside-file write, entity/network access or HTML execution. | Keep archive/XML rejection controls distinct from authenticated transport. Frozen error/block outcomes still apply at the composed-report boundary. |
| E17 | Verify an unchanged saved directory, tamper a copy, replay identical bytes twice without network, then refresh online into another new directory. Retain original timestamps and report hashes, and the fresh official crypto audit separately. | Full positive replay needs E01's qualified capture. Existing actual legacy failed-capture replay proves backward compatibility only; it had no artifact/crypto stage. |
| E18 | Reproduce pinned Checkwash #419 with original native output and explicit escape disclosure; inspect separately controlled custom/queue/octopus/unsupported source inputs. | Keep the observed scanner pass; do not change its detector/labels. Unsupported source remains unconfirmed. Existing native escape probe does not by itself close full-product source handling. |

## E06 minimum implementation

Keep frozen selector/report schemas unchanged. Version the **complete inspection
profile**, with an approved mapping from workflow, logical job and exact matrix
tuple to one exact platform job name. Preserve the platform name in raw evidence.
GitHub Jobs API does not independently supply structured matrix values, so neither
display-name parsing nor copying selector values into evidence can establish a
cell. Resolve all ordinary run/attempt/check/App identities first.

Credit a runtime cell only after an authenticated artifact is bound to a qualified
fixed producer revision whose reviewed profile establishes that runtime. The
initial `pytest-isolated-container-v1` runtime is Python 3.11.16; a narrow
`python: "3.11"` axis may derive from that qualified profile. It cannot establish
3.12 even when a job is named 3.12. Qualification absence, ambiguous mapping or
an unproven runtime remains a gap. Complete inventory with no matching declared
cell records `required_matrix_cell_missing`. Further runtime values need their
own actual qualification, not a general promise of matrix support.

Profile v1 validation and saved-byte replay remain unchanged. A fixed literal
matrix caller may be used for qualification, but the reusable producer gains no
dynamic caller inputs. Profile v2 also permits the separately validated optional
ledger declaration; neither feature silently changes old profiles.

## Receipt and completion rule

Each variant gets a new receipt directory with immutable product/fixture IDs,
public-access provenance, approved profile bytes, environment/tool hashes,
commands, original HTTP request/response records, source objects, archive and
member hashes, crypto inputs/results, native engine outputs/exits, final recheck,
three axes, readiness and CLI exit. Compare actual output to the preregistered
expectation without rewriting it. Record the exact discrepancy when it fails.

For controlled responses record the untouched baseline digest, mutation bytes,
purpose and boundary exercised. Label actual hosted, native subprocess,
parser/binder control and authored orchestration evidence separately. Reuse is
allowed only for the claim the original evidence establishes. Run all mandatory
variants, aggregate their receipts, and review unresolved deviations before
changing integration status. Frozen validation, local Docker controls and a green
unit suite remain separate evidence. Independent external maintainer reproduction
is still G08; another local agent cannot supply it.
