import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401  (sets the isolated environment)

import approval_mcp
import approval_store
import dashboard

PDF_HEADER = b"%PDF-1.4"


class ApprovalFlowTests(unittest.TestCase):
    """End-to-end: MCP quote -> PDF -> owner approval -> Gmail/bridge payload."""

    def setUp(self):
        if approval_store.QUEUE.exists():
            approval_store.QUEUE.unlink()

    def create_quote(self, name="Mario Rossi", email="mario@example.com", price=900):
        result = approval_mcp._handle_tool("request_quote", {
            "client_name": name,
            "client_email": email,
            "client_company": "Cliente Demo Srl",
            "client_address": "Via Roma 1, Milano",
            "items": [
                {"description": "Sito web vetrina con menu e prenotazioni", "qty": 1, "unit_price": price, "unit": "forfait"},
                {"description": "Manutenzione mensile", "qty": 12, "unit_price": 35, "unit": "mese"},
            ],
            "notes": "Preventivo dimostrativo per il test di approvazione.",
            "reason": "Il cliente ha chiesto un preventivo",
        })
        self.assertFalse(result["isError"])
        request = approval_store.get_request(result["structuredContent"]["request_id"])
        self.assertIsNotNone(request)
        pdf_path = approval_store.quote_path(request)
        self.assertIsNotNone(pdf_path)
        self.assertTrue(pdf_path.read_bytes().startswith(PDF_HEADER))
        return request, pdf_path

    def test_gog_approval_sends_html_and_pdf(self):
        request, pdf_path = self.create_quote()
        captured = {}

        def fake_run(cmd, timeout=20, extra_env=None):
            captured["cmd"] = list(cmd)
            html_path = Path(cmd[cmd.index("--body-html-file") + 1])
            captured["html"] = html_path.read_text(encoding="utf-8")
            captured["pdf"] = Path(cmd[cmd.index("--attach") + 1]).read_bytes()
            return 0, '{"id":"msg-approval-test"}', ""

        with patch.object(dashboard, "bridge_client", None), \
                patch.object(dashboard, "run", fake_run):
            approved = dashboard.approve_request(request["id"])

        self.assertTrue(approved["ok"])
        self.assertIn("--body-html-file", captured["cmd"])
        self.assertIn("--attach", captured["cmd"])
        self.assertTrue(captured["pdf"].startswith(PDF_HEADER))
        self.assertEqual(len(captured["pdf"]), pdf_path.stat().st_size)
        self.assertIn("PDF ALLEGATO", captured["html"])
        self.assertIn("#3B0F70", captured["html"])
        self.assertIn("msg-approval-test", approved["result"])
        self.assertEqual(approval_store.get_request(request["id"])["status"], "approved")

    def test_bridge_approval_sends_base64_pdf(self):
        request, _pdf_path = self.create_quote("Anna Verdi", "anna@example.com", 1200)
        seen = {}

        def load_config(_path):
            return {"url": "https://script.google.com/macros/s/test/exec", "secret": "s" * 40}

        def send_email(payload, _config):
            seen.update(payload)
            return {"id": "bridge-approval-test"}

        fake_bridge = SimpleNamespace(load_config=load_config, send_email=send_email)
        with patch.object(dashboard, "bridge_client", fake_bridge):
            approved = dashboard.approve_request(request["id"])

        self.assertTrue(approved["ok"])
        attachment = seen["attachments"][0]
        self.assertTrue(attachment["filename"].endswith(".pdf"))
        self.assertEqual(attachment["mimeType"], "application/pdf")
        self.assertTrue(base64.b64decode(attachment["data"]).startswith(PDF_HEADER))
        self.assertIn("PDF ALLEGATO", seen["html"])
        self.assertEqual(approval_store.get_request(request["id"])["status"], "approved")

    def test_approval_queue_records_both_requests(self):
        first, _ = self.create_quote("Mario Rossi", "mario@example.com", 900)
        second, _ = self.create_quote("Anna Verdi", "anna@example.com", 1200)
        with patch.object(dashboard, "bridge_client", None), \
                patch.object(dashboard, "run", lambda *args, **kwargs: (0, '{"id":"x"}', "")):
            self.assertTrue(dashboard.approve_request(first["id"])["ok"])
            self.assertTrue(dashboard.approve_request(second["id"])["ok"])
        history = approval_store.list_requests(50)
        statuses = {row["id"]: row["status"] for row in history["recent"]}
        self.assertEqual(statuses[first["id"]], "approved")
        self.assertEqual(statuses[second["id"]], "approved")
        self.assertEqual(history["counts"]["approved"], 2)


if __name__ == "__main__":
    unittest.main()
