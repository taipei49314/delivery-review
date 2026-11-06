# delivery-review

Review the evidence for a fixed GitHub pull request under an explicit policy.
The development CLI connects Checkwash, Pinwash and optional Ledgerwash results
with the PR's source identity, GitHub Actions execution and pytest evidence.
Its output supports human review; it does not prove task correctness.

**Current milestone: implementation toward 1.0 (`1.0.0.dev0`), not a stable release.**
The M0 contracts and acceptance specifications remain frozen. Full inspection
collects original GitHub/source/artifact bytes, runs pinned static readers and
composes JSON, Markdown and HTML reports under an independently approved policy.
Offline replay reconstructs source and reparses native streams, including fresh
offline signature verification. The fixed head/merge producer has
[reviewed hosted qualification](qualification/README.md); full E01–E18 product
integration and stable-release qualification remain pending.
An installed development candidate has completed the actual public head/merge
chain and qualified offline replay/refresh; exact sources, remaining corrections
and retained failures are in the [qualification record](docs/qualification/2026-11-06-development.md).

The remaining acceptance criteria and implementation order are tracked in
[RELEASE-1.0.md](docs/RELEASE-1.0.md). Source is licensed under Apache-2.0;
recorded third-party evidence is not automatically relicensed.

**Known release blocker:** the current freshness check relies on PR API
`base.sha`, which stayed stale after an actual target-branch update. A `current`
result cannot yet establish the target branch's current tip in that scenario.
[The round handoff and original evidence](docs/qualification/2026-11-06-round-closeout.md)
describe the required independent branch-reference fix and remaining gates.

The open-source implementation direction is now defined by
[SPEC-001](docs/specs/SPEC-001-github-observations.md) and
[ADR-001](docs/amendments/ADR-001-observation-profile.md): portable GitHub
observations, a standard reference flow, then independent reproduction before
qualification. This profile is deliberately separate from delivery readiness.

## What is implemented

- Full `inspect`, `verify`, `replay` and `refresh` commands with exact source
  snapshots, independent test/producer binding and the unchanged three-axis
  decision contract. [SPEC-009](docs/specs/SPEC-009-full-inspection.md) describes
  the original-evidence envelope, toolchain and replay limits. The bundled
  registry admits only the two reviewed fixed workflow paths at `3d3e545`.
- Six offline JSON Schemas and strict cross-document validation.
- A reference state reducer with independent evidence, freshness and policy axes.
- A separate strict GitHub observation profile and pure run-selection kernel over
  raw listing bytes. It checks pagination, selects by run sequence without
  cherry-picking and preserves association gaps; it does not confirm checkout or
  latest attempt. [Public API probes](docs/specs/SPEC-001-feasibility.md) are
  field research, not successful product integration.
- A bounded GET collector, create-once raw captures, pure observations mapper,
  JSON/Markdown/self-contained HTML output and inspect/verify/replay/refresh commands.
  See [GITHUB-OBSERVATIONS.md](docs/GITHUB-OBSERVATIONS.md) for setup, semantics
  and the limits of these platform observations.
- Create-once local evidence bundles, bounded integrity verification and offline
  replay into a new bundle. See [EVIDENCE-BUNDLES.md](docs/EVIDENCE-BUNDLES.md) for
  the API, commands, format and limitations.
- Forty synthetic acceptance specifications across eighteen scenario families,
  with separately frozen expected results and file hashes.
- Representative synthetic GitHub API response fragments for E01. They are not
  recordings and have not been mapped by a collector.
- Candidate locks for Checkwash 0.10.0, Pinwash 0.1.1 and Ledgerwash 0.5.0.
  Their published wheels were checked by SHA-256, METADATA and RECORD. Public
  CLI probes and pure adapters retain native findings, verdicts and coverage.
  Bounded isolated execution and native adapters are implemented; see
  [SPEC-003](docs/specs/SPEC-003-engine-adapters.md).
- A bounded pytest 8.4.2 xunit2 parser with real authored pytest probes;
  [SPEC-002](docs/specs/SPEC-002-pytest-producer.md) separates parsing from
  producer/artifact authentication and delivery readiness.
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
storage, replay, profiles, HTTP boundaries, static Git objects, subprocess limits,
producer isolation controls and full orchestration with authored inputs. Tests
that mock HTTP, scanner results or signatures say so explicitly. These are not
the real E01–E18 product acceptance runs, and do not query GitHub by default.
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

## Build and install the development CLI

```sh
uv build
python -m venv .install
.install/bin/python -m pip install dist/delivery_review-1.0.0.dev0-py3-none-any.whl
.install/bin/delivery-review --help
.install/bin/delivery-review observations --help
```

On Windows the executables are under `.install/Scripts`. A wheel contains its
offline schemas and formats and can replay outside the source checkout. Live
capture requires a clean source checkout or a build recording a clean source
revision with a consistent installed inventory. A dirty development build can
verify/replay but cannot claim a clean collector revision. The inventory is a
consistency check, not a publisher signature. See [INSTALL.md](docs/INSTALL.md)
for the reproducible development install and scope.

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
| [docs/GITHUB-OBSERVATIONS.md](docs/GITHUB-OBSERVATIONS.md) | Raw capture/mapper and the platform-observation CLI preview. |
| [docs/specs/SPEC-001-github-observations.md](docs/specs/SPEC-001-github-observations.md) | Target raw GitHub capture, observations and replay; implemented subset is stated explicitly. |
| [docs/amendments/ADR-001-observation-profile.md](docs/amendments/ADR-001-observation-profile.md) | Additive observation format and unchanged readiness/release requirements. |
| [docs/PRODUCT-PLAN-v0.1.md](docs/PRODUCT-PLAN-v0.1.md) | Original planning context; frozen contract wins if wording differs. |
| [locks/engine-candidates.json](locks/engine-candidates.json) | Byte-verified engine candidates; not qualified adapters. |
| [STATE.md](STATE.md) | Current implementation status and next acceptance gate. |

## Remaining M1 work

The raw observation path now needs controlled same-repository live qualification
and independent reproduction. Producer qualification, artifact transport, pytest
parsing and engine adapters follow the standard public end-to-end path. Platform
observations and saved normalized replay cannot substitute for their evidence.

This is an independent private incubation repository. Existing Wash engines
remain separately versioned; their source and historical corpus are not vendored.
No open-source license has been selected for this repository yet.
