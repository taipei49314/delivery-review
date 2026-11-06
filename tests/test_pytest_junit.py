"""Authored XML controls informed by a local pytest 8.4.2 qualification.

No reviewed project tests or third-party fixtures execute in this suite.
"""
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.adapters.pytest_junit import (
    DIALECT, JUnitError, JUnitLimits, parse_pytest_junit,
)
from delivery_review.contracts import ContractError


def testcase(name="test_authored", outcome=None, *, classname="test_sample", message="authored detail", text="authored trace"):
    attributes = f'classname={quoteattr(classname)} name={quoteattr(name)} time="0.001"'
    detail = ""
    if outcome:
        kind = ' type="pytest.skip"' if outcome == "skipped" else ""
        detail = f'<{outcome} message={quoteattr(message)}{kind}>{escape(text)}</{outcome}>'
    return f'<testcase {attributes}>{detail}</testcase>'


def document(cases=None, *, tests=None, failures=0, errors=0, skipped=0):
    cases = [testcase()] if cases is None else cases
    tests = len(cases) if tests is None else tests
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites name="pytest tests"><testsuite name="delivery-review"'
            f' errors="{errors}" failures="{failures}" skipped="{skipped}" tests="{tests}"'
            ' time="0.012" timestamp="2026-11-06T00:00:00+00:00" hostname="authored-host">'
            + "".join(cases) + '</testsuite></testsuites>').encode("utf-8")


class PytestJUnitTests(unittest.TestCase):
    def assertRefused(self, raw, code=None, **kwargs):
        with self.assertRaises(JUnitError) as caught:
            parse_pytest_junit(raw, **kwargs)
        self.assertIsInstance(caught.exception, ContractError)
        if code:
            self.assertEqual(code, caught.exception.code)
        self.assertEqual(caught.exception.code, str(caught.exception))

    def test_pass_observes_counts_and_exact_bytes_without_granting_trust(self):
        raw = document()
        observed = parse_pytest_junit(raw)
        self.assertTrue(observed["valid"])
        self.assertEqual([], observed["reason_codes"])
        self.assertEqual({"tests": 1, "failures": 0, "errors": 0, "skipped": 0}, observed["native_counts"])
        self.assertEqual(1, observed["case_counts"]["passed"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), observed["raw_sha256"])
        self.assertEqual(len(raw), observed["size_bytes"])
        self.assertEqual("0.012", observed["suite_attributes"]["time"])
        for forbidden in ("ready_for_review", "state", "binding", "exit_code", "source"):
            self.assertNotIn(forbidden, observed)

    def test_all_native_outcomes_and_untrusted_text_are_preserved(self):
        hostile = '<script>" & [link](javascript:alert(1))</script>'
        raw = document([testcase("pass"), testcase("fail", "failure", message=hostile, text=hostile),
                        testcase("setup", "error"), testcase("skip", "skipped")],
                       failures=1, errors=1, skipped=1)
        observed = parse_pytest_junit(raw)
        self.assertTrue(observed["valid"])
        self.assertEqual({"tests": 4, "passed": 1, "failures": 1, "errors": 1, "skipped": 1}, observed["case_counts"])
        self.assertEqual(["partial_skips", "test_failures", "test_errors"], observed["reason_codes"])
        self.assertEqual(hostile, observed["cases"][1]["detail"]["attributes"]["message"])
        self.assertEqual(hostile, observed["cases"][1]["detail"]["text"])

    def test_zero_and_all_skipped_cannot_look_like_ordinary_pass(self):
        zero = parse_pytest_junit(document([]))
        skipped = parse_pytest_junit(document([testcase(outcome="skipped")], skipped=1))
        self.assertTrue(zero["valid"])
        self.assertTrue(skipped["valid"])
        self.assertEqual(["zero_tests"], zero["reason_codes"])
        self.assertEqual(["all_tests_skipped"], skipped["reason_codes"])
        self.assertEqual(0, skipped["case_counts"]["passed"])

    def test_collection_error_can_have_empty_classname(self):
        observed = parse_pytest_junit(document([testcase("test_module", "error", classname="", message="collection failure")], errors=1))
        self.assertTrue(observed["valid"])
        self.assertEqual(["test_errors"], observed["reason_codes"])
        self.assertEqual("", observed["cases"][0]["attributes"]["classname"])

    def test_xfail_is_skip_and_strict_xpass_is_failure(self):
        raw = document([testcase(outcome="skipped")], skipped=1).replace(b"pytest.skip", b"pytest.xfail")
        observed = parse_pytest_junit(raw)
        self.assertEqual(["all_tests_skipped"], observed["reason_codes"])
        self.assertEqual("pytest.xfail", observed["cases"][0]["detail"]["attributes"]["type"])
        observed = parse_pytest_junit(document([testcase(outcome="failure", message="[XPASS(strict)] authored")], failures=1))
        self.assertEqual(["test_failures"], observed["reason_codes"])

    def test_duplicate_identity_is_invalid_without_deduplicating_native_data(self):
        observed = parse_pytest_junit(document([testcase(), testcase()]))
        self.assertFalse(observed["valid"])
        self.assertEqual(["duplicate_case_id"], observed["reason_codes"])
        self.assertEqual(2, len(observed["cases"]))
        self.assertEqual(2, observed["native_counts"]["tests"])

    def test_classname_is_part_of_case_identity(self):
        observed = parse_pytest_junit(document([testcase(classname="one"), testcase(classname="two")]))
        self.assertTrue(observed["valid"])

    def test_native_teardown_phase_quirks_are_preserved_and_rejected(self):
        # Actual pytest 8.4.2 shape: passing call + failing teardown increments
        # native tests twice but serializes one error testcase.
        observed = parse_pytest_junit(document([testcase(outcome="error", message='failed on teardown with "ValueError"')], tests=2, errors=1))
        self.assertFalse(observed["valid"])
        self.assertEqual(["inconsistent_counts", "test_errors"], observed["reason_codes"])
        self.assertEqual(2, observed["native_counts"]["tests"])
        self.assertEqual(1, observed["case_counts"]["tests"])
        # Failing call + failing teardown serializes two records with one ID,
        # while native tests=1; neither the failure nor the error is discarded.
        observed = parse_pytest_junit(document([testcase(outcome="failure"), testcase(outcome="error")], tests=1, failures=1, errors=1))
        self.assertFalse(observed["valid"])
        self.assertEqual(["duplicate_case_id", "inconsistent_counts", "test_failures", "test_errors"], observed["reason_codes"])
        self.assertEqual(1, observed["native_counts"]["tests"])
        self.assertEqual(2, len(observed["cases"]))

    def test_each_count_contradiction_remains_visible(self):
        for field in ("tests", "failures", "errors", "skipped"):
            with self.subTest(field=field):
                observed = parse_pytest_junit(document(**{field: 3}))
                self.assertFalse(observed["valid"])
                self.assertIn("inconsistent_counts", observed["reason_codes"])
                self.assertEqual(3, observed["native_counts"][field])
                self.assertEqual(1, observed["case_counts"]["passed"])

    def test_counts_are_strict_integers_with_no_coercion(self):
        for value in ("-1", "1.0", "+1", "01", " 1", "1e0", "NaN", "9007199254740992", "9" * 100):
            with self.subTest(value=value):
                self.assertRefused(document().replace(b'tests="1"', f'tests="{value}"'.encode()), "invalid_junit_counts")

    def test_duration_does_not_accept_nan_infinity_or_arbitrary_float_formats(self):
        for value in ("NaN", "Infinity", "-0.001", "1e8", "1", "0.001\n", "9" * 100):
            with self.subTest(value=value):
                self.assertRefused(document().replace(b'time="0.001"', f'time="{value}"'.encode()))

    def test_dtd_internal_external_and_parameter_entities_never_resolve(self):
        documents = [
            b'<!DOCTYPE testsuites>',
            b'<!DOCTYPE testsuites [<!ENTITY x "expanded">]>',
            b'<!DOCTYPE testsuites SYSTEM "file:///authored-missing">',
            b'<!DOCTYPE testsuites [<!ENTITY x SYSTEM "https://example.invalid/secret">]>',
            b'<!DOCTYPE testsuites [<!ENTITY % p SYSTEM "file:///authored-missing">%p;]>',
        ]
        body = document().split(b"?>", 1)[1]
        with patch("builtins.open", side_effect=AssertionError("no files")), patch("socket.create_connection", side_effect=AssertionError("no network")):
            for prefix in documents:
                with self.subTest(prefix=prefix):
                    self.assertRefused(prefix + body, "unsafe_junit_xml")

    def test_processing_instructions_and_unqualified_shapes_are_refused(self):
        self.assertRefused(b'<?xml-stylesheet href="https://example.invalid/"?>' + document().split(b"?>", 1)[1], "unsafe_junit_xml")
        for raw in (
            document().replace(b'<testsuites name="pytest tests">', b'<testsuites xmlns="urn:other" name="pytest tests">'),
            document().replace(b'name="delivery-review"', b'name="pytest"'),
            document().replace(b'time="0.001"', b'time="0.001" file="project.py"'),
            document().replace(b'</testcase>', b'<system-out>ignored?</system-out></testcase>'),
            document().replace(b'</testcase>', b'<properties><property name="x" value="y"/></properties></testcase>'),
            document().replace(b'</testsuites>', b'<testsuite/></testsuites>'),
            document().replace(b'</testcase>', b'<testcase/></testcase>'),
        ):
            with self.subTest(raw=raw):
                self.assertRefused(raw)

    def test_missing_attributes_multiple_outcomes_and_nested_detail_are_refused(self):
        for raw in (
            document().replace(b' classname="test_sample"', b''),
            document().replace(b'name="test_authored"', b'name=""'),
            document([testcase(outcome="failure")], failures=1).replace(b'</testcase>', b'<error message="second"/></testcase>'),
            document([testcase(outcome="failure")], failures=1).replace(b'authored trace', b'<nested/>'),
            document([testcase(outcome="skipped")], skipped=1).replace(b'pytest.skip', b'unknown.skip'),
            document().replace(b'</testcase>', b'not whitespace</testcase>'),
        ):
            with self.subTest(raw=raw):
                self.assertRefused(raw)

    def test_malformed_xml_and_error_messages_do_not_echo_input(self):
        for raw in (b"", b"secret-token", document()[:-1], document() + b"secret-token",
                    document().replace(b'name="test_authored"', b'name="test_authored" name="secret-token"')):
            with self.subTest(raw=raw):
                self.assertRefused(raw, "malformed_junit_xml")

    def test_only_utf8_bytes_and_known_dialect_are_accepted(self):
        self.assertRefused(document().decode(), "invalid_junit_input")
        self.assertRefused(bytearray(document()), "invalid_junit_input")
        self.assertRefused(document() + b"\xff", "invalid_junit_encoding")
        self.assertRefused(document().replace(b'encoding="utf-8"', b'encoding="iso-8859-1"'), "invalid_junit_encoding")
        self.assertRefused(document(), "unsupported_junit_dialect", dialect="pytest-latest")
        self.assertEqual(DIALECT, parse_pytest_junit(b"\xef\xbb\xbf" + document())["dialect"])

    def test_byte_limit_includes_comments_and_exact_boundary(self):
        raw = document()
        self.assertTrue(parse_pytest_junit(raw, limits=replace(JUnitLimits(), max_bytes=len(raw)))["valid"])
        self.assertRefused(raw, "junit_resource_limit", limits=replace(JUnitLimits(), max_bytes=len(raw) - 1))
        self.assertRefused(raw + b'<!--' + b'x' * len(raw) + b'-->', "junit_resource_limit", limits=replace(JUnitLimits(), max_bytes=len(raw)))

    def test_element_case_attribute_depth_and_text_limits(self):
        raw = document([testcase("a", "failure"), testcase("b", "failure")], failures=2)
        for changes in (
            {"max_elements": 5}, {"max_cases": 1}, {"max_attributes": 7},
            {"max_attribute_chars": 5}, {"max_depth": 3},
            {"max_text_chars": 5}, {"max_total_text_chars": len("authored trace")},
        ):
            with self.subTest(changes=changes):
                self.assertRefused(raw, "junit_resource_limit", limits=replace(JUnitLimits(), **changes))
        observed = parse_pytest_junit(raw, limits=replace(JUnitLimits(), max_elements=6, max_cases=2, max_depth=4, max_text_chars=len("authored trace"), max_total_text_chars=2 * len("authored trace")))
        self.assertTrue(observed["valid"])

    def test_invalid_or_raised_limits_are_refused(self):
        for limits in (None, {}, replace(JUnitLimits(), max_bytes=True),
                       replace(JUnitLimits(), max_bytes=0), replace(JUnitLimits(), max_depth=100)):
            with self.subTest(limits=limits):
                self.assertRefused(document(), "invalid_junit_limits", limits=limits)

    def test_crlf_raw_digest_and_decimal_spelling_are_not_normalized(self):
        raw = document([testcase(outcome="failure", text="line1\r\nline2")], failures=1)
        observed = parse_pytest_junit(raw)
        self.assertEqual("line1\nline2", observed["cases"][0]["detail"]["text"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), observed["raw_sha256"])
        self.assertNotEqual(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(), observed["raw_sha256"])


if __name__ == "__main__":
    unittest.main()
