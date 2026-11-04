import unittest
from datetime import datetime, timedelta

import support  # noqa: F401  (sets the isolated environment)
import reminders

NOW = datetime.now(reminders.ROME)


def request(status="pending", kind="email", age_hours=5, start_hours=None):
    payload = {"to": "cliente@example.com", "subject": "Preventivo"}
    if kind == "calendar":
        payload = {"summary": "Call sito", "start": (NOW + timedelta(hours=start_hours or 20)).isoformat()}
    return {
        "id": f"apr-{kind}-{status}",
        "type": kind,
        "status": status,
        "created_at": (NOW - timedelta(hours=age_hours)).isoformat(timespec="seconds"),
        "payload": payload,
    }


class ReminderTests(unittest.TestCase):
    def test_collect_ignores_handled_requests(self):
        items = reminders.collect([request(status="approved")], {}, now=NOW)
        self.assertEqual(items, [])

    def test_collect_includes_overdue_and_deadlines(self):
        items = reminders.collect(
            [request(), request(kind="calendar", age_hours=0)],
            {"expiring": [{"id": "google_oauth", "label": "Google OAuth", "days_left": 2}]},
            now=NOW,
        )
        keys = {item["key"] for item in items}
        self.assertIn("approval:apr-email-pending", keys)
        self.assertIn("deadline:apr-calendar-pending", keys)
        self.assertIn("token:google_oauth", keys)

    def test_far_tokens_are_not_reminded(self):
        items = reminders.collect([], {"expiring": [{"id": "x", "label": "X", "days_left": 40}]}, now=NOW)
        self.assertEqual(items, [])

    def test_message_mentions_business_and_limits_length(self):
        items = reminders.collect([request()], {}, now=NOW)
        message = reminders.build_message(items, {"business_name": "Officina Demo"}, now=NOW)
        self.assertIn("Officina Demo", message)
        self.assertIn("cliente@example.com", message)
        self.assertLessEqual(len(message), 1750)

    def test_dedup_window_and_force(self):
        sent = []
        initial = {}
        items = reminders.collect([request()], {}, now=NOW)
        fresh = reminders.pending_items(items, initial, now=NOW, cooldown_hours=12)
        self.assertEqual(len(fresh), len(items))
        state = {"sent": {item["key"]: NOW.isoformat() for item in items}}
        self.assertEqual(reminders.pending_items(items, state, now=NOW, cooldown_hours=12), [])
        later = NOW + timedelta(hours=13)
        self.assertEqual(len(reminders.pending_items(items, state, now=later, cooldown_hours=12)), len(items))
        reminders.self_test()
        self.assertEqual(sent, [])

    def test_run_without_items_does_not_send(self):
        result = reminders.run([], {}, state_path="/tmp/never-used-reminders-state.json",
                               now=NOW, sender=lambda text: True)
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "nothing_new")


if __name__ == "__main__":
    unittest.main()
