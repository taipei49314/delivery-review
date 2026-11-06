"""Canonical measured checkout identities for the two fixed producer workflows."""
import hashlib
import re
import zlib

from delivery_review.contracts import ContractError
from delivery_review.evidence.store import _read
from delivery_review.source_snapshot import _commit, verify_snapshot

MODES = frozenset({'pr_head', 'github_merge'})


def _need(condition, reason):
    if not condition:
        raise ContractError(reason)


def _sha(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{40}', value) is not None


def commit_parents(raw, expected_sha):
    """Recompute the actual canonical Git identity before reading parent claims."""
    _need(_sha(expected_sha) and type(raw) is bytes and len(raw) <= 8 * 1024 * 1024,
          'tested_source_object_invalid')
    header, separator, body = raw.partition(b'\0')
    _need(separator and header == b'commit ' + str(len(body)).encode()
          and hashlib.sha1(raw, usedforsecurity=False).hexdigest() == expected_sha, 'tested_source_object_invalid')
    _, parents = _commit(body)
    return parents


def read_commit(snapshot, sha):
    verify_snapshot(snapshot)
    _need(_sha(sha), 'tested_source_object_invalid')
    compressed = _read(snapshot.root / '.git/objects' / sha[:2] / sha[2:], 8 * 1024 * 1024)
    inflater = zlib.decompressobj()
    raw = inflater.decompress(compressed, 8 * 1024 * 1024 + 1)
    _need(inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail
          and len(raw) <= 8 * 1024 * 1024, 'tested_source_object_invalid')
    commit_parents(raw, sha)
    return raw


def measured_source(*, inventory, source_mode, pr_head_sha, checked_out_commit_bytes):
    """Derive frozen receipt source from measured inventory and original object.

    This proves local Git consistency, not GitHub membership or source-policy
    approval. The hosted controller and verifier independently establish those.
    """
    _need(type(source_mode) is str and source_mode in MODES
          and _sha(inventory.get('head_sha')) and _sha(inventory.get('base_tip_sha')),
          'unsupported_measured_source')
    checked, base = inventory['head_sha'], inventory['base_tip_sha']
    if source_mode == 'pr_head':
        _need(pr_head_sha is None or pr_head_sha == checked, 'tested_head_mismatch')
        if checked_out_commit_bytes is not None:
            commit_parents(checked_out_commit_bytes, checked)
        return {'mode': 'pr_head', 'base_tip_sha': base, 'head_sha': checked,
                'checked_out_sha': checked, 'merge_parents': []}
    _need(_sha(pr_head_sha) and pr_head_sha != checked, 'tested_merge_head_invalid')
    parents = commit_parents(checked_out_commit_bytes, checked)
    _need(parents == [base, pr_head_sha], 'tested_merge_parents_mismatch')
    return {'mode': 'github_merge', 'base_tip_sha': base, 'head_sha': pr_head_sha,
            'checked_out_sha': checked, 'merge_parents': parents}
