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


if __name__ == "__main__":
    unittest.main()
