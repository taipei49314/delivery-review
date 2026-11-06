# Pinned engine regression observations

On 2026-11-06, 46 native CLI executions exercised the installed, pinned engines
and the real `delivery_review.engine_runner` and adapters on Linux / Python
3.11.16. All 46 original outputs and finding identities were preserved. The
predeclared comparisons matched in 44 cases; two authored Ledgerwash coverage
expectations were wrong and remain recorded as mismatches. This is a development
regression observation, not a full product acceptance or independent accuracy
result.

A separately documented version-2 expectation amendment corrected only those
two authored absence labels. Both reran successfully with the same product
module hashes, engine pins and source inventories; the original 44/46 receipt
remains unchanged. The evidence consists of the original 46-case run plus a
separate two-case correction, not a new all-pass 46-case receipt.

## Source and execution identity

The run used the three wheel versions and SHA-256 pins in
[ENGINE-LOCK.md](ENGINE-LOCK.md): Checkwash 0.10.0, Pinwash 0.1.1 and Ledgerwash
0.5.0. Each case called the public CLI through a fresh isolated environment,
with native threshold `high` and evaluation date `2026-11-06`. The six
Ledgerwash controls also compare default and `require_complete` policy.

The tested product modules were byte-identical to commit
`8bb4337d5c7f643e10acd56b5fe1fc28a542fdd8`. The new qualification harness was
uncommitted during execution; the working tree was therefore not a clean
release candidate. Its exact bytes, the case selection and individual product
module hashes are retained with the run. The platform was Ubuntu 24.04 under
WSL2, Linux x86-64, Python 3.11.16. This does not qualify another platform.

| Retained identity | SHA-256 |
| --- | --- |
| `receipt.json` | `d2b3abd91da0bc70ed0e4eaabbc5d9ff9e39bf08df4eb875c8278658181422e0` |
| `inventory.json` | `43f20951ff351bd1dc61e11374de36497f52e5183fc0b5f4581eb9523259fea1` |
| `source-context.json` | `7f75adeff62a315c96069038a133505747d6db4a334d784d6334a8189d78176b` |
| Original `runner.py` | `0e0db040032b8b92822e36984b95fd680e4883d6c60c7051af16eaf4cedb1764` |
| `selection.json` | `c2afdb70fef2f36115338050b60f72f17a3a9bdc757549dd5b086c9b5fd1cdcd` |

Local raw evidence is in the ignored directory
`work/engine-qualification/regression-linux-001/`. Its inventory covers 1,702
files and was independently rehashed after execution. It includes original
upstream bytes, acquisition URLs and hashes, each case's original expectation,
canonical and loose Git objects, source inventory, installation and process
manifests, native stdout/stderr, coverage output and adapter observation. The
receipt records completion at `2026-11-06T06:46:54.040701+00:00`.

The hashes identify local observations; they are not signatures, a hosted
qualification or a claim that uncommitted code has been independently released.

## Public fixture provenance and labels

Thirty cases were selected before execution from the public Checkwash source
at `100699796d942af30644738ffbcd1f1c939411c5` (`v0.10.0`). The
[selection manifest](../tools/qualification/engine_regression_cases.json) lists
each upstream path, original Git blob identity and length. Anonymous HTTPS
downloads from the fixed source commit were checked against those identities;
original bytes and SHA-256 digests remain outside the tracked repository. The
upstream [Apache-2.0 license](https://github.com/taipei49314/checkwash/blob/100699796d942af30644738ffbcd1f1c939411c5/LICENSE)
is retained with the download.

An anonymous repository API check also returned `private: false` and
`visibility: public`, and an anonymous exact-commit fixture re-download matched
the original bytes on 2026-11-06. The API response, fixture and receipt are
retained in `work/engine-qualification/public-regression-availability/`.
Neither check used credentials or environment proxies; a URL or authenticated
connector response alone was not treated as proof of public availability.

Selection covers Python assertion removal, weakening, substitution, expected
values, tolerance, skip and exception handling; CI triggers, steps, guardrail
configuration and exemptions; JavaScript assertions, exceptions and test
registration; refactors, stronger checks and an opaque-source residual. The
GitHub directory listing was capped at 1,000 names. This is a purposeful subset,
not a full corpus inventory, random sample or held-out benchmark.

The first-party harness reads only inert `.gwcase` sections. It constructs exact
Git snapshots from `before` and `after`; unchanged `head` context is present on
both sides. Deleted and added files remain distinct. It refuses task/environment
overrides and ambiguous paths. The subjects' tests, scripts and dependencies
are never executed or installed. The upstream
[fixture runner](https://github.com/taipei49314/checkwash/blob/100699796d942af30644738ffbcd1f1c939411c5/tests/test_cases_runner.py)
and [format implementation](https://github.com/taipei49314/checkwash/blob/100699796d942af30644738ffbcd1f1c939411c5/src/checkwash/cases.py)
were consulted and retained as provenance, not imported. Their original
in-memory test interface differs from this public-CLI execution, so the
materialization method is recorded with each observation.

All 30 explicit upstream `expect` lists matched the resulting native findings;
all 30 adapter executions completed. Matching requires the same number of
visible findings, distinct matching rows and the declared scalar fields;
expected list fields are subset constraints, as in the upstream runner. The
raw native result, every adapter native row and the normalized rule, severity
and fingerprint were checked separately for preservation.

Filename polarity and prose are not substituted for explicit expectations:

- `assert_removed_moved_neg` has prose suggesting an informational refactor,
  but its pinned `expect` requires `ASSERT_REMOVED` at `high`; the actual CLI
  blocks. This is a retained upstream-label discrepancy, not an independently
  confirmed clean refactor.
- `expected_hardcoded_pos` expects warning findings with repair credit and the
  actual CLI passes at `high`. A `_pos` suffix does not mean the native verdict
  must block.
- Seven fixtures have empty native finding expectations for reordering,
  renaming, formatting, re-raising, tighter tolerance or equivalent JavaScript
  spelling. These all completed with no findings. Their upstream authorship
  remains the label source; this run does not prove semantic equivalence.

No private PR data, customer source, internal transcripts or historical agent
reconstructions were used. These previously authored upstream cases provide
regression coverage, not independent evidence of scanner accuracy.

## Observed behavior

| Group | Cases | Native observation and adapter result |
| --- | ---: | --- |
| Public Checkwash `.gwcase` subset | 30 | All declared finding expectations matched; all adapters completed. |
| Checkwash and Pinwash weakening seeds | 2 | Both native CLIs blocked; original findings retained. |
| Valid BASE exemptions | 2 | Both passed. Checkwash retained an `allowlisted` warning row; Pinwash emitted no finding. The wrapper preserved both native behaviors. |
| New HEAD exemptions | 2 | Both blocked with the weakening and `EXEMPTION_ADDED`; both adapters set review required. |
| Authored Pinwash production refactor | 1 | Native pass, no findings, adapter completed. |
| Invalid Pinwash BASE configuration | 1 | Native exit 0 / pass was retained, while the adapter correctly reported `error`, `not_judged` and `native_configuration_error`. |
| Authored Checkwash #419 routes | 2 | Both native CLIs passed with a warning `ASSERT_WEAKENED` carrying `REPAIR_EVIDENCE`; preserved without relabeling. |
| Absent Ledgerwash input, default and strict | 2 | Native pass / block respectively; adapter status and coverage both `not_provided`. Strict native block remained known. These two authored comparisons mismatch as explained below. |
| Malformed Ledgerwash input, default and strict | 2 | Both blocked with `RECORD_UNPARSEABLE`; coverage gaps retained. |
| Partial Ledgerwash input, default and strict | 2 | Native pass / block respectively; coverage gaps retained. |

There were 35 Checkwash, five Pinwash and six Ledgerwash executions. Across all
46, native verdicts were 27 block and 19 pass. Adapter statuses were 43 completed,
one intentional configuration error and two `not_provided`; an absent optional
input was never represented as cleanly reviewed evidence.

The two mismatches are `ledgerwash-absent` and `ledgerwash-absent-strict`. The
authored harness incorrectly predeclared `coverage: gaps` for every Ledgerwash
control. The actual `coverage: not_provided` agrees with
[CONTRACT.md](CONTRACT.md)'s conditional-ledger rule. Their native exits,
verdicts and adapter statuses matched; the original receipts and expectations
remain unchanged and the overall result remains **44/46**, not an all-pass
claim.

The explicit [version-2 amendment](../tools/qualification/engine_regression_amendment_v2.json)
records the rationale, the original runner/receipt/input hashes, old outputs,
and the two corrected expectations. It changes no frozen fixture, product code,
engine pin, upstream expectation or contract. The targeted rerun in
`work/engine-qualification/regression-ledger-amendment-002/` completed at
`2026-11-06T06:56:23.291352+00:00`: two comparisons matched and both native
results were preserved. Its source inventories and observed outcomes are
identical to the two original cases; native stdout differs only in the temporary
`run.target` path. Selected source-file hashes were unchanged between start and
end and the three product module hashes match the original run.

| Amendment evidence | SHA-256 |
| --- | --- |
| `expectation-amendment-v2.json` | `9bbe3aefaf7b29de1088fd8d6e2c778917665d424632cb247306664f4c893f94` |
| Two-case `receipt.json` | `39ccccde8a3572ec2ea3cdcab944c80d1a8bdd7fd7ea5dc0398c8d3e16f4edfc` |
| Two-case `inventory.json` | `4026e626dfa978eec1012afa6277acf8995fd1fa4738f2035db8652d4e82f9ee` |
| Version-2 `runner.py` | `c9904df35bd5d5f48811c572af9eccb44f988b2137a6e3bd05e2f95121916c17` |

Independent review also strengthened future harness comparisons: public cases
must have completed adapter status, and preservation checks now compare the
adapter verdict with the native verdict when completed or `not_judged`
otherwise. A separate read-only audit of the original raw observations confirmed
all 30 public cases completed and all 46 adapter verdict mappings were preserved.
That audit is `work/engine-qualification/regression-original-audit-001.json`,
SHA-256 `f364a95905b030e6bb20c08aa986e42ad992c746e57f1ebedea2a8512cc7f754`.
It does not rewrite the original expectations or count. Six harness controls
pass on Windows/Python 3.12 and Linux/Python 3.11; these helper tests do not add
native or product integration executions.

## Checkwash #419 disclosure

The two executed controls weaken `assert compute_total([1, 2]) == 3` to `> 0`.
One also adds `+ 0` to the reached production function; the other changes only
a comment in an existing HTML production file. Checkwash 0.10.0 assigns
`REPAIR_EVIDENCE`, emits `ASSERT_WEAKENED` at `warn` and returns native pass in
both cases. The adapter retains that exact verdict, fingerprint and finding.
Native pass therefore cannot establish that weakened tests remain adequate.

Upstream [issue #419](https://github.com/taipei49314/checkwash/issues/419) was
closed as `not_planned`, with the remaining behavior documented as a heuristic
limitation. The [closing clarification](https://github.com/taipei49314/checkwash/issues/419#issuecomment-6513378276)
does not claim the residual bypass was fixed. This run reproduces two authored
routes with the pinned native CLI; it does not broaden that observation to all
rules or claim execution of the issue's original test harness.

These observations support the native-engine portions of E13 and E18. Full
report composition, freshness, authentic producer binding and unsupported
source handling are separate product controls. Their E01–E18 integration
statuses are not promoted by this regression run.

## Reproduction

From a Linux source checkout, first create the approved toolchain as described
in [INSTALL.md](INSTALL.md). Use new, nonexistent output directories; the
harness refuses overwrites:

```sh
python tools/qualification/engine_regression.py acquire \
  --out work/public-regression-source
python tools/qualification/engine_regression.py run \
  --source-cache work/public-regression-source \
  --tools work/approved-tools/toolchain.json \
  --out work/native-regression-run
```

The acquisition needs public HTTPS access. The run uses the already downloaded,
hash-checked wheels and operator Python/Git from `toolchain.json`; source
materialization and engine execution do not fetch subject dependencies. The
temporary snapshots must reside on a filesystem supporting the verified
Unix modes, rather than a Windows-mounted WSL directory. Raw evidence is saved
under the explicit output path before temporary environments are removed.

The original run's exact harness is retained as `runner.py`; later changes to
the tracked harness do not change that historical observation. Inspect the
receipt's counts, mismatches and limitations: successfully writing a receipt
or exiting the harness normally is not an all-pass qualification verdict.
To repeat only the documented correction, pass
`--case-set ledger-absence-amendment` to `run` with a new output directory.
