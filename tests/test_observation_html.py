"""Untrusted platform strings stay text in the offline HTML report."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observation_fixtures import make_capture
from delivery_review.observations import render_html
from delivery_review.providers.github_observations import map_capture


class Nodes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.data = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, text):
        self.data.append(text)


class ObservationHTMLTests(unittest.TestCase):
    def test_native_strings_cannot_create_links_scripts_or_markup(self):
        result = map_capture(make_capture(), mode="offline_replay")
        payload = '</pre><script>alert(1)</script><img src="https://example.invalid/x" onerror="x">'
        result["reasons"].append(payload)
        result["workflows"][0]["jobs"][0]["name"] = payload
        document = render_html(result)
        tree = Nodes()
        tree.feed(document)
        self.assertNotIn("script", [tag for tag, _ in tree.elements])
        self.assertNotIn("img", [tag for tag, _ in tree.elements])
        for tag, attrs in tree.elements:
            self.assertFalse({"href", "src", "action"} & set(attrs))
            self.assertFalse(any(name.startswith("on") for name in attrs))
        self.assertIn(payload, "".join(tree.data))
        self.assertIn("default-src 'none'", document)
        self.assertIn("unknown", "".join(tree.data))
        self.assertIn("not established", "".join(tree.data))
        self.assertEqual(document, render_html(result))


if __name__ == "__main__":
    unittest.main()
