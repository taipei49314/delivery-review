# Implementation state

Milestone: **implementation toward complete open-source 1.0**.
M0 contract v1 and preregistered acceptance cases remain frozen.
Product release: **not released** (`1.0.0.dev0` development build).
Required gates and pending evidence: [RELEASE-1.0.md](docs/RELEASE-1.0.md).
Observed development results, exact sources and retained failures:
[2026-11-06 qualification record](docs/qualification/2026-11-06-development.md).
This round's completed work, confirmed base-tip freshness blocker and next steps:
[round closeout](docs/qualification/2026-11-06-round-closeout.md).

| Area | Status |
| --- | --- |
| Six schemas, four examples and semantic/reference state validation | Implemented. |
| Forty authored cases, eighteen families and frozen expected outputs | Implemented; synthetic contract checks only. |
| Actual E01 reference chain | Clean installed `d694175` completed the public hosted producer/artifact/signature/report chain: complete/current/clear, ready, exit 0. Exact-source details are in the development qualification record; later changes require their own checks. |
| Engine wheel SHA-256, METADATA and RECORD verification | Completed for three candidates; see ENGINE-LOCK.md. |
| Local M0 validation on Windows / Python 3.12 | PASS: 51 tests, 40 synthetic cases and 20 frozen files; see M0-VALIDATION.md. |
| EC pool Windows / Python 3.12 CI | `m0-contracts` is the configured route; execution results are the per-commit `ec / m0-contracts` check and EC receipt. |
| Linux CI | No qualified Linux pool lane. Clean native local Linux / official Python 3.11.16 passed all 635 tests, build and installed-package checks at `d694175`; exact-source Windows pool also passed 635 tests. These are separate from hosted product acceptance. |
| Installed engine CLI/JSON adapters | Bounded static execution and native adapters implemented; real pinned CLI probes on Windows/Python 3.12 and Linux/Python 3.11. Full product qualification remains separate. See SPEC-003. |
| Local evidence bundle and offline replay | Implemented for saved normalized observations; see EVIDENCE-BUNDLES.md. No provider mapping or producer authentication. |
| SPEC-001 observation profile | Defined separately from readiness; strict profile schema/validator implemented. See docs/specs/SPEC-001-github-observations.md and ADR-001. |
| Raw workflow-run selection | Pure kernel implemented with authored byte/page controls; not a full provider. Listing attempt and test checkout remain unconfirmed. |
| Public API feasibility | Read-only field probes recorded in docs/specs/SPEC-001-feasibility.md; no product integration qualification. |
| Source-pinned public API acquisition/replay smoke | 42 HTTP 200 responses, 32 native jobs retained, byte-identical offline replays; result partial due to listing PR-association gap. See docs/specs/SPEC-001-live-smoke.md. |
| Raw GitHub capture/reader, bounded GET transport and current-run/attempt/job/check mapping | Implemented with synthetic controls; see docs/GITHUB-OBSERVATIONS.md. Controlled live qualification remains pending. |
| Observation inspect/verify/replay/refresh, final recheck and JSON/Markdown/HTML output | Implemented; raw replay dispatches the saved mapper version. Mapper 2 resolves narrowly qualified listing omissions under ADR-002; mapper 1 output remains byte-identical. No delivery readiness or producer claim. |
| Installable CLI and source provenance | Development sdist/wheel build and an installed observation replay outside the source checkout completed. Bundled offline resources, clean-source identity and inventory controls implemented; exact-release qualification pending. |
| Complete inspection policy identity | ADR-003 and strict profile validator bind acquisition/producer selectors to the frozen policy identity; the composer preserves that approved identity in the full report. |
| Profile v2 acceptance semantics | Explicit matrix/runtime proof, original policy-proposal comparison, declared ledger inputs and frozen negative dispositions implemented with raw controls. Profile v1 replay remains historical. See SPEC-010. |
| Safe static source materialization | Bounded public/authenticated acquisition, exact object validation and offline restoration implemented; real public/private fetch feasibility passed both platforms. Full product source qualification pending. See SPEC-005. |
| Artifact transport | Bounded metadata/ZIP/attestation acquisition, exact raw capture and versioned replay implemented. Signed locators are explicitly omitted; original archives and compressed bundles remain. See SPEC-007. |
| Qualified producer workflow and pytest/JUnit parser | Fixed head/merge paths at `3d3e545` admitted after review of 18 actual hosted controls, offline cryptography and artifact attacks. Digest-bound original public evidence and two registry receipts are in qualification/README.md. Full product acceptance remains separate. |
| Product JSON/Markdown/HTML reports | Full composer, inspection CLI and original-evidence envelope implemented with authored controls on both platforms. Saved normalized and observation-only formats remain distinct. See SPEC-009. |
| E01–E18 product integration | In progress: actual qualified head/merge and offline replay/refresh pass at `d694175`; controlled wrong-App inspection exposed a correction. Required variants and later exact-source validation remain pending. |

The normative baseline is recorded in `acceptance/contract-freeze.json`.
CI results belong to their tested Git commit and workflow run; a green M0 run
does not change the integration statuses above or qualify later local M1 code.
The new storage/replay controls have separate local tests. Their execution
results must be recorded against the tested source; this status table is not
an E17 or end-to-end integration receipt.

The local M0 tested source is `fb4e0ebec0292437a7ed57f57edc3f630f6a63dc`.
The original hosted run
[40831222370](https://github.com/taipei49314/delivery-review/actions/runs/40831222370)
did not start because of GitHub billing restrictions. Per the user's subsequent
instruction, Actions now uses the existing EC pool through `m0-contracts`; see
`docs/CI-POOL.md`. The hosted workflow has been removed, and its old result is
retained as history. A Windows pool pass does not qualify Linux.

Next gates: qualify the installed complete inspection flow, publish and exercise
the standard producer/reference chain on Linux/Python 3.11, and record actual
E01–E18 outcomes. Independent reproduction
remains distinct from local tests. Platform metadata alone cannot satisfy these
obligations. The downloaded run-log ZIPs have no trustworthy per-step file
boundary; log text and artifact names cannot prove the producing job.
Keep expected outputs fixed;
record any necessary contract amendment with its reason and affected cases.

Known boundary: Checkwash #419 remains a documented escape, with a limited
positive E18 expectation. M0 does not repair or expand any engine detector.
