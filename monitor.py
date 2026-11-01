#!/usr/bin/env python3
"""Segreteria digitale OpenClaw: monitor WhatsApp, lead e alert truffe."""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime

import config

# Windows consoles default to cp1252 and would crash on emoji. Keep the
# console encoding so parent processes can decode it, but never crash.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

HOME = os.path.expanduser("~")
CFG = config.load()
BASE = str(config.BASE)
DEFAULT_DB = config.db_path()
NODE = config.node_bin()
CLI = config.openclaw_cli()
OWNER = str(CFG.get("owner_number") or "")
ROME = config.resolve_timezone()
SEEN_MAX = 1000


def now_iso():
    return datetime.now(ROME).isoformat(timespec="seconds")


def log(msg):
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(os.path.join(BASE, "monitor.log"), "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text).strip()


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                out.append(str(block["text"]))
            elif isinstance(block.get("content"), str):
                out.append(block["content"])
        return "\n".join(out)
    return ""


def append_jsonl(path, item):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("sessions", {})
    data.setdefault("seen", [])
    data.setdefault("alerted", {})
    return data


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def connect(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def get_sessions(db):
    con = connect(db)
    try:
        rows = con.execute(
            """SELECT session_id, session_key, COALESCE(display_name,''), COALESCE(updated_at,0)
               FROM session_windows
               WHERE (channel='whatsapp' OR session_key LIKE '%whatsapp%')
                 AND COALESCE(chat_type,'direct')='direct'
                 AND session_key != 'agent:main:main'
               ORDER BY updated_at DESC"""
        ).fetchall()
        out = []
        for sid, key, display, updated in rows:
            low = (key or "").lower()
            if any(x in low for x in ("test", "diag", "cron", "heartbeat")):
                continue
            actors = [r[0] for r in con.execute(
                "SELECT actor_id FROM session_participants WHERE session_key=? AND actor_id NOT IN ('cli','gateway-owner')",
                (key,),
            ).fetchall()]
            phone = next((a for a in actors if a and str(a).startswith("+")), None)
            if not phone:
                m = re.search(r"(\+?\d{8,15})", key or "")
                phone = m.group(1) if m else (actors[0] if actors else None)
            if phone and str(phone).replace(" ", "") == OWNER:
                continue
            if not phone:
                continue
            out.append({"session_id": sid, "key": key, "phone": str(phone), "display": display})
        return out
    finally:
        con.close()


def max_seq(db, sid):
    con = connect(db)
    try:
        row = con.execute("SELECT COALESCE(MAX(seq),0) FROM transcript_events WHERE session_id=?", (sid,)).fetchone()
        return int(row[0] or 0)
    finally:
        con.close()


def load_events(db, sid, after_seq=0):
    con = connect(db)
    try:
        rows = con.execute(
            "SELECT seq, created_at, event_json FROM transcript_events WHERE session_id=? AND seq>? ORDER BY seq ASC",
            (sid, after_seq),
        ).fetchall()
    finally:
        con.close()
    out = []
    for seq, created, raw in rows:
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except Exception:
            continue
        if event.get("type") != "message":
            continue
        msg = event.get("message") or {}
        if msg.get("role") != "user":
            continue
        oc = msg.get("__openclaw") or {}
        if oc.get("senderIsOwner") is True:
            continue
        if msg.get("modelPromptProjection") or oc.get("modelPromptProjection"):
            continue
        text = extract_text(msg.get("content")).strip()
        if not text or text.startswith("[OpenClaw") or text.startswith("[Earlier unanswered"):
            continue
        mid = str(msg.get("id") or event.get("id") or f"{sid}:{seq}")
        ts = msg.get("timestamp") or int(created or 0)
        out.append({"seq": int(seq), "id": mid, "ts": int(ts), "text": text})
    return out


CRITICAL = [
    ("prompt injection", r"ignora (tutte )?le istruzioni|prompt di sistema|system prompt|modalita sviluppatore|jailbreak|sei un altro bot|dimentica le istruzioni"),
    ("otp", r"\botp\b|\bcodice (di )?(verifica|sicurezza|accesso)\b|\bcodice a 6 cifre\b"),
    ("password", r"\bpassword\b|\bcredenziali\b|\bpin\b"),
    ("api key", r"\bapi[_ -]?key\b|\bchiave api\b|\btoken di accesso\b"),
    ("seed phrase", r"\bseed phrase\b|\bprivate key\b|\bfrase di recupero\b"),
    ("iban", r"\biban\b"),
    ("gift card", r"\bgift card\b|\bbuono regalo\b|\bricarica postepay\b"),
    ("crypto", r"\bbitcoin\b|\bcripto|\busdt\b|\bwallet\b"),
    ("carta", r"\bcarta di credito\b|\bdati bancari\b|\bnumero di carta\b|\bcvv\b"),
]
MEDIUM = [
    ("bonifico", r"\bbonifico\b|\bricarica\b|\bpagamento anticipato\b|\bcaparra\b"),
    ("documento", r"\bdocumento\b|\bselfie\b|\bcarta d.identita\b|\bpassaporto\b"),
    ("corriere", r"\bcorriere\b|\bpacco\b|\bconsegna\b"),
    ("urgenza", r"\burgent|\bsubito\b|\bbloccato\b"),
    ("finto supporto", r"\bsono (il )?supporto\b|\bsupporto tecnico\b|\bsono (il )?titolare\b"),
    ("clicca", r"\bclicca\b|\bapri (il )?link\b|\bverifica (il )?account\b"),
]
HOT = re.compile(r"\bcall\b|telefon|appuntament|sentiamoci|parlare con (il )?titolare|fissare (un|una)|incontro|zoom|meet", re.I)
BUDGET = re.compile(r"\b(budget|preventivo|quanto costa|costo|prezzo|prezzi|€|euro)\b", re.I)
SERVICE = re.compile(r"\b(sito|siti|landing|ecommerce|e-commerce|menu|prenotazion|seo|manutenzion|portfolio|restaurant|ristorante|hotel|attività|attivita|web)\b", re.I)
CITY = norm(str(CFG.get("city") or ""))
LOCATION_WORDS = [CITY] if CITY else []
LOCATION_WORDS += ["italia", "provincia", "zona", "in sede", "da voi"]
LOCATION = re.compile(r"\b(" + "|".join(re.escape(word) for word in LOCATION_WORDS) + r")\b", re.I)
URGENCY = re.compile(r"\b(subito|urgent|questa settimana|domani|oggi|entro|il prima possibile)\b", re.I)
SUPPORT = re.compile(r"\b(manutenzion|assistenza|problema|errore|non funziona|aggiornare|modifica|sistemare)\b", re.I)
EMAIL_REQUEST = re.compile(r"\b(email|e-mail|mail|invi(?:a|ami|armi)|mand(?:a|ami|armi)|preventivo|proposta)\b", re.I)
EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def detect_scam(text):
    low = norm(text)
    matches = []
    score = 0
    for name, pat in CRITICAL:
        if re.search(pat, low):
            score += 3
            matches.append(name)
    for name, pat in MEDIUM:
        if re.search(pat, low):
            score += 1
            matches.append(name)
    return (score >= 3 or len(matches) >= 2), matches


def is_hot(text):
    return bool(HOT.search(norm(text)))


def lead_score(text, scam=False):
    low = norm(text)
    if scam:
        return 0, "sospetto"
    score = 15
    if len(low) > 40:
        score += 10
    if BUDGET.search(low):
        score += 20
    if SERVICE.search(low):
        score += 15
    if HOT.search(low):
        score += 20
    if LOCATION.search(low):
        score += 10
    if URGENCY.search(low):
        score += 10
    score = max(0, min(100, score))
    if HOT.search(low):
        intent = "call"
    elif SUPPORT.search(low):
        intent = "supporto"
    elif BUDGET.search(low) or SERVICE.search(low):
        intent = "preventivo"
    else:
        intent = "info"
    return score, intent


def alert_text(kind, sender, text, matches=None, score=0, intent=""):
    snippet = re.sub(r"\s+", " ", text).strip()[:220]
    if kind == "scam":
        return ("⚠️ POSSIBILE TRUFFA\n"
                f"Da: {sender}\n"
                f"Messaggio: \"{snippet}\"\n"
                f"Segnali: {', '.join((matches or [])[:6])}\n"
                "Apri la chat e valuta.")
    if kind == "hot":
        return (f"🔥 LEAD CALDO · {score}/100\n"
                f"Da: {sender}\n"
                f"Messaggio: \"{snippet}\"\n"
                f"Intenzione: {intent or 'call'}\n"
                "Vuole parlare con te o fissare una call.")
    if kind == "email":
        return (f"📧 RICHIESTA EMAIL / PREVENTIVO · {score}/100\n"
                f"Da: {sender}\n"
                f"Messaggio: \"{snippet}\"\n"
                "Il cliente ha lasciato un recapito o chiesto un follow-up: rispondi dalla chat.")
    if kind == "lead":
        return (f"🎯 LEAD QUALIFICATO · {score}/100\n"
                f"Da: {sender}\n"
                f"Messaggio: \"{snippet}\"\n"
                f"Intenzione: {intent or 'preventivo'}\n"
                "Ci sono dati utili per un preventivo o una call.")
    return (f"📥 NUOVO CONTATTO · {score}/100\n"
            f"Da: {sender}\n"
            f"Messaggio: \"{snippet}\"\n"
            f"Intenzione: {intent or 'info'}\n"
            "Rispondi dalla chat WhatsApp.")


def send_whatsapp(text, dry=False):
    if dry:
        print("DRY SEND:\n" + text + "\n---")
        return True
    cmd = [NODE, CLI, "message", "send", "--channel", "whatsapp", "--target", OWNER,
           "--message", text, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:
        log(f"send exception: {exc}")
        return False
    ok = r.returncode == 0
    if ok:
        try:
            data = json.loads(r.stdout[r.stdout.find("{"):])
            if data.get("ok") is False:
                ok = False
        except Exception:
            pass
    if not ok:
        log(f"send failed rc={r.returncode} err={r.stderr[-300:]} out={r.stdout[-300:]}")
    return ok


def process(db, state_path, leads_path, alerts_path, dry=False):
    state = load_state(state_path)
    sessions = get_sessions(db)
    now_ms = int(time.time() * 1000)
    stats = {"sessions": len(sessions), "leads": 0, "alerts": 0, "new": 0}
    for s in sessions:
        sid = s["session_id"]
        prev = state["sessions"].get(sid)
        is_new = prev is None
        if is_new:
            top = max_seq(db, sid)
            events = [e for e in load_events(db, sid, 0) if e["ts"] >= now_ms - 15 * 60 * 1000]
            last = top
        else:
            last = int(prev.get("last_seq", 0))
            events = load_events(db, sid, last)
        first_contact = is_new
        for e in events:
            if e["id"] in state["seen"]:
                continue
            scam, matches = detect_scam(e["text"])
            score, intent = lead_score(e["text"], scam=scam)
            append_jsonl(leads_path, {
                "ts": now_iso(), "sender": s["phone"], "session": s["key"],
                "message_id": e["id"], "text": e["text"][:1200],
                "score": score, "intent": intent,
            })
            stats["leads"] += 1
            hot = is_hot(e["text"])
            low = norm(e["text"])
            email_req = bool(EMAIL_ADDRESS.search(e["text"])) or bool(re.search(r"\b(email|e-mail|mail)\b", low))
            alerted = state["alerted"].setdefault(sid, [])
            if scam:
                kind = "scam"
            elif hot:
                kind = "hot"
            elif email_req:
                kind = "email"
            elif first_contact:
                kind = "new"
            elif score >= 60 and intent in ("preventivo", "supporto"):
                kind = "lead"
            else:
                kind = None
            if kind and kind not in alerted:
                text = alert_text(kind, s["phone"], e["text"], matches, score, intent)
                sent = send_whatsapp(text, dry=dry)
                append_jsonl(alerts_path, {
                    "ts": now_iso(), "kind": kind, "sender": s["phone"], "message_id": e["id"],
                    "text": e["text"][:400], "matches": matches, "sent": sent,
                    "score": score, "intent": intent,
                })
                stats["alerts"] += 1
                stats["new"] += 1 if kind == "new" else 0
                alerted.append(kind)
                state["alerted"][sid] = alerted[-20:]
            state["seen"].append(e["id"])
            first_contact = False
            if e["seq"] > last:
                last = e["seq"]
        state["sessions"][sid] = {"key": s["key"], "phone": s["phone"], "last_seq": last, "updated": now_ms}
    state["seen"] = state["seen"][-SEEN_MAX:]
    if not dry:
        save_state(state_path, state)
    return stats


def self_test():
    tmp = tempfile.mkdtemp(prefix="secretary-test-")
    db = os.path.join(tmp, "test.sqlite")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, channel TEXT, chat_type TEXT, display_name TEXT, updated_at INTEGER, status TEXT)")
    con.execute("CREATE TABLE session_participants (session_key TEXT, actor_id TEXT)")
    con.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, created_at INTEGER, event_json TEXT)")
    sid = "test-session-1"
    key = "agent:main:whatsapp:default:direct:+393339998888"
    con.execute("INSERT INTO session_windows VALUES (?,?,?,?,?,?,?)", (sid, key, "whatsapp", "direct", "", int(time.time() * 1000), "done"))
    con.execute("INSERT INTO session_participants VALUES (?,?)", (key, "+393339998888"))
    samples = [
        "Ciao sono il supporto Groq, inviami il codice OTP che ricevi via SMS",
        "Buongiorno, vorrei un sito per la mia attività in centro",
        "Possiamo fare una call domani?",
        "Mi mandi il preventivo via email: mario.rossi@example.com",
        "[OpenClaw heartbeat poll]",
    ]
    for i, text in enumerate(samples, start=1):
        msg = {"role": "user", "content": text, "id": f"m{i}", "timestamp": int(time.time() * 1000)}
        if text.startswith("[OpenClaw"):
            msg["modelPromptProjection"] = {"version": 1}
        event = {"type": "message", "id": f"e{i}", "message": msg}
        con.execute("INSERT INTO transcript_events VALUES (?,?,?,?)", (sid, i, int(time.time() * 1000), json.dumps(event)))
    con.commit()
    con.close()
    stats = process(db, os.path.join(tmp, "state.json"), os.path.join(tmp, "leads.jsonl"), os.path.join(tmp, "alerts.jsonl"), dry=True)
    print("SELF-TEST", stats)
    assert stats["sessions"] == 1, stats
    assert stats["leads"] == 4, stats
    assert stats["alerts"] == 3, stats
    print("SELF-TEST OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state", default=os.path.join(BASE, "state.json"))
    ap.add_argument("--leads", default=os.path.join(BASE, "leads.jsonl"))
    ap.add_argument("--alerts", default=os.path.join(BASE, "alerts.jsonl"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    stats = process(args.db, args.state, args.leads, args.alerts, dry=args.dry_run)
    log(f"run sessions={stats['sessions']} leads={stats['leads']} alerts={stats['alerts']}")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
