import unittest

import support  # noqa: F401
import setup


class PersonaTests(unittest.TestCase):
    def test_render_replaces_all_placeholders(self):
        text = setup.render_persona(
            {
                "business_name": "Officina Demo",
                "bot_name": "Sofia",
                "owner_name": "Mario Rossi",
                "owner_number": "+393331112222",
                "language": "italiano",
                "max_reply_lines": 5,
                "hours": "Lun–Ven 9:00–18:00",
                "city": "Milano",
                "services": ["riparazioni", "preventivi"],
                "site_url": "https://example.com",
                "pricing_note": "I prezzi li conferma il titolare.",
            }
        )
        self.assertNotIn("{{", text)
        self.assertIn("Officina Demo", text)
        self.assertIn("Sofia", text)
        self.assertIn("Mario Rossi", text)
        self.assertIn("+393331112222", text)
        self.assertIn("riparazioni; preventivi", text)
        self.assertIn("massimo 5 righe", text)

    def test_render_without_owner_number(self):
        text = setup.render_persona(
            {
                "business_name": "Demo",
                "bot_name": "Bot",
                "owner_name": "Titolare",
                "services": [],
            }
        )
        self.assertNotIn("{{", text)
        self.assertNotIn("numero verificato None", text)


if __name__ == "__main__":
    unittest.main()
