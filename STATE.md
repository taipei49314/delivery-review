# Implementation state

Milestone: **M0 — contract v1 and preregistered acceptance cases**.
Product release: **not released** (`0.0.0` source-checkout validation tooling).

| Area | Status |
| --- | --- |
| Six schemas, four examples and semantic/reference state validation | Implemented. |
| Forty authored cases, eighteen families and frozen expected outputs | Implemented; synthetic contract checks only. |
| E01 representative GitHub fragments | Authored; collector mapping NOT_RUN. |
| Engine wheel SHA-256, METADATA and RECORD verification | Completed for three candidates; see ENGINE-LOCK.md. |
| Installed engine CLI/JSON adapters | NOT_RUN. |
| GitHub collector, evidence bundle/replay and final live recheck | Not implemented. |
| Qualified producer workflow and pytest/JUnit parser | Not implemented. |
| Product JSON/Markdown/HTML reports | Not implemented. |
| E01–E18 product integration | NOT_RUN. |

The normative baseline is recorded in `acceptance/contract-freeze.json`.
CI results belong to their tested Git commit and workflow run; a green M0 run
does not change the integration statuses above.

Next gate: implement the M1 provider and immutable evidence store. Qualify
identity, pagination, latest attempt selection, raw inventory integrity and
snapshot drift against actual derived outputs. Keep expected outputs fixed;
record any necessary contract amendment with its reason and affected cases.

Known boundary: Checkwash #419 remains a documented escape, with a limited
positive E18 expectation. M0 does not repair or expand any engine detector.
