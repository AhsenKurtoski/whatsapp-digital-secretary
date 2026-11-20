"""Test dei moduli che compongono la piattaforma Gina."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402
import cache  # noqa: E402
import conversations  # noqa: E402
import gdpr  # noqa: E402
import identity  # noqa: E402
import lead_intel  # noqa: E402
import memory  # noqa: E402
import quotas  # noqa: E402
import router  # noqa: E402
import stats  # noqa: E402
import webhooks  # noqa: E402


class TempBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class LeadIntelTests(unittest.TestCase):
    def test_quote_in_italian(self):
        result = lead_intel.classify("Ciao, avrei bisogno di un preventivo per il sito del ristorante")
        self.assertEqual(result["intent"], "quote")
        self.assertEqual(result["language"], "it")
        self.assertGreaterEqual(result["score"], 60)

    def test_quote_in_english(self):
        result = lead_intel.classify("Hello, I would like a quote for a new website, please")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["intent"], "quote")

    def test_appointment_and_support(self):
        self.assertEqual(lead_intel.classify("Can we book a meeting tomorrow?")["intent"], "appointment")
        self.assertEqual(lead_intel.classify("Il sito non funziona, c'è un errore")["intent"], "support")

    def test_scam_is_never_a_lead(self):
        result = lead_intel.classify("Sono il supporto, inviami il codice OTP via SMS")
        self.assertTrue(result["suspicious"])
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["needs_human"])

    def test_redaction(self):
        masked = lead_intel.redact("mario.rossi@example.com +39 333 123 4567")
        self.assertIn("[email]", masked)
        self.assertIn("[telefono]", masked)
        self.assertNotIn("mario.rossi", masked)

    def test_audit_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = lead_intel.audit("+393331112222", "preventivo", lead_intel.classify("preventivo"), Path(tmp))
            self.assertEqual(entry["intent"], "quote")
            self.assertTrue((Path(tmp) / "lead_audit.jsonl").is_file())


class CacheTests(unittest.TestCase):
    def test_ttl_and_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.TTLCache(Path(tmp) / "c.json", max_entries=2, default_ttl=2)
            store.set("Che orari avete?", "9-18")
            self.assertEqual(store.get("che ORARI avete??"), "9-18")
            self.assertIsNone(store.get("altro"))
            store.set("scaduta", "x", ttl=-1)
            self.assertIsNone(store.get("scaduta"))
            stats_now = store.stats()
            self.assertEqual(stats_now["hits"], 1)
            self.assertGreaterEqual(stats_now["misses"], 2)

    def test_eviction_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            store = cache.TTLCache(path, max_entries=2, default_ttl=600)
            for index in range(4):
                store.set(f"k{index}", str(index))
            self.assertEqual(store.stats()["entries"], 2)
            reloaded = cache.TTLCache(path, max_entries=2)
            self.assertEqual(reloaded.get("k3"), "3")


class IdentityTests(TempBase):
    def test_roles_hashing_and_audit(self):
        admin = identity.create_user("Owner", "admin", token="owner-token-0123456789", base=self.base)
        self.assertEqual(admin["token"], "owner-token-0123456789")
        self.assertNotIn("token", identity.list_users(self.base)[0])
        saved = json.loads(identity.users_path(self.base).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["users"][0]["token"]), 64)

        self.assertEqual(identity.authenticate("owner-token-0123456789", self.base)["role"], "admin")
        self.assertIsNone(identity.authenticate("nope", self.base))
        viewer = identity.create_user("Reader", "viewer", base=self.base)
        self.assertFalse(identity.role_allows("viewer", "operator"))
        self.assertTrue(identity.role_allows("operator", "viewer"))
        self.assertTrue(identity.revoke_user("Reader", self.base))
        self.assertIsNone(identity.authenticate(viewer["token"], self.base))
        self.assertFalse(identity.revoke_user("Reader", self.base))

        identity.audit("Owner", "approve", "apr-1", base=self.base)
        self.assertEqual(identity.recent_audit(5, self.base)[0]["action"], "approve")

    def test_invalid_role(self):
        with self.assertRaises(ValueError):
            identity.create_user("X", "root", base=self.base)


class AuditRotationTests(TempBase):
    def _write(self, count: int = 6) -> None:
        with unittest.mock.patch.object(identity, "AUDIT_MAX_BYTES", 240):
            for index in range(count):
                identity.audit("Owner", f"azione-{index}", "x" * 60, base=self.base)

    def test_rotation_keeps_every_entry_and_the_chain(self):
        self._write()
        segments = identity.audit_segments(self.base)
        self.assertGreaterEqual(len(segments), 2, segments)
        report = identity.verify_audit(self.base)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["entries"], 6)
        self.assertEqual(report["chained"], 6)
        self.assertEqual(report["segments"], len(segments))
        actions = []
        for path in segments + [identity.audit_path(self.base)]:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    actions.append(json.loads(line)["action"])
        self.assertEqual(actions, [f"azione-{index}" for index in range(6)])

    def test_new_entries_continue_the_chain_after_rotation(self):
        self._write()
        identity.audit("Owner", "dopo-la-rotazione", "y" * 60, base=self.base)
        report = identity.verify_audit(self.base)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["chained"], 7)
        self.assertEqual(identity.recent_audit(1, self.base)[0]["action"], "dopo-la-rotazione")

    def test_events_panel_reads_rotated_segments(self):
        self._write()
        # Il file vivo può non esistere: la storia sta nei segmenti ruotati.
        rows = identity.recent_audit(10, self.base)
        self.assertEqual(
            [row["action"] for row in rows],
            [f"azione-{index}" for index in reversed(range(6))],
        )
        identity.audit("Owner", "nuova", "z", base=self.base)
        newest = identity.recent_audit(2, self.base)
        self.assertEqual([row["action"] for row in newest], ["nuova", "azione-5"])

    def test_events_panel_stops_at_the_limit_instead_of_parsing_everything(self):
        self._write()
        self.assertGreaterEqual(len(identity.audit_segments(self.base)), 2)
        parsed = []
        real_loads = json.loads

        def counting(raw, *args, **kwargs):
            parsed.append(raw)
            return real_loads(raw, *args, **kwargs)

        # Sostituisce solo il `json` visto da identity: il modulo globale resta intatto.
        with unittest.mock.patch.object(identity, "json", unittest.mock.Mock(loads=counting)):
            rows = identity.recent_audit(2, self.base)
        self.assertEqual([row["action"] for row in rows], ["azione-5", "azione-4"])
        self.assertEqual(len(parsed), 2, "deve fermarsi al limite, non leggere tutto l'archivio")

    def test_order_across_multiple_segments_and_an_empty_live_file(self):
        def add(action: str) -> None:
            identity.audit("Owner", action, base=self.base)

        add("a0")
        self.assertIsNotNone(identity.rotate_audit(self.base), "segmento con a0")
        add("a1")
        add("a2")
        self.assertIsNotNone(identity.rotate_audit(self.base), "segmento con a1 e a2")
        add("a3")
        add("a4")
        add("a5")
        self.assertEqual(len(identity.audit_segments(self.base)), 2)
        self.assertEqual(
            [row["action"] for row in identity.recent_audit(10, self.base)],
            ["a5", "a4", "a3", "a2", "a1", "a0"],
            "file vivo e poi segmenti dal più recente, con l'ordine interno conservato",
        )
        self.assertEqual(
            [row["action"] for row in identity.recent_audit(4, self.base)],
            ["a5", "a4", "a3", "a2"],
            "il limite può cadere a metà di un segmento",
        )
        # File vivo vuoto (per esempio dopo un crash): si continua dai segmenti.
        identity.audit_path(self.base).write_text("", encoding="utf-8")
        self.assertEqual(
            [row["action"] for row in identity.recent_audit(3, self.base)],
            ["a2", "a1", "a0"],
        )
        self.assertEqual(
            [row["action"] for row in identity.recent_audit(1, self.base)], ["a2"],
        )

    def test_events_panel_caps_an_absurd_limit(self):
        for index in range(5):
            identity.audit("Owner", f"c{index}", base=self.base)
        with unittest.mock.patch.object(identity, "MAX_RECENT_AUDIT", 2):
            rows = identity.recent_audit(10_000, self.base)
        self.assertEqual([row["action"] for row in rows], ["c4", "c3"])

    def test_deleting_a_segment_breaks_the_chain(self):
        self._write()
        segments = identity.audit_segments(self.base)
        self.assertGreaterEqual(len(segments), 2, segments)
        segments[0].unlink()
        report = identity.verify_audit(self.base)
        self.assertFalse(report["ok"], report)
        self.assertIsNotNone(report["broken_in"], report)

    def test_edited_entry_in_a_segment_breaks_the_chain(self):
        self._write()
        segment = identity.audit_segments(self.base)[0]
        lines = segment.read_text(encoding="utf-8").splitlines()
        segment.write_text(
            "\n".join([lines[0].replace("azione-0", "azione-9")] + lines[1:]) + "\n",
            encoding="utf-8",
        )
        self.assertFalse(identity.verify_audit(self.base)["ok"])


class MemoryTests(TempBase):
    def test_contact_file_is_encrypted_at_rest(self):
        phone = "+393331112299"
        memory.record(phone, "user", "Vorrei un preventivo per il sito", base=self.base)
        memory.remember(phone, "budget", "3000 euro", base=self.base)
        raw = memory._path(phone, self.base).read_bytes()
        self.assertTrue(raw.startswith(memory.MAGIC), "il file deve essere cifrato")
        self.assertNotIn(phone.encode(), raw)
        self.assertNotIn(b"3000 euro", raw)
        profile = memory.profile(phone, self.base)
        self.assertEqual(profile["facts"]["budget"], "3000 euro")
        self.assertEqual(profile["messages_in"], 1)

    def test_memory_lifecycle(self):
        phone = "+393331112222"
        memory.record(phone, "user", "Vorrei un preventivo per il sito", intent="quote",
                      score=75, name="Mario", base=self.base)
        memory.record(phone, "assistant", "Certo, a che email?", base=self.base)
        profile = memory.profile(phone, self.base)
        self.assertEqual(profile["messages_in"], 1)
        self.assertEqual(profile["language"], "it")
        self.assertEqual(profile["max_score"], 75)

        memory.remember(phone, "servizio", "sito web", base=self.base)
        self.assertIn("sito web", memory.context(phone, base=self.base))

        memory.record(phone, "user", "scrivi a mario@example.com", base=self.base)
        self.assertIn("[email]", memory.transcript(phone, 1, base=self.base)[0]["text"])

        memory.set_handoff(phone, True, "Anna", base=self.base)
        self.assertTrue(memory.is_handoff(phone, self.base))
        self.assertEqual(memory.conversations(base=self.base)[0]["handoff"], True)

        self.assertTrue(memory.forget(phone, self.base))
        self.assertFalse(memory.forget(phone, self.base))

    def test_fact_update_and_relevance(self):
        """Un fatto aggiornato non deve essere scartato al posto di uno vecchio."""
        phone = "+393334445555"
        memory.remember(phone, "servizio", "sito web", base=self.base)
        memory.remember(phone, "budget", "1000 euro", base=self.base)
        profile = memory.remember(phone, "servizio", "e-commerce", base=self.base)
        self.assertEqual(list(profile["facts"])[-1], "servizio")
        self.assertIn("updated_at", profile["fact_meta"]["servizio"])

        ctx = memory.context(phone, base=self.base, query="quanto costa l'e-commerce?")
        self.assertIn("e-commerce", ctx)
        self.assertNotIn("sito web", ctx)

        other = "+393336667777"
        memory.record(other, "user", "Cerco un e-commerce con magazzino", base=self.base)
        memory.remember(other, "interesse", "e-commerce", base=self.base)
        hits = memory.search("e-commerce", base=self.base)
        self.assertEqual({row["phone"] for row in hits}, {phone, other})
        self.assertTrue(any(row["type"] == "message" for row in hits))

    def test_demo_seed_populates_memory(self):
        import demo

        demo.seed_memory(self.base)
        rows = memory.conversations(base=self.base)
        self.assertEqual(len(rows), 3)
        mario = memory.profile("+393331112233", self.base)
        self.assertEqual(mario["facts"]["budget"], "3000 euro")
        self.assertEqual(mario["name"], "Mario Bianchi")

    def test_consecutive_duplicates_are_collapsed(self):
        phone = "+393338889999"
        memory.record(phone, "user", "Buongiorno", base=self.base)
        memory.record(phone, "user", "Buongiorno", base=self.base)
        self.assertEqual(len(memory.transcript(phone, base=self.base)), 1)


class QuotasTests(TempBase):
    def test_usage_thresholds(self):
        quotas.record("groq", ok=True, latency_ms=800, tokens=100, base=self.base)
        quotas.record("groq", ok=False, status=429, base=self.base)
        data = quotas.status(self.base)
        groq = next(p for p in data["providers"] if p["provider"] == "groq")
        self.assertEqual(groq["requests"], 2)
        self.assertEqual(groq["rate_limited"], 1)
        self.assertIn(groq["level"], {"warn", "critical"})
        self.assertTrue(data["alerts"])

    def test_alerts_are_english_when_requested(self):
        quotas.record("groq", ok=False, status=429, base=self.base)
        data = quotas.status(self.base, lang="en")
        self.assertTrue(data["alerts"])
        self.assertIn("quota", data["alerts"][0].lower())
        self.assertIn("requests today", data["alerts"][0])
        self.assertNotIn("richieste", data["alerts"][0])

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            quotas.record("openai", base=self.base)


class RouterTests(TempBase):
    def test_reasons_are_english_when_requested(self):
        decision = router.choose(load=2, base=self.base, lang="en")
        text = " ".join(decision["reasons"])
        self.assertIn("active load: 2 conversations", text)
        self.assertIn("chosen provider:", text)
        self.assertNotIn("carico", text)

    def test_prefers_fast_healthy_provider(self):
        quotas.record("groq", ok=True, latency_ms=400, base=self.base)
        quotas.record("gemini", ok=True, latency_ms=2400, base=self.base)
        decision = router.choose(load=0, base=self.base)
        self.assertEqual(decision["provider"], "groq")
        self.assertTrue(decision["fallbacks"])
        self.assertIn("reasons", decision)

    def test_rate_limited_provider_falls_back(self):
        quotas.record("groq", ok=True, latency_ms=300, base=self.base)
        for _ in range(3):
            quotas.record("gemini", ok=False, status=429, base=self.base)
        decision = router.choose(load=0, base=self.base)
        self.assertEqual(decision["ranking"][0]["provider"], "groq")

    def test_apply_uses_cli_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "openclaw"
            fake.write_text("#!/usr/bin/env bash\necho \"$@\"\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            result = router.apply_choice("groq/llama-3.3-70b-versatile", cli=str(fake))
            self.assertTrue(result["ok"])
            self.assertIn("agents.defaults.model.primary", result["output"])


class WebhooksTests(TempBase):
    def test_signed_delivery_and_filtering(self):
        received = []

        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append((self.rfile.read(length), self.headers.get("X-Gina-Signature")))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/hook"
            webhooks.add_endpoint(url, ["lead.created"], secret="s3cret", base=self.base)
            self.assertTrue(webhooks.emit("lead.created", {"score": 80}, base=self.base)["ok"])
            self.assertEqual(len(received), 1)
            import hashlib
            import hmac
            expected = "sha256=" + hmac.new(b"s3cret", received[0][0], hashlib.sha256).hexdigest()
            self.assertEqual(received[0][1], expected)
            webhooks.emit("quote.created", {}, base=self.base)
            self.assertEqual(len(received), 1)
            self.assertTrue(webhooks.deliveries(5, self.base))
        finally:
            server.shutdown()
            server.server_close()

    def test_rejects_public_http(self):
        with self.assertRaises(ValueError):
            webhooks.add_endpoint("http://example.com/hook", base=self.base)


class StatsTests(TempBase):
    def test_kpis(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        (self.base / "leads.jsonl").write_text(
            json.dumps({"ts": now, "phone": "+393331112222", "score": 80, "intent": "preventivo"}) + "\n",
            encoding="utf-8",
        )
        (self.base / "approvals.jsonl").write_text(
            json.dumps({"type": "quote", "status": "approved", "created_at": now,
                        "payload": {"totals": {"total": 1000.0}}}) + "\n",
            encoding="utf-8",
        )
        memory.record("+393331112222", "user", "preventivo", base=self.base)
        memory.record("+393331112222", "assistant", "ecco", base=self.base)
        data = stats.compute(self.base)
        self.assertEqual(data["kpis"]["leads"], 1)
        self.assertEqual(data["kpis"]["quotes_approved"], 1)
        self.assertEqual(data["kpis"]["lead_conversion_rate"], 1.0)
        self.assertEqual(len(data["daily"]), stats.DAYS)
        self.assertFalse(data["empty"])


class GdprTests(TempBase):
    def test_delete_contact_removes_everything(self):
        phone = "+393331112222"
        memory.record(phone, "user", "preventivo", base=self.base)
        (self.base / "leads.jsonl").write_text(
            json.dumps({"phone": phone}) + "\n" + json.dumps({"phone": "+393339998888"}) + "\n",
            encoding="utf-8",
        )
        (self.base / "quotes").mkdir()
        (self.base / "quotes" / "apr-1.pdf").write_bytes(b"%PDF")
        (self.base / "approvals.jsonl").write_text(
            json.dumps({"id": "apr-1", "type": "quote", "client_phone": phone,
                        "payload": {"pdf": "apr-1.pdf"}}) + "\n", encoding="utf-8")
        result = gdpr.delete_contact(phone, self.base)
        self.assertEqual(result["deleted"]["memory"], 1)
        self.assertEqual(result["deleted"]["leads"], 1)
        self.assertEqual(result["deleted"]["pdf"], 1)
        self.assertFalse((self.base / "quotes" / "apr-1.pdf").exists())
        self.assertEqual(len(gdpr._rows(self.base / "leads.jsonl")), 1)

    def test_export_contains_contact_data(self):
        phone = "+393331112222"
        memory.record(phone, "user", "ciao", base=self.base)
        exported = gdpr.export_contact(phone, self.base)
        self.assertEqual(exported["memory"]["phone"], phone)
        self.assertIn("where", gdpr.inventory(self.base))


class BackupTests(TempBase):
    @unittest.skipUnless(backup is not None, "cryptography non disponibile")
    def test_create_verify_restore(self):
        source = self.base / "source"
        source.mkdir()
        (source / "config.json").write_text('{"a": 1}', encoding="utf-8")
        created = backup.create(self.base / "state", paths=[source], out_dir=self.base / "out", keep=1)
        self.assertTrue(Path(created["path"]).is_file())
        self.assertTrue(backup.verify(created["path"], self.base / "state")["ok"])
        restored = backup.restore(created["path"], self.base / "restored", self.base / "state")
        self.assertTrue(restored["ok"])
        target = self.base / "restored" / str((source / "config.json").resolve()).lstrip("/")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}')

    def test_tampered_archive_fails_verification(self):
        source = self.base / "source"
        source.mkdir()
        (source / "a.txt").write_text("contenuto", encoding="utf-8")
        created = backup.create(self.base / "state", paths=[source], out_dir=self.base / "out")
        blob = Path(created["path"]).read_bytes()
        tampered = self.base / "out" / "tampered.ginabk"
        tampered.write_bytes(blob[:-5] + b"xxxxx")
        self.assertFalse(backup.verify(tampered, self.base / "state")["ok"])


class BackupCliTests(TempBase):
    def test_empty_source_is_rejected(self):
        with self.assertRaises(ValueError):
            backup.create(self.base / "state", paths=[self.base / "assente"], out_dir=self.base / "out")

    def test_missing_archive_fails_cleanly(self):
        result = backup.verify(self.base / "nope.ginabk", self.base)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_wrong_passphrase_cannot_restore(self):
        source = self.base / "source"
        source.mkdir()
        (source / "a.txt").write_text("dato", encoding="utf-8")
        created = backup.create(self.base / "state", paths=[source], out_dir=self.base / "out")
        os.environ["SECRETARY_BACKUP_PASSPHRASE"] = "sbagliata"
        try:
            self.assertFalse(backup.verify(created["path"], self.base / "state")["ok"])
            self.assertFalse(backup.restore(created["path"], self.base / "r", self.base / "state")["ok"])
        finally:
            os.environ.pop("SECRETARY_BACKUP_PASSPHRASE", None)

    def test_default_directory_and_listing(self):
        source = self.base / "source"
        source.mkdir()
        (source / "a.txt").write_text("dato", encoding="utf-8")
        state = self.base / "state"
        created = backup.create(state, paths=[source], keep=1)
        self.assertTrue(created["path"].startswith(str(state / "backups")))
        self.assertEqual(len(backup.list_backups(state)), 1)
        self.assertIn("name", backup.list_backups(state)[0])


class WebhookEdgeTests(TempBase):
    def test_retry_then_success(self):
        attempts = {"n": 0}

        class Flaky(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                attempts["n"] += 1
                self.send_response(500 if attempts["n"] == 1 else 200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Flaky)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            webhooks.add_endpoint(f"http://127.0.0.1:{server.server_port}/h", ["lead.created"],
                                  secret="s", base=self.base)
            result = webhooks.emit("lead.created", {}, base=self.base)
            self.assertTrue(result["ok"], result)
            self.assertEqual(attempts["n"], 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_duplicate_and_unknown_event(self):
        url = "http://127.0.0.1:9/hook"
        webhooks.add_endpoint(url, ["lead.created"], secret="s", base=self.base)
        with self.assertRaises(ValueError):
            webhooks.add_endpoint(url, ["lead.created"], base=self.base)
        with self.assertRaises(ValueError):
            webhooks.emit("evento.ignoto", {}, base=self.base)
        with self.assertRaises(ValueError):
            webhooks.test_endpoint(999, base=self.base)


class WizardTests(TempBase):
    def test_run_aborts_when_whatsapp_missing(self):
        import setup_wizard

        answers = iter(["", "", "", "n"])
        with unittest.mock.patch.object(setup_wizard, "whatsapp_status", return_value={"ok": False}):
            saved = []
            ok = setup_wizard.run({}, saved.append, ask=lambda _prompt: next(answers), out=lambda *_: None)
        self.assertFalse(ok)
        self.assertEqual(saved, [])

    def test_run_saves_and_writes_validated_key(self):
        import setup_wizard

        answers = iter(["chiave-lunga-abcdef", "", "test@example.com", "s"])
        with unittest.mock.patch.object(setup_wizard, "validate_ai_key",
                                        return_value={"ok": True, "models": 3}), \
             unittest.mock.patch.object(setup_wizard, "whatsapp_status",
                                        return_value={"ok": True}), \
             unittest.mock.patch.object(setup_wizard, "send_test_email",
                                        return_value={"ok": True}), \
             unittest.mock.patch.object(setup_wizard.config, "BASE", self.base):
            saved = []
            ok = setup_wizard.run({"google_account": ""}, saved.append,
                                  ask=lambda _prompt: next(answers), out=lambda *_: None)
        self.assertTrue(ok)
        self.assertEqual(len(saved), 1)
        text = (self.base / "secrets.env").read_text(encoding="utf-8")
        self.assertIn("GROQ_API_KEY=chiave-lunga-abcdef", text)
        self.assertNotIn("chiave-lunga-abcdef", json.dumps(saved[0]))


class ConversationsTests(TempBase):
    def test_transcript_from_openclaw_db(self):
        db = self.base / "openclaw.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE session_windows (session_id TEXT, session_key TEXT, display_name TEXT, "
            "updated_at INTEGER, channel TEXT, chat_type TEXT)"
        )
        con.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, created_at INTEGER, event_json TEXT)")
        now = int(time.time() * 1000)
        con.execute("INSERT INTO session_windows VALUES (?,?,?,?,?,?)",
                    ("s1", "whatsapp:+393335554444", "Mario", now, "whatsapp", "direct"))
        for index, (role, text) in enumerate([("user", "Ciao"), ("assistant", "Come posso aiutarti?")]):  # noqa: B007
            event = {"type": "message", "message": {"role": role, "content": text,
                                                     "timestamp": now + index * 1000}}
            con.execute("INSERT INTO transcript_events VALUES (?,?,?,?)",
                        ("s1", index, now + index * 1000, json.dumps(event)))
        con.commit()
        con.close()

        original = conversations.config.db_path
        conversations.config.db_path = lambda: str(db)
        try:
            rows = conversations.list_conversations(base=self.base)
            self.assertEqual(rows[0]["phone"], "+393335554444")
            data = conversations.transcript("+393335554444", base=self.base)
            self.assertEqual(data["source"], "openclaw")
            self.assertEqual(len(data["messages"]), 2)
            conversations.set_handoff("+393335554444", True, "Anna", base=self.base)
            self.assertTrue(conversations.list_conversations(base=self.base)[0]["handoff"])
        finally:
            conversations.config.db_path = original


if __name__ == "__main__":
    unittest.main()
