"""Traduzione completa della dashboard: ogni stringa statica ha l'inglese."""
import html
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Nomi propri, comandi e identificatori che restano identici in inglese.
IDENTICAL_ALLOWED = {
    "AIza… AIza… AIza…", "Bind", "Call", "Digest", "EN", "Health", "IT",
    "MCP server", "Monitor", "Msg in/out", "PID", "Score", "Token", "Uptime",
    "Watchdog", "WhatsApp", "anti-jailbreak", "gemini-3.5-flash", "gog",
    "gog-secretary auth add {{GOOGLE_ACCOUNT}} --services gmail,calendar --gmail-scope read-send",
    "gog-secretary auth credentials set ~/Downloads/client_secret_*.json",
    "groq2…groq7", "gsk_… gsk_…", "http://127.0.0.1:…/oauth2/callback?code=…",
    "bash ~/.openclaw/secretary/google-bridge/installa-bridge.sh",
    "~/.openclaw/secretary/dashboard_token", "⤓ CSV", "No", "Google AI Studio",
    "Groq Console", "Cloudflare", "OAuth locale", "Desktop", "Dashboard uptime",
    "Endpoint", "Gateway",
}


def normalize(text):
    return " ".join(html.unescape(str(text)).split())


def static_strings():
    source = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    body = re.sub(r"<script.*?</script>", "", source, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    found = set()
    for match in re.finditer(r">([^<>]+)<", body):
        text = normalize(match.group(1))
        if text and len(text) > 1 and re.search(r"[A-Za-zÀ-ÿ]", text) and not text.startswith("{{"):
            found.add(text)
    for attr in ("placeholder", "title", "aria-label"):
        for match in re.finditer(attr + r'="([^"]+)"', body):
            found.add(normalize(match.group(1)))
    return found


class DashboardI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.translations = json.loads((ROOT / "i18n.json").read_text(encoding="utf-8"))
        cls.normalized = {normalize(key): value for key, value in cls.translations.items()}

    def test_translation_file_is_valid_and_has_no_duplicate_keys(self):
        pairs = json.loads(
            (ROOT / "i18n.json").read_text(encoding="utf-8"),
            object_pairs_hook=lambda items: items,
        )
        keys = [key for key, _value in pairs]
        self.assertEqual(len(keys), len(set(keys)), "chiavi duplicate in i18n.json")
        for key, value in self.translations.items():
            self.assertTrue(key.strip())
            self.assertTrue(isinstance(value, str) and value.strip(), key)

    def test_every_static_string_has_an_english_translation(self):
        missing = sorted(text for text in static_strings() if text not in self.normalized)
        self.assertEqual(missing, [], "stringhe senza traduzione: " + "; ".join(missing[:10]))

    def test_translated_values_are_real_translations(self):
        same = sorted(
            key for key, value in self.translations.items()
            if normalize(key) == normalize(value) and normalize(key) not in IDENTICAL_ALLOWED
        )
        self.assertEqual(same, [], "valori non tradotti: " + "; ".join(same[:10]))

    def test_template_placeholder_is_served_filled(self):
        source = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("{{I18N_TEXT}}", source)
        self.assertIn("TEXT_EN[key]", source)
        dashboard = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('page.replace("{{I18N_TEXT}}"', dashboard)
        self.assertIn('"i18n.json"', dashboard)

    def test_language_switch_restores_italian(self):
        source = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("I18N_ORIGINALS", source)
        self.assertIn("i18nTree(document.body,state.lang==='en')", source)
        self.assertIn("new MutationObserver", source)


if __name__ == "__main__":
    unittest.main()
