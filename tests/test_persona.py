import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_non_interactive_update_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            cfg = tmp / "config.json"
            cfg.write_text(json.dumps({
                "business_name": "Officina Demo",
                "owner_name": "Mario Rossi",
                "bot_name": "Sofia",
                "owner_number": "+393331112222",
                "city": "Milano",
                "dashboard_port": 8799,
                "max_concurrent": 7,
                "max_reply_lines": 4,
                "whatsapp_number": "+393331112222",
            }), encoding="utf-8")
            base = tmp / "base"
            workspace = tmp / "workspace"
            with mock.patch.object(setup.config, "DEFAULT_CONFIG_PATH", cfg), \
                 mock.patch.object(setup.config, "ensure_base", lambda: (base.mkdir(), base)[1]), \
                 mock.patch.object(setup.config, "workspace_dir", lambda: workspace), \
                 mock.patch.object(setup.sys, "argv", ["setup.py", "--non-interactive"]):
                self.assertEqual(setup.main(), 0)
            saved = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(saved["bot_name"], "Sofia")
            self.assertEqual(saved["dashboard_port"], 8799)
            self.assertEqual(saved["max_concurrent"], 7)
            self.assertEqual(saved["max_reply_lines"], 4)
            self.assertEqual((workspace / "AGENTS.md").read_text(encoding="utf-8").count("Officina Demo"), 3)


if __name__ == "__main__":
    unittest.main()
