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

    def test_self_test(self):
        healthcheck.self_test()


if __name__ == "__main__":
    unittest.main()
