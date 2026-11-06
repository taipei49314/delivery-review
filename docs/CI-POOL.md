# M0 CI through the existing EC pool

User instruction on 2026-10-29: `action走ci pool`.
EC admission and execution are recorded under **T-628**. This repository stays
independent; EC supplies execution and receipts for its declared workload.

## Route

1. Review and commit product changes, including `ec-workloads.json`.
2. Use the normal EC task record and check the current admission. The approved
   canonical declaration hash must match the selected product commit.
3. Dispatch `pool-workload.yml` in `taipei49314/estate-consolidation` from its
   reviewed current `main` SHA, with the inputs below.
4. Read the native job outcome, the private receipt and the check on the exact
   product commit. A successful dispatch request alone is not a passing test.

| Input | Value |
| --- | --- |
| `repository` | `taipei49314/delivery-review` |
| `workload` | `m0-contracts` |
| `sha` | Full reviewed product commit SHA. |
| `source_sha` | Full reviewed current EC main SHA. |
| `host` | `any`, or an eligible named workload host selected for this execution. |
| `parameters` | `{}` |

The workload uses `profile-ec-integrity`, one Windows/X64 job, Python 3.12,
no persistent workload cache and a ten-minute limit. The runner remains
registered to EC. It does not need registration or an administrative credential
in this product repository. The existing workload App reads this repository
and writes its checks; workload code receives no App key or token.

The former hosted matrix was removed. Product pushes do not automatically
dispatch the central pool. Each execution follows EC's existing task and
exact-source dispatch procedure; there is no new recurring automation.

## Workload and result

`pool/run_contracts.py` creates a fresh job-local venv and installs binary-only
dependencies from public PyPI using `requirements-contract.txt` hashes. It runs
the frozen contract validator and the complete unittest suite. The test runner
requires at least the existing 51 tests and rejects failures, errors, skips,
expected failures and unexpected successes. A reduction of the baseline needs
explicit review. It also checks the checkout SHA and tracked-source cleanliness.

The complete suite includes the later collector, storage, subprocess, producer
and packaging controls. Its test step may consume up to 480 seconds, subject to
the workload's existing shared 540-second deadline. The admitted ten-minute
outer limit, profile, credentials and declaration hash remain unchanged. The
original 90-second test-step allowance is insufficient for the 527-test candidate;
its timed-out run is retained as failure, not a completed suite result.

EC publishes the native check **`ec / m0-contracts`** on the selected product
commit. Receipt files are retained in EC under
`sweep-receipts/<run>/<attempt>/workload-m0-contracts`:

- `ec-workload.json`: independent EC source, runner, generation and exit metadata.
- `m0-result.json`: workload steps, source/runtime and hashes of generated logs.
- `unittest.json`: actual test counts and outcome.
- `*.stdout.log` and `*.stderr.log`: raw subprocess output.
- `SUMMARY.md`: concise scope and result copied into the native check.

All records identify their actual run and source. A doc-only follow-up commit
does not inherit another commit's check as if it had been independently run.

This lane checks M0 contracts and authored acceptance fixtures. It does not
execute a delivery-review collector, installed Wash scanners or E01–E18 product
integrations. Those stay `NOT_RUN`. Linux and other Python versions also remain
unqualified by a Windows/Python 3.12 pool result.
