"""Public CLI regression over a fixed public subset and first-party controls.

No reviewed tests, scripts, dependencies, upstream runner, or detector internals
are executed. Third-party fixture bytes remain in the explicit scratch output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import urllib.request

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'src'))
from delivery_review.adapters.engines import PINS
from delivery_review.engine_runner import run_engine
from delivery_review.inspection_cli import load_tools
from delivery_review.source_snapshot import materialize_snapshot, export_snapshot

SELECTION = Path(__file__).with_name('engine_regression_cases.json')
AMENDMENT = Path(__file__).with_name('engine_regression_amendment_v2.json')
DATE = '2026-11-06'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def blob_sha(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw, usedforsecurity=False).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def acquire(destination):
    selection = json.loads(SELECTION.read_bytes())
    destination.mkdir(parents=False, exist_ok=False)
    upstream = selection['upstream']
    prefix = f"https://raw.githubusercontent.com/{upstream['repository']}/{upstream['source_sha']}/"
    entries = selection['cases'] + [
        {'path': 'LICENSE', 'git_blob_sha': '973f35b9ab7627f1ae64c2a74f52d35109898fcd'},
        {'path': 'tests/test_cases_runner.py'}, {'path': 'src/checkwash/cases.py'},
        {'path': 'docs/KNOWN-ISSUES-v0.9.0.md'}]
    rows = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for entry in entries:
        url = prefix + entry['path']
        request = urllib.request.Request(url, headers={'User-Agent': 'delivery-review-regression/1', 'Accept-Encoding': 'identity'})
        with opener.open(request, timeout=20) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError('public fixture response refused')
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024 or ('size_bytes' in entry and len(raw) != entry['size_bytes']) or (
                'git_blob_sha' in entry and blob_sha(raw) != entry['git_blob_sha']):
            raise ValueError('public fixture size or Git blob mismatch')
        save(destination / entry['path'], raw)
        rows.append({'path': entry['path'], 'source_url': url, 'size_bytes': len(raw),
                     'sha256': sha(raw), 'git_blob_sha': blob_sha(raw)})
    save(destination / 'selection.json', SELECTION.read_bytes())
    save(destination / 'acquisition.json', encoded({'kind': 'public-fixture-acquisition',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'upstream': upstream, 'files': rows}))
    return rows


def parse_case(raw):
    """Read only the documented inert sections; task/env overrides are refused."""
    if len(raw) > 65536:
        raise ValueError('fixture too large')
    sections, key, lines = {}, None, []
    for line in raw.decode('utf-8-sig').replace('\r\n', '\n').split('\n'):
        match = re.fullmatch(r'=== (meta|expect|before: .+|after: .+|head: .+) ===', line)
        if match:
            if key is not None:
                sections[key] = '\n'.join(lines)
            key, lines = match[1], []
            if key in sections:
                raise ValueError('duplicate fixture section')
        elif line.startswith('==='):
            raise ValueError('unsupported fixture section')
        elif key is not None:
            lines.append(line)
        elif line.strip():
            raise ValueError('content before fixture section')
    if key is not None:
        sections[key] = '\n'.join(lines)
    expected = json.loads(sections['expect'])
    before, after, unchanged = {}, {}, {}
    for label, content in sections.items():
        if ': ' not in label:
            continue
        side, path = label.split(': ', 1)
        if not re.fullmatch(r'[A-Za-z0-9_.\-/]+', path) or any(part in {'', '.', '..', '.git'} for part in path.split('/')):
            raise ValueError('unsupported fixture path')
        {'before': before, 'after': after, 'head': unchanged}[side][path] = content.encode()
    if set(unchanged) & (set(before) | set(after)):
        raise ValueError('ambiguous unchanged fixture path')
    return {**unchanged, **before}, {**unchanged, **after}, expected, sections.get('meta', '')


class Objects:
    def __init__(self):
        self.values = {}

    def add(self, kind, payload):
        raw = kind.encode() + b' ' + str(len(payload)).encode() + b'\0' + payload
        oid = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        self.values[oid] = raw
        return oid

    def tree(self, files):
        root = {}
        for path, raw in files.items():
            cursor = root
            parts = path.split('/')
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = raw
        def build(node):
            entries = []
            for name, value in node.items():
                mode = b'40000' if isinstance(value, dict) else b'100644'
                oid = build(value) if isinstance(value, dict) else self.add('blob', value)
                entries.append((name.encode() + (b'/' if isinstance(value, dict) else b''), mode, name.encode(), oid))
            return self.add('tree', b''.join(mode + b' ' + name + b'\0' + bytes.fromhex(oid)
                for _, mode, name, oid in sorted(entries)))
        return build(root)

    def commit(self, files, parent=None):
        body = b'tree ' + self.tree(files).encode() + b'\n'
        if parent:
            body += b'parent ' + parent.encode() + b'\n'
        body += b'author Regression <fixture@example.invalid> 1 +0000\ncommitter Regression <fixture@example.invalid> 1 +0000\n\nauthored materialization of declared fixture bytes\n'
        return self.add('commit', body)


def expectation_match(expected, actual):
    if len(expected) != len(actual):
        return False
    def matches(want, have):
        return all((set(value) <= set(have.get(key) or []) if isinstance(value, list) else value == have.get(key))
                   for key, value in want.items())
    def assign(index, available):
        return index == len(expected) or any(matches(expected[index], actual[item]) and
            assign(index + 1, available - {item}) for item in available)
    return assign(0, set(range(len(actual))))


def preserve_native(observation, raw_files):
    """Check the adapter retained native facts without relabelling or inventing rows."""
    native = json.loads(raw_files['stdout'])
    return (native == observation['parsed_native']
        and observation['verdict'] == (native['verdict'] if observation['status'] == 'completed' else 'not_judged')
        and all(row['native'] == native['findings'][index] for index, row in enumerate(observation['findings']))
        and len(observation['findings']) == len(native['findings'])
        and all(row['normalized']['fingerprint'] == row['native']['fingerprint']
                and row['normalized']['rule'] == row['native']['rule']
                and row['normalized']['severity'] == row['native']['severity'] for row in observation['findings']))


def compare_case_expectation(expectation, actual, native):
    if 'upstream_findings' in expectation:
        visible = [row for row in native.get('findings', []) if not row.get('allowlisted', False)]
        return actual['adapter_status'] == 'completed' and expectation_match(expectation['upstream_findings'], visible)
    matched = all(actual.get(key) == value for key, value in expectation.items() if key != 'allowlisted_rules')
    if 'allowlisted_rules' in expectation:
        matched &= expectation['allowlisted_rules'] == [row['rule'] for row in native.get('findings', []) if row.get('allowlisted')]
    return matched


def run_case(output, tools, *, case_id, engine, before, after, expectation, origin, require_complete=False):
    case = output / 'cases' / case_id
    case.mkdir(parents=True, exist_ok=False)
    save(case / 'input.json', encoded({'case_id': case_id, 'engine': engine, 'origin': origin,
        'expected': expectation, 'label_method': 'upstream declared fixture expectation' if origin['kind'] == 'public_gwcase' else
        'first-party preregistered behavior control; not an adjudicated accuracy label',
        'limitations': ['fixture tests never executed', 'not independent accuracy evidence', 'not full product acceptance']}))
    objects = Objects()
    base = objects.commit(before)
    head = objects.commit(after, base)
    policy = {'threshold': 'high', 'evaluation_date': DATE}
    if require_complete:
        policy['require_complete'] = True
    save(case / 'native-policy.json', encoded(policy))
    with tempfile.TemporaryDirectory(prefix='delivery-review-native-regression-') as temporary:
        snapshot = materialize_snapshot(Path(temporary) / 'subject', repository='authored/native-regression',
            head_sha=head, base_tip_sha=base, objects=objects.values, git_executable=tools.git_executable)
        save(case / 'source-inventory.json', snapshot.inventory_bytes)
        for oid, raw in export_snapshot(snapshot).items():
            save(case / 'loose-objects' / oid, raw)
        for oid, raw in objects.values.items():
            save(case / 'canonical-objects' / oid, raw)
        result = run_engine(snapshot, engine_name=engine, wheel_path=tools.engine_wheels[engine],
            python_executable=tools.python_executable, git_executable=tools.git_executable,
            destination=Path(temporary) / 'engine', native_policy=policy)
        observation = result.observation
        for relative in ['execution.json', 'observation.json', 'installation.json']:
            save(case / relative, (result.directory / relative).read_bytes())
        for directory in ['setup', 'raw']:
            for file in sorted((result.directory / directory).iterdir()):
                save(case / directory / file.name, file.read_bytes())
        native = observation['parsed_native'] or {}
        actual = {'native_exit': observation['process']['exit_code'], 'native_verdict': native.get('verdict'),
            'adapter_status': observation['status'], 'adapter_verdict': observation['verdict'],
            'coverage': observation['coverage']['status'], 'known_block': observation['known_block'],
            'review_required': observation['review_required'], 'rules': [row['rule'] for row in native.get('findings', [])],
            'reason_codes': observation['reason_codes']}
        matched = compare_case_expectation(expectation, actual, native)
        preserved = preserve_native(observation, result.raw_files)
    row = {'case_id': case_id, 'engine': engine, 'origin': origin['kind'], 'subject': {'base': base, 'head': head},
        'expectation_matched': bool(matched), 'native_preserved': preserved, 'actual': actual,
        'execution_sha256': sha(result.manifest_bytes), 'source_inventory_sha256': sha(snapshot.inventory_bytes),
        'full_product_integration': 'NOT_RUN'}
    save(case / 'result.json', encoded(row))
    print(json.dumps(row), flush=True)
    return row, native


BASE_TEST = b'from app import value\n\ndef test_value():\n    assert value() == 7\n'
WEAK_TEST = b'from app import value\n\ndef test_value():\n    pass\n'
WORKFLOW = b'name: regression\non: pull_request\njobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@' + b'a' * 40 + b'\n      - run: python -m pytest\n'


def authored(output, tools, *, absence_only=False):
    rows = []
    origin = {'kind': 'authored_first_party_control', 'source': 'tools/qualification/engine_regression.py'}
    def run(case_id, engine, before, after, expected, **kwargs):
        row, native = run_case(output, tools, case_id=case_id, engine=engine, before=before, after=after,
            expectation=expected, origin=origin, **kwargs)
        rows.append(row)
        return native
    before = {'app.py': b'def value():\n    return 7\n', 'tests/test_app.py': BASE_TEST,
              '.github/workflows/ci.yml': WORKFLOW}
    if absence_only:
        ledger_controls(run, before, absence_only=True)
        return rows
    check_after = {**before, 'tests/test_app.py': WEAK_TEST}
    pin_after = {**before, '.github/workflows/ci.yml': WORKFLOW.replace(b'@' + b'a' * 40, b'@main')}
    seeds = {}
    for engine, after, rule in [('checkwash', check_after, 'ASSERT_REMOVED'), ('pinwash', pin_after, 'JUDGE_UNPINNED')]:
        native = run(engine + '-weakening-seed', engine, before, after,
            {'native_exit': 1, 'native_verdict': 'block', 'adapter_status': 'completed', 'rules': [rule]})
        seeds[engine] = next(row for row in native['findings'] if row['rule'] == rule)
        entry = seeds[engine]
        exemption = ('[[allow]]\n' + '\n'.join(key + ' = ' + json.dumps(value) for key, value in {
            'rule': rule, 'fingerprint': entry['fingerprint'], 'reason': 'Authored regression control',
            'author': 'fixture', 'created': '2026-11-01', 'expires': '2026-11-30'}.items()) + '\n').encode()
        for side in ('base', 'head'):
            path = f'.{engine}/allow.toml'
            base_files = {**before, **({path: exemption} if side == 'base' else {})}
            head_files = {**after, path: exemption}
            expected = {'native_exit': 0 if side == 'base' else 1, 'native_verdict': 'pass' if side == 'base' else 'block',
                        'adapter_status': 'completed'}
            if engine == 'checkwash' and side == 'base':
                expected['allowlisted_rules'] = [rule]
            elif side == 'base':
                expected['rules'] = []
            else:
                expected['review_required'] = True
            run(engine + '-exemption-' + side, engine, base_files, head_files, expected)
    run('pinwash-refactor', 'pinwash', before, {**before, 'app.py': b'def value():\n    result = 7\n    return result\n'},
        {'native_exit': 0, 'native_verdict': 'pass', 'adapter_status': 'completed', 'rules': []})
    run('pinwash-invalid-config', 'pinwash', {**before, '.pinwash/allow.toml': b'\xff'},
        {**before, '.pinwash/allow.toml': b'\xff'}, {'native_exit': 0, 'native_verdict': 'pass', 'adapter_status': 'error'})
    for route in ('symbol', 'opaque'):
        base = {'billing.py': b'def compute_total(items):\n    return sum(items)\n',
            'tests/test_total.py': b'from billing import compute_total\n\ndef test_total():\n    assert compute_total([1, 2]) == 3\n',
            'app/templates/invoice.html': b'<!-- invoice -->\n'}
        after = {**base, 'tests/test_total.py': base['tests/test_total.py'].replace(b'== 3', b'> 0')}
        after['billing.py' if route == 'symbol' else 'app/templates/invoice.html'] = (
            b'def compute_total(items):\n    return sum(items) + 0\n' if route == 'symbol' else b'<!-- invoice comment changed -->\n')
        run('checkwash-419-' + route, 'checkwash', base, after,
            {'native_exit': 0, 'native_verdict': 'pass', 'adapter_status': 'completed', 'rules': ['ASSERT_WEAKENED']})
    ledger_controls(run, before)
    return rows


def ledger_controls(run, before, *, absence_only=False):
    task = {'schema_version': 1, 'id': 'T-1', 'description': 'Authored unverified task', 'status': 'TODO',
            'state': 'TODO', 'owner': '', 'time': '', 'evidence': '', 'handoff': None, 'history': []}
    for name, extra in [('absent', {}), ('malformed', {'governance/tasks/T-1.json': b'{invalid-json'}),
                        ('partial', {'governance/tasks/T-1.json': encoded(task)})]:
        if absence_only and name != 'absent':
            continue
        for strict in (False, True):
            block = strict or name == 'malformed'
            run('ledgerwash-' + name + ('-strict' if strict else ''), 'ledgerwash', before, {**before, **extra},
                {'native_exit': int(block), 'native_verdict': 'block' if block else 'pass',
                 'adapter_status': 'not_provided' if name == 'absent' else 'completed',
                 'coverage': 'not_provided' if name == 'absent' else 'gaps'},
                require_complete=strict)


def run(cache, output, config, *, case_set='all'):
    if case_set not in {'all', 'ledger-absence-amendment'}:
        raise ValueError('unsupported case set')
    source_paths = ['src/delivery_review/engine_runner.py', 'src/delivery_review/adapters/engines.py',
        'src/delivery_review/source_snapshot.py', 'tools/qualification/engine_regression.py',
        'tools/qualification/engine_regression_cases.json', 'tools/qualification/engine_regression_amendment_v2.json']
    source_files = [{'path': name, 'sha256': sha((PROJECT / name).read_bytes())} for name in source_paths]
    selection = json.loads(SELECTION.read_bytes())
    acquisition = json.loads((cache / 'acquisition.json').read_bytes())
    if acquisition['upstream'] != selection['upstream'] or (cache / 'selection.json').read_bytes() != SELECTION.read_bytes():
        raise ValueError('source selection changed')
    tools = load_tools(config, live=True)
    output.mkdir(parents=False, exist_ok=False)
    save(output / 'selection.json', SELECTION.read_bytes())
    save(output / 'expectation-amendment-v2.json', AMENDMENT.read_bytes())
    save(output / 'runner.py', Path(__file__).read_bytes())
    for entry in acquisition['files']:
        raw = (cache / entry['path']).read_bytes()
        if len(raw) != entry['size_bytes'] or sha(raw) != entry['sha256'] or blob_sha(raw) != entry['git_blob_sha']:
            raise ValueError('saved public fixture bytes changed')
        save(output / 'upstream' / entry['path'], raw)
    save(output / 'upstream/acquisition.json', (cache / 'acquisition.json').read_bytes())
    rows = []
    for entry in selection['cases']:
        if case_set != 'all':
            break
        raw = (cache / entry['path']).read_bytes()
        if blob_sha(raw) != entry['git_blob_sha'] or len(raw) != entry['size_bytes']:
            raise ValueError('selected public fixture bytes changed')
        before, after, expected, metadata = parse_case(raw)
        origin = {'kind': 'public_gwcase', **selection['upstream'], 'path': entry['path'],
            'raw_sha256': sha(raw), 'git_blob_sha': entry['git_blob_sha'], 'metadata_verbatim': metadata,
            'materialization': 'before/after sections; unchanged head sections copied to both sides; no task/env overrides'}
        row, unused = run_case(output, tools, case_id=entry['case_id'], engine='checkwash', before=before, after=after,
            expectation={'upstream_findings': expected}, origin=origin)
        rows.append(row)
    rows.extend(authored(output, tools, absence_only=case_set == 'ledger-absence-amendment'))
    final_source_files = [{'path': name, 'sha256': sha((PROJECT / name).read_bytes())} for name in source_paths]
    receipt = {'kind': 'delivery-review-native-engine-regression', 'version': 1,
        'expectation_version': 2, 'case_set': case_set,
        'observed_at': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(), 'python': platform.python_version(),
        'evaluation_date': DATE, 'engine_pins': {name: {'version': value[0], 'wheel_sha256': value[1]} for name, value in PINS.items()},
        'source_files': source_files, 'final_source_files': final_source_files,
        'source_files_unchanged': source_files == final_source_files,
        'upstream': selection['upstream'], 'results': rows, 'case_count': len(rows),
        'expectations_matched': sum(row['expectation_matched'] for row in rows),
        'native_preserved': sum(row['native_preserved'] for row in rows),
        'full_product_integration': 'NOT_RUN', 'independent_accuracy_evidence': False,
        'limitations': ['in-sample authored upstream cases; not a random or held-out corpus',
            'fixtures adapted to immutable Git snapshots and public CLI; upstream in-memory expectations retained separately',
            'source tests and production never executed', 'no product report freshness or hosted producer qualification']}
    save(output / 'receipt.json', encoded(receipt))
    inventory = [{'path': path.relative_to(output).as_posix(), 'size_bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())}
                 for path in sorted(output.rglob('*')) if path.is_file()]
    save(output / 'inventory.json', encoded(inventory))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    download = commands.add_parser('acquire')
    download.add_argument('--out', required=True, type=Path)
    execute = commands.add_parser('run')
    execute.add_argument('--source-cache', required=True, type=Path)
    execute.add_argument('--tools', required=True, type=Path)
    execute.add_argument('--out', required=True, type=Path)
    execute.add_argument('--case-set', choices=['all', 'ledger-absence-amendment'], default='all')
    args = parser.parse_args()
    if args.command == 'acquire':
        result = acquire(args.out)
        print(json.dumps({'operation': 'acquire', 'files': len(result)}))
    else:
        result = run(args.source_cache, args.out, args.tools, case_set=args.case_set)
        print(json.dumps({'cases': result['case_count'], 'matched': result['expectations_matched'],
                          'native_preserved': result['native_preserved'], 'full_product_integration': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
