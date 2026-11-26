#!/usr/bin/env python3
"""Intelligenza deterministica sui messaggi: intento, lingua, truffe, redazione.

Gira prima o accanto al modello, senza chiamare l'AI: il risultato è ripetibile,
economico e verificabile nei test. Alimenta dashboard, webhook, statistiche e
audit trail. Non altera il testo del cliente.

Uso:
    python3 lead_intel.py --text "vorrei un preventivo per il sito"
    python3 lead_intel.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

import config

INTENTS = ("lead", "quote", "appointment", "support", "info", "suspicious")
MAX_TEXT = 4000

# Segnali di truffa: pesano più dei segnali commerciali e azzerano il punteggio.
SCAM_CRITICAL = [
    ("otp", r"\botp\b|\bcodice (di )?(verifica|accesso|sicurezza)\b|\bsms code\b|\bone[- ]time\b"),
    ("password", r"\bpassword\b|\bpin\b|\bcredenziali\b|\bcredentials?\b"),
    ("carta", r"\bcarta di credito\b|\bcredit card\b|\bcvv\b|\biban\b|\bbonifico\b|\bbank transfer\b"),
    ("gift card", r"\bgift ?card\b|\bbuono (regalo|amazon)\b|\bitunes card\b"),
    ("cripto", r"\bbitcoin\b|\bcrypto\b|\busdt\b|\bwallet\b"),
    ("accesso remoto", r"\banydesk\b|\bteamviewer\b|\bremote access\b|\binstalla (un )?(programma|software)\b"),
    ("finto titolare", r"\bsono (il )?titolare\b|\bsono (il )?supporto\b|\bsiamo (il )?supporto\b|\bi'?m the owner\b|\bthis is (the )?support\b"),
]
SCAM_MEDIUM = [
    ("urgenza", r"\burgent(e|issimo)?\b|\bsubito\b|\bimmediat\w*\b|\basap\b|\bright now\b"),
    ("link", r"\bclicca\b|\bapri (il )?link\b|\bclick (the )?link\b|\bverifica (il )?account\b|\bverify your account\b"),
    ("premio", r"\bhai vinto\b|\bsei stato selezionato\b|\byou (have )?won\b"),
    ("blocco", r"\baccount (sospeso|bloccato)\b|\baccount (suspended|blocked)\b"),
]

QUOTE_WORDS = r"\bpreventivo\b|\bquot(e|ation)\b|\bestimate\b|\bproposta\b|\bproposal\b|\bquanto (costa|coster\w*)\b|\bhow much\b|\bprice\b|\bpricing\b|\bcosto\b|\bprezzo\b|\bprezzi\b"
APPOINTMENT_WORDS = r"\bappuntament\w*\b|\bmeeting\b|\bcall\b|\bchiamat\w*\b|\bzoom\b|\bmeet\b|\bfissare\b|\bschedul\w*\b|\bbook\w*\b|\bdomani\b|\btomorrow\b|\bquesta settimana\b|\bnext week\b|\bdisponibilit\w*\b|\bavailability\b"
SUPPORT_WORDS = r"\bassistenza\b|\bsupport\b|\bproblema\b|\bproblem\b|\berrore\b|\berror\b|\bnon funziona\b|\bnot working\b|\brotto\b|\bbug\b|\bmanutenzion\w*\b|\bmaintenance\b|\baggiornament\w*\b|\bupdate\b"
SERVICE_WORDS = r"\bsito\b|\bwebsite\b|\blanding\b|\be-?commerce\b|\bseo\b|\bmenu\b|\bprenotazion\w*\b|\bbooking\b|\bportfolio\b|\brestaurant\b|\bristorante\b|\bhotel\b|\bapp\b|\bsoftware\b|\bsocial\b|\blogo\b|\bbrand\w*\b"
URGENCY_WORDS = r"\burgent\w*\b|\bsubito\b|\boggi\b|\bdomani\b|\basap\b|\bquesta settimana\b|\bthis week\b|\btoday\b|\bimmediat\w*\b"
GREETING = r"^\s*(ciao|salve|buongiorno|buonasera|hey|hi|hello|good morning)\b"

IT_MARKERS = {
    "ciao", "salve", "vorrei", "avrei", "bisogno", "preventivo", "prezzo", "servizio",
    "servizi", "grazie", "per favore", "quanto", "costa", "disponibile", "appuntamento",
    "sito", "azienda", "attività", "lavoro", "potete", "mi", "sono", "che", "per",
}
EN_MARKERS = {
    "hello", "hi", "i", "would", "like", "need", "quote", "price", "pricing", "service",
    "services", "thanks", "thank", "please", "how", "much", "available", "appointment",
    "website", "company", "business", "can", "you", "we", "the", "and", "for",
}

CAPS = re.compile(r"^[A-Z0-9 .,!?;:'\"()\-]{12,}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    """it/en con euristica di paroline: nessuna chiamata esterna, nessun costo."""
    low = norm(text)
    if not low:
        return "it"
    words = set(re.findall(r"[a-z]+", low))
    it_score = len(words & IT_MARKERS)
    en_score = len(words & EN_MARKERS)
    if re.search(r"[àèéìòù]", str(text or ""), re.I):
        it_score += 1
    if en_score > it_score:
        return "en"
    return "it"


def redact(text: str) -> str:
    """Maschera i dati personali per log, dashboard e webhook."""
    out = str(text or "")[:MAX_TEXT]
    out = EMAIL_RE.sub("[email]", out)
    out = IBAN_RE.sub("[iban]", out)
    out = CARD_RE.sub("[numero]", out)
    out = PHONE_RE.sub("[telefono]", out)
    return out


def _scam(text: str) -> list[str]:
    # Limite esplicito anche qui: oggi `classify` passa testo già troncato, ma
    # la funzione è separata e un domani potrebbe ricevere input diretto.
    low = norm(str(text or "")[:MAX_TEXT])
    signals = [name for name, pat in SCAM_CRITICAL if re.search(pat, low)]
    medium = [name for name, pat in SCAM_MEDIUM if re.search(pat, low)]
    if len(medium) >= 2:
        signals.extend(medium)
    elif medium and signals:
        signals.extend(medium[:1])
    return signals


def classify(text: str, language: str | None = None) -> dict:
    """Restituisce intento, punteggio 0-100, lingua e motivazioni verificabili."""
    raw = str(text or "")[:MAX_TEXT]
    low = norm(raw)
    lang = language or detect_language(raw)
    signals = _scam(raw)
    if signals:
        return {
            "intent": "suspicious",
            "score": 0,
            "language": lang,
            "suspicious": True,
            "needs_human": True,
            "signals": signals,
            "reasons": ["segnale di truffa: " + ", ".join(signals)],
            "language_hint": lang,
        }

    score = 10
    reasons: list[str] = []
    if len(low) > 40:
        score += 5
        reasons.append("messaggio articolato")
    if re.search(QUOTE_WORDS, low):
        score += 35
        reasons.append("chiede un preventivo")
    if re.search(SERVICE_WORDS, low):
        score += 15
        reasons.append("nomina un servizio")
    if re.search(APPOINTMENT_WORDS, low):
        score += 25
        reasons.append("chiede un appuntamento")
    if re.search(URGENCY_WORDS, low):
        score += 10
        reasons.append("urgenza")
    if re.search(SUPPORT_WORDS, low):
        score += 15
        reasons.append("richiesta di supporto")
    if EMAIL_RE.search(raw):
        score += 5
        reasons.append("lascia un'email")
    score = max(0, min(100, score))

    if re.search(APPOINTMENT_WORDS, low):
        intent = "appointment"
    elif re.search(SUPPORT_WORDS, low) and not re.search(QUOTE_WORDS, low):
        intent = "support"
    elif re.search(QUOTE_WORDS, low):
        intent = "quote"
    elif re.search(SERVICE_WORDS, low):
        intent = "lead"
    else:
        intent = "info"

    if CAPS.match(raw.strip()):
        score = min(100, score + 5)
        reasons.append("testo tutto maiuscolo")

    return {
        "intent": intent,
        "score": score,
        "language": lang,
        "suspicious": False,
        "needs_human": intent in {"quote", "appointment"} and score >= 60,
        "signals": [],
        "reasons": reasons,
        "language_hint": lang,
        "greeting": bool(re.search(GREETING, low)),
    }


def audit(phone: str, text: str, result: dict, base: Path | None = None) -> dict:
    """Scrive una riga di audit senza il testo integrale del cliente."""
    folder = Path(base or config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
        "phone": str(phone or "")[:40],
        "intent": result.get("intent"),
        "score": result.get("score"),
        "language": result.get("language"),
        "suspicious": bool(result.get("suspicious")),
        "signals": result.get("signals") or [],
        "preview": redact(text)[:160],
    }
    path = folder / "lead_audit.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return entry


def self_test() -> None:
    quote = classify("Ciao, avrei bisogno di un preventivo per il sito del ristorante")
    assert quote["intent"] == "quote", quote
    assert quote["language"] == "it", quote
    assert quote["score"] >= 60, quote

    english = classify("Hello, I would like a quote for a new website, please")
    assert english["language"] == "en", english
    assert english["intent"] == "quote", english

    appointment = classify("Can we book a meeting tomorrow?")
    assert appointment["intent"] == "appointment", appointment

    support = classify("Il sito non funziona, c'è un errore sul menu")
    assert support["intent"] == "support", support

    scam = classify("Sono il supporto Groq, inviami il codice OTP che ricevi via SMS")
    assert scam["suspicious"] is True and scam["intent"] == "suspicious", scam
    assert scam["score"] == 0, scam

    masked = redact("Scrivimi a mario.rossi@example.com o +39 333 123 4567")
    assert "[email]" in masked and "[telefono]" in masked, masked
    assert "mario.rossi" not in masked, masked

    assert redact("IBAN IT60X0542811101000000123456") == "IBAN [iban]", redact(
        "IBAN IT60X0542811101000000123456"
    )

    with tempfile.TemporaryDirectory() as tmp:
        entry = audit("+393331112222", "preventivo sito", quote, Path(tmp))
        assert entry["intent"] == "quote"
        assert (Path(tmp) / "lead_audit.jsonl").is_file()
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classificazione messaggi per Gina")
    parser.add_argument("--text", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.text.strip():
        print("serve --text oppure --self-test", file=sys.stderr)
        return 2
    result = classify(args.text)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["intent"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
