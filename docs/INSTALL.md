# Development installation and release provenance

There is no qualified stable 1.0 release yet. Build from a reviewed revision;
do not assume a similarly named package on an index is this project's release.
Linux/Python 3.11 is the initial product target. Record exact Python, OS, source
and artifact hashes when reproducing a qualification.

## Build

From a clean checkout with Python >=3.11 and uv:

```sh
uv sync --locked
uv run --locked python tools/validate_contracts.py
uv run --locked python -m unittest discover -s tests -v
uv build
uv export --locked --no-emit-project --format requirements-txt --output-file work/runtime-requirements.txt
```

The build backend is pinned in `pyproject.toml`. Source distributions retain
original schema bytes and source-file hashes. Wheels bundle the same definitions
inside the package; they never fetch JSON Schema references. The build records
the clean source commit when available and hashes its installed Python/JSON
files. Unverified source archives and dirty trees deliberately produce an
unverified build identity. Copying a manifest does not authenticate its publisher.

## Install outside the checkout

```sh
python -m venv work/install
work/install/bin/python -m pip install --require-hashes --only-binary=:all: -r work/runtime-requirements.txt
work/install/bin/python -m pip install --no-deps dist/delivery_review-1.0.0.dev0-py3-none-any.whl
work/install/bin/delivery-review --version
work/install/bin/delivery-review observations --help
```

Windows uses `Scripts/python.exe` and `Scripts/delivery-review.exe`. The pinned
runtime dependency export comes from the source's `uv.lock`. A plain pip install
also works but resolves transitive dependencies at installation time and is not
the exact qualification environment.

## Prepare inspection tools

With Git on the operator's PATH, run this command from the installed verifier's
Python 3.11 or 3.12 environment on Linux amd64 or Windows amd64:

```sh
delivery-review tools install --out review-tools
delivery-review inspect https://github.com/OWNER/REPO/pull/NUMBER --policy approved-profile.json --tools review-tools/toolchain.json --out first-review
delivery-review replay first-review --tools review-tools/toolchain.json --out replay-review
delivery-review refresh first-review --tools review-tools/toolchain.json --out refreshed-review
```

`review-tools` must be new and its parent must already exist. Provisioning
downloads the three fixed Wash wheels and the platform's fixed GitHub CLI
archive, verifies their exact sizes and SHA-256 values, then writes
`toolchain.json`. It selects the running Python and the operator's Git. It
does not install wheels, invoke pip, execute downloaded programs, inspect a PR,
or use GitHub credentials. Unsupported platforms/Python versions fail before
downloading. Linux/Python 3.11 remains the initial product qualification target;
the additional bootstrap platform does not establish product qualification.

The output retains the original GitHub CLI archive, its selected executable,
download receipt and the bundled public trusted-root export. Downloaded file
paths are relative to the configuration; Git and Python paths are absolute.
Move the directory only where those operator runtimes remain available. Each
inspection independently rechecks its tool hashes. A failed preparation leaves
no usable configuration; preserve any partial output for diagnosis and choose
a new output directory when retrying.

Downloads use fixed official HTTPS sources, no environment proxies, a 32 MiB
per-file hard cap and at most 60 seconds per download within a 180-second
download budget. Only the GitHub release's single redirect to the exact
`release-assets.githubusercontent.com` host is followed. The installer reads
one exact regular archive member without extracting archive paths. The root
export is fixed offline material: provisioning never silently rotates trust
roots. Sources, hashes and trust-bootstrap limits are recorded in
[TOOLCHAIN-PROVENANCE.md](TOOLCHAIN-PROVENANCE.md).

Approve `approved-profile.json` independently of the reviewed PR before using
it. Installing tools does not approve a policy, qualify a producer, authenticate
a saved report, or establish a stable release. Full replay freshly runs the
pinned official offline signature verifier when a saved attestation is present;
it does not collect new platform facts or run the three scanners. Its current
freshness remains unknown. See [SPEC-009](specs/SPEC-009-full-inspection.md).

## Observation commands

The full inspection profile uses a selector-bound policy identity. Its
construction/checking commands are:

```sh
delivery-review policy seal draft-profile.json --out profile-for-review.json
delivery-review policy verify profile-for-review.json
```

Both commands explicitly report `approval: not_established`. A seal is a digest
identity, not a signature or approval. Review all selectors independently of the
PR before authorizing use. Use `examples/inspection/profile-v2.json` as the current
synthetic format example; its IDs and producer revision must not be used as real
approved identities. The original `profile.json` stays a historical v1 example.
See [SPEC-010](specs/SPEC-010-inspection-profile-v2.md) for explicit versioning,
matrix runtime proof, ledger declarations and proposed-policy observations.

Observation-only commands still take the separate SPEC-001 observation profile:

```sh
delivery-review observations inspect https://github.com/OWNER/REPO/pull/NUMBER --profile profile.json --out new-result
delivery-review observations verify new-result/capture
delivery-review observations replay new-result/capture --out replay-result
delivery-review observations refresh new-result/capture --out refreshed-result
```

Approve the profile independently of the reviewed PR. Use `--anonymous` for
public data without credentials. Otherwise a process-local `GH_TOKEN` or
`GITHUB_TOKEN` may authorize read-only API requests; no credential is saved in
the capture. Output directories must be new. Offline replay does not read a
token or execute Git, a scanner or a network request. Its freshness is always
unknown; captured freshness remains historical. Observation exit status reports
collection completeness, never delivery readiness.

Legacy normalized bundles use `delivery-review bundle verify SOURCE` or
`delivery-review bundle replay SOURCE NEW_DESTINATION`. Successful normalized
replay returns product exit 2 because current freshness cannot be established.

JSON, Markdown and HTML observation reports are separate from the eventual
full readiness report. HTML is self-contained and script-free; provider strings
are escaped. Original raw bytes remain in the capture. Preserve the original
capture when sharing a report, subject to its data permissions and privacy.

## Release verification

A stable release must publish exact source, wheel/sdist hashes and matching
qualification receipts before its tag is considered qualified. Compare those
hashes through a trusted release channel before installation. The installed
inventory detects changed/missing/extra package modules, but a local attacker
able to rewrite the inventory can also forge consistency. It is not a signature
and it does not verify Python, Git, the OS or the publisher. Three engines are
separate hash-pinned distributions; they are not dependencies imported into
this verifier's interpreter.
