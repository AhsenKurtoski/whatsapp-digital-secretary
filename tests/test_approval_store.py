import os
import stat
import unittest
from datetime import datetime, timedelta, timezone

import support  # noqa: F401
import approval_store


class ApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        queue = approval_store.QUEUE
        if queue.exists():
            queue.unlink()
        approval_store.BASE.mkdir(parents=True, exist_ok=True)

    def test_email_request_is_created_with_html(self):
        row = approval_store.append_request(
            "email",
            {"to": "cliente@example.com", "subject": "Preventivo", "body": "Ciao,\n\nquesto è un test."},
            client_name="Cliente",
            client_phone="+393331112222",
            reason="follow-up",
        )
        self.assertEqual(row["type"], "email")
        self.assertEqual(row["status"], "pending")
        self.assertTrue(row["payload"]["html"].startswith("<!DOCTYPE html>"))
        if os.name != "nt":  # Windows does not expose POSIX permission bits.
            self.assertEqual(stat.S_IMODE(os.stat(approval_store.QUEUE).st_mode), 0o600)

    def test_duplicate_pending_returns_same_request(self):
        payload = {"to": "cliente@example.com", "subject": "Ciao", "body": "Test"}
        first = approval_store.append_request("email", payload)
        second = approval_store.append_request("email", payload)
        self.assertEqual(first["id"], second["id"])

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValueError):
            approval_store.append_request("email", {"to": "not-an-email", "subject": "x", "body": "y"})

    def test_calendar_end_must_be_after_start(self):
        start = datetime.now(timezone.utc) + timedelta(days=1)
        with self.assertRaises(ValueError):
            approval_store.append_request(
                "calendar",
                {"summary": "Call", "start": start.isoformat(), "end": start.isoformat()},
            )

    def test_status_can_only_change_once(self):
        row = approval_store.append_request(
            "email", {"to": "cliente@example.com", "subject": "Ciao", "body": "Test"}
        )
        approval_store.set_status(row["id"], "approved", result="ok")
        with self.assertRaises(ValueError):
            approval_store.set_status(row["id"], "rejected")

    def test_quote_request_generates_pdf(self):
        row = approval_store.append_request(
            "quote",
            {
                "client_name": "Mario Rossi",
                "client_email": "cliente@example.com",
                "items": [{"description": "Sito vetrina", "qty": 1, "unit_price": 900}],
            },
            client_name="Mario Rossi",
            client_phone="+393331112222",
            reason="preventivo richiesto",
        )
        payload = row["payload"]
        self.assertEqual(row["type"], "quote")
        self.assertTrue(payload["number"].startswith("PREV-"))
        self.assertTrue(payload["pdf"].endswith(".pdf"))
        self.assertEqual(payload["totals"]["total"], 1098.0)
        self.assertEqual(payload["to"], "cliente@example.com")
        self.assertIn("html", payload)
        path = approval_store.quote_path(row)
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes()[:8], b"%PDF-1.4")

    def test_quote_requires_a_valid_email(self):
        with self.assertRaises(ValueError):
            approval_store.append_request(
                "quote",
                {"client_name": "Mario", "items": [{"description": "Sito", "unit_price": 10}]},
            )

    def test_duplicate_quote_returns_the_same_pdf(self):
        payload = {
            "client_name": "Mario Rossi",
            "client_email": "cliente@example.com",
            "items": [{"description": "Sito vetrina", "unit_price": 900}],
        }
        first = approval_store.append_request("quote", payload)
        second = approval_store.append_request("quote", payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["payload"]["pdf"], second["payload"]["pdf"])

    def test_mcp_quote_tool_creates_a_pending_request(self):
        import approval_mcp

        result = approval_mcp._handle_tool("request_quote", {
            "client_name": "Mario Rossi",
            "client_email": "cliente@example.com",
            "items": [{"description": "Sito vetrina", "unit_price": 900}],
            "reason": "richiesta cliente",
        })
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["type"], "quote")
        self.assertTrue(result["structuredContent"]["pdf"])
        self.assertIn("attesa di approvazione", result["content"][0]["text"])

    def test_html_escapes_untrusted_body(self):
        row = approval_store.append_request(
            "email",
            {"to": "cliente@example.com", "subject": "<script>alert(1)</script>", "body": "<b>ciao</b>"},
        )
        self.assertNotIn("<script>alert(1)</script>", row["payload"]["html"])
        self.assertIn("&lt;b&gt;ciao&lt;/b&gt;", row["payload"]["html"])


if __name__ == "__main__":
    unittest.main()
