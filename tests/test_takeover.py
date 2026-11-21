"""Invio dal dashboard come operatore umano (handoff) e rate limiting configurabile."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

import conversations
import dashboard
import identity
import memory

ROOT = Path(__file__).resolve().parents[1]
PHONE = "+393331112222"


class SendValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_invalid_phone(self):
        for phone in ("", "abc", "123", "+39 333"):
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    conversations.send_message(
                        phone, "ciao", base=self.base, command="/bin/true"
                    )

    def test_rejects_empty_long_and_control_text(self):
        memory.set_handoff(PHONE, True, "Anna", base=self.base)
        for text in ("", "   ", "x" * (conversations.MAX_SEND + 1), "ciao\x00"):
            with self.subTest(text=text[:10]):
                with self.assertRaises(ValueError):
                    conversations.send_message(
                        PHONE, text, base=self.base, command="/bin/true"
                    )

    def test_requires_handoff_before_writing(self):
        memory.set_handoff(PHONE, False, base=self.base)
        with self.assertRaises(PermissionError):
            conversations.send_message(
                PHONE, "ciao", base=self.base, command="/bin/true"
            )

    def test_missing_openclaw_is_a_clear_error(self):
        memory.set_handoff(PHONE, True, "Anna", base=self.base)
        with self.assertRaises(RuntimeError):
            conversations.send_message(
                PHONE, "ciao", base=self.base, command="/nonexistent/openclaw-cli"
            )


class SendCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        memory.set_handoff(PHONE, True, "Anna", base=self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_uses_whatsapp_send_and_records_redacted_memory(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"messageId":"m1"}', ""))
        with mock.patch.object(conversations.subprocess, "run", runner):
            result = conversations.send_message(
                PHONE, "Le scrivo io, mi occupo del preventivo.", "Anna",
                base=self.base, command="openclaw",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["message_id"], "m1")
        self.assertEqual(result["operator"], "Anna")
        argv = runner.call_args[0][0]
        self.assertEqual(argv[:3], ["openclaw", "message", "send"])
        self.assertIn("--channel", argv)
        self.assertIn("whatsapp", argv)
        self.assertIn("--target", argv)
        self.assertIn(PHONE, argv)
        self.assertIn("--message", argv)
        self.assertIn("--json", argv)
        profile = memory.profile(PHONE, base=self.base)
        self.assertEqual(profile["messages_out"], 1)
        messages = conversations.transcript(PHONE, base=self.base)["messages"]
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertIn("Le scrivo io", messages[-1]["text"])

    def test_audit_stores_the_number_but_not_the_text(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"messageId":"m2"}', ""))
        with mock.patch.object(conversations.subprocess, "run", runner):
            conversations.send_message(PHONE, "Testo riservato 3331112222", "Anna",
                                       base=self.base, command="openclaw")
        audit = identity.audit_path(self.base).read_text(encoding="utf-8")
        self.assertIn("chat.send", audit)
        self.assertIn(PHONE, audit)
        self.assertNotIn("Testo riservato", audit)

    def test_failed_send_reports_error_and_writes_nothing_to_memory(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 2, "", "gateway offline"))
        with mock.patch.object(conversations.subprocess, "run", runner):
            result = conversations.send_message(
                PHONE, "ciao", "Anna", base=self.base, command="openclaw"
            )
        self.assertFalse(result["ok"])
        self.assertIn("gateway offline", result["error"])
        self.assertEqual(memory.profile(PHONE, base=self.base)["messages_out"], 0)

    def test_openclaw_error_payload_is_reported(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, '{"error":"not connected"}', "")
        )
        with mock.patch.object(conversations.subprocess, "run", runner):
            result = conversations.send_message(
                PHONE, "ciao", "Anna", base=self.base, command="openclaw"
            )
        self.assertFalse(result["ok"])


class RateLimitConfigTests(unittest.TestCase):
    def test_values_are_clamped_and_invalid_falls_back(self):
        with mock.patch.object(dashboard, "RATE_CFG", {"login_max_fails": 3}):
            self.assertEqual(dashboard._rate_value("login_max_fails", 10, 1, 1000), 3)
        with mock.patch.object(dashboard, "RATE_CFG", {"login_max_fails": 0}):
            self.assertEqual(dashboard._rate_value("login_max_fails", 10, 1, 1000), 1)
        with mock.patch.object(dashboard, "RATE_CFG", {"login_max_fails": 99999}):
            self.assertEqual(dashboard._rate_value("login_max_fails", 10, 1, 1000), 1000)
        with mock.patch.object(dashboard, "RATE_CFG", {"login_max_fails": "tanti"}):
            self.assertEqual(dashboard._rate_value("login_max_fails", 10, 1, 1000), 10)
        with mock.patch.object(dashboard, "RATE_CFG", {"send_seconds": -5}):
            self.assertEqual(dashboard._rate_value("send_seconds", 1, 0, 60), 0)

    def test_defaults_are_exposed_in_status(self):
        self.assertGreaterEqual(dashboard.LOGIN_MAX_FAILS, 1)
        self.assertGreaterEqual(dashboard.LOGIN_WINDOW, 30)
        self.assertGreaterEqual(dashboard.ACTION_SECONDS, 0)
        self.assertGreaterEqual(dashboard.SEND_SECONDS, 0)


class DashboardWiringTests(unittest.TestCase):
    def test_send_route_is_wired_to_conversations(self):
        source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"/api/conversations/send"', source)
        self.assertIn("conversations_view.send_message", source)

    def test_ui_has_the_operator_composer_in_both_languages(self):
        page = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        for element in ('id="convSendForm"', 'id="convSendText"', 'id="convSendBtn"'):
            self.assertIn(element, page)
        for key in ("conv.send", "conv.sent", "conv.sendError", "conv.messagePh"):
            self.assertGreaterEqual(page.count(f"'{key}':"), 2, key)


if __name__ == "__main__":
    unittest.main()
