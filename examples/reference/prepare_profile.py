"""Construct an inspection profile for external review; this is not approval."""
import argparse
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'src'))
from delivery_review.contracts import loads_strict
from delivery_review.inspection_profile import seal_inspection_profile
PRODUCER_PATHS = {'pr_head': '.github/workflows/pytest-producer.yml',
                  'github_merge': '.github/workflows/pytest-producer-merge.yml'}


def prepare(*, repository, repository_id, workflow_id, job_name, producer_sha, source_mode='pr_head'):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or any(part in {'.', '..'} for part in repository.split('/'))
            or not re.fullmatch(r'[0-9a-f]{40}', producer_sha)
            or any(type(value) is not int or not 0 < value <= 2**53 - 1 for value in (repository_id, workflow_id))
            or type(source_mode) is not str or source_mode not in PRODUCER_PATHS):
        raise ValueError('invalid reference identity')
    profile = loads_strict((PROJECT / 'examples/inspection/profile.json').read_bytes())
    profile['profile_version'] = 2
    profile['name'] = 'authored-public-reference'
    profile['policy'].update(repository_id=repository_id, allowed_source_modes=[source_mode])
    profile['github']['profile_id'] = 'authored-public-reference-observations'
    profile['github']['repository'] = {'id': repository_id, 'full_name': repository}
    profile['github']['workflows'][0].update(id=workflow_id, path='.github/workflows/delivery-review.yml', job_names=[job_name])
    requirement = profile['policy']['requirements'][0]
    requirement['selector'].update(workflow_id=workflow_id, job_name=job_name,
        producer_repository_id=1438021135, producer_path=PRODUCER_PATHS[source_mode], producer_sha=producer_sha)
    profile['policy']['requirements'][:0] = [
        {'id': 'source', 'kind': 'source', 'required': True, 'minimum_binding': 'reported', 'selector': {}},
        {'id': 'ci-job', 'kind': 'ci_job', 'required': True, 'minimum_binding': 'platform_bound',
         'selector': {'workflow_id': workflow_id, 'app_id': 15368, 'job_name': job_name}}]
    profile['producers']['pytest-linux']['repository_full_name'] = 'taipei49314/delivery-review'
    return seal_inspection_profile(profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--repository-id', required=True, type=int)
    parser.add_argument('--workflow-id', required=True, type=int)
    parser.add_argument('--job-name', required=True, help='exact observed platform name, including reusable-workflow prefix')
    parser.add_argument('--producer-sha', required=True)
    parser.add_argument('--source-mode', choices=tuple(PRODUCER_PATHS), default='pr_head')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    raw = prepare(repository=args.repository, repository_id=args.repository_id, workflow_id=args.workflow_id,
                  job_name=args.job_name, producer_sha=args.producer_sha, source_mode=args.source_mode)
    with args.out.open('xb') as stream:
        stream.write(raw)
    print('Created a sealed profile for independent review. Approval: not established.')


if __name__ == '__main__':
    main()
