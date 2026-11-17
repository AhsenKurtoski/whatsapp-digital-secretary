"""La pipeline live: intento, memoria e cache nella stessa risposta.

Questi test dimostrano che i tre moduli non sono isolati: la stessa funzione
usata dal tool MCP `client_brief` classifica il messaggio, legge la memoria del
cliente e restituisce una risposta pronta verificata.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

import cache
import config
import live
import memory

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    "business_name": "Officina Demo",
    "hours": "Lun–Ven 9:00–18:00",
    "city": "Milano",
    "services": ["riparazioni", "preventivi", "assistenza"],
    "site_url": "https://officina.example",
    "pricing_note": "I prezzi li conferma il titolare.",
}


class LiveModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-test-"))
        self.base = self.tmp / "base"
        self.base.mkdir()
        env = mock.patch.dict(os.environ, {"GINA_CACHE_FILE": str(self.tmp / "cache.json")})
        env.start()
        self.addCleanup(env.stop)
        cache.reset_shared()
        self.addCleanup(cache.reset_shared)
        patch = mock.patch.object(config, "_read_file", return_value=dict(CFG))
        patch.start()
        self.addCleanup(patch.stop)

    def test_faq_answers_are_deterministic_in_both_languages(self):
        cases = [
            ("A che ora siete aperti?", "Siamo aperti Lun–Ven 9:00–18:00.", "faq:hours"),
            ("Dove vi trovate?", "Ci trovi a Milano.", "faq:location"),
            ("Quali servizi offrite?", "Ci occupiamo di: riparazioni, preventivi, assistenza.", "faq:services"),
            ("Qual è il vostro sito?", "Il nostro sito è https://officina.example", "faq:site"),
            ("Quanto costa?", "I prezzi li conferma il titolare.", "faq:prices"),
        ]
        for question, expected, source in cases:
            answer, got_source = live.faq_answer(question, "it")
            self.assertEqual(answer, expected)
            self.assertEqual(got_source, source)
        self.assertEqual(
            live.faq_answer("What are your opening hours?", "en")[0],
            "Our opening hours are Lun–Ven 9:00–18:00.",
        )

    def test_faq_skips_missing_config_instead_of_inventing(self):
        with mock.patch.object(config, "_read_file", return_value={"city": "", "site_url": ""}):
            self.assertEqual(live.faq_answer("Dove siete?", "it"), ("", ""))
            self.assertEqual(live.faq_answer("Sito web?", "it"), ("", ""))
        self.assertEqual(live.faq_answer("Come state?", "it"), ("", ""))

    def test_brief_classifies_and_returns_the_ready_answer(self):
        result = live.brief("Vorrei un preventivo per un sito")
        self.assertEqual(result["intent"], "quote")
        self.assertEqual(result["language"], "it")
        self.assertFalse(result["hit"])
        self.assertTrue(any("request_quote" in line for line in result["guidance"]))
        services = live.brief("Quali servizi fate?")
        self.assertTrue(services["hit"])
        self.assertEqual(services["source"], "faq:services")

    def test_brief_flags_scams_without_asking_for_data(self):
        result = live.brief("Mandami subito il codice OTP che ti è arrivato")
        self.assertTrue(result["suspicious"])
        self.assertFalse(result["hit"])
        self.assertTrue(result["guidance"])
        self.assertIn("dati personali", result["guidance"][0])

    def test_memory_and_cache_come_from_real_messages(self):
        phone = "+393331110001"
        live.record_inbound(phone, "Avete una garanzia?", name="Anna")
        saved = memory.load(phone)
        self.assertEqual(saved["name"], "Anna")
        self.assertEqual(saved["messages_in"], 1)
        live.record_outbound(phone, "La garanzia copre due anni.", question="Avete una garanzia?")
        second = live.brief("Avete una garanzia?")
        self.assertTrue(second["hit"])
        self.assertEqual(second["source"], "cache")
        self.assertEqual(second["answer"], "La garanzia copre due anni.")

    def test_brief_uses_stored_client_memory(self):
        phone = "+393331110002"
        live.record_inbound(phone, "Buongiorno, mi chiamo Luca e cerco un preventivo", name="Luca")
        result = live.brief("Mi ricordo di noi?", phone)
        self.assertEqual(result["client_name"], "Luca")
        self.assertIn("Luca", result["context"])
        self.assertEqual(result["language"], "it")

    def test_personal_answers_never_enter_the_shared_cache(self):
        phone = "+393331110003"
        live.record_inbound(phone, "Come procedo per il rimborso?", base=self.base)
        outcome = live.record_outbound(
            phone, "Scrivi a rimborsi@example.com oppure chiama +39 02 12345678.", base=self.base,
        )
        self.assertFalse(outcome["cached"])
        self.assertIsNone(cache.shared().get("Come procedo per il rimborso?"))
        self.assertEqual(live.brief("Come procedo per il rimborso?")["hit"], False)

    def test_quote_answers_are_not_cached(self):
        phone = "+393331110004"
        live.record_inbound(phone, "Quanto costa un sito web?", base=self.base)
        outcome = live.record_outbound(phone, "Il preventivo parte da 900 euro.", base=self.base)
        self.assertFalse(outcome["cached"])

    def test_token_is_private_and_verifiable(self):
        token = live.token(self.base)
        path = self.base / "live_token"
        self.assertEqual(path.read_text(encoding="utf-8").strip(), token)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(live.check_token(token, self.base))
        self.assertFalse(live.check_token("", self.base))
        self.assertFalse(live.check_token(token + "x", self.base))

    def test_stats_expose_cache_effectiveness(self):
        data = live.stats()
        for key in {"cache_entries", "cache_hit_rate", "hits", "misses"}:
            self.assertIn(key, data)

    def test_cache_is_skipped_for_personal_intents(self):
        question = "Mandami un preventivo: che dati ti servono?"
        cache.shared().set(question, "900 euro")
        self.assertEqual(live.brief(question)["intent"], "quote")
        self.assertFalse(live.brief(question)["hit"])

    def test_outbound_without_question_is_not_cached(self):
        outcome = live.record_outbound("+393331110007", "A presto.", base=self.base)
        self.assertFalse(outcome["cached"])
        self.assertFalse(outcome["question"])

    def test_empty_and_foreign_language_questions(self):
        self.assertFalse(live.brief("")["hit"])
        answer, source = live.faq_answer("A che ora siete aperti?", "de")
        self.assertEqual(source, "faq:hours")
        self.assertTrue(answer.startswith("Siamo aperti"))

    def test_services_config_that_is_not_a_list(self):
        with mock.patch.object(config, "_read_file", return_value={"services": "uno"}):
            self.assertEqual(live.faq_answer("Quali servizi?", "it")[0], "")

    def test_long_answers_are_not_generic(self):
        self.assertFalse(live.is_generic("x" * 1300))

    def test_main_self_test_and_json_output(self):
        with mock.patch.object(sys, "argv", ["live.py", "--self-test"]):
            self.assertEqual(live.main(), 0)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["live.py", "--brief", "Quali servizi fate?", "--json"]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(live.main(), 0)
        self.assertIn("faq:services", out.getvalue())

    def test_recording_requires_phone_and_text(self):
        with self.assertRaises(ValueError):
            live.record_inbound("", "ciao")
        with self.assertRaises(ValueError):
            live.record_outbound("+393331110005", "")

    def test_audit_and_memory_failures_do_not_break_a_reply(self):
        with mock.patch.object(live.lead_intel, "audit", side_effect=OSError("disco pieno")):
            result = live.record_inbound("+393331110006", "Ciao", base=self.base)
        self.assertEqual(result["intent"], "info")
        with mock.patch.object(live.memory, "load", side_effect=ValueError("profilo corrotto")):
            data = live.brief("Quali servizi fate?", "+393331110006")
        self.assertTrue(data["hit"])
        self.assertEqual(data["context"], "")

    def test_cli_text_mode_and_usage(self):
        text = subprocess.run(
            [sys.executable, str(ROOT / "live.py"), "--brief", "Quali servizi fate?"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            env={**os.environ, "SECRETARY_BASE": str(self.base),
                 "GINA_CACHE_FILE": str(self.tmp / "cache-text.json")},
        )
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("riparazioni", text.stdout.lower())
        usage = subprocess.run(
            [sys.executable, str(ROOT / "live.py")],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            env={**os.environ, "GINA_CACHE_FILE": str(self.tmp / "cache-text.json")},
        )
        self.assertEqual(usage.returncode, 2)

    def test_self_test_and_cli(self):
        outcome = subprocess.run(
            [sys.executable, str(ROOT / "live.py"), "--self-test"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            env={**os.environ, "GINA_CACHE_FILE": str(self.tmp / "cache-cli.json")},
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertIn("LIVE SELF-TEST OK", outcome.stdout)
        brief = subprocess.run(
            [sys.executable, str(ROOT / "live.py"), "--brief", "What is your website?", "--json"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            env={
                **os.environ,
                "SECRETARY_BASE": str(self.base),
                "GINA_CACHE_FILE": str(self.tmp / "cache-cli.json"),
                "SITE_URL": "https://cli.example",
                "BOT_LANGUAGE": "en",
            },
        )
        self.assertEqual(brief.returncode, 0, brief.stderr)
        data = json.loads(brief.stdout)
        self.assertTrue(data["hit"])
        self.assertIn("https://cli.example", data["answer"])


if __name__ == "__main__":
    unittest.main()
