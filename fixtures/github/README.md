# Synthetic GitHub response fragments

These five JSON files are authored, reduced API-shaped fragments for the
E01 happy-head contract. They contain fictional repository, PR, run, job and
artifact identities. They were not fetched from GitHub and are not official
GitHub API schemas or evidence of an execution.

| File | Intended observation |
| --- | --- |
| `pr-start.json` | PR head/base repository identity and source SHAs. |
| `workflow-run-attempt1.json` | Selected run/attempt and referenced workflow SHA. |
| `jobs-attempt1.json` | Required pytest job in that attempt. |
| `artifacts.json` | Claimed artifact metadata belonging to the run. |
| `merge-commit.json` | Candidate merge commit with ordered base/head parents. |

Repeated-character SHAs and the artifact digest are placeholders, not hashes of
these fixture bytes or a supplied archive. The M0 freeze records the actual
fixture-file digests separately. The normalized examples use their own authored
placeholder digests; they are not derived from these fragments.

Collector mapping, job/check/App confirmation, producer repository resolution,
artifact download/verification, receipt/JUnit parsing, pagination and the final
live recheck are **NOT_RUN**. These fragments do not form a complete evidence
bundle. M1 must supply the remaining inputs and test actual derived outputs.
