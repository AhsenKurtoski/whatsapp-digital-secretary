#!/usr/bin/env python3
"""Promemoria WhatsApp per il titolare.

Controlla ciò che richiede la sua attenzione e invia un solo messaggio
consolidato, con deduplicazione per non ripetersi ogni ora:

- email e appuntamenti in attesa di approvazione da troppo tempo;
- appuntamenti in attesa che stanno per arrivare;
- token o servizi in scadenza letti dall'ultima scansione Health.

Uso:
    python3 reminders.py             # invia se c'è qualcosa di nuovo
    python3 reminders.py --dry-run   # stampa senza inviare
    python3 reminders.py --json
    python3 reminders.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import config

ROME = config.resolve_timezone()
STATE = os.path.join(str(config.BASE), "reminders_state.json")
HEALTH = os.path.join(str(config.BASE), "health_state.json")
DEFAULT_MIN_AGE_MINUTES = 30
DEFAULT_COOLDOWN_HOURS = 12
MAX_LINES = 18


def _parse(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ROME)
    return stamp.astimezone(ROME)


def _age_text(minutes: float) -> str:
    if minutes < 60:
        return f"{int(max(1, minutes))} min"
    if minutes < 60 * 24:
        return f"{int(minutes // 60)} h"
    return f"{int(minutes // (60 * 24))} g"


def _label(request: dict) -> str:
    payload = request.get("payload") or {}
    if request.get("type") == "quote":
        return f"Preventivo {payload.get('number') or ''} per {payload.get('client_name') or 'cliente'}".strip()
    if request.get("type") == "calendar":
        return f"Appuntamento: {payload.get('summary') or 'senza titolo'}"
    return f"Email a {payload.get('to') or 'cliente'}: {payload.get('subject') or 'senza oggetto'}"


def collect(
    requests: list[dict],
    health: dict | None = None,
    now: datetime | None = None,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    deadline_hours: int = 48,
    expiring_days: int = 7,
) -> list[dict]:
    """Elementi che meritano un promemoria, con chiave stabile per il dedup."""
    now = now or datetime.now(ROME)
    items: list[dict] = []
    for request in requests:
        if str(request.get("status")) != "pending":
            continue
        created = _parse(request.get("created_at"))
        age = (now - created).total_seconds() / 60 if created else 0
        label = _label(request)
        if age >= min_age_minutes:
            items.append({
                "key": f"approval:{request.get('id')}",
                "kind": "approval",
                "text": f"⏳ {label} — in attesa da {_age_text(age)}",
                "sort": -age,
            })
        if str(request.get("type")) == "calendar":
            payload = request.get("payload") or {}
            start = _parse(payload.get("start"))
            if start:
                hours = (start - now).total_seconds() / 3600
                if 0 <= hours <= deadline_hours:
                    items.append({
                        "key": f"deadline:{request.get('id')}",
                        "kind": "deadline",
                        "text": f"📅 {label} — tra {_age_text(hours * 60)} ({start.strftime('%d/%m %H:%M')})",
                        "sort": -hours,
                    })
    for token in (health or {}).get("expiring") or []:
        if not isinstance(token, dict):
            continue
        days = token.get("days_left")
        if token.get("expired"):
            when = "già scaduto"
        elif isinstance(days, (int, float)) and days < 1:
            when = "tra poche ore"
        elif isinstance(days, (int, float)):
            when = f"tra {int(days)} giorni"
        else:
            when = "in scadenza"
        if isinstance(days, (int, float)) and not token.get("expired") and days > expiring_days:
            continue
        items.append({
            "key": f"token:{token.get('id') or token.get('label')}",
            "kind": "token",
            "text": f"🔑 {token.get('label') or token.get('id')}: {when}"
                    + (f" ({token.get('expires')})" if token.get("expires") else ""),
            "sort": 1,
        })
    return items


def build_message(items: list[dict], cfg: dict | None = None, now: datetime | None = None) -> str:
    cfg = cfg if isinstance(cfg, dict) else config.load()
    now = now or datetime.now(ROME)
    brand = str(cfg.get("business_name") or "Attività").strip()
    header = [
        f"🔔 Promemoria segreteria — {now.strftime('%d/%m %H:%M')}",
        brand,
        "",
    ]
    ordered = sorted(items, key=lambda item: (item.get("sort", 0), item.get("key", "")))
    lines = [str(item.get("text") or "") for item in ordered if item.get("text")]
    lines = lines[:MAX_LINES]
    if not lines:
        return ""
    footer = ["", "Apri la dashboard per approvare o rinviare."]
    return "\n".join(header + lines + footer)[:1750]


def load_state(path: str = STATE) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict, path: str = STATE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def pending_items(items: list[dict], state: dict, now: datetime | None = None,
                  cooldown_hours: int = DEFAULT_COOLDOWN_HOURS) -> list[dict]:
    """Filtra gli elementi già notificati di recente (dedup per chiave)."""
    now = now or datetime.now(ROME)
    sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    fresh: list[dict] = []
    for item in items:
        stamp = _parse(sent.get(item["key"]))
        if stamp and (now - stamp) < timedelta(hours=cooldown_hours):
            continue
        fresh.append(item)
    return fresh


def send_whatsapp(text: str, cfg: dict | None = None, dry: bool = False) -> bool:
    cfg = cfg if isinstance(cfg, dict) else config.load()
    target = str(cfg.get("owner_number") or "").strip()
    if dry:
        print("DRY SEND:\n" + text)
        return True
    if not target:
        print("owner_number non configurato: promemoria non inviato.", file=sys.stderr)
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
    return True


def run(
    requests: list[dict] | None = None,
    health: dict | None = None,
    state_path: str = STATE,
    health_path: str = HEALTH,
    dry: bool = False,
    force: bool = False,
    now: datetime | None = None,
    sender=None,
) -> dict:
    cfg = config.load()
    now = now or datetime.now(ROME)
    if requests is None:
        try:
            import approval_store

            requests = approval_store.list_requests(300).get("recent") or []
        except Exception:
            requests = []
    if health is None:
        try:
            with open(health_path, encoding="utf-8") as handle:
                health = json.load(handle)
        except Exception:
            health = {}
    min_age = int(config.load().get("reminder_min_age_minutes") or DEFAULT_MIN_AGE_MINUTES)
    cooldown = int(config.load().get("reminder_cooldown_hours") or DEFAULT_COOLDOWN_HOURS)
    items = collect(requests, health, now=now, min_age_minutes=min_age)
    state = load_state(state_path)
    fresh = items if force else pending_items(items, state, now=now, cooldown_hours=cooldown)
    if not fresh:
        return {"sent": False, "items": len(items), "fresh": 0, "reason": "nothing_new"}
    text = build_message(fresh, cfg, now=now)
    ok = (sender or (lambda message: send_whatsapp(message, cfg, dry=dry)))(text)
    if ok and not dry:
        sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
        for item in fresh:
            sent[item["key"]] = now.isoformat(timespec="seconds")
        # Conserva solo le ultime 300 chiavi.
        if len(sent) > 300:
            ordered = sorted(sent.items(), key=lambda kv: kv[1], reverse=True)[:300]
            sent = dict(ordered)
        save_state({"sent": sent, "last_run": now.isoformat(timespec="seconds")}, state_path)
    return {"sent": bool(ok), "items": len(items), "fresh": len(fresh), "message": text}


def self_test() -> None:
    now = datetime.now(ROME)
    requests = [
        {
            "id": "apr-1",
            "type": "email",
            "status": "pending",
            "created_at": (now - timedelta(hours=3)).isoformat(timespec="seconds"),
            "payload": {"to": "cliente@example.com", "subject": "Preventivo"},
        },
        {
            "id": "apr-2",
            "type": "calendar",
            "status": "pending",
            "created_at": now.isoformat(timespec="seconds"),
            "payload": {
                "summary": "Call sito",
                "start": (now + timedelta(hours=20)).isoformat(timespec="seconds"),
            },
        },
        {
            "id": "apr-3",
            "type": "email",
            "status": "approved",
            "created_at": (now - timedelta(hours=9)).isoformat(timespec="seconds"),
            "payload": {"to": "x@example.com", "subject": "Vecchia"},
        },
    ]
    health = {"expiring": [{"id": "google_oauth", "label": "Google OAuth", "days_left": 2}]}
    items = collect(requests, health, now=now)
    keys = {item["key"] for item in items}
    assert "approval:apr-1" in keys, keys
    assert "deadline:apr-2" in keys, keys
    assert "approval:apr-3" not in keys, keys
    assert "token:google_oauth" in keys, keys
    message = build_message(items, {"business_name": "Demo"}, now=now)
    assert "Demo" in message and "Google OAuth" in message, message

    tmp = tempfile.mkdtemp(prefix="secretary-reminders-")
    state_path = os.path.join(tmp, "state.json")
    sent: list[str] = []
    first = run(requests, health, state_path=state_path, now=now, sender=lambda text: sent.append(text) or True)
    assert first["sent"] is True, first
    second = run(requests, health, state_path=state_path, now=now, sender=lambda text: sent.append(text) or True)
    assert second["sent"] is False and second["reason"] == "nothing_new", second
    third = run(requests, health, state_path=state_path, now=now, force=True,
                sender=lambda text: sent.append(text) or True)
    assert third["sent"] is True, third
    assert len(sent) == 2, sent
    print("SELF-TEST OK")
    print(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promemoria WhatsApp per il titolare")
    parser.add_argument("--state", default=STATE)
    parser.add_argument("--health", default=HEALTH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = run(state_path=args.state, health_path=args.health, dry=args.dry_run, force=args.force)
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "message"}, ensure_ascii=False))
    elif result.get("message"):
        print(result["message"])
    else:
        print("Nessun promemoria nuovo.")
    return 0 if result.get("sent") or result.get("reason") == "nothing_new" else 1


if __name__ == "__main__":
    raise SystemExit(main())
