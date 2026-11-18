"""Sito pubblico: build SEO, verifica Google/Bing, analytics e security headers."""
import json
import tempfile
import unittest
from pathlib import Path

import seo_audit
import site_build

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    "base_url": "https://example.com",
    "site_name": "Gina Test",
    "owner_name": "Ahsen Kurtoski",
    "owner_url": "https://github.com/AhsenKurtoski",
    "repo_url": "https://github.com/AhsenKurtoski/whatsapp-digital-secretary",
    "description": "Segreteria digitale WhatsApp self-hosted con approvazione umana e dashboard privata.",
    "analytics": {"ga4_measurement_id": "", "cloudflare_token": ""},
    "verification": {"google": "", "bing": "", "yandex": ""},
}


class SiteBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-test-"))
        self.config = self.tmp / "config.json"
        self.write(CONFIG)
        self.out = self.tmp / "dist"

    def write(self, data):
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def test_build_produces_a_complete_indexable_site(self):
        result = site_build.build(self.config, self.out)
        self.assertTrue(result["ok"])
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("https://example.com/", index)
        self.assertIn("Ahsen Kurtoski", index)
        self.assertIn("Person", index)
        self.assertIn("SoftwareApplication", index)
        self.assertNotIn("{{", index)
        for name in ("privacy.html", "404.html", "sitemap.xml", "robots.txt", "_headers", "vercel.json"):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertTrue((self.out / ".well-known" / "security.txt").is_file())
        self.assertTrue((self.out / "assets" / "og.png").is_file())
        self.assertTrue((self.out / "assets" / "dashboard.png").is_file())
        robots = (self.out / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("Sitemap: https://example.com/sitemap.xml", robots)
        headers = (self.out / "_headers").read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", headers)
        self.assertIn("Strict-Transport-Security", headers)

    def test_verification_tokens_become_meta_tags(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["verification"] = {"google": "google-token-123", "bing": "BING123456", "yandex": "yandex-token"}
        self.write(data)
        site_build.build(self.config, self.out)
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn('name="google-site-verification" content="google-token-123"', index)
        self.assertIn('name="msvalidate.01" content="BING123456"', index)
        self.assertIn('name="yandex-verification" content="yandex-token"', index)

    def test_analytics_is_opt_in_and_validated(self):
        site_build.build(self.config, self.out)
        config = (self.out / "assets" / "analytics.config.js").read_text(encoding="utf-8")
        self.assertIn('"ga4": ""', config)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["analytics"]["ga4_measurement_id"] = "G-TEST12345"
        self.write(data)
        site_build.build(self.config, self.out)
        config = (self.out / "assets" / "analytics.config.js").read_text(encoding="utf-8")
        self.assertIn("G-TEST12345", config)
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("analytics.js", index)
        self.assertNotIn("gtag(", index, "lo snippet GA deve essere esterno, non inline")

    def test_invalid_ga_id_stops_the_build(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["analytics"]["ga4_measurement_id"] = "UA-123"
        self.write(data)
        with self.assertRaises(SystemExit):
            site_build.build(self.config, self.out)

    def test_missing_base_url_stops_the_build(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["base_url"] = ""
        self.write(data)
        with self.assertRaises(SystemExit):
            site_build.build(self.config, self.out)

    def test_quotes_and_script_tags_in_text_are_escaped(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["description"] = 'Gina \"self-hosted\" per l\'attività </script><script>alert(1)</script>.'
        self.write(data)
        site_build.build(self.config, self.out)
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("</script><script>", index)
        self.assertIn("\\u003c/script\\u003e", index)
        report = seo_audit.audit(self.out)
        self.assertTrue(report["ok"], report["errors"])

    def test_base_url_override_is_supported(self):
        site_build.build(self.config, self.out, base_url_override="https://preview.example.org")
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("https://preview.example.org/", index)

    def test_built_site_passes_its_own_audit(self):
        site_build.build(self.config, self.out)
        report = seo_audit.audit(self.out)
        self.assertTrue(report["ok"], report["errors"])

    def test_self_tests_are_real(self):
        self.assertEqual(site_build.self_test(), 0)
        self.assertEqual(seo_audit.self_test(), 0)

    def test_vercel_manifest_matches_the_build(self):
        """The root vercel.json is what Vercel reads; it must not drift."""
        root = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        expected = [{"key": key, "value": value} for key, value in site_build.SECURITY_HEADERS]
        self.assertEqual(root["headers"][0]["headers"], expected)
        self.assertFalse(root.get("cleanUrls", False))
        site_build.build(self.config, self.out)
        built = json.loads((self.out / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(built, root)


    def test_cloudflare_analytics_and_default_security_contact(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["analytics"]["cloudflare_token"] = "cf-token-123"
        data.pop("security_txt_contact", None)
        self.write(data)
        site_build.build(self.config, self.out)
        config = (self.out / "assets" / "analytics.config.js").read_text(encoding="utf-8")
        self.assertIn("cf-token-123", config)
        security = (self.out / ".well-known" / "security.txt").read_text(encoding="utf-8")
        self.assertIn("security/advisories/new", security)

    def test_invalid_values_are_rejected(self):
        for change in (
            {"base_url": "ftp://example.com"},
            {"description": "x" * 400},
            {"owner_name": ""},
            {"security_txt_contact": "not a url"},
        ):
            data = json.loads(self.config.read_text(encoding="utf-8"))
            data.update(change)
            self.write(data)
            with self.assertRaises(SystemExit):
                site_build.build(self.config, self.out)


class SeoAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seo-audit-test-"))
        self.out = self.tmp / "dist"
        config = self.tmp / "config.json"
        config.write_text(json.dumps(CONFIG), encoding="utf-8")
        site_build.build(config, self.out)

    def read(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def write(self, name, text):
        (self.out / name).write_text(text, encoding="utf-8")

    def test_missing_description_is_an_error(self):
        self.write("privacy.html", self.read("privacy.html").replace(
            '<meta name="description" content=', '<meta name="x-description" content='
        ))
        report = seo_audit.audit(self.out)
        self.assertFalse(report["ok"])
        self.assertTrue(any("description" in error for error in report["errors"]))

    def test_two_h1_is_an_error(self):
        self.write("index.html", self.read("index.html").replace("<h1>", "<h1>Doppio</h1><h1>", 1))
        report = seo_audit.audit(self.out)
        self.assertFalse(report["ok"])
        self.assertTrue(any("h1" in error for error in report["errors"]))

    def test_missing_headers_is_an_error(self):
        (self.out / "_headers").unlink()
        report = seo_audit.audit(self.out)
        self.assertFalse(report["ok"])
        self.assertTrue(any("_headers" in error for error in report["errors"]))

    def test_unresolved_placeholder_is_an_error(self):
        self.write("index.html", self.read("index.html") + "{{LEFTOVER}}")
        report = seo_audit.audit(self.out)
        self.assertFalse(report["ok"])

    def test_empty_analytics_is_reported_as_warning(self):
        report = seo_audit.audit(self.out)
        self.assertTrue(report["ok"])
        self.assertTrue(any("GA4" in warning for warning in report["warnings"]))

    def test_every_broken_signal_is_reported(self):
        index = self.read("index.html")
        index = index.replace('<link rel="canonical" href="https://example.com/">', "")
        for prop in ("og:title", "og:description", "og:url", "og:image"):
            index = index.replace(f'<meta property="{prop}" content="', f'<meta property="x-{prop}" content="')
        index = index.replace('<meta name="twitter:card" content="summary_large_image">', "")
        index = index.replace("<body>", '<body><img src="x.png"><a target="_blank" href="https://x.it">x</a>')
        index = index.replace('{\n  "@context"', '{ invalid\n  "@context"', 1)
        self.write("index.html", index)
        for name in ("sitemap.xml", "robots.txt", "_headers", "vercel.json"):
            (self.out / name).unlink()
        (self.out / ".well-known" / "security.txt").unlink()
        (self.out / "assets" / "analytics.config.js").unlink()
        report = seo_audit.audit(self.out)
        self.assertFalse(report["ok"])
        text = " | ".join(report["errors"])
        for expected in ("canonical", "og:title", "twitter:card", "alt", "noopener",
                         "JSON-LD", "sitemap", "robots", "_headers", "vercel.json",
                         "security.txt", "analytics.config.js"):
            self.assertIn(expected, text, expected)
        self.assertGreaterEqual(len(report["errors"]), 10)


if __name__ == "__main__":
    unittest.main()
