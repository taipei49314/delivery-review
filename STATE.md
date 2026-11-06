# Implementation state

Milestone: **implementation toward complete open-source 1.0**.
M0 contract v1 and preregistered acceptance cases remain frozen.
Product release: **not released** (`1.0.0.dev0` development build).
Required gates and pending evidence: [RELEASE-1.0.md](docs/RELEASE-1.0.md).

| Area | Status |
| --- | --- |
| Six schemas, four examples and semantic/reference state validation | Implemented. |
| Forty authored cases, eighteen families and frozen expected outputs | Implemented; synthetic contract checks only. |
| E01 representative GitHub fragments | Authored; collector mapping NOT_RUN. |
| Engine wheel SHA-256, METADATA and RECORD verification | Completed for three candidates; see ENGINE-LOCK.md. |
| Local M0 validation on Windows / Python 3.12 | PASS: 51 tests, 40 synthetic cases and 20 frozen files; see M0-VALIDATION.md. |
| EC pool Windows / Python 3.12 CI | `m0-contracts` is the configured route; execution results are the per-commit `ec / m0-contracts` check and EC receipt. |
| Linux CI | NOT_RUN; no qualified Linux pool lane. A local Ubuntu 24.04 WSL2 / Python 3.11.16 environment now runs focused development checks; full qualification remains pending. |
| Installed engine CLI/JSON adapters | Three hash-pinned wheels exercised through public CLIs on authored Windows/Python 3.12 probes; pure adapters implemented. Bounded execution integration and Linux qualification pending. See SPEC-003. |
| Local evidence bundle and offline replay | Implemented for saved normalized observations; see EVIDENCE-BUNDLES.md. No provider mapping or producer authentication. |
| SPEC-001 observation profile | Defined separately from readiness; strict profile schema/validator implemented. See docs/specs/SPEC-001-github-observations.md and ADR-001. |
| Raw workflow-run selection | Pure kernel implemented with authored byte/page controls; not a full provider. Listing attempt and test checkout remain unconfirmed. |
| Public API feasibility | Read-only field probes recorded in docs/specs/SPEC-001-feasibility.md; no product integration qualification. |
| Source-pinned public API acquisition/replay smoke | 42 HTTP 200 responses, 32 native jobs retained, byte-identical offline replays; result partial due to listing PR-association gap. See docs/specs/SPEC-001-live-smoke.md. |
| Raw GitHub capture/reader, bounded GET transport and current-run/attempt/job/check mapping | Implemented with synthetic controls; see docs/GITHUB-OBSERVATIONS.md. Controlled live qualification remains pending. |
| Observation inspect/verify/replay/refresh, final recheck and JSON/Markdown/HTML output | Implemented; raw replay dispatches the saved mapper version. Mapper 2 resolves narrowly qualified listing omissions under ADR-002; mapper 1 output remains byte-identical. No delivery readiness or producer claim. |
| Installable CLI and source provenance | Development sdist/wheel build and an installed observation replay outside the source checkout completed. Bundled offline resources, clean-source identity and inventory controls implemented; exact-release qualification pending. |
| Complete inspection policy identity | ADR-003 and strict profile validator bind acquisition/producer selectors to the frozen policy identity. This does not yet compose a full readiness report. |
| Safe static source materialization | Local canonical Git objects can be independently hashed and materialized without checkout/hooks/filters. Remote acquisition and repository membership qualification pending. See SPEC-005. |
| Artifact transport | Not implemented. |
| Qualified producer workflow and pytest/JUnit parser | Bounded pytest 8.4.2 xunit2 parser implemented with real authored pytest probes. Hosted producer and independent artifact/job binding remain pending. See SPEC-002 and SPEC-004. |
| Product JSON/Markdown/HTML reports | Saved-report replay emits contract-v1 JSON. Observation JSON/Markdown/HTML are separate; full delivery composition remains pending. |
| E01–E18 product integration | NOT_RUN. |

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

Next gates: finish bounded source/scanner/artifact execution, implement the
isolated producer and verified attestation chain, then compose reports and
qualify the public reference flow on Linux/Python 3.11. Independent reproduction
remains distinct from local tests. Platform metadata alone cannot satisfy these
obligations. The downloaded run-log ZIPs have no trustworthy per-step file
boundary; log text and artifact names cannot prove the producing job.
Keep expected outputs fixed;
record any necessary contract amendment with its reason and affected cases.

Known boundary: Checkwash #419 remains a documented escape, with a limited
positive E18 expectation. M0 does not repair or expand any engine detector.
