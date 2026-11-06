# delivery-review

Review the evidence for a fixed GitHub pull request under an explicit policy.
The planned product connects Checkwash, Pinwash and optional Ledgerwash results
with the PR's source identity, GitHub Actions execution and pytest evidence.
Its output supports human review; it does not prove task correctness.

**Current milestone: M1 in progress — local evidence storage and offline replay.**
The M0 contracts and acceptance specifications remain frozen. The first M1 slice
preserves raw bytes and replays saved normalized observations; it does not derive
facts from GitHub responses or authenticate a producer. There is no working PR
inspection CLI yet. All E01–E18 product integrations remain **NOT_RUN**.

## What is implemented

- Six offline JSON Schemas and strict cross-document validation.
- A reference state reducer with independent evidence, freshness and policy axes.
- Create-once local evidence bundles, bounded integrity verification and offline
  replay into a new bundle. See [EVIDENCE-BUNDLES.md](docs/EVIDENCE-BUNDLES.md) for
  the API, commands, format and limitations.
- Forty synthetic acceptance specifications across eighteen scenario families,
  with separately frozen expected results and file hashes.
- Representative synthetic GitHub API response fragments for E01. They are not
  recordings and have not been mapped by a collector.
- Candidate locks for Checkwash 0.10.0, Pinwash 0.1.1 and Ledgerwash 0.5.0.
  Their published wheels were downloaded and checked by SHA-256, METADATA and
  RECORD without installation or execution. Adapter compatibility is NOT_RUN.
- EC pool workload `m0-contracts`, using the existing Windows/Python 3.12
  generation and hash-pinned dependencies. See [CI-POOL.md](docs/CI-POOL.md) for
  dispatch and receipt rules, and [M0-VALIDATION.md](docs/M0-VALIDATION.md) for results.

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

## Run local validation

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

The validator checks the frozen M0 contracts. The unittest suite also exercises
M1 storage and replay with controlled synthetic inputs. These checks do not run
the 40 preregistered scenarios through a complete product, execute engines or
query GitHub.
Wheel acquisition is a separate optional operation documented in
[ENGINE-LOCK.md](docs/ENGINE-LOCK.md), and is not part of ordinary contract CI.

Actions validation is dispatched from the existing
[EC pool workflow](https://github.com/taipei49314/estate-consolidation/actions/workflows/pool-workload.yml)
at a reviewed product commit. EC publishes `ec / m0-contracts` on that commit.
The earlier GitHub-hosted matrix has been removed. A product push alone does
not dispatch the central pool; use the recorded EC admission/dispatch procedure.
This lane qualifies Windows/Python 3.12 only.
Existing successful M0 receipts identify their tested commits; they do not
qualify later local M1 changes.

## Verify and replay a saved bundle

```sh
uv run --locked python tools/evidence.py verify work/source-bundle
uv run --locked python tools/evidence.py replay work/source-bundle work/replayed-bundle
```

`verify` exits 0 for local integrity success, which is not a readiness decision.
A successful `replay` writes a new bundle and exits **2** because freshness is
`unknown`. Its JSON summary distinguishes a written result from an error. The
destination must not exist; the source remains unchanged. To create the source
bundle, use the API and synthetic walkthrough in
[EVIDENCE-BUNDLES.md](docs/EVIDENCE-BUNDLES.md).

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
| [docs/EVIDENCE-BUNDLES.md](docs/EVIDENCE-BUNDLES.md) | Implemented local bundle format, storage API and offline replay. |
| [docs/PRODUCT-PLAN-v0.1.md](docs/PRODUCT-PLAN-v0.1.md) | Original planning context; frozen contract wins if wording differs. |
| [locks/engine-candidates.json](locks/engine-candidates.json) | Byte-verified engine candidates; not qualified adapters. |
| [STATE.md](STATE.md) | Current implementation status and next acceptance gate. |

## Remaining M1 work

The next slice maps raw provider fixtures to observations, then adds a read-only
GitHub provider, source/run/attempt selection, complete pagination, bounded
artifact transport and final live rechecks. A dedicated live fixture PR must
establish the platform relationships independently. Saved normalized-observation
replay is not evidence that these mappings or integrations have run. Engine
subprocess adapters and the qualified pytest producer follow the gates in the
architecture document.

This is an independent private incubation repository. Existing Wash engines
remain separately versioned; their source and historical corpus are not vendored.
No open-source license has been selected for this repository yet.
