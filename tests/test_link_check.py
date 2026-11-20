"""Link relativi della documentazione: nessun 404 nella guida di installazione."""
import tempfile
import unittest
from pathlib import Path

import link_check

ROOT = Path(__file__).resolve().parents[1]


class LinkCheckTests(unittest.TestCase):
    def test_repository_has_no_broken_relative_links(self):
        report = link_check.check(ROOT)
        self.assertTrue(report["ok"], "\n".join(report["broken"]))
        self.assertGreater(report["checked"], 40, report)

    def test_self_test_passes(self):
        self.assertEqual(link_check.self_test(), 0)

    def test_broken_link_is_detected(self):
        with tempfile.TemporaryDirectory(prefix="link-broken-") as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[manca](docs/nope.md)\n", encoding="utf-8")
            report = link_check.check(root)
            self.assertFalse(report["ok"])
            self.assertIn("docs/nope.md", report["broken"][0])

    def test_code_fences_absolute_urls_and_anchors_are_ignored(self):
        with tempfile.TemporaryDirectory(prefix="link-ignore-") as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "```bash\ncurl -O https://example.com/file\n[manca](non-esiste.md)\n```\n"
                "[web](https://example.com) [ancora](#sezione) [assoluto](/x.html)\n",
                encoding="utf-8",
            )
            report = link_check.check(root)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["checked"], 0)

    def test_html_attribute_is_checked(self):
        with tempfile.TemporaryDirectory(prefix="link-html-") as tmp:
            root = Path(tmp)
            (root / "index.html").write_text('<img src="assets/missing.webp" alt="x">', encoding="utf-8")
            report = link_check.check(root)
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["broken"]), 1)


if __name__ == "__main__":
    unittest.main()
