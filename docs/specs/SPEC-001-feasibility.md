# SPEC-001: public API field feasibility

Recorded 2026-11-06. Scope: bounded, read-only API research. This is not a
delivery-review provider execution, a controlled live fixture run, or E01-E18
qualification. No workflow was triggered and no artifact was downloaded.

## Public observations

| Source | What was observed | Consequence |
| --- | --- | --- |
| [Checkwash PR 587](https://github.com/taipei49314/checkwash/pull/587) | Repo ID 1320152564; head `c51d47620380a05af1a16fca628da93daf91a94e`, base `003e12dd5d447ed45e4ffd5658f331a8541ffbfb`. Git commit API returns merge `7cd8549c5a556a82c967e74cfae46f1a86053abc` with these ordered parents. | The commit relationship is observable; it does not prove workflow checkout. This merged PR is historical. |
| [Checkwash intake run 41913448234](https://github.com/taipei49314/checkwash/actions/runs/41913448234) | Workflow 353769343, `.github/workflows/quality-candidate-intake.yml`, event `workflow_dispatch`, run number 42, attempt 1. Head is the PR base; PR and referenced-workflow arrays are empty. | A title mentioning PR/head is not source proof. This execution is outside the PR-event/head profile. |
| [Attempt-1 jobs](https://api.github.com/repos/taipei49314/checkwash/actions/runs/41913448234/attempts/1/jobs?per_page=100) | Six jobs expose IDs, run/attempt/head and check URLs; three success, three skipped. | Attempt-specific jobs and independent check reads are viable inputs. |
| [PR-head checks](https://api.github.com/repos/taipei49314/checkwash/commits/c51d47620380a05af1a16fca628da93daf91a94e/check-runs?per_page=100) | Four successful checks, App 15368, suite 113579203520. Intake suite is 113578786999. Head-filtered runs returned zero. | Same App and green names cannot associate these checks with the intake run. |
| [Checkwash artifacts](https://api.github.com/repos/taipei49314/checkwash/actions/runs/41913448234/artifacts?per_page=100) | Three metadata objects expose digest, expiry and workflow_run identity, with no job ID or attempt field. | Run-level metadata cannot independently prove attempt/job association or downloaded-byte integrity. |
| [pytest-dev/pytest run 42140548288](https://github.com/pytest-dev/pytest/actions/runs/42140548288) | Event `pull_request`, workflow 22158026, `.github/workflows/test.yml`, run number 11915, attempt 1. Base repo ID 37489525, fork head repo ID 1311593321. PR and referenced-workflow arrays are empty. | Missing PR association is possible for a PR-event run. Preserve unconfirmed association. This fork is outside the first same-repository profile. |
| [pytest attempt jobs](https://api.github.com/repos/pytest-dev/pytest/actions/runs/42140548288/attempts/1/jobs?per_page=100) and [artifacts](https://api.github.com/repos/pytest-dev/pytest/actions/runs/42140548288/artifacts?per_page=100) | 32 jobs consistently expose run/attempt/head/check URL; 30 success, two failure. Three artifacts lack job/attempt fields. | Confirms metadata shapes on an independent public project, not source or producer qualification. |

## Transport and byte provenance

The connector returned 15 saved responses, including two allowlist errors for
individual check-run routes. These are not GitHub 404s or missing platform checks.
A local `gh api --include` GET to
[`/repos/taipei49314/checkwash/check-runs/126866777842`](https://api.github.com/repos/taipei49314/checkwash/check-runs/126866777842)
then returned HTTP 200: App 15368, intake suite 113578786999 and head
`003e12dd5d447ed45e4ffd5658f331a8541ffbfb`. That resolves the connector capability
gap. Its exact gh response body was saved: 3,072 bytes, SHA-256
`8943f1e6b84a4276f2b222e3ff51f12fb4bc01b612caf11f1190697b6b279f8c`.
Selected response headers were saved separately; no token, authorization header
or raw transport stderr was retained.

Research files remain in ignored `work/spec-001-probe/`. Connector files are
explicitly reconstructed connector JSON, not original HTTP response bytes. Their
manifests record file hashes, request URLs and available retrieval times; missing
retrieval times remain null. No Link headers were captured by the connector, so
those files cannot qualify pagination or serve as sealed SPEC-001 captures. They
are not committed as redistributable fixtures. The gh body has a separate byte
representation and metadata record; it is not substituted for a connector result.

## Supported decisions and remaining gate

These reads support a portable GET transport, explicit IDs, attempt-specific jobs,
independent checks and conservative handling of missing associations. They do not
establish all relationships needed for workflow-bound evidence. Artifact names
and self-reported receipts cannot close those gaps.

Still unqualified: controlled same-repository PR-event happy path, complete
multi-page capture, rate/permission handling, original-response retention by the
product, current-run/latest-attempt validation, final recheck, full raw capture
replay, artifact download, receipt/JUnit and external reproduction. No complete
SPEC-001 gate or E01-E18 product obligation is closed by this probe.
