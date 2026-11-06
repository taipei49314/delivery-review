"""Host-authored receipt of observations, not an authentication mechanism."""
from copy import deepcopy
import hashlib

from delivery_review.contracts import loads_strict, validate_receipt
from delivery_review.contracts import canonical_bytes
from delivery_review.adapters.pytest_junit import JUnitError, parse_pytest_junit
from .host import ProducerError, ProducerExecution


def build_receipt(execution: ProducerExecution, *, source_claim: dict, producer_identity: dict,
                  recorded_at: str, receipt_id: str) -> dict:
    """Record supplied platform identities and measured source/exit/JUnit bytes.

    ``source_claim`` contains repository_id, head_repository_id, pr_number and
    source. These IDs still need independent platform and attestation checks.
    A receipt alone never upgrades producer_declared to workflow_bound.
    """
    if type(execution) is not ProducerExecution or type(source_claim) is not dict or set(source_claim) != {
            'repository_id', 'head_repository_id', 'pr_number', 'source'}:
        raise ProducerError('invalid_receipt_input')
    inventory = loads_strict(execution.source_inventory_bytes)
    expected = {'mode': 'pr_head', 'base_tip_sha': inventory['base_tip_sha'], 'head_sha': inventory['head_sha'],
                'checked_out_sha': inventory['head_sha'], 'merge_parents': []}
    if source_claim['source'] != expected:
        raise ProducerError('receipt_source_mismatch')
    if (execution.stopped is not True or execution.cleaned is not True or execution.error_codes
            or execution.setup is None or execution.setup.error is not None or execution.setup.exit_code != 0
            or execution.test is None or execution.test.error is not None or type(execution.test.exit_code) is not int
            or execution.test.exit_code not in {0, 1, 2, 3, 4, 5}
            or execution.junit_bytes is None):
        raise ProducerError('incomplete_producer_execution')
    raw = execution.junit_bytes
    try:
        observed = parse_pytest_junit(raw)
    except JUnitError:
        raise ProducerError('invalid_receipt_junit') from None
    if not observed['valid'] or canonical_bytes(observed) != execution.junit_observation_bytes:
        raise ProducerError('invalid_receipt_junit')
    counts = observed['case_counts']
    result = {
        'schema_version': 1, 'receipt_id': receipt_id, 'recorded_at': recorded_at,
        **deepcopy(source_claim), 'provenance': 'producer_declared', 'producer': deepcopy(producer_identity),
        'execution': {'command': list(execution.command),
                      'setup_exit': None if execution.setup is None else execution.setup.exit_code,
                      'test_exit': None if execution.test is None else execution.test.exit_code,
                      # No independent collect-only invocation took place.
                      'collection_exit': None,
                      'tests': {'passed': counts['passed'], 'failed': counts['failures'],
                                'errors': counts['errors'], 'skipped': counts['skipped']},
                      'junit': {'path': 'junit.xml', 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}}}
    validate_receipt(result)
    return result
