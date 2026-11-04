#!/usr/bin/env python3
"""Storico reale dei contatti WhatsApp e riepiloghi per il titolare.

Legge in sola lettura il database locale di OpenClaw (sessioni e trascrizioni)
e lo unisce ai lead già classificati dal monitor. Non chiama l'AI e non modifica
nulla: produce un riepilogo deterministico delle richieste passate.

Uso:
    python3 contacts.py --json
    python3 contacts.py --send          # invia il riepilogo al numero del titolare
    python3 contacts.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import config

MAX_TEXT = 400
MAX_EVENTS = 400
ROME = config.resolve_timezone()


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(row[0]) for row in rows}
    except sqlite3.Error:
        return set()


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                out.append(str(block["text"]))
            elif isinstance(block.get("content"), str):
                out.append(block["content"])
        return "\n".join(out)
    return ""


def _phone_from(key: str, actors: list[str]) -> str:
    for actor in actors:
        text = str(actor or "").strip()
        if text.startswith("+") and text[1:].isdigit():
            return text
    match = re.search(r"(\+?\d{8,15})", str(key or ""))
    return match.group(1) if match else ""


def _iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, ROME).isoformat(timespec="seconds")
    except Exception:
        return ""


def _read_leads(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                phone = str(item.get("sender") or "").strip()
                if not phone:
                    continue
                entry = out.setdefault(phone, {"score": 0, "intents": {}, "messages": 0})
                entry["messages"] += 1
                try:
                    score = int(item.get("score") or 0)
                except Exception:
                    score = 0
                entry["score"] = max(entry["score"], score)
                intent = str(item.get("intent") or "info")
                entry["intents"][intent] = int(entry["intents"].get(intent, 0)) + 1
    except FileNotFoundError:
        pass
    return out


def load_contacts(
    db_path: str | None = None,
    leads_path: str | None = None,
    limit: int = 40,
) -> list[dict]:
    """Contatti WhatsApp diretti con storico, escluso il numero del titolare."""
    cfg = config.load()
    owner = str(cfg.get("owner_number") or "").strip()
    db_path = db_path or config.db_path()
    leads_path = leads_path or os.path.join(str(config.BASE), "leads.jsonl")
    limit = max(1, min(int(limit or 40), 200))
    if not os.path.exists(db_path):
        return []

    contacts: list[dict] = []
    try:
        con = _connect(db_path)
    except sqlite3.Error:
        return []
    try:
        tables = _tables(con)
        if "session_windows" not in tables:
            return []
        sessions = con.execute(
            """SELECT session_id, session_key, COALESCE(display_name,''), COALESCE(updated_at,0)
               FROM session_windows
               WHERE (channel='whatsapp' OR session_key LIKE '%whatsapp%')
                 AND COALESCE(chat_type,'direct')='direct'
                 AND session_key != 'agent:main:main'
               ORDER BY updated_at DESC"""
        ).fetchall()
        for session_id, session_key, display_name, updated_at in sessions:
            key = str(session_key or "")
            low = key.lower()
            if any(marker in low for marker in ("test", "diag", "cron", "heartbeat")):
                continue
            actors: list[str] = []
            if "session_participants" in tables:
                actors = [
                    str(row[0])
                    for row in con.execute(
                        "SELECT actor_id FROM session_participants WHERE session_key=? "
                        "AND actor_id NOT IN ('cli','gateway-owner')",
                        (key,),
                    ).fetchall()
                ]
            phone = _phone_from(key, actors)
            if not phone:
                continue
            if owner and phone.replace(" ", "") == owner.replace(" ", ""):
                continue
            inbound = 0
            outbound = 0
            last_messages: list[dict] = []
            first_ts = 0
            last_ts = int(updated_at or 0)
            if "transcript_events" in tables:
                rows = con.execute(
                    "SELECT seq, created_at, event_json FROM transcript_events "
                    "WHERE session_id=? ORDER BY seq DESC LIMIT ?",
                    (str(session_id), MAX_EVENTS),
                ).fetchall()
                for _seq, created, raw in rows:
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(event, dict) or event.get("type") != "message":
                        continue
                    message = event.get("message") or {}
                    role = str(message.get("role") or "")
                    openclaw = message.get("__openclaw") or {}
                    stamp = message.get("timestamp") or created or 0
                    try:
                        stamp = int(stamp)
                    except Exception:
                        stamp = 0
                    if role == "assistant":
                        outbound += 1
                        last_ts = max(last_ts, stamp)
                        continue
                    if role != "user" or openclaw.get("senderIsOwner") is True:
                        continue
                    text = _extract_text(message.get("content")).strip()
                    if not text or text.startswith("[OpenClaw") or text.startswith("[Earlier unanswered"):
                        continue
                    inbound += 1
                    if stamp:
                        first_ts = stamp if not first_ts else min(first_ts, stamp)
                        last_ts = max(last_ts, stamp)
                    if len(last_messages) < 3:
                        last_messages.append({
                            "ts": _iso(stamp),
                            "text": re.sub(r"\s+", " ", text)[:MAX_TEXT],
                        })
            if not inbound and not outbound:
                continue
            contacts.append({
                "phone": phone,
                "name": str(display_name or "").strip(),
                "session": key,
                "messages_in": inbound,
                "messages_out": outbound,
                "last_at": _iso(last_ts),
                "first_at": _iso(first_ts),
                "last_messages": list(reversed(last_messages)),
            })
    except sqlite3.Error:
        return []
    finally:
        con.close()

    leads = _read_leads(leads_path)
    for contact in contacts:
        lead = leads.get(contact["phone"]) or {}
        contact["score"] = int(lead.get("score") or 0)
        contact["intents"] = lead.get("intents") or {}
        contact["lead_messages"] = int(lead.get("messages") or 0)
        top_intent = ""
        if contact["intents"]:
            top_intent = sorted(contact["intents"].items(), key=lambda kv: -kv[1])[0][0]
        contact["intent"] = top_intent or ("info" if contact["messages_in"] else "")

    contacts.sort(key=lambda item: (item.get("last_at") or ""), reverse=True)
    return contacts[:limit]


def _short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m %H:%M")
    except Exception:
        return "-"


def summary_text(
    contacts: list[dict],
    cfg: dict | None = None,
    pending_by_phone: dict[str, int] | None = None,
    max_contacts: int = 8,
) -> str:
    """Riepilogo WhatsApp compatto: chi ha scritto, cosa voleva, cosa è aperto."""
    cfg = cfg if isinstance(cfg, dict) else config.load()
    brand = str(cfg.get("business_name") or "Attività").strip()
    pending_by_phone = pending_by_phone or {}
    today = datetime.now(ROME).strftime("%d/%m/%Y")
    if not contacts:
        return (
            f"📇 Contatti WhatsApp — {today}\n"
            f"{brand}\n\n"
            "Nessun contatto cliente registrato finora. "
            "Lo storico si popola dalle conversazioni WhatsApp del bot."
        )

    total_messages = sum(int(c.get("messages_in") or 0) for c in contacts)
    open_requests = sum(int(v or 0) for v in pending_by_phone.values())
    lines = [
        f"📇 Contatti WhatsApp — {today}",
        f"{len(contacts)} persone · {total_messages} messaggi ricevuti",
    ]
    if open_requests:
        lines.append(f"⏳ {open_requests} richieste in attesa di approvazione")
    lines.append("")

    ranked = sorted(
        contacts,
        key=lambda item: (int(item.get("score") or 0), item.get("last_at") or ""),
        reverse=True,
    )
    for contact in ranked[:max_contacts]:
        name = contact.get("name") or ""
        phone = contact.get("phone") or "sconosciuto"
        label = f"{name} · {phone}" if name else phone
        score = int(contact.get("score") or 0)
        crown = " 🔥" if score >= 70 else ""
        lines.append(f"{label}{crown}")
        meta = [f"{_short_date(contact.get('last_at'))}"]
        if contact.get("messages_in"):
            meta.append(f"{contact['messages_in']} msg")
        if score:
            meta.append(f"score {score}/100")
        if contact.get("intent"):
            meta.append(str(contact["intent"]))
        pending = int(pending_by_phone.get(phone, 0) or 0)
        if pending:
            meta.append(f"{pending} da approvare")
        lines.append("   " + " · ".join(meta))
        last = (contact.get("last_messages") or [{}])[-1]
        snippet = re.sub(r"\s+", " ", str(last.get("text") or "")).strip()
        if snippet:
            lines.append(f'   "{snippet[:160]}"')
        lines.append("")

    if len(contacts) > max_contacts:
        lines.append(f"+ altri {len(contacts) - max_contacts} contatti nella dashboard.")
    text = "\n".join(lines).strip()
    return text[:1750]


def send_whatsapp(text: str, target: str | None = None, dry: bool = False) -> bool:
    """Invia il riepilogo al titolare usando la CLI di OpenClaw."""
    cfg = config.load()
    target = str(target or cfg.get("owner_number") or "").strip()
    if dry:
        print("DRY SEND:\n" + text)
        return True
    if not target:
        print("owner_number non configurato: riepilogo non inviato.", file=sys.stderr)
        return False
    cmd = [
        config.node_bin(), config.openclaw_cli(), "message", "send",
        "--channel", "whatsapp", "--target", target,
        "--message", text, "--json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:
        print(f"invio non riuscito: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print((result.stderr or result.stdout or "")[-300:], file=sys.stderr)
        return False
    try:
        payload = json.loads(result.stdout[result.stdout.find("{"):])
        if payload.get("ok") is False:
            return False
    except Exception:
        pass
    return True


def self_test() -> None:
    tmp = tempfile.mkdtemp(prefix="secretary-contacts-")
    db = os.path.join(tmp, "test.sqlite")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session_windows (session_id TEXT, session_key TEXT, channel TEXT, chat_type TEXT, display_name TEXT, updated_at INTEGER, status TEXT)")
    con.execute("CREATE TABLE session_participants (session_key TEXT, actor_id TEXT)")
    con.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, created_at INTEGER, event_json TEXT)")
    now = int(time.time() * 1000)
    key = "agent:main:whatsapp:default:direct:+393331112222"
    con.execute("INSERT INTO session_windows VALUES (?,?,?,?,?,?,?)", ("s1", key, "whatsapp", "direct", "Mario", now, "done"))
    con.execute("INSERT INTO session_participants VALUES (?,?)", (key, "+393331112222"))
    owner_key = "agent:main:whatsapp:default:direct:+399999999999"
    con.execute("INSERT INTO session_windows VALUES (?,?,?,?,?,?,?)", ("s2", owner_key, "whatsapp", "direct", "Titolare", now, "done"))
    con.execute("INSERT INTO session_participants VALUES (?,?)", (owner_key, "+399999999999"))
    samples = [
        {"role": "user", "content": "Buongiorno, vorrei un sito per il ristorante", "id": "m1"},
        {"role": "assistant", "content": "Ciao Mario, che tipo di sito ti serve?", "id": "m2"},
        {"role": "user", "content": "Mi mandi un preventivo via email", "id": "m3"},
    ]
    for index, message in enumerate(samples, start=1):
        message.update({"timestamp": now + index * 1000})
        event = {"type": "message", "id": f"e{index}", "message": message}
        con.execute("INSERT INTO transcript_events VALUES (?,?,?,?)", ("s1", index, now + index * 1000, json.dumps(event)))
    con.commit()
    con.close()

    previous_owner = os.environ.get("OWNER_NUMBER")
    os.environ["OWNER_NUMBER"] = "+399999999999"
    try:
        contacts = load_contacts(db, os.path.join(tmp, "missing.jsonl"))
    finally:
        if previous_owner is None:
            os.environ.pop("OWNER_NUMBER", None)
        else:
            os.environ["OWNER_NUMBER"] = previous_owner
    assert len(contacts) == 1, contacts
    assert contacts[0]["phone"] == "+393331112222", contacts
    assert contacts[0]["messages_in"] == 2, contacts
    assert contacts[0]["messages_out"] == 1, contacts
    text = summary_text(contacts, {"business_name": "Demo"}, {"+393331112222": 1})
    assert "Mario" in text and "1 richieste" in text, text
    print("SELF-TEST OK")
    print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Storico contatti WhatsApp")
    parser.add_argument("--db", default=config.db_path())
    parser.add_argument("--leads", default=os.path.join(str(config.BASE), "leads.jsonl"))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    contacts = load_contacts(args.db, args.leads, args.limit)
    text = summary_text(contacts)
    if args.send:
        ok = send_whatsapp(text, dry=args.dry_run)
        if args.json:
            print(json.dumps({"ok": ok, "contacts": len(contacts), "text": text}, ensure_ascii=False))
        return 0 if ok else 1
    if args.json:
        print(json.dumps(contacts, ensure_ascii=False, indent=1))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
