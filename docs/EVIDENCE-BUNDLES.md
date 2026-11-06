# Local evidence bundles and offline replay

This first M1 slice saves exact bytes and replays **saved normalized observations**
under their saved policy. It does not query GitHub, map provider payloads to facts,
authenticate a producer, execute PR code, run scanners or parse JUnit. Binding
levels and findings remain saved claims; local digest checks never promote them.
E01–E18 product integration statuses remain `NOT_RUN`, including the online
refresh portion of E17.

## Bundle format 1

```text
bundle/
  manifest.json
  policy.json
  report.json
  raw/
    ...selected original bytes...
```

The separate `bundle_version: 1` format does not alter frozen contract v1.
`policy.json` and `report.json` retain the exact supplied bytes, including
whitespace and line endings. Raw files are opaque bytes: storing an archive or
XML file neither extracts it nor validates its contents.

The manifest has exactly these fields:

| Field | Meaning |
| --- | --- |
| `kind` | `delivery-review-evidence-bundle` |
| `bundle_version` | Integer `1` |
| `replay_basis` | `saved_normalized_report` |
| `observed_at` | Exactly the saved report's observation time |
| `files` | Exact inventory of `policy.json`, `report.json` and `raw/` files, each with `path`, `size_bytes` and independently calculated `sha256` |
| `derived_from` | `null` for a newly saved snapshot; replay records source `manifest_sha256` and `report_sha256` |

The manifest does not hash itself. The report's `raw_inventory` contains exactly
the `raw/` files, excluding policy, report and manifest to avoid self-reference.
The policy's raw-byte hash in the manifest is distinct from the canonical policy
hash used by `report.policy.canonical_sha256`; both are checked.

Every non-null evidence `raw_sha256`, evidence `artifact_sha256`, producer proof
`api_response_sha256` and engine `raw_report_sha256` must resolve to retained raw
bytes. Engine `artifact_sha256` is the pinned installation wheel identity and is
not treated as a reference to a bundled report. Matching a digest proves retained
byte consistency, not a valid source/run/artifact relationship.

## Storage API

Use `delivery_review.evidence.create_bundle(destination, policy_bytes=...,
report_bytes=..., raw_files=...)` with original `bytes` and a mapping of portable
`raw/...` paths to bytes. The destination's parent must exist, and the destination
itself must not exist, even as an empty or interrupted directory. Invalid input
raises `BundleError` without repairing the report or replacing its hashes.

The store writes files exclusively and writes the final manifest last. An
interrupted write can leave a partial directory; without a valid manifest it
cannot be verified or replayed. Choose a new path for a subsequent attempt.

`verify_bundle(source)` returns a verified in-memory snapshot with original
`policy_bytes`, `report_bytes`, `manifest_bytes`, an immutable `raw_files` mapping
and parsed `.policy`/`.report` properties. It checks the exact tree, all file
sizes and hashes, the contract and policy relationship, the report inventory and
referenced raw digests. Missing files, extra files/directories and altered bytes
are errors. The verifier does not silently update a manifest.

Both APIs use caller-controlled `BundleLimits`; limits are never read from the
untrusted manifest:

| Limit | Default |
| --- | --- |
| Files, including policy and report | 100 |
| Bytes per file | 25 MiB |
| Total file bytes, excluding manifest | 100 MiB |
| Manifest bytes | 2 MiB |

Contract JSON and the manifest also retain the strict 2 MiB JSON parser ceiling.
These are local storage limits, separate from later artifact extraction limits.
Raw paths use ASCII segments beginning with a letter, digit or underscore,
followed by letters, digits, underscores, dots or hyphens. Each segment is at most
128 characters, the path at most 512 characters and at most 16 segments. Absolute
paths, traversal, backslashes, ADS, reserved Windows names, trailing dots/spaces,
case collisions and file/directory collisions are refused. Files must be regular
files with one link; symlinks, hardlinks and Windows reparse points are refused,
including in directory ancestors.

Keep the bundle tree exclusively owned during IO. This API provides create-once
behavior and byte-integrity checks, not protection against concurrent hostile
filesystem changes or an owner who rewrites both data and manifest.

## Offline commands

From the repository root after `uv sync --locked`:

```sh
uv run --locked python tools/evidence.py verify work/source-bundle
uv run --locked python tools/evidence.py replay work/source-bundle work/replayed-bundle
```

`verify` returns operational exit 0 for successful integrity checks and explicitly
reports authentication as `not_established`. It makes no readiness decision.

`replay` verifies the complete source before creating a separate destination.
It keeps saved policy/raw bytes, observation time, subject, evidence, engines,
obligations, findings and limitations. It creates a deterministic replay report
ID, sets `mode=offline_replay`, clears the new `recheck` and recomputes the three
state axes. The manifest's `replay_basis` and CLI authentication metadata state
the limited scope without modifying the saved limitations list. The original
report and its historical recheck remain untouched in the source bundle.

A successful replay has freshness `unknown`, `ready_for_review=false` and exit
**2**, even when the saved evidence/policy decisions are complete/clear. A known
policy block remains visible. A JSON summary with `status: written` identifies
successful output; errors are emitted on stderr with `status: error` and also
exit 2. Source and destination may not contain each other; an existing destination
is never overwritten. Replay of the same source into two new destinations yields
the same report bytes. Online `refresh` is not implemented.

## Synthetic API walkthrough

The M0 examples contain fictional hashes and cannot be sealed directly as a
verified bundle. This demonstration instead creates an explicitly synthetic,
incomplete observation with actual raw-file hashes. It is not a GitHub recording
or a successful delivery report. Save the following as `work/create_bundle_demo.py`
and run `uv run --locked python work/create_bundle_demo.py` from the repository
root. Create `work/` first if it does not exist.

```python
from pathlib import Path
import hashlib
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from delivery_review.contracts import canonical_bytes, derive_state, exit_for, loads_strict
from delivery_review.evidence import create_bundle

policy_bytes = (root / "examples/policy.json").read_bytes()
report = loads_strict((root / "examples/report.json").read_bytes())
raw = b'{ "origin": "synthetic_bundle_demo" }\r\n'
report["report_id"] = "synthetic-bundle-demo"
report["mode"] = "offline_replay"
report["recheck"] = None
report["engines"] = []
report["evidence"] = []
report["obligations"][0].update(
    outcome="missing", effect="clear", evidence_ids=[],
    reason_codes=["synthetic_missing_execution"],
)
report["raw_inventory"] = [{
    "path": "raw/demo.json", "size_bytes": len(raw),
    "sha256": hashlib.sha256(raw).hexdigest(),
}]
report["limitations"] = [
    "synthetic_bundle_demo",
    "Fictional subject and observation time; no GitHub or engine execution.",
]
report["state"] = derive_state(report)
report["exit_code"] = exit_for(report["state"])
report["ready_for_review"] = False
create_bundle(
    root / "work/demo-source", policy_bytes=policy_bytes,
    report_bytes=canonical_bytes(report) + b"\n",
    raw_files={"raw/demo.json": raw},
)
```

Then verify `work/demo-source` and replay it into `work/demo-replay`. Rerunning
the creation example requires a fresh destination. For local regression checks,
run `uv run --locked python -m unittest discover -s tests -v`; these controlled
storage/replay tests remain separate from live provider and release qualification.
