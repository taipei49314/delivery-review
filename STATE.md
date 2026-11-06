# Implementation state

Milestone: **M1 in progress — local evidence storage and offline replay**.
M0 contract v1 and preregistered acceptance cases remain frozen.
Product release: **not released** (`0.0.0` source-checkout tooling).

| Area | Status |
| --- | --- |
| Six schemas, four examples and semantic/reference state validation | Implemented. |
| Forty authored cases, eighteen families and frozen expected outputs | Implemented; synthetic contract checks only. |
| E01 representative GitHub fragments | Authored; collector mapping NOT_RUN. |
| Engine wheel SHA-256, METADATA and RECORD verification | Completed for three candidates; see ENGINE-LOCK.md. |
| Local M0 validation on Windows / Python 3.12 | PASS: 51 tests, 40 synthetic cases and 20 frozen files; see M0-VALIDATION.md. |
| EC pool Windows / Python 3.12 CI | `m0-contracts` is the configured route; execution results are the per-commit `ec / m0-contracts` check and EC receipt. |
| Linux CI | NOT_RUN; no qualified Linux pool lane for this repository. |
| Installed engine CLI/JSON adapters | NOT_RUN. |
| Local evidence bundle and offline replay | Implemented for saved normalized observations; see EVIDENCE-BUNDLES.md. No provider mapping or producer authentication. |
| GitHub collector, run/attempt selection, artifact transport and final live recheck | Not implemented. |
| Qualified producer workflow and pytest/JUnit parser | Not implemented. |
| Product JSON/Markdown/HTML reports | Replay emits a contract-v1 JSON report; live report flow and Markdown/HTML rendering are not implemented. |
| E01–E18 product integration | NOT_RUN. |

The normative baseline is recorded in `acceptance/contract-freeze.json`.
CI results belong to their tested Git commit and workflow run; a green M0 run
does not change the integration statuses above or qualify later local M1 code.
The new storage/replay controls have separate local tests. Their execution
results must be recorded against the tested source; this status table is not
an E17 or end-to-end integration receipt.

The local tested source is `fb4e0ebec0292437a7ed57f57edc3f630f6a63dc`.
The original hosted run
[40831222370](https://github.com/taipei49314/delivery-review/actions/runs/40831222370)
did not start because of GitHub billing restrictions. Per the user's subsequent
instruction, Actions now uses the existing EC pool through `m0-contracts`; see
`docs/CI-POOL.md`. The hosted workflow has been removed, and its old result is
retained as history. A Windows pool pass does not qualify Linux.

Next gate: derive observations from raw M1 provider fixtures, then implement
the read-only GitHub provider. Qualify identity, pagination, latest attempt
selection, artifact transport and snapshot drift against actual derived
outputs. Online refresh remains unimplemented. Keep expected outputs fixed;
record any necessary contract amendment with its reason and affected cases.

Known boundary: Checkwash #419 remains a documented escape, with a limited
positive E18 expectation. M0 does not repair or expand any engine detector.
