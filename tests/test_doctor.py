import json
import tempfile
import unittest
from pathlib import Path

import doctor


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-test-"))
        self.base = self.tmp / "base"
        self.workspace = self.tmp / "workspace"
        self.base.mkdir()
        self.workspace.mkdir()
        for name in doctor.REQUIRED_FILES:
            (self.base / name).write_text("# fixture\n", encoding="utf-8")
        (self.base / "dashboard.html").write_text(
            'data-tab="quotes" id="quotesBody" data-tab="contacts"', encoding="utf-8"
        )
        (self.base / "plugins" / "secretary-guard").mkdir(parents=True)
        (self.base / "plugins" / "secretary-guard" / "index.js").write_text(
            "duplicate_suppressed\n", encoding="utf-8"
        )
        (self.base / "secrets.env").write_text("GEMINI_API_KEY=placeholder\n", encoding="utf-8")
        (self.workspace / "AGENTS.md").write_text(
            "Non sono abilitato · Messaggi ripetuti · request_quote\n", encoding="utf-8"
        )
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({
            "business_name": "Officina Demo",
            "owner_number": "+393331112222",
            "google_account": "demo@example.com",
            "vat_rate": 22,
        }), encoding="utf-8")
        self.gateway = self.tmp / "openclaw.json"
        self.gateway.write_text(json.dumps({
            "messages": {
                "inbound": {"byChannel": {"whatsapp": 1800}},
                "queue": {"byChannel": {"whatsapp": "collect"}},
            },
            "mcp": {"servers": {"secretary": {"toolFilter": {"include": [
                "request_email", "request_calendar", "request_quote",
            ]}}}},
        }), encoding="utf-8")

    def run_checks(self):
        return doctor.run_checks(
            self.base, self.config, self.gateway, self.workspace,
            services=False, http=False,
        )

    def test_complete_install_has_no_failures(self):
        report = doctor.summarize(self.run_checks())
        failures = [c for c in report["checks"] if c["status"] == "fail"]
        self.assertEqual(failures, [])
        self.assertTrue(report["overall"] in {"ok", "warn"})

    def test_old_dashboard_is_reported_with_remedy(self):
        (self.base / "dashboard.html").write_text("vecchia dashboard", encoding="utf-8")
        checks = self.run_checks()
        ui = next(c for c in checks if c["id"] == "dashboard_ui")
        self.assertEqual(ui["status"], "fail")
        self.assertIn("scripts/install.sh", ui["action"])

    def test_missing_quote_in_mcp_filter_is_reported(self):
        self.gateway.write_text(json.dumps({
            "messages": {"inbound": {"byChannel": {"whatsapp": 1800}}},
            "mcp": {"servers": {"secretary": {"toolFilter": {"include": [
                "request_email", "request_calendar",
            ]}}}},
        }), encoding="utf-8")
        checks = self.run_checks()
        mcp = next(c for c in checks if c["id"] == "mcp_config")
        self.assertEqual(mcp["status"], "fail")
        self.assertIn("approval_setup.py", mcp["action"])

    def test_missing_persona_rules_is_reported(self):
        (self.workspace / "AGENTS.md").write_text("vecchia persona\n", encoding="utf-8")
        checks = self.run_checks()
        persona = next(c for c in checks if c["id"] == "persona")
        self.assertEqual(persona["status"], "fail")

    def test_every_check_has_label_and_detail(self):
        for check in self.run_checks():
            self.assertTrue(check["label"])
            self.assertTrue(check["detail"])
            self.assertIn(check["status"], {"ok", "warn", "fail", "info"})


if __name__ == "__main__":
    unittest.main()
