import unittest

import support  # noqa: F401

import email_template


class EmailTemplateTests(unittest.TestCase):
    def test_contains_brand_and_escapes_html(self):
        html = email_template.render_email(
            "Preventivo <script>", "Ecco il preventivo.\n\n<b>test</b>\n\nhttps://example.com", client_name="Mario"
        )
        self.assertIn("Attività Demo", html)
        self.assertIn("Ciao Mario", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;b&gt;test&lt;/b&gt;", html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("viewport", html)

    def test_plain_text_fallback_is_not_html(self):
        html = email_template.render_email("Oggetto", "Testo semplice")
        self.assertIn("Testo semplice", html)
        self.assertIn("<!DOCTYPE html>", html)

    def test_full_colour_layout_covers_every_section(self):
        html = email_template.render_email("Oggetto", "Testo")
        self.assertGreaterEqual(html.count("bgcolor="), 20)
        for colour in (
            email_template.HERO,
            email_template.CARD,
            email_template.VIOLET,
            email_template.CYAN,
            email_template.FUCHSIA,
        ):
            self.assertIn(colour, html)
        self.assertIn('name="color-scheme" content="light only"', html)
        self.assertIn("background-color:", html)

    def test_quote_email_has_summary_total_and_attachment_badge(self):
        quote = {
            "number": "PREV-2026-001",
            "currency": "EUR",
            "valid_until": "2026-12-01",
            "items": [
                {"description": "Sito", "qty": 1, "unit_price": 900, "total": 900},
                {"description": "Manutenzione", "qty": 1, "unit_price": 198, "total": 198},
            ],
            "totals": {"total": 1098.0},
        }
        html = email_template.render_email(
            "Il tuo preventivo", "Ciao,\n\nin allegato trovi il preventivo.", quote=quote
        )
        self.assertIn("PREV-2026-001", html)
        self.assertIn("€ 1.098,00", html)
        self.assertIn("01/12/2026", html)
        self.assertIn("2 voci", html)
        self.assertIn("PDF ALLEGATO", html)
        self.assertIn("Preventivo pronto", html)
        self.assertIn("Il tuo preventivo", html)

    def test_quote_fields_are_escaped(self):
        html = email_template.render_email(
            "Preventivo",
            "Ciao",
            quote={
                "number": "<script>alert(1)</script>",
                "currency": "EUR",
                "items": [],
                "totals": {"total": 10},
            },
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_no_external_assets_or_tracking(self):
        html = email_template.render_email("Oggetto", "Testo con https://example.com")
        self.assertNotIn("<img", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("pixel", html.lower())

    def test_mailto_and_site_links_are_rendered_when_configured(self):
        html = email_template.render_email("Oggetto", "Testo")
        self.assertIn("mailto:demo@example.com", html)
        self.assertIn("https://example.com", html)


if __name__ == "__main__":
    unittest.main()
