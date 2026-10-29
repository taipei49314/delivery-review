# M0 validation record

Date: 2026-10-29. Repository: `taipei49314/delivery-review` (private).
Tested source: [`fb4e0ebec0292437a7ed57f57edc3f630f6a63dc`](https://github.com/taipei49314/delivery-review/commit/fb4e0ebec0292437a7ed57f57edc3f630f6a63dc).
The follow-up commit only records these results in README, STATE and this file.

## Local result: PASS for M0 contract checks

Environment: Windows, CPython 3.12.10, pinned virtual environment from `uv.lock`.

```text
python tools/validate_contracts.py
M0 contract consistency: 6 schemas, 4 examples, 40 synthetic cases valid.
M0 baseline: 20 frozen files verified.
Live collector/scanners and E01-E18 product integration: NOT_RUN.

python -m unittest discover -s tests -q
Ran 51 tests
OK
```

The suite includes the 40 preregistered case subchecks, negative controls for
source/run/attempt and policy invariants, frozen-file mutation detection, and
eleven wheel-verification tests. These checks do not execute a GitHub collector,
an installed Wash engine, a producer workflow, a JUnit parser or a product
scenario-to-report mapping. Product integration remains **NOT_RUN**.

The three published engine wheels were separately downloaded and verified by
archive SHA-256, byte length, METADATA identity and RECORD inventory. Their
candidate receipts are in `locks/engine-candidates.json`; installation and
adapter qualification are **NOT_RUN**. See `ENGINE-LOCK.md` for reproduction.

## Hosted result: BLOCKED_EXTERNAL

Workflow: `M0 contracts`. Intended matrix: Ubuntu/Python 3.11 and
Windows/Python 3.12.

[Run 40831222370](https://github.com/taipei49314/delivery-review/actions/runs/40831222370)
targets the tested source above. GitHub created both jobs, then refused to start
their runners. Both have empty step lists. GitHub's annotation reports failed
recent account payments **or** a spending limit that needs adjustment; it does
not establish which cause applies. This is not a test failure, a successful CI
run or verification on either hosted operating system.

The earlier initial run failed to parse an unquoted YAML command containing
`--only-binary=:all:`. The tested source fixes this with a folded YAML scalar;
the second run's populated job matrix confirms workflow parsing succeeded.

No billing settings, spending limits or repository visibility were changed.
This run is retained as history. The user subsequently instructed
`action走ci pool`; the hosted workflow was removed. Use the EC route below for
current validation. Do not substitute a local or Windows pool result for
hosted cross-platform validation.

## Current route: EC pool

T-628 admits `m0-contracts` through the existing EC Windows/Python 3.12 pool.
Its entry uses the same pinned dependencies, frozen contracts and full unittest
suite, with raw logs and structured counts in private EC receipts. The native
`ec / m0-contracts` check identifies the exact product commit and links to its
actual Actions run. See [CI-POOL.md](CI-POOL.md) for the procedure and record paths.

Pool execution results are attached to the tested commit by EC, rather than
rewriting this historical local/hosted record. Require a successful native run,
matching source and passing raw workload receipt before accepting a pool result.
Product integrations and Linux qualification remain NOT_RUN.

## Remaining implementation gates

- M1: actual provider observations, immutable raw bundles, replay and freshness.
- Engine adapters: pinned subprocess execution and public JSON compatibility.
- Qualified producer and pytest/JUnit parsing, including artifact/source binding.
- Actual E01–E18 integrations and reviewable JSON/Markdown/HTML output.

The scope completed here is the independent repository and reviewable M0
contract/acceptance baseline. The planned v0.1 product is not released.
