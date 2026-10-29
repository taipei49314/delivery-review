# delivery-review

Review the evidence for a fixed GitHub pull request under an explicit policy.
The planned product connects Checkwash, Pinwash and optional Ledgerwash results
with the PR's source identity, GitHub Actions execution and pytest evidence.
Its output supports human review; it does not prove task correctness.

**Current milestone: M0 — contracts and acceptance specifications.** There is
no working PR inspection CLI yet. The collector, installed engine adapters,
producer workflow, JUnit parser and report renderer are subsequent milestones.
All E01–E18 product integrations are **NOT_RUN**.

## What is implemented

- Six offline JSON Schemas and strict cross-document validation.
- A reference state reducer with independent evidence, freshness and policy axes.
- Forty synthetic acceptance specifications across eighteen scenario families,
  with separately frozen expected results and file hashes.
- Representative synthetic GitHub API response fragments for E01. They are not
  recordings and have not been mapped by a collector.
- Candidate locks for Checkwash 0.10.0, Pinwash 0.1.1 and Ledgerwash 0.5.0.
  Their published wheels were downloaded and checked by SHA-256, METADATA and
  RECORD without installation or execution. Adapter compatibility is NOT_RUN.
- Configured Linux/Python 3.11 and Windows/Python 3.12 contract CI using hashed
  dependencies. Hosted execution is currently blocked by GitHub account billing;
  [M0-VALIDATION.md](docs/M0-VALIDATION.md) records the local results and failed-start run.

## Decisions

| Axis | Values |
| --- | --- |
| Evidence | `complete`, `incomplete`, `error` |
| Freshness | `current`, `stale`, `unknown` |
| Policy | `clear`, `review`, `block` |

Only `complete / current / clear` is `ready_for_review` (exit 0). Complete,
current evidence needing review or blocked by policy exits 1. Every other state
exits 2. A processing error must not conceal a known policy block.

The policy is approved outside the reviewed PR. Runs and artifacts must match
the fixed source, workflow, run and attempt. An old green run cannot replace a
new failed or pending run. A receipt cannot authenticate its own producer.

## Run M0 validation

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are sufficient:

```sh
uv sync --locked
uv run --locked python tools/validate_contracts.py
uv run --locked python -m unittest discover -s tests -v
```

Alternatively, in an activated virtual environment:

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements-contract.txt
python tools/validate_contracts.py
python -m unittest discover -s tests -v
```

These commands check authored contracts and expected normalized reports. They
do not run the 40 scenarios against a product, execute engines or query GitHub.
Wheel acquisition is a separate optional operation documented in
[ENGINE-LOCK.md](docs/ENGINE-LOCK.md), and is not part of ordinary contract CI.

## Repository map

| Path | Purpose |
| --- | --- |
| [docs/CONTRACT.md](docs/CONTRACT.md) | Normative identities, trust boundaries, state and exit rules. |
| [schemas/](schemas/) | Machine-readable contract v1. |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | E01–E18 scenarios and the integration evidence still required. |
| [acceptance/manifest.json](acceptance/manifest.json) | Frozen case bytes and expected-output digests. |
| [acceptance/contract-freeze.json](acceptance/contract-freeze.json) | M0 normative-file inventory and digests. |
| [fixtures/cases/](fixtures/cases/) | Forty preregistered synthetic normalized reports. |
| [fixtures/github/](fixtures/github/) | Authored provider fragments; mapping is NOT_RUN. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component boundaries, reuse and milestones. |
| [docs/PRODUCT-PLAN-v0.1.md](docs/PRODUCT-PLAN-v0.1.md) | Original planning context; frozen contract wins if wording differs. |
| [locks/engine-candidates.json](locks/engine-candidates.json) | Byte-verified engine candidates; not qualified adapters. |
| [STATE.md](STATE.md) | Current implementation status and next acceptance gate. |

## Next milestone

M1 implements a read-only GitHub provider and immutable evidence bundles, starting
with raw fixture replay, then a dedicated live fixture PR. It must independently
establish source/run/attempt identities and preserve raw bytes before a result
can be considered product integration evidence. Engine subprocess adapters and
the qualified pytest producer follow the gates in the architecture document.

This is an independent private incubation repository. Existing Wash engines
remain separately versioned; their source and historical corpus are not vendored.
No open-source license has been selected for this repository yet.
