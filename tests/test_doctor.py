import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def break_gateway(self):
        self.gateway.write_text(json.dumps({
            "messages": {"queue": {"mode": "steer"}},
            "mcp": {"servers": {"secretary": {"toolFilter": {"include": [
                "request_email", "request_calendar",
            ]}}}},
        }), encoding="utf-8")

    def test_plan_repairs_quote_allowlist_and_policy(self):
        self.break_gateway()
        plan = doctor.plan_fixes(
            self.base, self.run_checks(), self.config, self.gateway
        )
        keys = [item["key"] for item in plan]
        self.assertEqual(keys[:2], ["approval_setup", "gateway_policy"])
        self.assertEqual(keys[-1], "restart:openclaw-gateway.service")
        self.assertTrue(all(item["prompt"].endswith("[s/N] ") for item in plan))

    def test_plan_is_empty_for_healthy_install(self):
        self.assertEqual(
            doctor.plan_fixes(self.base, self.run_checks(), self.config, self.gateway),
            [],
        )

    def test_plan_skips_restarts_when_offline(self):
        self.break_gateway()
        plan = doctor.plan_fixes(
            self.base, self.run_checks(), self.config, self.gateway, can_restart=False
        )
        self.assertEqual(
            [item["key"] for item in plan], ["approval_setup", "gateway_policy"]
        )

    def test_plan_leaves_unfixable_dashboard_alone(self):
        (self.base / "dashboard.html").write_text("vecchia dashboard", encoding="utf-8")
        plan = doctor.plan_fixes(
            self.base, self.run_checks(), self.config, self.gateway, can_restart=False
        )
        self.assertEqual(plan, [])

    def test_run_fixes_applies_each_step_in_order(self):
        self.break_gateway()
        plan = doctor.plan_fixes(
            self.base, self.run_checks(), self.config, self.gateway
        )
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "ok", "")

        lines = []
        outcome = doctor.run_fixes(
            plan, assume_yes=True, runner=fake_run, out=lines.append
        )
        self.assertEqual(outcome["applied"], [item["key"] for item in plan])
        self.assertEqual(outcome["failed"], [])
        self.assertEqual(calls[0][-1], str(self.base / "approval_setup.py"))
        self.assertEqual(calls[-1][-1], "openclaw-gateway.service")
        self.assertTrue(any("Ricollega i preventivi" in line for line in lines))
        self.assertTrue(any("✅" in line for line in lines))

    def test_run_fixes_makes_no_change_without_confirmation(self):
        self.break_gateway()
        plan = doctor.plan_fixes(
            self.base, self.run_checks(), self.config, self.gateway
        )

        def must_not_run(*_args, **_kwargs):
            raise AssertionError("nessun comando deve partire senza conferma")

        outcome = doctor.run_fixes(
            plan, confirm=lambda _prompt: False, runner=must_not_run,
            out=lambda *_args: None,
        )
        self.assertEqual(outcome["applied"], [])
        self.assertEqual(outcome["skipped"], [item["key"] for item in plan])

    def test_apply_fix_reports_last_error_line(self):
        item = {"key": "x", "command": ["false"], "done": ""}

        def fake_run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 2, "", "prima\nultima riga")

        ok, message = doctor.apply_fix(item, runner=fake_run)
        self.assertFalse(ok)
        self.assertEqual(message, "ultima riga")

    def test_cli_rejects_json_with_fix(self):
        with patch.object(sys, "argv", ["doctor.py", "--json", "--fix"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    doctor.main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
