"""Offline state recomputation over verified saved normalized observations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..contracts import canonical_bytes, derive_state, exit_for
from .store import (
    DEFAULT_LIMITS, BundleLimits, _boundary, _create_bundle, _need, verify_bundle,
)


@_boundary
def replay_bundle(source: str | Path, destination: str | Path, *,
                  limits: BundleLimits = DEFAULT_LIMITS) -> dict:
    """Create a separate replay bundle without touching the original.

Successful replay always has report exit 2: offline observations cannot establish
current freshness. No credential, executable or network interface is involved.
"""
    # Match the store's lexical normalization. Links (including loops) are
    # rejected by its lstat directory checks, without following them to resolve.
    origin = Path(os.path.abspath(source))
    target = Path(os.path.abspath(destination))
    _need(not target.is_relative_to(origin) and not origin.is_relative_to(target),
          "replay destination must be separate from source bundle")
    saved = verify_bundle(source, limits=limits)
    report = saved.report
    manifest_digest = hashlib.sha256(saved.manifest_bytes).hexdigest()
    report["report_id"] = "replay-" + manifest_digest[:32]
    report["mode"] = "offline_replay"
    report["recheck"] = None  # Historical live recheck remains in the original bundle.
    report["state"] = derive_state(report)
    report["exit_code"] = exit_for(report["state"])
    report["ready_for_review"] = False
    # Keep every original limitation, including a list at the contract's limit.
    # Replay scope is declared by mode, manifest replay_basis and CLI metadata.
    _create_bundle(destination, policy_bytes=saved.policy_bytes,
                   report_bytes=canonical_bytes(report) + b"\n", raw_files=saved.raw_files,
                   limits=limits, derived_from={
                       "manifest_sha256": manifest_digest,
                       "report_sha256": hashlib.sha256(saved.report_bytes).hexdigest(),
                   })
    return report
