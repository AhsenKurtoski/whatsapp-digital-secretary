import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def health_state():
    return {
        "version": "1.1.0",
        "generated_at": "2026-11-01T21:51:21+01:00",
        "overall": "critical",
        "counts": {"ok": 13, "info": 2, "warn": 1, "critical": 1},
        "checks": [
            {"id": "gateway", "label": "Gateway OpenClaw", "status": "ok", "detail": "Attivo."},
            {
                "id": "google_oauth",
                "label": "Google OAuth (gog)",
                "status": "critical",
                "detail": "Token scaduto: riautorizza o attiva il bridge.",
            },
            {
                "id": "mcp",
                "label": "MCP Google Workspace",
                "status": "warn",
                "detail": "Non connesso.",
            },
        ],
        "tokens": [],
        "alerts": [],
    }


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="digest-tests-"))
        self.health = self.tmp / "health_state.json"
        self.leads = self.tmp / "leads.jsonl"
        self.state = self.tmp / "digest_state.json"
        self.health.write_text(json.dumps(health_state()), encoding="utf-8")
        self.leads.write_text("", encoding="utf-8")

    def run_digest(self, *extra):
        env = os.environ.copy()
        env["SECRETARY_BASE"] = str(self.tmp)
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "digest.py"),
                "--dry-run",
                "--force",
                "--health",
                str(self.health),
                "--leads",
                str(self.leads),
                "--alerts",
                str(self.tmp / "alerts.jsonl"),
                "--state",
                str(self.state),
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

    def test_health_problems_are_sent_even_without_leads(self):
        result = self.run_digest()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Health", result.stdout)
        self.assertIn("Google OAuth", result.stdout)
        self.assertIn("Nessun nuovo contatto oggi", result.stdout)

    def test_healthy_day_without_leads_sends_nothing(self):
        data = health_state()
        data["overall"] = "ok"
        data["counts"] = {"ok": 15, "info": 0, "warn": 0, "critical": 0}
        data["checks"] = [{"id": "gateway", "label": "Gateway", "status": "ok", "detail": "ok"}]
        self.health.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_digest()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(payload["sent"], False)

    def test_digest_keeps_contacts_and_adds_health(self):
        lead = {
            "ts": "2026-11-01T10:00:00+01:00",
            "sender": "+393331112222",
            "text": "Vorrei un preventivo",
            "intent": "preventivo",
            "score": 80,
        }
        self.leads.write_text(json.dumps(lead) + "\n", encoding="utf-8")
        result = self.run_digest("--date", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Messaggi ricevuti: 1", result.stdout)
        self.assertIn("Health", result.stdout)
        self.assertIn("1 critici", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
