"""Le CLI reali dei moduli: stessi percorsi usati da chi amministra l'installazione.

I comandi vengono eseguiti in-process chiamando ``main()`` con ``sys.argv``
reale: è lo stesso codice di ``python3 modulo.py ...`` e la copertura lo misura.
"""
import contextlib
import importlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

import config
import memory

ROOT = Path(__file__).resolve().parents[1]


def run_cli(module_name, *args):
    module = importlib.import_module(module_name)
    out, err = io.StringIO(), io.StringIO()
    argv = [f"{module_name}.py", *args]
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = module.main()
        except SystemExit as exc:
            code = int(exc.code or 0)
    return SimpleNamespace(returncode=int(code or 0), stdout=out.getvalue(), stderr=err.getvalue())


class CliTests(unittest.TestCase):
    def test_dashboard_help_does_not_start_the_server(self):
        result = run_cli("dashboard", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--port", result.stdout)
        self.assertIn("127.0.0.1", result.stdout)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cli-test-"))
        os.environ["GINA_CACHE_FILE"] = str(self.tmp / "cache.json")

    def test_lead_intel_cli_classifies_and_prints_json(self):
        result = run_cli("lead_intel", "--text", "Vorrei un preventivo per un sito", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["intent"], "quote")
        plain = run_cli("lead_intel", "--text", "Buongiorno")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("info", plain.stdout)

    def test_cache_cli_prints_statistics(self):
        result = run_cli("cache", "--stats")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hit", result.stdout.lower())

    def test_stats_cli_prints_kpis(self):
        result = run_cli("stats", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kpis", json.loads(result.stdout))

    def test_quotas_cli_records_usage(self):
        recorded = run_cli("quotas", "--record", "groq", "--ok", "--status", "200",
                           "--tokens", "120", "--latency", "0.4")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        result = run_cli("quotas", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("groq", result.stdout)
        self.assertTrue(data.get("date"))

    def test_router_cli_recommends_a_model_under_load(self):
        result = run_cli("router", "--load", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip())

    def test_memory_cli_reads_exports_and_forgets(self):
        phone = "+393331230101"
        memory.record(phone, "user", "Ciao, cerco un preventivo", name="Elena")
        memory.remember(phone, "servizio", "sito web")
        profile = run_cli("memory", "--profile", phone)
        self.assertEqual(profile.returncode, 0, profile.stderr)
        self.assertIn("Elena", profile.stdout)
        context = run_cli("memory", "--context", phone)
        self.assertEqual(context.returncode, 0, context.stderr)
        self.assertIn("sito web", context.stdout)
        listing = run_cli("memory", "--list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("Elena", listing.stdout)
        forgotten = run_cli("memory", "--forget", phone, "--yes")
        self.assertEqual(forgotten.returncode, 0, forgotten.stderr)
        self.assertNotIn("Elena", run_cli("memory", "--profile", phone).stdout)

    def _seed_session(self, phone="+393331230102", name="Mario"):
        db = config.db_path()
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS session_windows (session_id TEXT PRIMARY KEY, "
                "session_key TEXT, display_name TEXT, updated_at REAL, channel TEXT, chat_type TEXT)"
            )
            con.execute(
                "INSERT OR REPLACE INTO session_windows VALUES (?,?,?,?,?,?)",
                ("s-cli", f"whatsapp:{phone}", name, 1900000000.0, "whatsapp", "direct"),
            )
            con.commit()
        finally:
            con.close()

    def test_conversations_cli_lists_reads_and_hands_off(self):
        phone = "+393331230102"
        self._seed_session(phone)
        listing = run_cli("conversations", "--list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        rows = json.loads(listing.stdout)
        self.assertTrue(any(row["phone"] == phone for row in rows), rows)
        transcript = run_cli("conversations", "--transcript", phone)
        self.assertEqual(transcript.returncode, 0, transcript.stderr)
        self.assertEqual(json.loads(transcript.stdout)["phone"], phone)
        handoff = run_cli("conversations", "--handoff", phone, "--operator", "Anna")
        self.assertEqual(handoff.returncode, 0, handoff.stderr)
        self.assertTrue(json.loads(handoff.stdout)["handoff"]["active"])
        released = run_cli("conversations", "--release", phone)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertFalse(json.loads(released.stdout)["handoff"]["active"])

    def test_webhooks_cli_lifecycle(self):
        added = run_cli("webhooks", "--add", "https://example.com/hook", "--events", "lead.created")
        self.assertEqual(added.returncode, 0, added.stderr)
        entry = json.loads(added.stdout.splitlines()[0])
        self.assertTrue(entry["secret"])
        listing = run_cli("webhooks", "--list")
        self.assertIn("example.com", listing.stdout)
        deliveries = run_cli("webhooks", "--deliveries")
        self.assertEqual(deliveries.returncode, 0, deliveries.stderr)
        removed = run_cli("webhooks", "--remove", str(entry["id"]))
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("rimosso", removed.stdout)
        rejected = run_cli("webhooks", "--add", "http://public.example.com/hook")
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("https", rejected.stderr)

    def test_identity_cli_adds_lists_and_revokes(self):
        created = run_cli("identity", "--add", "Operatrice CLI", "--role", "operator")
        self.assertEqual(created.returncode, 0, created.stderr)
        data = json.loads(created.stdout)
        self.assertEqual(data["role"], "operator")
        listing = run_cli("identity", "--list")
        self.assertIn("Operatrice CLI", listing.stdout)
        revoked = run_cli("identity", "--revoke", "Operatrice CLI")
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertIn("revocato", revoked.stdout)
        invalid = run_cli("identity", "--add", "Senza Ruolo", "--role", "root")
        self.assertEqual(invalid.returncode, 1)

    def test_backup_cli_creates_verifies_and_restores(self):
        canary = Path(config.BASE) / "canary.txt"
        canary.write_text("dato di prova", encoding="utf-8")
        with mock.patch.dict(os.environ, {"SECRETARY_BACKUP_PASSPHRASE": "passphrase-di-prova"}):
            created = run_cli("backup", "--create")
            self.assertEqual(created.returncode, 0, created.stderr)
            entry = json.loads(created.stdout)
            archive = Path(entry["path"])
            self.assertTrue(archive.exists())
            listing = run_cli("backup", "--list")
            self.assertIn("gina-", listing.stdout)
            verified = run_cli("backup", "--verify", str(archive))
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            self.assertTrue(json.loads(verified.stdout)["ok"])
            self.assertEqual(run_cli("backup", "--restore", str(archive)).returncode, 2)
            restored = run_cli("backup", "--restore", str(archive), "--target", str(self.tmp / "restore"))
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertTrue(json.loads(restored.stdout)["ok"])
            tampered = self.tmp / "tampered.ginabk"
            tampered.write_bytes(archive.read_bytes()[:-8] + b"00000000")
            broken = run_cli("backup", "--verify", str(tampered))
            self.assertEqual(broken.returncode, 1)
            self.assertFalse(json.loads(broken.stdout)["ok"])

    def test_remote_access_cli_prints_the_private_tunnel(self):
        result = run_cli("remote_access", "--host", "host.tailnet.ts.net", "--port", "8790")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("127.0.0.1:8790:127.0.0.1:8790", result.stdout)
        self.assertIn("ssh", result.stdout)
        rejected = run_cli("remote_access", "--host", "../bad host")
        self.assertEqual(rejected.returncode, 2)

    def test_remote_access_check_reports_environment(self):
        result = run_cli("remote_access", "--check", "--json")
        data = json.loads(result.stdout)
        ids = {item["id"] for item in data["checks"]}
        self.assertEqual(ids, {"ssh", "tailscale", "dashboard"})
        self.assertEqual(result.returncode, 0 if data["ok"] else 1)

    def test_gdpr_cli_reports_exports_and_deletes(self):
        phone = "+393331230103"
        memory.record(phone, "user", "Ciao, mi chiamo Paolo", name="Paolo")
        report = run_cli("gdpr", "--report")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("memory_profiles", report.stdout)
        out = self.tmp / "export.json"
        exported = run_cli("gdpr", "--export", phone, "--out", str(out))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("Paolo", out.read_text(encoding="utf-8"))
        retention = run_cli("gdpr", "--retention", "30")
        self.assertEqual(retention.returncode, 0, retention.stderr)
        deleted = run_cli("gdpr", "--delete", phone, "--yes")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertNotEqual(memory.profile(phone).get("name"), "Paolo")


if __name__ == "__main__":
    unittest.main()
