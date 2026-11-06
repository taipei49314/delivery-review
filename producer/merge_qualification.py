"""Local authored checkout-selection probe, not hosted E03 qualification.

Only the built-in fixture is executed. The same literal tests require the merge
tree's marker, so a head checkout fails and the two-parent merge passes. Original
objects, measured inventories, native output and receipts are preserved.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
import tempfile

from delivery_review.contracts import canonical_bytes
from delivery_review.source_snapshot import materialize_snapshot
from .bootstrap import producer_path
from .host import execute_pytest
from .receipt import build_receipt


def _fixture():
    objects = {}
    def add(kind, body):
        raw = kind.encode() + b' ' + str(len(body)).encode() + b'\0' + body
        sha = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        objects[sha] = raw
        return sha
    test = add('blob', b'''from pathlib import Path
def test_actual_checkout_is_merge():
    assert (Path(__file__).resolve().parents[1] / 'checkout-marker.txt').read_bytes() == b'merge\\n'
''')
    tests = add('tree', b'100644 test_checkout.py\0' + bytes.fromhex(test))
    def commit(marker, parents):
        tree = add('tree', b'100644 checkout-marker.txt\0' + bytes.fromhex(add('blob', marker))
                   + b'40000 tests\0' + bytes.fromhex(tests))
        body = b'tree ' + tree.encode() + b'\n'
        body += b''.join(b'parent ' + parent.encode() + b'\n' for parent in parents)
        body += (b'author Fixture <fixture@example.invalid> 1 +0000\n'
                 b'committer Fixture <fixture@example.invalid> 1 +0000\n\nauthored merge probe\n')
        return add('commit', body)
    base = commit(b'base\n', [])
    head = commit(b'head\n', [base])
    head_objects = dict(objects)
    merge = commit(b'merge\n', [base, head])
    return base, head, merge, head_objects, objects


def qualify(destination: Path, *, runtime, image_digest, wheelhouse, git_executable):
    """Run the explicitly requested local probe using an approved local runtime."""
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    base, head, merge, head_objects, objects = _fixture()
    canonical = destination / 'canonical-git-objects'
    canonical.mkdir()
    for sha, raw in objects.items():
        (canonical / sha).write_bytes(raw)
    summary = {'kind': 'authored-local-merge-checkout-probe', 'product_integration': 'NOT_RUN',
        'hosted_qualification': False, 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(), 'python': platform.python_version(),
        'base_tip_sha': base, 'pr_head_sha': head, 'merge_sha': merge, 'image': image_digest, 'runs': []}
    # Native Linux temporary storage is necessary for measured Git file modes.
    with tempfile.TemporaryDirectory(prefix='delivery-review-merge-probe-') as temporary:
        for mode, checked, source_objects, expected_exit in (
                ('pr_head', head, head_objects, 1), ('github_merge', merge, objects, 0)):
            saved = destination / mode
            saved.mkdir()
            snapshot = materialize_snapshot(Path(temporary) / mode, repository='authored/reference',
                head_sha=checked, base_tip_sha=base, objects=source_objects, git_executable=git_executable)
            execution = execute_pytest(snapshot, runtime=runtime, image_digest=image_digest,
                wheelhouse=wheelhouse, test_path='tests', source_mode=mode, pr_head_sha=head)
            (saved / 'source-inventory.json').write_bytes(execution.source_inventory_bytes)
            (saved / 'tested-source.commit').write_bytes(execution.checked_out_commit_bytes)
            for phase in ('setup', 'test'):
                observed = getattr(execution, phase)
                if observed is not None:
                    (saved / (phase + '.stdout')).write_bytes(observed.stdout)
                    (saved / (phase + '.stderr')).write_bytes(observed.stderr)
            if execution.junit_bytes is not None:
                (saved / 'junit.xml').write_bytes(execution.junit_bytes)
            if execution.junit_observation_bytes is not None:
                (saved / 'junit-observation.json').write_bytes(execution.junit_observation_bytes)
            result = {'mode': mode, 'checked_out_sha': checked, 'expected_native_test_exit': expected_exit,
                'actual_setup_exit': None if execution.setup is None else execution.setup.exit_code,
                'actual_native_test_exit': None if execution.test is None else execution.test.exit_code,
                'stopped': execution.stopped, 'cleaned': execution.cleaned, 'errors': list(execution.error_codes)}
            summary['runs'].append(result)
            (saved / 'execution.json').write_bytes(canonical_bytes(result))
            # Authored numeric/producer IDs are visibly synthetic. They are not
            # platform provenance or a qualified producer approval.
            receipt = build_receipt(execution, source_claim={'repository_id': 9001,
                'head_repository_id': 9001, 'pr_number': 7, 'source': {'mode': mode,
                'base_tip_sha': base, 'head_sha': head, 'checked_out_sha': checked,
                'merge_parents': [] if mode == 'pr_head' else [base, head]}},
                producer_identity={'repository_id': 9002, 'workflow_path': producer_path(mode),
                    'workflow_sha': 'd' * 40, 'run_id': 1001, 'attempt': 1, 'job_id': 2001},
                recorded_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                receipt_id='authored-local-' + mode)
            (saved / 'receipt.json').write_bytes(canonical_bytes(receipt))
    summary['local_control_pass'] = all(run['actual_setup_exit'] == 0
        and run['actual_native_test_exit'] == run['expected_native_test_exit']
        and run['stopped'] and run['cleaned'] and not run['errors'] for run in summary['runs'])
    (destination / 'summary.json').write_bytes(canonical_bytes(summary))
    return summary
