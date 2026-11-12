import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401
import dashboard


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="email-delivery-"))
        self.pdf = folder / "Preventivo-DEMO.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")

    def payload(self):
        return {
            "to": "cliente@example.com",
            "subject": "Il tuo preventivo",
            "body": "Ciao,\n\nin allegato trovi il preventivo.",
            "html": '<html><body bgcolor="#120D1E">preventivo</body></html>',
        }

    def test_gog_receives_html_file_and_pdf_attachment(self):
        captured = {}

        def fake_run(cmd, timeout=20, extra_env=None):
            captured["cmd"] = list(cmd)
            html_path = Path(cmd[cmd.index("--body-html-file") + 1])
            captured["html"] = html_path.read_text(encoding="utf-8")
            captured["attach"] = cmd[cmd.index("--attach") + 1]
            return 0, '{"id":"msg-1"}', ""

        with patch.object(dashboard, "bridge_client", None), \
                patch.object(dashboard, "run", fake_run):
            result = dashboard._execute_email(self.payload(), attachments=[str(self.pdf)])

        self.assertIn("--body-html-file", captured["cmd"])
        self.assertEqual(captured["attach"], str(self.pdf))
        self.assertIn("#120D1E", captured["html"])
        self.assertIn("msg-1", result)
        self.assertIn("PDF allegato", result)

    def test_bridge_receives_html_and_base64_pdf(self):
        captured = {}

        def load_config(_path):
            return {"url": "https://script.google.com/macros/s/test/exec", "secret": "s" * 40}

        def send_email(payload, _config):
            captured.update(payload)
            return {"id": "bridge-1"}

        fake_bridge = SimpleNamespace(load_config=load_config, send_email=send_email)
        with patch.object(dashboard, "bridge_client", fake_bridge):
            result = dashboard._execute_email(self.payload(), attachments=[str(self.pdf)])

        self.assertEqual(captured["html"], self.payload()["html"])
        attachment = captured["attachments"][0]
        self.assertEqual(attachment["filename"], "Preventivo-DEMO.pdf")
        self.assertEqual(attachment["mimeType"], "application/pdf")
        self.assertEqual(base64.b64decode(attachment["data"]), self.pdf.read_bytes())
        self.assertIn("bridge", result)
        self.assertIn("PDF allegato", result)

    def test_demo_quote_email_contains_summary_and_a_real_pdf(self):
        captured = {}

        def fake_execute(payload, attachments=None):
            captured["payload"] = payload
            captured["paths"] = list(attachments or [])
            self.assertTrue(Path(captured["paths"][0]).is_file())
            self.assertTrue(Path(captured["paths"][0]).read_bytes().startswith(b"%PDF-1.4"))
            return "Email inviata con PDF"

        with patch.object(dashboard, "GOOGLE_ACCOUNT", "owner@example.com"), \
                patch.object(dashboard, "_execute_email", fake_execute):
            result = dashboard.send_test_quote_email()

        html = captured["payload"]["html"]
        self.assertIn("PDF ALLEGATO", html)
        self.assertIn("Preventivo pronto", html)
        self.assertIn("€", html)
        self.assertEqual(captured["payload"]["to"], "owner@example.com")
        self.assertFalse(Path(captured["paths"][0]).exists())
        self.assertTrue(result["ok"])
        self.assertTrue(result["pdf"])

    def test_quote_attachment_comes_from_the_private_quotes_folder(self):
        out = dashboard._quote_attachments({"pdf": "../secrets.env"})
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
