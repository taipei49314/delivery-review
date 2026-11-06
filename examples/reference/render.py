"""Create a new authored reference tree; never publish or run it automatically."""
import argparse
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
VARIANTS = ('base', 'e01', 'test_failure', 'zero_tests', 'all_skipped', 'hostile_html')
PRODUCER_PATHS = {'pr_head': '.github/workflows/pytest-producer.yml',
                  'github_merge': '.github/workflows/pytest-producer-merge.yml'}


def files(producer_sha, variant, source_mode='pr_head'):
    if (not re.fullmatch(r'[0-9a-f]{40}', producer_sha) or variant not in VARIANTS
            or type(source_mode) is not str or source_mode not in PRODUCER_PATHS):
        raise ValueError('exact producer SHA, declared variant and fixed source mode required')
    names = ('README.md', 'parcel.py', 'tests/test_parcel.py')
    result = {name: (HERE / 'repository' / name).read_bytes() for name in names}
    result['LICENSE'] = (PROJECT / 'LICENSE').read_bytes()
    result['requirements.txt'] = (PROJECT / 'producer/requirements.txt').read_bytes()
    template = (HERE / 'consumer.yml.template').read_bytes()
    if template.count(b'REPLACE_WITH_APPROVED_PRODUCER_SHA') != 1 or template.count(PRODUCER_PATHS['pr_head'].encode()) != 1:
        raise ValueError('invalid consumer template')
    result['.github/workflows/delivery-review.yml'] = template.replace(
        b'REPLACE_WITH_APPROVED_PRODUCER_SHA', producer_sha.encode()).replace(
        PRODUCER_PATHS['pr_head'].encode(), PRODUCER_PATHS[source_mode].encode())
    if variant == 'e01':
        before = b'    total = 0\n    for quantity in quantities:\n        total += quantity\n    return total\n'
        if result['parcel.py'].count(before) != 1:
            raise ValueError('reference source changed')
        result['parcel.py'] = result['parcel.py'].replace(before, b'    return sum(quantities)\n')
    elif variant == 'test_failure':
        result['tests/test_parcel.py'] += b'\n\ndef test_authored_failure():\n    assert total_units([1, 2]) == 999\n'
    elif variant == 'zero_tests':
        result['tests/test_parcel.py'] = b'# Authored zero-collection control, not a passing test suite.\n'
    elif variant == 'all_skipped':
        result['tests/test_parcel.py'] += b'\n\npytestmark = pytest.mark.skip(reason="authored all-skipped control")\n'
    elif variant == 'hostile_html':
        result['tests/test_parcel.py'] += b'\n\ndef test_authored_html_failure():\n    assert False, "<script>alert(1)</script>&\\\"\\\'"\n'
    manifest = {'kind': 'delivery-review-authored-reference', 'fixture_version': 1, 'origin': 'authored_first_party',
        'variant': variant, 'producer_sha': producer_sha, 'source_mode': source_mode,
        'producer_path': PRODUCER_PATHS[source_mode], 'product_integration': 'NOT_RUN',
        'files': [{'path': name, 'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(result.items())]}
    result['fixture-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    return result


def render(destination, *, producer_sha, variant, source_mode='pr_head'):
    content = files(producer_sha, variant, source_mode)
    supplied = Path(destination).absolute()
    destination = supplied.parent.resolve(strict=True) / supplied.name
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError('destination must be new under an existing directory')
    if destination == PROJECT or PROJECT in destination.parents:
        raise ValueError('reference destination must be outside the product checkout')
    destination.mkdir(mode=0o700)
    for name, raw in content.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--producer-sha', required=True)
    parser.add_argument('--variant', choices=VARIANTS, default='base')
    parser.add_argument('--source-mode', choices=tuple(PRODUCER_PATHS), default='pr_head',
                        help='select a fixed reusable workflow while authoring; never a workflow input')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = render(args.out, producer_sha=args.producer_sha, variant=args.variant, source_mode=args.source_mode)
    print(json.dumps({'operation': 'author_reference', 'destination': str(result), 'product_integration': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
