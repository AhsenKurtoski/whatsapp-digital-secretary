#!/usr/bin/env python3
"""Conversazioni WhatsApp in sola lettura, con redazione e handoff.

Legge i transcript locali di OpenClaw senza mai scriverli. La dashboard mostra
la conversazione, l'operatore può prendere il controllo (handoff) e Gina smette
di rispondere finché il controllo non viene rilasciato.

Uso:
    python3 conversations.py --list
    python3 conversations.py --transcript +393331112222
    python3 conversations.py --self-test
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
from pathlib import Path

import config
import contacts as contacts_history
import lead_intel
import memory

MAX_MESSAGES = 200
MAX_TEXT = 1500
MAX_SEND = 1000
E164 = re.compile(r"\+?[1-9]\d{7,14}")


def _db() -> str:
    return config.db_path()


def _sessions(limit: int) -> list[dict]:
    db = _db()
    if not Path(db).exists():
        return []
    try:
        con = contacts_history._connect(db)
    except Exception:
        return []
    try:
        tables = contacts_history._tables(con)
        if "session_windows" not in tables:
            return []
        rows = con.execute(
            """SELECT session_id, session_key, COALESCE(display_name,''), COALESCE(updated_at,0)
               FROM session_windows
               WHERE (channel='whatsapp' OR session_key LIKE '%whatsapp%')
                 AND COALESCE(chat_type,'direct')='direct'
                 AND session_key != 'agent:main:main'
               ORDER BY updated_at DESC LIMIT ?""",
            (max(1, min(int(limit or 50), 200)),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    owner = str(config.get("owner_number") or "").replace(" ", "")
    out = []
    for session_id, key, display, updated in rows:
        low = str(key or "").lower()
        if any(marker in low for marker in ("test", "diag", "cron", "heartbeat")):
            continue
        actors: list[str] = []
        try:
            con = contacts_history._connect(db)
            if "session_participants" in contacts_history._tables(con):
                actors = [
                    str(row[0])
                    for row in con.execute(
                        "SELECT actor_id FROM session_participants WHERE session_key=? "
                        "AND actor_id NOT IN ('cli','gateway-owner')",
                        (str(key),),
                    ).fetchall()
                ]
            con.close()
        except Exception:
            actors = []
        phone = contacts_history._phone_from(str(key or ""), actors)
        if not phone or (owner and phone.replace(" ", "") == owner):
            continue
        out.append({
            "session_id": str(session_id),
            "session_key": str(key or ""),
            "phone": phone,
            "name": str(display or "").strip(),
            "updated_at": contacts_history._iso(updated),
        })
    return out


def transcript(phone: str, limit: int = MAX_MESSAGES, redact: bool = True,
               base: Path | None = None) -> dict:
    """Messaggi ordinati dal più vecchio al più recente, con overlay di memoria."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        raise ValueError("numero mancante")
    matches = [s for s in _sessions(200) if digits in s["session_key"] or digits in s["phone"]]
    if not matches:
        # Nessun transcript OpenClaw: si usa la memoria locale, se presente.
        rows = memory.transcript(phone, limit, redact=redact, base=base)
        profile = memory.profile(phone, base)
        return {
            "phone": phone,
            "name": profile.get("name", ""),
            "messages": rows,
            "handoff": profile.get("handoff", {}),
            "facts": profile.get("facts", {}),
            "source": "memory",
        }
    session = matches[0]
    messages: list[dict] = []
    db = _db()
    try:
        con = contacts_history._connect(db)
        try:
            rows = con.execute(
                "SELECT created_at, event_json FROM transcript_events WHERE session_id=? "
                "ORDER BY seq ASC LIMIT ?",
                (session["session_id"], max(1, min(int(limit or MAX_MESSAGES), MAX_MESSAGES))),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        rows = []
    for created, raw in rows:
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
        if role not in {"user", "assistant"}:
            continue
        text = contacts_history._extract_text(message.get("content")).strip()
        if not text or text.startswith("[OpenClaw") or text.startswith("[Earlier unanswered"):
            continue
        stamp = message.get("timestamp") or created or 0
        try:
            stamp = int(stamp)
        except Exception:
            stamp = 0
        messages.append({
            "ts": contacts_history._iso(stamp),
            "role": role,
            "text": lead_intel.redact(text)[:MAX_TEXT] if redact else text[:MAX_TEXT],
        })
    profile = memory.profile(phone, base)
    return {
        "phone": phone,
        "name": session.get("name") or profile.get("name", ""),
        "messages": messages,
        "handoff": profile.get("handoff", {}),
        "facts": profile.get("facts", {}),
        "source": "openclaw",
    }


def list_conversations(limit: int = 50, base: Path | None = None) -> list[dict]:
    """Unisce i transcript reali alla memoria (lingua, handoff, anteprima)."""
    merged: dict[str, dict] = {}
    for item in _sessions(limit):
        merged[item["phone"]] = {
            "phone": item["phone"],
            "name": item.get("name", ""),
            "last_at": item.get("updated_at", ""),
            "messages": 0,
            "language": "",
            "handoff": False,
            "preview": "",
        }
    for row in memory.conversations(limit, base):
        entry = merged.setdefault(row["phone"], {
            "phone": row["phone"], "name": row.get("name", ""), "last_at": row.get("last_seen", ""),
            "messages": 0, "language": "", "handoff": False, "preview": "",
        })
        entry["language"] = row.get("language", "") or entry.get("language", "")
        entry["handoff"] = bool(row.get("handoff"))
        entry["messages"] = int(row.get("messages_in") or 0) + int(row.get("messages_out") or 0)
        entry["preview"] = row.get("preview") or entry.get("preview", "")
        entry["name"] = entry.get("name") or row.get("name", "")
        entry["last_at"] = max(str(entry.get("last_at") or ""), str(row.get("last_seen") or ""))
    rows = list(merged.values())
    rows.sort(key=lambda r: str(r.get("last_at") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 50), 200))]


def set_handoff(phone: str, active: bool, operator: str = "", base: Path | None = None) -> dict:
    profile = memory.set_handoff(phone, active, operator, base)
    event = None
    if active:
        try:
            import webhooks
            event = webhooks.emit("handoff.started", {
                "phone": lead_intel.redact(phone),
                "operator": operator,
            }, base=base)
        except Exception:
            event = None
    return {"handoff": profile.get("handoff", {}), "webhook": event}


def send_message(
    phone: str,
    text: str,
    operator: str = "",
    base: Path | None = None,
    command: str = "",
) -> dict:
    """Invia un messaggio WhatsApp come operatore umano.

    Si può scrivere al cliente solo dopo aver preso il controllo (handoff):
    così Gina non risponde in parallelo e il cliente non riceve due voci.
    Il testo non viene loggato né salvato in chiaro: la memoria registra la
    versione redatta, l'audit solo numero e operatore.
    """
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not E164.fullmatch("+" + digits) and not E164.fullmatch(digits):
        raise ValueError("numero non valido: usa il formato +39…")
    body = str(text or "").strip()
    if not body:
        raise ValueError("scrivi un messaggio")
    if len(body) > MAX_SEND:
        raise ValueError(f"messaggio troppo lungo (max {MAX_SEND} caratteri)")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in body):
        raise ValueError("il messaggio contiene caratteri non consentiti")
    target = "+" + digits
    profile = memory.profile(target, base)
    if not bool((profile.get("handoff") or {}).get("active")):
        raise PermissionError("prendi prima il controllo della conversazione")

    cli = command or config.openclaw_cli()
    argv = [
        cli, "message", "send",
        "--channel", "whatsapp",
        "--target", target,
        "--message", body,
        "--json",
    ]
    env = dict(os.environ)
    env.setdefault("OPENCLAW_CONFIG", str(config.OPENCLAW_CONFIG))
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, env=env, check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("OpenClaw non trovato: impossibile inviare") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("invio non completato entro 60 secondi") from None

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    payload: dict = {}
    start = stdout.find("{")
    if start >= 0:
        try:
            parsed = json.loads(stdout[start:])
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}
    ok = result.returncode == 0 and not payload.get("error")
    message_id = str(
        payload.get("messageId") or payload.get("id") or payload.get("message_id") or ""
    )[:80]
    if ok:
        memory.record(target, "assistant", body, base=base)
        try:
            import identity

            identity.audit(operator or "operatore", "chat.send", target, True, base=base)
        except Exception:
            pass
    return {
        "ok": ok,
        "target": target,
        "operator": str(operator or "")[:60],
        "message_id": message_id,
        "error": "" if ok else (stderr.strip() or stdout.strip() or "invio non riuscito")[-300:],
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db = base / "openclaw.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE session_windows (session_id TEXT, session_key TEXT, display_name TEXT, "
            "updated_at INTEGER, channel TEXT, chat_type TEXT)"
        )
        con.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, created_at INTEGER, event_json TEXT)")
        now = int(time.time() * 1000)
        con.execute("INSERT INTO session_windows VALUES (?,?,?,?,?,?)",
                    ("s1", "whatsapp:+393331112222", "Mario", now, "whatsapp", "direct"))
        for index, (role, text) in enumerate([
            ("user", "Ciao, vorrei un preventivo per il sito"),
            ("assistant", "Certo, a che email lo mando?"),
        ]):
            event = {"type": "message", "message": {
                "role": role, "content": text, "timestamp": now + index * 1000,
            }}
            con.execute("INSERT INTO transcript_events VALUES (?,?,?,?)",
                        ("s1", index, now + index * 1000, json.dumps(event)))
        con.commit()
        con.close()

        old = config.db_path
        config.db_path = lambda: str(db)  # type: ignore[assignment]
        try:
            rows = list_conversations(base=base)
            assert rows and rows[0]["phone"] == "+393331112222", rows
            data = transcript("+393331112222", base=base)
            assert data["source"] == "openclaw", data
            assert len(data["messages"]) == 2, data
            assert data["messages"][0]["role"] == "user", data

            assert transcript("+393339998888", base=base)["source"] == "memory"
            assert set_handoff("+393331112222", True, "Anna", base=base)["handoff"]["active"] is True
            assert list_conversations(base=base)[0]["handoff"] is True

            # Invio da parte dell'operatore. Il comando vero non viene mai
            # eseguito nei test: si usa sempre uno stub.
            stub = base / "openclaw_stub.sh"
            stub.write_text(
                "#!/bin/sh\necho '{\"messageId\":\"m1\"}'\nexit 0\n", encoding="ascii"
            )
            stub.chmod(0o755)

            # Con l'handoff attivo: validazione del testo prima dell'invio.
            set_handoff("+393331112222", True, "Anna", base=base)
            for bad in ("", "   ", "x" * (MAX_SEND + 1), "ciao\x00"):
                try:
                    send_message("+393331112222", bad, base=base, command=str(stub))
                    raise AssertionError("atteso ValueError")
                except ValueError:
                    pass

            # Senza handoff non si scrive al cliente.
            set_handoff("+393331112222", False, base=base)
            try:
                send_message("+393331112222", "ciao", base=base, command=str(stub))
                raise AssertionError("atteso PermissionError senza handoff")
            except PermissionError:
                pass

            set_handoff("+393331112222", True, "Anna", base=base)
            sent = send_message("+393331112222", "Le scrivo io", "Anna", base=base, command=str(stub))
            assert sent["ok"] is True and sent["message_id"] == "m1", sent
            assert memory.profile("+393331112222", base=base)["messages_out"] >= 1
        finally:
            config.db_path = old  # type: ignore[assignment]
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversazioni WhatsApp in sola lettura")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--transcript", default="")
    parser.add_argument("--no-redact", action="store_true")
    parser.add_argument("--handoff", default="")
    parser.add_argument("--release", default="")
    parser.add_argument("--operator", default="")
    parser.add_argument("--send", default="", help="numero a cui scrivere come operatore")
    parser.add_argument("--text", default="", help="testo del messaggio (con --send)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        if args.handoff:
            print(json.dumps(set_handoff(args.handoff, True, args.operator), ensure_ascii=False, indent=2))
            return 0
        if args.release:
            print(json.dumps(set_handoff(args.release, False, args.operator), ensure_ascii=False, indent=2))
            return 0
        if args.send:
            try:
                result = send_message(args.send, args.text, args.operator)
            except PermissionError as exc:
                print(f"conversations: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        if args.transcript:
            print(json.dumps(transcript(args.transcript, redact=not args.no_redact), ensure_ascii=False, indent=2))
            return 0
    except ValueError as exc:
        print(f"conversations: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(list_conversations(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
