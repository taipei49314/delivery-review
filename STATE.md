# Implementation state

Milestone: **M0 — contract v1 and preregistered acceptance cases**.
Product release: **not released** (`0.0.0` source-checkout validation tooling).

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
| GitHub collector, evidence bundle/replay and final live recheck | Not implemented. |
| Qualified producer workflow and pytest/JUnit parser | Not implemented. |
| Product JSON/Markdown/HTML reports | Not implemented. |
| E01–E18 product integration | NOT_RUN. |

The normative baseline is recorded in `acceptance/contract-freeze.json`.
CI results belong to their tested Git commit and workflow run; a green M0 run
does not change the integration statuses above.

The local tested source is `fb4e0ebec0292437a7ed57f57edc3f630f6a63dc`.
The original hosted run
[40831222370](https://github.com/taipei49314/delivery-review/actions/runs/40831222370)
did not start because of GitHub billing restrictions. Per the user's subsequent
instruction, Actions now uses the existing EC pool through `m0-contracts`; see
`docs/CI-POOL.md`. The hosted workflow has been removed, and its old result is
retained as history. A Windows pool pass does not qualify Linux.

Next gate: implement the M1 provider and immutable evidence store. Qualify
identity, pagination, latest attempt selection, raw inventory integrity and
snapshot drift against actual derived outputs. Keep expected outputs fixed;
record any necessary contract amendment with its reason and affected cases.

Known boundary: Checkwash #419 remains a documented escape, with a limited
positive E18 expectation. M0 does not repair or expand any engine detector.
