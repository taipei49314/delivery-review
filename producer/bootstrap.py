"""Trusted reusable-workflow bootstrap; no project checkout or import.

Claims are obtained directly from the authenticated Actions HTTPS endpoint.
Decoding here is not a standalone JWT signature verifier. Verifiers must use
the independently verified Sigstore certificate, not this decoded observation.
No bearer token, JWT or endpoint locator is written to output or disk.
"""
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

PRODUCER_REPOSITORY = 'taipei49314/delivery-review'
PRODUCER_REPOSITORY_ID = 1438021135
PRODUCER_PATH = '.github/workflows/pytest-producer.yml'
PRODUCER_PATHS = {'pr_head': PRODUCER_PATH, 'github_merge': '.github/workflows/pytest-producer-merge.yml'}
AUDIENCE = 'delivery-review-producer-v1'


class BootstrapError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(condition, code):
    if not condition:
        raise BootstrapError(code)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate_json_key')
            value[key] = item
        return value
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)


def number(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,15}', value)
            and int(value) <= 2**53 - 1, 'invalid_platform_identity')
    return int(value)


def producer_path(source_mode):
    require(type(source_mode) is str and source_mode in PRODUCER_PATHS, 'unsupported_producer_source_mode')
    return PRODUCER_PATHS[source_mode]


def validate_claims(claims, environment, *, now, source_mode='pr_head'):
    path = producer_path(source_mode)
    require(type(claims) is dict, 'invalid_oidc_claims')
    require(claims.get('iss') == 'https://token.actions.githubusercontent.com'
            and claims.get('aud') == AUDIENCE, 'oidc_issuer_or_audience_mismatch')
    require(all(type(claims.get(key)) is int for key in ('exp', 'iat', 'nbf'))
            and claims['iat'] <= now + 30 and claims['nbf'] <= now + 30 < claims['exp']
            and now < claims['exp'] <= claims['iat'] + 3600, 'oidc_time_invalid')
    require(environment.get('GITHUB_SERVER_URL') == 'https://github.com'
            and environment.get('GITHUB_API_URL') == 'https://api.github.com'
            and environment.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and claims.get('runner_environment') == 'github-hosted'
            and claims.get('event_name') == environment.get('GITHUB_EVENT_NAME') == 'pull_request',
            'unsupported_hosted_execution')
    repository = claims.get('repository')
    require(type(repository) is str and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            and all(part not in {'.', '..'} for part in repository.split('/'))
            and repository == environment.get('GITHUB_REPOSITORY'), 'oidc_repository_mismatch')
    for key, variable in [('repository_id', 'GITHUB_REPOSITORY_ID'), ('run_id', 'GITHUB_RUN_ID'),
                          ('run_attempt', 'GITHUB_RUN_ATTEMPT')]:
        number(claims.get(key))
        require(claims[key] == environment.get(variable), 'oidc_execution_mismatch')
    number(claims.get('check_run_id'))
    sha, producer_sha = claims.get('sha'), claims.get('job_workflow_sha')
    require(type(sha) is str and re.fullmatch(r'[0-9a-f]{40}', sha)
            and sha == environment.get('GITHUB_SHA'), 'oidc_source_mismatch')
    require(type(producer_sha) is str and re.fullmatch(r'[0-9a-f]{40}', producer_sha)
            and claims.get('job_workflow_ref') == PRODUCER_REPOSITORY + '/' + path + '@' + producer_sha,
            'producer_must_be_called_at_full_sha')
    ref = claims.get('ref')
    require(type(ref) is str and re.fullmatch(r'refs/pull/[1-9][0-9]{0,15}/merge', ref)
            and ref == environment.get('GITHUB_REF'), 'oidc_pr_ref_mismatch')
    return {key: claims[key] for key in ('repository', 'repository_id', 'run_id', 'run_attempt', 'check_run_id',
            'sha', 'ref', 'job_workflow_ref', 'job_workflow_sha', 'event_name', 'runner_environment', 'iat', 'exp')}


def request_claims(environment, *, now=None, source_mode='pr_head'):
    """Only the trusted runner environment supplies the endpoint and credential."""
    producer_path(source_mode)
    endpoint, token = environment.get('ACTIONS_ID_TOKEN_REQUEST_URL'), environment.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    require(type(endpoint) is str and len(endpoint) <= 8192 and endpoint.isascii()
            and not any(ord(c) <= 32 or ord(c) == 127 for c in endpoint), 'invalid_oidc_endpoint')
    parsed = urllib.parse.urlsplit(endpoint)
    require(parsed.scheme == 'https' and parsed.port in {None, 443} and not parsed.username
            and not parsed.password and not parsed.fragment and parsed.path.startswith('/')
            and re.fullmatch(r'[a-z0-9-]+\.actions\.githubusercontent\.com', parsed.hostname or ''),
            'invalid_oidc_endpoint')
    require(type(token) is str and 16 <= len(token) <= 16384 and token.isascii()
            and not any(c.isspace() for c in token), 'invalid_oidc_request_token')
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    require(len(dict(query)) == len(query) and all(key != 'audience' for key, _ in query), 'invalid_oidc_query')
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query + [('audience', AUDIENCE)])))
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
        'Accept': 'application/json', 'Accept-Encoding': 'identity'}, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        require(response.getcode() == 200 and response.geturl() == url, 'oidc_endpoint_failed')
        raw = response.read(32769)
    require(len(raw) <= 32768, 'oidc_response_limit')
    value = strict_json(raw)
    require(type(value) is dict and type(value.get('value')) is str, 'invalid_oidc_response')
    jwt = value['value'].split('.')
    require(len(jwt) == 3 and all(re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in jwt), 'invalid_oidc_token')
    header = strict_json(base64.urlsafe_b64decode(jwt[0] + '=' * (-len(jwt[0]) % 4)))
    require(header.get('alg') == 'RS256' and type(header.get('kid')) is str, 'unsupported_oidc_token')
    claims = strict_json(base64.urlsafe_b64decode(jwt[1] + '=' * (-len(jwt[1]) % 4)))
    return validate_claims(claims, environment, now=int(time.time()) if now is None else now, source_mode=source_mode)


def main():
    try:
        require(sys.argv[1:] in ([], ['github_merge']), 'invalid_bootstrap_command')
        mode = 'github_merge' if sys.argv[1:] else 'pr_head'
        claims = request_claims(os.environ, source_mode=mode)
        # Name reuse must not bootstrap code from a replacement repository.
        url = 'https://api.github.com/repos/' + PRODUCER_REPOSITORY
        request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
            'Accept-Encoding': 'identity', 'User-Agent': 'delivery-review-producer/1',
            'X-GitHub-Api-Version': '2026-03-10'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=20) as response:
            require(response.status == 200 and response.geturl() == url, 'producer_repository_unavailable')
            raw = response.read(262145)
        require(len(raw) <= 262144, 'producer_repository_response_limit')
        repository = strict_json(raw)
        require(repository.get('id') == PRODUCER_REPOSITORY_ID
                and repository.get('full_name') == PRODUCER_REPOSITORY, 'producer_repository_mismatch')
        output = Path(os.environ['GITHUB_OUTPUT'])
        with output.open('a', encoding='utf-8') as stream:
            stream.write('producer_sha=' + claims['job_workflow_sha'] + '\n')
        return 0
    except Exception:
        print('delivery-review: trusted producer bootstrap refused')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
