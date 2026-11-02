import json
import sys
import unittest

import support  # noqa: F401
import healthcheck


class HealthcheckTests(unittest.TestCase):
    def test_overall_ignores_info(self):
        self.assertEqual(healthcheck.overall([{"status": "ok"}, {"status": "info"}]), "ok")
        self.assertEqual(healthcheck.overall([{"status": "ok"}, {"status": "warn"}]), "warn")
        self.assertEqual(
            healthcheck.overall([{"status": "ok"}, {"status": "critical"}, {"status": "warn"}]),
            "critical",
        )

    def test_google_expired_without_bridge_is_critical(self):
        alerts = healthcheck.build_alerts(
            [
                {"id": "google_oauth", "status": "critical", "detail": "invalid_grant", "action": ""},
                {"id": "google_bridge", "status": "info", "detail": "non configurato", "meta": {"configured": False}},
            ]
        )
        alert = next(item for item in alerts if item["key"] == "google_oauth_expired")
        self.assertEqual(alert["severity"], "critical")
        self.assertIn("bridge", alert["action"].lower())

    def test_google_expired_with_bridge_is_only_a_warning(self):
        alerts = healthcheck.build_alerts(
            [
                {"id": "google_oauth", "status": "critical", "detail": "invalid_grant", "action": ""},
                {"id": "google_bridge", "status": "ok", "detail": "ok", "meta": {"configured": True}},
            ]
        )
        alert = next(item for item in alerts if item["key"] == "google_oauth_expired")
        self.assertEqual(alert["severity"], "warn")

    def test_google_oauth_is_informational_when_bridge_handles_gmail(self):
        original_run = healthcheck._run
        original_gog = healthcheck.GOG
        original_bridge = healthcheck.bridge_configured
        healthcheck.GOG = sys.executable
        healthcheck.bridge_configured = lambda: True
        healthcheck._run = lambda cmd, **kwargs: (
            1,
            "",
            '{"error":{"message":"OAuth token expired or revoked (invalid_grant)"}}',
        )
        try:
            check = healthcheck.check_google_oauth()
        finally:
            healthcheck._run = original_run
            healthcheck.GOG = original_gog
            healthcheck.bridge_configured = original_bridge
        self.assertEqual(check["status"], "info")
        self.assertIn("bridge", check["detail"].lower())
        self.assertEqual(check["token"]["expires_iso"], "")
        self.assertEqual(check["token"]["status"], "info")
        self.assertEqual(healthcheck.build_alerts([check]), [])

    def test_message_is_bounded_and_has_a_title(self):
        message = healthcheck.render_message(
            {"generated_at": "2026-11-01T20:00:00+01:00", "counts": {"critical": 1, "warn": 0, "ok": 3}},
            [
                {
                    "key": "google_oauth_expired",
                    "severity": "critical",
                    "title": "Gmail/Calendar non funzionano",
                    "message": "Token Google scaduto.",
                    "action": "Attiva il bridge",
                }
            ],
        )
        self.assertIn("Health Segreteria", message)
        self.assertIn("Gmail/Calendar", message)
        self.assertLessEqual(len(message), 1800)

    def test_cooldowns_are_sensible(self):
        self.assertEqual(healthcheck.alert_cooldown({"key": "google_oauth_expired", "severity": "critical"}), 21600)
        self.assertEqual(healthcheck.alert_cooldown({"key": "whatsapp", "severity": "critical"}), 3600)
        self.assertEqual(healthcheck.alert_cooldown({"key": "disk", "severity": "critical"}), 21600)

    def test_config_check_reads_the_test_business(self):
        check = healthcheck.check_config()
        self.assertIn(check["status"], {"ok", "warn"})
        self.assertEqual(check["id"], "config")

    def test_expiring_tokens_filters_and_sorts(self):
        now = healthcheck.datetime.now(healthcheck.timezone.utc)
        soon = (now + healthcheck.timedelta(days=3)).isoformat()
        late = (now + healthcheck.timedelta(days=30)).isoformat()
        past = (now - healthcheck.timedelta(days=1)).isoformat()
        tokens = [
            {"id": "a", "label": "Google OAuth", "status": "warn", "expires_iso": soon, "expires": "x"},
            {"id": "b", "label": "Lontano", "status": "ok", "expires_iso": late, "expires": "y"},
            {"id": "c", "label": "Scaduto", "status": "critical", "expires_iso": past, "expires": "z"},
            {"id": "d", "label": "Senza scadenza", "status": "ok", "expires": "nessuna scadenza"},
        ]
        result = healthcheck.expiring_tokens(tokens)
        self.assertEqual([item["id"] for item in result], ["c", "a"])
        self.assertTrue(result[0]["expired"])
        self.assertFalse(result[1]["expired"])
        self.assertGreater(result[1]["days_left"], 2)

    def test_systemctl_next_parses_json_microseconds(self):
        payload = json.dumps([{"unit": "openclaw-secretary-health.timer", "next": 1793622558821633}])
        original = healthcheck._run

        def fake(cmd, **kwargs):
            return (0, "", "") if "show" in cmd else (0, payload, "")

        healthcheck._run = fake
        try:
            value = healthcheck._systemctl_next("openclaw-secretary-health.timer")
        finally:
            healthcheck._run = original
        self.assertRegex(value, r"^\d{2}/\d{2} \d{2}:\d{2}$")

    def test_systemctl_next_ignores_other_units(self):
        payload = json.dumps([{"unit": "other.timer", "next": 1793622558821633}])
        original = healthcheck._run
        healthcheck._run = lambda cmd, **kw: (0, "", "") if "show" in cmd else (0, payload, "")
        try:
            value = healthcheck._systemctl_next("openclaw-secretary-health.timer")
        finally:
            healthcheck._run = original
        self.assertEqual(value, "-")

    def test_self_test(self):
        healthcheck.self_test()


if __name__ == "__main__":
    unittest.main()
