"""Host-only platform cross-checks for the fixed reusable producer.

OIDC observations are obtained by bootstrap.request_claims, never caller input
to the hosted entry point. Every job is selected through an explicit check URL;
check-run ID and job ID are never assumed to be numerically equal.
"""
from pathlib import Path
import hashlib
import re
import urllib.parse

from delivery_review.contracts import canonical_bytes, loads_strict
from delivery_review.providers.github_routes import (ACQUISITION_ERRORS, API_VERSION, next_page, positive, sha,
                                                     validate_response_headers)
from .bootstrap import (PRODUCER_REPOSITORY, PRODUCER_REPOSITORY_ID, PRODUCER_PATH,
                        BootstrapError, number, require, producer_path)

APP_ID = 15368
PLATFORM_FAILURE_CODES = frozenset({'producer_source_mode_mismatch', 'platform_repository_mismatch',
    'producer_repository_mismatch', 'platform_pr_mismatch', 'platform_source_missing', 'fork_source_unsupported',
    'event_source_ref_mismatch', 'platform_run_mismatch', 'producer_reference_missing',
    'producer_reference_unconfirmed', 'platform_attempt_mismatch', 'ambiguous_job_inventory',
    'job_check_relation_unconfirmed', 'platform_job_attempt_mismatch', 'platform_job_mismatch',
    'platform_check_mismatch', 'platform_job_url_mismatch'})


def artifact_name(identity):
    return 'delivery-review-' + '-'.join(str(identity[key]) for key in ('run_id', 'run_attempt', 'check_run_id'))


def derive_identity(claims, *, repository, producer_repository, pull_request, merge_ref,
                    run, attempt, jobs, check, source_mode='pr_head'):
    """Pure consistency boundary. Network provenance belongs to collect_identity."""
    repo_id, run_id, attempt_id, check_id = (number(claims[key]) for key in
                                           ('repository_id', 'run_id', 'run_attempt', 'check_run_id'))
    name = claims['repository']
    path = producer_path(source_mode)
    require(claims['job_workflow_ref'] == PRODUCER_REPOSITORY + '/' + path + '@' + claims['job_workflow_sha'],
            'producer_source_mode_mismatch')
    pr_number = int(claims['ref'].split('/')[2])
    require(positive(repository.get('id')) and repository.get('id') == repo_id
            and repository.get('full_name') == name, 'platform_repository_mismatch')
    require(producer_repository.get('id') == PRODUCER_REPOSITORY_ID
            and producer_repository.get('full_name') == PRODUCER_REPOSITORY, 'producer_repository_mismatch')
    require(positive(pull_request.get('number')) and pull_request.get('number') == pr_number
            and pull_request.get('state') == 'open', 'platform_pr_mismatch')
    head, base = pull_request.get('head'), pull_request.get('base')
    require(type(head) is dict and type(base) is dict and sha(head.get('sha')) and sha(base.get('sha')),
            'platform_source_missing')
    require(all(type(side.get('repo')) is dict and positive(side['repo'].get('id')) and side['repo'].get('id') == repo_id
                and side['repo'].get('full_name') == name for side in (head, base)), 'fork_source_unsupported')
    # API 2026-03-10 removed merge_commit_sha from PR payloads. Independently
    # observe the exact platform PR merge ref instead of assuming its identity.
    expected_ref = f'refs/pull/{pr_number}/merge'
    require(type(merge_ref) is dict and claims['ref'] == expected_ref and merge_ref.get('ref') == expected_ref
            and merge_ref.get('url') == f'https://api.github.com/repos/{name}/git/{expected_ref}'
            and type(merge_ref.get('object')) is dict and merge_ref['object'].get('type') == 'commit'
            and sha(merge_ref['object'].get('sha')) and merge_ref['object']['sha'] == claims['sha']
            and merge_ref['object'].get('url') == f'https://api.github.com/repos/{name}/git/commits/{claims["sha"]}',
            'event_source_ref_mismatch')
    # Deprecated PR merge_commit_sha is not a live source of identity. Historical
    # capture replay is a separate verifier path; no field is synthesized here.
    for value in (run, attempt):
        require(positive(value.get('id')) and positive(value.get('run_attempt'))
                and value.get('id') == run_id and value.get('run_attempt') == attempt_id
                and value.get('event') == 'pull_request' and value.get('head_sha') == head['sha']
                and type(value.get('repository')) is dict and value['repository'].get('id') == repo_id
                and type(value.get('head_repository')) is dict and value['head_repository'].get('id') == repo_id,
                'platform_run_mismatch')
        references = value.get('referenced_workflows')
        require(type(references) is list, 'producer_reference_missing')
        matching = [item for item in references if type(item) is dict and type(item.get('path')) is str
                    and item['path'].split('@', 1)[0] == PRODUCER_REPOSITORY + '/' + path]
        require(len(matching) == 1 and matching[0].get('path') == claims['job_workflow_ref']
                and matching[0].get('sha') == claims['job_workflow_sha'], 'producer_reference_unconfirmed')
    require(positive(run.get('workflow_id')) and run.get('workflow_id') == attempt.get('workflow_id')
            and positive(run.get('check_suite_id')) and run.get('check_suite_id') == attempt.get('check_suite_id'),
            'platform_attempt_mismatch')
    require(type(jobs) is list and all(type(job) is dict and positive(job.get('id')) for job in jobs)
            and len({job['id'] for job in jobs}) == len(jobs), 'ambiguous_job_inventory')
    check_url = f'https://api.github.com/repos/{name}/check-runs/{check_id}'
    candidates = [job for job in jobs if job.get('check_run_url') == check_url]
    require(len(candidates) == 1, 'job_check_relation_unconfirmed')
    job = candidates[0]
    require('run_attempt' not in job or positive(job['run_attempt']) and job['run_attempt'] == attempt_id,
            'platform_job_attempt_mismatch')
    require(job.get('run_id') == run_id and job.get('head_sha') == head['sha']
            and job.get('run_url') == f'https://api.github.com/repos/{name}/actions/runs/{run_id}'
            and job.get('status') == 'in_progress' and job.get('conclusion') is None,
            'platform_job_mismatch')
    require(check.get('id') == check_id and check.get('head_sha') == head['sha']
            and type(check.get('app')) is dict and check['app'].get('id') == APP_ID
            and type(check.get('check_suite')) is dict and check['check_suite'].get('id') == run['check_suite_id']
            and check.get('name') == job.get('name') and check.get('status') == 'in_progress'
            and check.get('conclusion') is None,
            'platform_check_mismatch')
    expected_path = f'/{name}/actions/runs/{run_id}/job/{job["id"]}'
    for value in (job.get('html_url'), check.get('details_url')):
        require(type(value) is str, 'platform_job_url_mismatch')
        parsed = urllib.parse.urlsplit(value)
        require(parsed.scheme == 'https' and parsed.netloc == 'github.com' and parsed.path == expected_path
                and not parsed.fragment and parsed.query in {'', f'pr={pr_number}'}, 'platform_job_url_mismatch')
    return {'repository_id': repo_id, 'repository_full_name': name, 'pr_number': pr_number,
            'head_repository_id': repo_id, 'head_sha': head['sha'], 'base_tip_sha': base['sha'],
            'event_source_sha': claims['sha'], 'run_id': run_id, 'run_attempt': attempt_id,
            'check_run_id': check_id, 'job_id': job['id'], 'workflow_id': run['workflow_id'],
            'job_name': job['name'], 'app_id': APP_ID, 'producer_repository_id': PRODUCER_REPOSITORY_ID,
            'producer_repository_full_name': PRODUCER_REPOSITORY, 'producer_path': path,
            'producer_sha': claims['job_workflow_sha'], 'source_mode': source_mode}


def collect_identity(claims, *, transport, destination: Path, source_mode='pr_head'):
    producer_path(source_mode)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    records = []
    total = 0
    def get(label, path):
        nonlocal total
        require(len(records) < 24 and total < 8 * 1024 * 1024, 'platform_capture_limit')
        require(label in {'repository', 'producer_repository', 'pull_request', 'merge_ref', 'run', 'attempt', 'check'}
                or label in {'jobs-' + str(page) for page in range(1, 11)}, 'invalid_platform_record_label')
        maximum = min(1024 * 1024, 8 * 1024 * 1024 - total)
        response = transport.get(path, api_version=API_VERSION, max_bytes=maximum,
                                 timeout_seconds=20)
        raw = response['body']
        status, error = response['status_code'], response['error']
        require((status is None or type(status) is int and 100 <= status <= 599)
                and type(response['complete']) is bool
                and (error is None or type(error) is dict and set(error) == {'code'}
                     and type(error['code']) is str and error['code'] in ACQUISITION_ERRORS)
                and (raw is None or type(raw) is bytes and len(raw) <= maximum), 'invalid_platform_response')
        headers = validate_response_headers(response['response_headers'])
        if raw is not None:
            total += len(raw)
            (destination / (label + '.json')).write_bytes(raw)
        record = {'path': path, 'file': None if raw is None else label + '.json',
            'sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
            'size_bytes': None if raw is None else len(raw), 'status_code': status,
            'response_headers': headers, 'complete': response['complete'], 'error': error}
        records.append(record)
        (destination / (label + '.response.json')).write_bytes(canonical_bytes(record) + b'\n')
        if status != 200 or response['complete'] is not True or error is not None or raw is None:
            # Labels/codes are closed sets, status is a validated integer. Never
            # emit response bodies, exception text, credentials or API URLs.
            code = 'none' if error is None else error['code']
            print(f'delivery-review: platform response refused route={label} status={status} code={code}', flush=True)
            raise BootstrapError('platform_response_unavailable')
        return loads_strict(raw), response['response_headers']
    base = '/repos/' + claims['repository']
    run_id, attempt_id = number(claims['run_id']), number(claims['run_attempt'])
    values = {}
    routes = {'repository': base, 'producer_repository': '/repos/' + PRODUCER_REPOSITORY,
              'pull_request': base + '/pulls/' + claims['ref'].split('/')[2],
              'merge_ref': base + '/git/ref/pull/' + claims['ref'].split('/')[2] + '/merge',
              'run': base + '/actions/runs/' + str(run_id),
              'attempt': base + f'/actions/runs/{run_id}/attempts/{attempt_id}',
              'check': base + '/check-runs/' + str(number(claims['check_run_id']))}
    for label, route in routes.items():
        values[label], _ = get(label, route)
    page, jobs, total_count = 1, [], None
    while page is not None:
        path = base + f'/actions/runs/{run_id}/attempts/{attempt_id}/jobs?page={page}&per_page=100'
        value, headers = get('jobs-' + str(page), path)
        require(type(value) is dict and type(value.get('total_count')) is int and type(value.get('jobs')) is list
                and 0 <= value['total_count'] <= 1000, 'invalid_job_page')
        require(total_count is None or total_count == value['total_count'], 'job_pagination_changed')
        total_count = value['total_count']
        jobs.extend(value['jobs'])
        page = next_page(headers, path, max_pages=10)
    require(len(jobs) == total_count, 'job_pagination_incomplete')
    try:
        identity = derive_identity(claims, jobs=jobs, source_mode=source_mode, **values)
    except BootstrapError as error:
        code = str(error) if str(error) in PLATFORM_FAILURE_CODES else 'platform_identity_refused'
        (destination / 'failure.json').write_bytes(canonical_bytes({'code': code}) + b'\n')
        print('delivery-review: platform identity refused code=' + code, flush=True)
        raise
    (destination / 'manifest.json').write_bytes(canonical_bytes({'records': records, 'identity': identity}) + b'\n')
    return identity
