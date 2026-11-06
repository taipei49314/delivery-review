"""Reports preserve decisions and treat every provider string as text."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError
from delivery_review.reporting import render_report_html, render_report_markdown


class HTMLTree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class ReportRenderingTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "examples/policy.json").read_bytes())
        self.report = json.loads((ROOT / "examples/report.json").read_bytes())

    def test_validated_state_and_full_report_are_retained_without_mutation(self):
        before = deepcopy(self.report)
        document = render_report_html(self.report, self.policy)
        markdown = render_report_markdown(self.report, self.policy)
        self.assertEqual(before, self.report)
        self.assertIn('Ready for human review: **true**', markdown)
        self.assertIn('complete', document)
        self.assertIn('current', document)
        self.assertIn('clear', document)
        self.assertIn(self.report['subject']['head_sha'], document)
        self.assertIn(self.report['evidence'][0]['raw_sha256'], document)
        self.assertIn('synthetic_contract_fixture', document)
        self.assertEqual(document, render_report_html(self.report, self.policy))

    def test_invalid_saved_decision_cannot_be_rendered_as_ready(self):
        self.report['ready_for_review'] = False
        for render in (render_report_html, render_report_markdown):
            with self.subTest(renderer=render), self.assertRaises(ContractError):
                render(self.report, self.policy)

    def test_untrusted_action_and_limitations_cannot_execute_or_hide_scope(self):
        payload = '</pre><script>evil()</script><img src="https://example.invalid" onerror="evil()">'
        self.report['action_items'] = [{'id': 'action', 'obligation_ids': ['pytest-linux'], 'message': payload}]
        self.report['limitations'].append(payload)
        tree = HTMLTree()
        tree.feed(render_report_html(self.report, self.policy))
        for tag, attrs in tree.elements:
            self.assertNotIn(tag, {'script', 'img', 'iframe', 'object', 'embed', 'a'})
            self.assertFalse(any(key.startswith('on') for key in attrs))
        self.assertIn(payload, ''.join(tree.text))
        self.assertIn('does not prove program correctness', ''.join(tree.text))
        markdown = render_report_markdown(self.report, self.policy)
        self.assertNotIn('<script>', markdown)
        self.assertIn('evil', markdown)


if __name__ == '__main__':
    unittest.main()
