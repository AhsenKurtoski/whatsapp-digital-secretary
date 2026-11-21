"""Accessibilità della dashboard: lingua, skip link, etichette, Live region."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dashboard.html"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


class Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.stack = []
        self.text = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag not in VOID:
            self.stack.append(tag)
            self.text.setdefault(tag, [])
        if self.stack:
            self.text.setdefault(" ".join(self.stack), [])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break

    def handle_data(self, data):
        for tag in set(self.stack):
            self.text.setdefault(tag, []).append(data)


def parse():
    parser = Collector()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser


class DashboardAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = parse()
        cls.tags = cls.parser.tags
        cls.source = PAGE.read_text(encoding="utf-8")

    def test_language_is_declared(self):
        html = [attrs for tag, attrs in self.tags if tag == "html"]
        self.assertEqual(len(html), 1)
        self.assertEqual(html[0].get("lang"), "it")

    def test_skip_link_points_to_main_landmark(self):
        links = [attrs for tag, attrs in self.tags if tag == "a" and attrs.get("class") == "skip-link"]
        self.assertTrue(links)
        self.assertEqual(links[0].get("href"), "#main")
        mains = [attrs for tag, attrs in self.tags if attrs.get("id") == "main"]
        self.assertTrue(mains)
        self.assertEqual(mains[0].get("role", "main"), "main")

    def test_every_form_field_has_a_label(self):
        label_for = {
            attrs.get("for") for tag, attrs in self.tags
            if tag == "label" and attrs.get("for")
        }
        missing = []
        for tag, attrs in self.tags:
            if tag not in {"input", "textarea", "select"}:
                continue
            if attrs.get("type") in {"hidden", "submit", "button", "reset"}:
                continue
            field_id = attrs.get("id")
            labelled = (
                (field_id and field_id in label_for)
                or attrs.get("aria-label")
                or attrs.get("aria-labelledby")
                or attrs.get("title")
            )
            if not labelled:
                missing.append(attrs.get("id") or attrs.get("name") or tag)
        self.assertEqual(missing, [], f"campi senza etichetta: {missing}")

    def test_buttons_have_an_accessible_name(self):
        missing = []
        for tag, attrs in self.tags:
            if tag != "button":
                continue
            if attrs.get("aria-label") or attrs.get("aria-labelledby") or attrs.get("title"):
                continue
            text = " ".join(self.parser.text.get("button", [])).strip()
            if not text:
                missing.append(attrs.get("id") or attrs.get("class") or "button")
        self.assertEqual(missing, [], f"pulsanti senza nome: {missing}")

    def test_images_have_alternative_text(self):
        for tag, attrs in self.tags:
            if tag == "img":
                self.assertIn("alt", attrs, attrs.get("src", "img"))

    def test_ids_are_unique(self):
        ids = [attrs["id"] for tag, attrs in self.tags if attrs.get("id")]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(duplicates, [])

    def test_live_regions_are_polite(self):
        for _tag, attrs in self.tags:
            if attrs.get("id") in {"transcriptBox", "convSendState", "toastBox"}:
                self.assertEqual(attrs.get("aria-live"), "polite", attrs.get("id"))

    def test_operator_composer_is_described(self):
        textareas = [attrs for tag, attrs in self.tags if attrs.get("id") == "convSendText"]
        self.assertEqual(len(textareas), 1)
        self.assertEqual(textareas[0].get("maxlength"), "1000")
        described = textareas[0].get("aria-describedby")
        self.assertTrue(described)
        self.assertTrue(any(attrs.get("id") == described for tag, attrs in self.tags))

    def test_tabs_declare_aria_controls(self):
        tabs = [attrs for tag, attrs in self.tags if "data-tab" in attrs]
        self.assertTrue(tabs)
        for attrs in tabs:
            self.assertIn(attrs.get("role"), {None, "tab"})
            if attrs.get("role") == "tab":
                self.assertIn(attrs.get("aria-selected"), {"true", "false"})
                self.assertIn(attrs.get("aria-controls"), attrs.values())

    def test_no_positive_tabindex(self):
        for tag, attrs in self.tags:
            value = attrs.get("tabindex")
            if value and re.fullmatch(r"\d+", value):
                self.assertLessEqual(int(value), 0, f"{tag} tabindex={value}")


if __name__ == "__main__":
    unittest.main()
