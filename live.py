#!/usr/bin/env python3
"""Pipeline live della segreteria: intento, memoria e cache nella stessa risposta.

Tre punti di ingresso, tutti usati dal percorso reale:

- ``brief()``  chiamato dal tool MCP ``client_brief`` a ogni messaggio: classifica
  (``lead_intel``), legge la memoria del cliente (``memory``) e restituisce una
  risposta pronta dalla cache o dalle FAQ deterministiche dell'attività.
- ``record_inbound()`` chiamato dal plugin ``secretary-guard`` per ogni messaggio
  in ingresso: classifica, scrive l'audit e aggiorna la memoria.
- ``record_outbound()`` chiamato per ogni risposta inviata: salva la risposta in
  cache solo se è generica (nessun dato personale) e aggiorna la memoria.

La cache è condivisa tra clienti: per questo una risposta viene memorizzata solo
se non contiene email, telefoni, IBAN, link personali o dati di preventivi.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path

import cache as reply_cache
import config
import lead_intel
import memory

MAX_QUESTION = 500
MAX_ANSWER = 1200
CACHE_TTL = 6 * 60 * 60

FAQ_PATTERNS: dict[str, str] = {
    "prices": r"\b(prezz\w*|quanto costa|quanto viene|costo|tariff\w*|pricing|price|cost)\b",
    "hours": r"\b(orari\w*|a che ora|quando (siete )?apert\w*|siete apert\w*|chius\w*|opening|hours|open)\b",
    "location": r"\b(dove (siete|si trova|vi trovate)|indirizzo|zona|location|where are you|address)\b",
    "services": r"\b(servizi\w*|cosa fate|di cosa vi occupate|service\w*|what do you do)\b",
    "site": r"\b(il vostro sito|vostro sito|sito web|sito internet|link del sito|website|your website|your site)\b",
}

# Risposte che non devono mai entrare in cache condivisa.
PERSONAL_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{7,}\d|\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b",
    re.IGNORECASE,
)
PERSONAL_INTENTS = {"quote", "appointment", "suspicious"}


def _token_path(base: Path | None = None) -> Path:
    return Path(base or config.BASE) / "live_token"


def token(base: Path | None = None) -> str:
    """Token dedicato server-to-server per il plugin, separato da quello dashboard."""
    path = _token_path(base)
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return value


def check_token(candidate: str, base: Path | None = None) -> bool:
    expected = token(base)
    return bool(candidate) and secrets.compare_digest(str(candidate), expected)


def _cfg_text(key: str) -> str:
    return str(config.get(key, "") or "").strip()


def _services(language: str) -> str:
    raw = config.get("services") or []
    items = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
    return ", ".join(items[:4])


def faq_answer(question: str, language: str = "it") -> tuple[str, str]:
    """Risposta deterministica per domande fattuali, senza inventare nulla."""
    text = str(question or "")[:MAX_QUESTION].lower()
    if not text.strip():
        return "", ""
    lang = "en" if str(language).lower().startswith("en") else "it"
    matched = next((name for name, pattern in FAQ_PATTERNS.items() if re.search(pattern, text)), "")
    if not matched:
        return "", ""
    business = _cfg_text("business_name")
    if matched == "hours":
        hours = _cfg_text("hours")
        if not hours:
            return "", ""
        return (f"Our opening hours are {hours}." if lang == "en" else f"Siamo aperti {hours}."), "faq:hours"
    if matched == "location":
        city = _cfg_text("city")
        if not city:
            return "", ""
        return (f"You can find us in {city}." if lang == "en" else f"Ci trovi a {city}."), "faq:location"
    if matched == "services":
        services = _services(lang)
        if not services:
            return "", ""
        return (f"We handle: {services}." if lang == "en" else f"Ci occupiamo di: {services}."), "faq:services"
    if matched == "site":
        site = _cfg_text("site_url")
        if not site:
            return "", ""
        return (f"Here is our website: {site}" if lang == "en" else f"Il nostro sito è {site}"), "faq:site"
    note = _cfg_text("pricing_note")
    if not note:
        return "", ""
    prefix = f"{business}: " if business and lang == "en" else ""
    return prefix + note, "faq:prices"


def is_generic(answer: str, question: str = "") -> bool:
    """True solo se la risposta può essere riusata con un altro cliente."""
    answer = str(answer or "").strip()
    if not answer or len(answer) > MAX_ANSWER:
        return False
    if PERSONAL_RE.search(answer) or PERSONAL_RE.search(str(question or "")):
        return False
    return True


def _cache() -> reply_cache.TTLCache:
    return reply_cache.shared()


def _last_question(phone: str, base: Path | None = None) -> str:
    try:
        for entry in reversed(memory.load(phone, base).get("transcript") or []):
            if entry.get("role") == "user":
                return str(entry.get("text") or "")
    except Exception:
        return ""
    return ""


def guidance(result: dict) -> list[str]:
    """Istruzioni brevi per il modello, coerenti con l'intento classificato."""
    intent = str(result.get("intent") or "")
    if result.get("suspicious"):
        return ["Non chiedere né confermare dati personali; avvisa il titolare."]
    if intent == "quote":
        return ["Chiedi i dettagli e l'email, poi usa request_quote con i prezzi confermati dal titolare."]
    if intent == "appointment":
        return ["Proponi due orari e usa request_calendar quando il cliente conferma."]
    if intent == "support":
        return ["Raccogli il problema in una riga e passa la richiesta al titolare."]
    return []


def brief(question: str, phone: str = "", name: str = "", base: Path | None = None) -> dict:
    """Contesto completo per la risposta live: intento, lingua, memoria e cache."""
    question = str(question or "")[:MAX_QUESTION]
    result = lead_intel.classify(question)
    language = str(result.get("language") or "it")
    answer, source = faq_answer(question, language)
    if not answer and is_generic("x", question) and str(result.get("intent")) not in PERSONAL_INTENTS:
        answer = _cache().get(question) or ""
        source = "cache" if answer else ""
    context = ""
    known_name = name
    if phone:
        try:
            profile = memory.load(phone, base)
            known_name = known_name or str(profile.get("name") or "")
            context = memory.context(phone, base=base)
        except Exception:
            context = ""
    return {
        "hit": bool(answer),
        "answer": answer,
        "source": source,
        "intent": result.get("intent"),
        "score": result.get("score"),
        "language": language,
        "suspicious": bool(result.get("suspicious")),
        "signals": result.get("signals") or [],
        "client_name": known_name,
        "context": context,
        "guidance": guidance(result),
    }


def record_inbound(phone: str, text: str, name: str = "", base: Path | None = None) -> dict:
    """Classifica e memorizza un messaggio reale in ingresso."""
    phone = str(phone or "").strip()
    text = str(text or "")[:MAX_QUESTION]
    if not phone or not text:
        raise ValueError("phone e text sono obbligatori")
    result = lead_intel.classify(text)
    try:
        lead_intel.audit(phone, text, result, base=base)
    except Exception:
        pass
    memory.record(
        phone,
        "user",
        text,
        intent=str(result.get("intent") or ""),
        score=int(result.get("score") or 0),
        name=name or None,
        base=base,
    )
    return {"intent": result.get("intent"), "score": result.get("score"),
            "language": result.get("language"), "suspicious": bool(result.get("suspicious"))}


def record_outbound(phone: str, text: str, question: str = "", base: Path | None = None) -> dict:
    """Memorizza la risposta inviata e la mette in cache solo se è generica."""
    phone = str(phone or "").strip()
    text = str(text or "")[:MAX_ANSWER]
    if not phone or not text:
        raise ValueError("phone e text sono obbligatori")
    memory.record(phone, "assistant", text, base=base)
    question = str(question or "")[:MAX_QUESTION] or _last_question(phone, base)
    result = lead_intel.classify(question) if question else {"intent": "", "language": "it"}
    cached = False
    if question and is_generic(text, question) and str(result.get("intent")) not in PERSONAL_INTENTS:
        _cache().set(question, text, ttl=CACHE_TTL)
        cached = True
    return {"cached": cached, "question": bool(question), "intent": result.get("intent")}


def stats() -> dict:
    data = _cache().stats()
    return {
        "cache_entries": int(data.get("entries") or 0),
        "cache_hit_rate": data.get("hit_rate"),
        "hits": int(data.get("hits") or 0),
        "misses": int(data.get("misses") or 0),
    }


def self_test() -> int:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="live-selftest-"))
    os.environ["GINA_CACHE_FILE"] = str(root / "cache.json")
    reply_cache.reset_shared()
    base = root / "base"
    base.mkdir()
    answer, source = faq_answer("A che ora siete aperti?", "it")
    if not answer or source != "faq:hours" or "9:00" not in answer:
        print("LIVE SELF-TEST FAIL: FAQ orari", file=sys.stderr)
        return 1
    answer_en, _ = faq_answer("What are your opening hours?", "en")
    if not answer_en.startswith("Our opening hours"):
        print("LIVE SELF-TEST FAIL: FAQ inglese", file=sys.stderr)
        return 1
    if is_generic("Scrivimi a mario@example.com"):
        print("LIVE SELF-TEST FAIL: email non rilevata", file=sys.stderr)
        return 1
    phone = "+393331110000"
    first = brief("Quali servizi fate?", phone, base=base)
    if not first["hit"] or first["source"] != "faq:services" or first["intent"] not in {"lead", "info"}:
        print(f"LIVE SELF-TEST FAIL: brief {first}", file=sys.stderr)
        return 1
    record_inbound(phone, "Avete un numero per il supporto?", "Mario", base=base)
    record_outbound(phone, "Scrivi a supporto@example.com e ti rispondiamo.", base=base)
    profile = memory.load(phone, base)
    if profile.get("messages_in") != 1 or profile.get("messages_out") != 1:
        print("LIVE SELF-TEST FAIL: memoria non aggiornata", file=sys.stderr)
        return 1
    if _cache().get("Avete un numero per il supporto?"):
        print("LIVE SELF-TEST FAIL: risposta personale in cache", file=sys.stderr)
        return 1
    record_inbound(phone, "Come funziona la garanzia?", base=base)
    record_outbound(phone, "La garanzia copre due anni.", base=base)
    second = brief("Come funziona la garanzia?", base=base)
    if second["source"] != "cache" or "garanzia" not in second["answer"]:
        print(f"LIVE SELF-TEST FAIL: cache live {second}", file=sys.stderr)
        return 1
    if not check_token(token(base), base) or check_token("sbagliato", base):
        print("LIVE SELF-TEST FAIL: token", file=sys.stderr)
        return 1
    print("LIVE SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline live della segreteria")
    parser.add_argument("--brief", metavar="TESTO", help="contesto per un messaggio")
    parser.add_argument("--phone", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.brief:
        parser.print_help()
        return 2
    data = brief(args.brief, args.phone)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data["answer"] or "(nessuna risposta pronta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
