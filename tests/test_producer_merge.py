"""Authored merge-source controls; these are not hosted E03 qualification."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from delivery_review.contracts import ContractError, canonical_bytes
from delivery_review.source_snapshot import materialize_snapshot
from producer.bootstrap import BootstrapError, PRODUCER_REPOSITORY, producer_path, validate_claims
from producer.host import execute_pytest
from producer.hosted import build_predicate, select_test_snapshot, tested_source_claim
from producer.platform_identity import derive_identity
from producer.receipt import build_receipt
from producer.source_identity import commit_parents, measured_source, read_commit
import test_producer_platform as platform_fixtures
import test_producer_receipt as receipt_fixtures
import test_source_snapshot as source_fixtures
from test_pytest_junit import document


def merge_objects(parents=None):
    objects, common, base, head = source_fixtures.fixture()
    before_merge = dict(objects.objects)
    merge = objects.commit(objects.files({'README.txt': b'measured merge checkout\n'}),
                           [base, head] if parents is None else parents(base, head, common))
    return objects, before_merge, base, head, merge


def merge_identity(base, head, merge):
    claims, environment, data = platform_fixtures.fixture()
    claims.update(sha=merge, job_workflow_ref=PRODUCER_REPOSITORY + '/' + producer_path('github_merge') + '@' + 'd' * 40)
    environment['GITHUB_SHA'] = merge
    data['pull_request']['base']['sha'] = base
    data['pull_request']['head']['sha'] = head
    data['merge_ref']['object'].update(sha=merge,
        url='https://api.github.com/repos/authored/reference/git/commits/' + merge)
    for run in (data['run'], data['attempt']):
        run.update(head_sha=head, referenced_workflows=[{'path': claims['job_workflow_ref'], 'sha': 'd' * 40}])
    data['jobs'][0]['head_sha'] = data['check']['head_sha'] = head
    return claims, environment, data


class ProducerMergeTests(unittest.TestCase):
    def test_measured_merge_preserves_three_distinct_commit_identities(self):
        objects, _, base, head, merge = merge_objects()
        raw = objects.objects[merge]
        observed = measured_source(inventory={'head_sha': merge, 'base_tip_sha': base},
            source_mode='github_merge', pr_head_sha=head, checked_out_commit_bytes=raw)
        self.assertEqual({'mode': 'github_merge', 'base_tip_sha': base, 'head_sha': head,
                          'checked_out_sha': merge, 'merge_parents': [base, head]}, observed)
        self.assertEqual(3, len({base, head, merge}))
        self.assertEqual(merge, hashlib.sha1(raw, usedforsecurity=False).hexdigest())

    def test_measured_merge_refuses_wrong_order_missing_extra_and_wrong_parents(self):
        for parents in (lambda b, h, c: [h, b], lambda b, h, c: [b], lambda b, h, c: [],
                        lambda b, h, c: [b, h, c], lambda b, h, c: [b, c]):
            objects, _, base, head, merge = merge_objects(parents)
            with self.subTest(parents=parents), self.assertRaisesRegex(ContractError, 'tested_merge_parents_mismatch'):
                measured_source(inventory={'head_sha': merge, 'base_tip_sha': base}, source_mode='github_merge',
                                pr_head_sha=head, checked_out_commit_bytes=objects.objects[merge])

    def test_canonical_original_commit_bytes_are_required_not_a_parent_claim(self):
        objects, _, base, head, merge = merge_objects()
        raw = objects.objects[merge]
        for bad in (None, b'', raw + b'\n', raw.replace(b'commit ', b'commit 0', 1), objects.objects[head]):
            with self.subTest(raw=bad), self.assertRaises(ContractError):
                measured_source(inventory={'head_sha': merge, 'base_tip_sha': base}, source_mode='github_merge',
                                pr_head_sha=head, checked_out_commit_bytes=bad)
        with self.assertRaises(ContractError):
            commit_parents(raw, head)
        for bad_head in (None, merge, 'branch', True):
            with self.subTest(head=bad_head), self.assertRaises(ContractError):
                measured_source(inventory={'head_sha': merge, 'base_tip_sha': base}, source_mode='github_merge',
                                pr_head_sha=bad_head, checked_out_commit_bytes=raw)

    def snapshots(self, directory, *, parents=None):
        objects, before_merge, base, head, merge = merge_objects(parents)
        options = {'repository': 'authored/reference', 'base_tip_sha': base, 'git_executable': shutil.which('git')}
        head_snapshot = materialize_snapshot(directory / 'head', head_sha=head, objects=before_merge, **options)
        event_snapshot = materialize_snapshot(directory / 'merge', head_sha=merge, objects=objects.objects, **options)
        claims, _, data = merge_identity(base, head, merge)
        return head_snapshot, event_snapshot, derive_identity(claims, source_mode='github_merge', **data)

    def test_fixed_mode_selects_actual_tree_and_refuses_swapped_or_changed_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            head, merge, identity = self.snapshots(Path(directory))
            selected = select_test_snapshot(head, merge, identity)
            self.assertIs(merge, selected)
            self.assertEqual(b'measured merge checkout\n', (selected.root / 'README.txt').read_bytes())
            self.assertEqual(identity['event_source_sha'], hashlib.sha1(read_commit(selected, selected.head_sha), usedforsecurity=False).hexdigest())
            head_identity = dict(identity, source_mode='pr_head', producer_path=producer_path('pr_head'))
            self.assertIs(head, select_test_snapshot(head, merge, head_identity))
            for first, second in ((merge, head), (head, head), (merge, merge)):
                with self.subTest(first=first.head_sha, second=second.head_sha), self.assertRaises((ValueError, OSError)):
                    select_test_snapshot(first, second, identity)
            for changed in (dict(identity, repository_full_name='authored/other'),
                            dict(identity, base_tip_sha=identity['head_sha']),
                            dict(identity, producer_path=producer_path('pr_head'))):
                with self.assertRaises(ValueError):
                    select_test_snapshot(head, merge, changed)

    def test_invalid_merge_relation_refuses_before_docker_runtime_or_guest(self):
        with tempfile.TemporaryDirectory() as directory:
            head, merge, identity = self.snapshots(Path(directory), parents=lambda b, h, c: [h, b])
            with patch('producer.host._runtime') as runtime, self.assertRaisesRegex(ContractError, 'tested_merge_parents_mismatch'):
                execute_pytest(merge, runtime=None, image_digest='sha256:' + 'e' * 64,
                    wheelhouse=Path(directory), test_path='tests', source_mode='github_merge', pr_head_sha=head.head_sha)
            runtime.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'event_source_parent_mismatch'):
                select_test_snapshot(head, merge, identity)

    def receipt_fixture(self):
        objects, _, base, head, merge = merge_objects()
        claims, _, data = merge_identity(base, head, merge)
        identity = derive_identity(claims, source_mode='github_merge', **data)
        helper = receipt_fixtures.ProducerReceiptTests(); helper.setUp()
        command = ('/work/venv/bin/python', '-I', '-m', 'pytest', '-c', '/trusted/pytest.ini',
            '--rootdir=/work/source', '--junitxml=/work/output/junit.xml', '--strict-markers', 'tests')
        execution = replace(helper.execute(document()), command=command,
            source_inventory_bytes=canonical_bytes({'head_sha': merge, 'base_tip_sha': base}),
            source_mode='github_merge', pr_head_sha=head, checked_out_commit_bytes=objects.objects[merge])
        source = {'repository_id': 9001, 'head_repository_id': 9001, 'pr_number': 7,
                  'source': tested_source_claim(identity)}
        producer = {'repository_id': identity['producer_repository_id'], 'workflow_path': identity['producer_path'],
            'workflow_sha': identity['producer_sha'], 'run_id': identity['run_id'], 'attempt': identity['run_attempt'],
            'job_id': identity['job_id']}
        return execution, source, producer, identity

    def test_merge_receipt_and_artifact_predicate_bind_measured_checkout(self):
        execution, source, producer, identity = self.receipt_fixture()
        make = lambda e, s: build_receipt(e, source_claim=s, producer_identity=producer,
            recorded_at='2026-11-06T00:00:00Z', receipt_id='authored-merge')
        receipt = make(execution, source)
        self.assertEqual(source['source'], receipt['source'])
        raw = canonical_bytes(receipt)
        predicate = build_predicate(identity, artifact_id=4001, artifact_sha256='f' * 64,
            receipt_bytes=raw, junit_bytes=document())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), predicate['receipt_sha256'])
        for replacement in (replace(execution, source_mode='pr_head'),
                            replace(execution, checked_out_commit_bytes=None),
                            replace(execution, pr_head_sha=source['source']['base_tip_sha'])):
            with self.assertRaises(ValueError): make(replacement, source)
        for key, value in (('mode', 'pr_head'), ('checked_out_sha', source['source']['head_sha']),
                           ('merge_parents', list(reversed(source['source']['merge_parents'])))):
            changed = deepcopy(source); changed['source'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): make(execution, changed)
            changed_receipt = deepcopy(receipt); changed_receipt['source'][key] = value
            with self.assertRaises(ValueError):
                build_predicate(identity, artifact_id=4001, artifact_sha256='f' * 64,
                                receipt_bytes=canonical_bytes(changed_receipt), junit_bytes=document())
        with self.assertRaisesRegex(ValueError, 'producer_source_mode_mismatch'):
            build_predicate(dict(identity, source_mode='pr_head'), artifact_id=4001, artifact_sha256='f' * 64,
                            receipt_bytes=raw, junit_bytes=document())

    def test_oidc_and_api_must_identify_the_same_fixed_mode_workflow(self):
        claims, environment, data = merge_identity('a' * 40, 'b' * 40, 'c' * 40)
        self.assertEqual(producer_path('github_merge'),
                         derive_identity(validate_claims(claims, environment, now=1100, source_mode='github_merge'),
                                         source_mode='github_merge', **data)['producer_path'])
        with self.assertRaises(BootstrapError): validate_claims(claims, environment, now=1100)
        with self.assertRaises(BootstrapError): derive_identity(claims, **data)
        head_claims, head_environment, head_data = platform_fixtures.fixture()
        with self.assertRaises(BootstrapError):
            validate_claims(head_claims, head_environment, now=1100, source_mode='github_merge')
        with self.assertRaises(BootstrapError): derive_identity(head_claims, source_mode='github_merge', **head_data)
        changed = deepcopy(data)
        changed['run']['referenced_workflows'] = head_data['run']['referenced_workflows']
        with self.assertRaises(BootstrapError): derive_identity(claims, source_mode='github_merge', **changed)
        for invalid in ('auto', '../pytest-producer.yml', [], None):
            with self.subTest(mode=invalid), self.assertRaises(BootstrapError): producer_path(invalid)

    def test_both_reusable_workflows_embed_same_bootstrap_and_literal_entrypoints(self):
        for mode, bootstrap_argument, command in (('pr_head', '', 'prepare'), ('github_merge', ' github_merge', 'prepare-merge')):
            workflow = (ROOT / producer_path(mode)).read_text()
            marker = "          python3 -I -" + bootstrap_argument + " <<'DELIVERY_REVIEW_BOOTSTRAP'\n"
            embedded = workflow.split(marker, 1)[1].split('          DELIVERY_REVIEW_BOOTSTRAP\n', 1)[0]
            self.assertEqual((ROOT / 'producer/bootstrap.py').read_text(),
                             ''.join(line[10:] + '\n' for line in embedded.splitlines()))
            self.assertIn('producer/hosted.py ' + command + '\n', workflow)
            self.assertIn('workflow_call: {}', workflow)
            self.assertNotIn('${{ inputs.', workflow)
            self.assertNotIn('secrets: inherit', workflow)
            self.assertNotIn('pull_request_target', workflow)


if __name__ == '__main__':
    unittest.main()
