"""Bounded, non-executing observations of a qualified pytest JUnit subset.

This module does not authenticate a producer, interpret process exits, or decide
readiness. Callers must reject observations with ``valid == False``. Native
counts and cases remain available when duplicate identities or contradictory
counts make a structurally parseable report invalid.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import hashlib
import re
from typing import Any
from xml.parsers import expat

from delivery_review.contracts import ContractError

DIALECT = "pytest-8.4.2-xunit2"


class JUnitError(ContractError):
    """The input cannot safely be observed in the qualified dialect."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JUnitLimits:
    """Callers may lower these ceilings, but cannot raise the hard limits."""

    max_bytes: int = 2 * 1024 * 1024
    max_depth: int = 8
    max_elements: int = 30002
    max_cases: int = 10000
    max_attributes: int = 8
    max_attribute_chars: int = 8192
    max_text_chars: int = 256 * 1024
    max_total_text_chars: int = 1024 * 1024


@dataclass
class _Node:
    tag: str
    attributes: dict[str, str]
    children: list[_Node] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    text_chars: int = 0


def _require(condition: bool, code: str = "unsupported_junit_dialect") -> None:
    if not condition:
        raise JUnitError(code)


def _limits(limits: JUnitLimits) -> None:
    _require(type(limits) is JUnitLimits, "invalid_junit_limits")
    ceiling = JUnitLimits()
    for item in fields(limits):
        value = getattr(limits, item.name)
        _require(type(value) is int and 0 < value <= getattr(ceiling, item.name),
                 "invalid_junit_limits")


def _tree(raw: bytes, limits: JUnitLimits) -> _Node:
    _require(type(raw) is bytes, "invalid_junit_input")
    _require(len(raw) <= limits.max_bytes, "junit_resource_limit")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise JUnitError("invalid_junit_encoding") from None
    parser = expat.ParserCreate(encoding="utf-8")
    stack: list[_Node] = []
    root: _Node | None = None
    element_count = 0
    case_count = 0
    text_count = 0

    def forbidden(*_args: Any) -> None:
        raise JUnitError("unsafe_junit_xml")

    def declaration(version: str, encoding: str | None, standalone: int) -> None:
        _require(version == "1.0" and
                 (encoding is None or encoding.lower() == "utf-8"),
                 "invalid_junit_encoding")

    def start(tag: str, attributes: dict[str, str]) -> None:
        nonlocal root, element_count, case_count
        element_count += 1
        case_count += tag == "testcase"
        _require(len(stack) < limits.max_depth and element_count <= limits.max_elements
                 and case_count <= limits.max_cases
                 and len(attributes) <= limits.max_attributes,
                 "junit_resource_limit")
        _require(all(len(key) <= limits.max_attribute_chars and
                     len(value) <= limits.max_attribute_chars
                     for key, value in attributes.items()), "junit_resource_limit")
        node = _Node(tag, dict(attributes))
        if stack:
            stack[-1].children.append(node)
        else:
            _require(root is None, "malformed_junit_xml")
            root = node
        stack.append(node)

    def end(_tag: str) -> None:
        stack.pop()

    def text(value: str) -> None:
        nonlocal text_count
        text_count += len(value)
        _require(text_count <= limits.max_total_text_chars, "junit_resource_limit")
        if stack:
            node = stack[-1]
            node.text_chars += len(value)
            _require(node.text_chars <= limits.max_text_chars, "junit_resource_limit")
            node.text_parts.append(value)

    parser.XmlDeclHandler = declaration
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.SkippedEntityHandler = forbidden
    parser.ProcessingInstructionHandler = forbidden
    # No DTD is permitted, even one that declares no entities. Predefined XML
    # escapes and numeric character references are decoded by Expat normally.
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(raw, True)
    except expat.ExpatError:
        raise JUnitError("malformed_junit_xml") from None
    _require(root is not None, "malformed_junit_xml")
    return root


def _attributes(node: _Node, required: set[str], optional: set[str] | None = None) -> None:
    keys = set(node.attributes)
    _require(required <= keys <= required | (optional or set()))


def _container(node: _Node) -> None:
    _require(not "".join(node.text_parts).strip())


def _count(value: str) -> int:
    _require(re.fullmatch(r"0|[1-9][0-9]{0,15}", value) is not None,
             "invalid_junit_counts")
    count = int(value)
    _require(count <= 2**53 - 1, "invalid_junit_counts")
    return count


def _duration(value: str) -> None:
    # Native decimal spelling is retained. No floating point coercion, NaN,
    # infinities, exponent notation, signs or unbounded integers are admitted.
    _require(re.fullmatch(r"(?:0|[1-9][0-9]{0,12})\.[0-9]{3}", value) is not None)


def parse_pytest_junit(raw: bytes, *, dialect: str = DIALECT,
                       limits: JUnitLimits = JUnitLimits()) -> dict[str, Any]:
    """Parse saved bytes without opening paths, executing code or fetching URLs.

    ``valid`` covers dialect semantics only. Even a valid report with no reason
    codes is an unauthenticated observation, not evidence that tests really ran.
    ``native_counts`` are suite declarations; ``case_counts`` count XML records.
    Pytest's phase-report quirks are deliberately not repaired or deduplicated.
    """
    _limits(limits)
    _require(type(dialect) is str and dialect == DIALECT, "unsupported_junit_dialect")
    root = _tree(raw, limits)
    _require(root.tag == "testsuites")
    _attributes(root, {"name"})
    _require(root.attributes["name"] == "pytest tests" and len(root.children) == 1)
    _container(root)
    suite = root.children[0]
    _require(suite.tag == "testsuite")
    _attributes(suite, {"name", "errors", "failures", "skipped", "tests", "time",
                        "timestamp", "hostname"})
    _require(suite.attributes["name"] == "delivery-review")
    _container(suite)
    _duration(suite.attributes["time"])
    native = {name: _count(suite.attributes[name])
              for name in ("tests", "failures", "errors", "skipped")}
    counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    cases = []
    identities: set[tuple[str, str]] = set()
    reasons: list[str] = []
    duplicate = False
    for case in suite.children:
        _require(case.tag == "testcase")
        _attributes(case, {"classname", "name", "time"})
        _require(bool(case.attributes["name"]))
        _duration(case.attributes["time"])
        _container(case)
        identity = (case.attributes["classname"], case.attributes["name"])
        duplicate = duplicate or identity in identities
        identities.add(identity)
        _require(len(case.children) <= 1)
        detail = None
        outcome = "passed"
        if case.children:
            child = case.children[0]
            _require(child.tag in {"failure", "error", "skipped"} and not child.children)
            _attributes(child, {"message"}, {"type"} if child.tag == "skipped" else set())
            if child.tag == "skipped":
                _require(child.attributes.get("type") in {"pytest.skip", "pytest.xfail"})
            outcome = {"failure": "failures", "error": "errors", "skipped": "skipped"}[child.tag]
            detail = {"tag": child.tag, "attributes": dict(child.attributes),
                      "text": "".join(child.text_parts)}
        counts["tests"] += 1
        counts[outcome] += 1
        cases.append({"attributes": dict(case.attributes), "outcome": outcome, "detail": detail})
    if duplicate:
        reasons.append("duplicate_case_id")
    if any(native[name] != counts[name] for name in native):
        reasons.append("inconsistent_counts")
    valid = not reasons
    if not cases:
        reasons.append("zero_tests")
    elif counts["skipped"] == counts["tests"]:
        reasons.append("all_tests_skipped")
    elif counts["skipped"]:
        reasons.append("partial_skips")
    if native["failures"] or counts["failures"]:
        reasons.append("test_failures")
    if native["errors"] or counts["errors"]:
        reasons.append("test_errors")
    return {"kind": "pytest-junit-observation", "observation_version": 1,
            "dialect": DIALECT, "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "valid": valid, "reason_codes": reasons,
            "suite_attributes": dict(suite.attributes), "native_counts": native,
            "case_counts": counts, "cases": cases}
