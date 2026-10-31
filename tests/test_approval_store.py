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

    def test_html_escapes_untrusted_body(self):
        row = approval_store.append_request(
            "email",
            {"to": "cliente@example.com", "subject": "<script>alert(1)</script>", "body": "<b>ciao</b>"},
        )
        self.assertNotIn("<script>alert(1)</script>", row["payload"]["html"])
        self.assertIn("&lt;b&gt;ciao&lt;/b&gt;", row["payload"]["html"])


if __name__ == "__main__":
    unittest.main()
