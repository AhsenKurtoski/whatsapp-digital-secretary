#!/usr/bin/env python3
"""Coda locale delle azioni che il bot propone e il titolare deve approvare.

Il bot non invia email e non crea eventi: scrive solo una richiesta qui.
La dashboard, dopo il login, esegue l'azione approvata tramite gog.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config

try:
    from email_template import render_email
except Exception:  # template opzionale: la coda continua a funzionare
    render_email = None

CFG = config.load()
BASE = config.BASE
QUEUE = BASE / "approvals.jsonl"
LOCK = BASE / "approvals.lock"
ROME = ZoneInfo(str(CFG.get("timezone") or "Europe/Rome"))
MAX_PENDING = 30
MAX_ROWS = 500
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def now_iso() -> str:
    return datetime.now(ROME).isoformat(timespec="seconds")


def _clean(value, limit: int, newlines: bool = False) -> str:
    text = str(value or "")
    text = text.replace("\x00", "")
    if not newlines:
        text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:limit]


def _email(value) -> str:
    address = str(value or "").strip()
    if len(address) > 254 or "\n" in address or "\r" in address or not EMAIL_RE.match(address):
        raise ValueError("indirizzo email non valido")
    return address


def _emails(value, limit: int = 5) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").split(",")
    out: list[str] = []
    for item in raw:
        if not str(item or "").strip():
            continue
        address = _email(item)
        if address.lower() not in [x.lower() for x in out]:
            out.append(address)
    if len(out) > limit:
        raise ValueError(f"massimo {limit} destinatari")
    return out


def _when(value) -> datetime:
    text = str(value or "").strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"data/ora non valida: {text[:40]}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ROME)
    return dt


def validate_email(payload: dict) -> dict:
    to = _emails(payload.get("to"))
    if not to:
        raise ValueError("destinatario mancante")
    subject = _clean(payload.get("subject"), 180)
    body = _clean(payload.get("body"), 8000, newlines=True)
    if not subject:
        raise ValueError("oggetto mancante")
    if not body:
        raise ValueError("testo email mancante")
    return {"to": ", ".join(to), "subject": subject, "body": body}


def validate_calendar(payload: dict) -> dict:
    summary = _clean(payload.get("summary") or payload.get("title"), 160)
    if not summary:
        raise ValueError("titolo appuntamento mancante")
    start = _when(payload.get("start") or payload.get("from"))
    end = _when(payload.get("end") or payload.get("to"))
    if end <= start:
        raise ValueError("la fine deve essere dopo l'inizio")
    if start < datetime.now(ROME) - timedelta(hours=2):
        raise ValueError("l'inizio è troppo nel passato")
    if end - start > timedelta(days=7):
        raise ValueError("appuntamento troppo lungo")
    tz = _clean(payload.get("timezone") or "Europe/Rome", 64) or "Europe/Rome"
    attendees = _emails(payload.get("attendees") or [], limit=10)
    location = _clean(payload.get("location"), 240)
    description = _clean(payload.get("description"), 4000, newlines=True)
    return {
        "summary": summary,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": tz,
        "attendees": attendees,
        "location": location,
        "description": description,
    }


@contextmanager
def _locked():
    BASE.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_unlocked() -> list[dict]:
    if not QUEUE.exists():
        return []
    out: list[dict] = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
    return out[-MAX_ROWS:]


def _write_unlocked(rows: list[dict]) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE.with_suffix(".jsonl.tmp")
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows[-MAX_ROWS:])
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload + ("\n" if payload else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, QUEUE)
    os.chmod(QUEUE, 0o600)


def _fingerprint(kind: str, payload: dict) -> str:
    raw = json.dumps({"type": kind, "payload": payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_request(
    kind: str,
    payload: dict,
    client_name: str = "",
    client_phone: str = "",
    reason: str = "",
) -> dict:
    kind = str(kind or "").strip().lower()
    if kind not in {"email", "calendar"}:
        raise ValueError("tipo richiesta non valido")
    clean_payload = validate_email(payload) if kind == "email" else validate_calendar(payload)
    fingerprint = _fingerprint(kind, clean_payload)
    if kind == "email" and render_email is not None:
        # L'HTML è generato solo qui, mai preso dal testo non attendibile del bot.
        clean_payload["html"] = render_email(
            clean_payload.get("subject", ""),
            clean_payload.get("body", ""),
            client_name=_clean(client_name, 80),
        )
    with _locked():
        rows = _read_unlocked()
        pending = [row for row in rows if row.get("status") == "pending"]
        for row in pending:
            if row.get("fingerprint") == fingerprint:
                return row
        if len(pending) >= MAX_PENDING:
            raise ValueError("troppe richieste in attesa; attendi l'approvazione del titolare")
        request = {
            "id": "apr-" + datetime.now(ROME).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2),
            "type": kind,
            "status": "pending",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "client_name": _clean(client_name, 120),
            "client_phone": _clean(client_phone, 40),
            "reason": _clean(reason, 500),
            "payload": clean_payload,
            "fingerprint": fingerprint,
            "result": "",
            "error": "",
        }
        rows.append(request)
        _write_unlocked(rows)
        return request


def list_requests(limit: int = 100) -> dict:
    limit = max(1, min(int(limit or 100), 300))
    with _locked():
        rows = list(reversed(_read_unlocked()))
    pending = [row for row in rows if row.get("status") == "pending"]
    return {
        "pending": pending[:limit],
        "recent": rows[:limit],
        "counts": {
            "pending": len(pending),
            "approved": sum(1 for row in rows if row.get("status") == "approved"),
            "rejected": sum(1 for row in rows if row.get("status") == "rejected"),
            "failed": sum(1 for row in rows if row.get("status") == "failed"),
        },
        "limits": {"max_pending": MAX_PENDING},
    }


def get_request(request_id: str) -> dict | None:
    request_id = str(request_id or "").strip()
    if not request_id:
        return None
    with _locked():
        for row in _read_unlocked():
            if row.get("id") == request_id:
                return row
    return None


def set_status(request_id: str, status: str, result: str = "", error: str = "") -> dict:
    if status not in {"approved", "rejected", "failed"}:
        raise ValueError("stato non valido")
    request_id = str(request_id or "").strip()
    with _locked():
        rows = _read_unlocked()
        target = None
        for row in rows:
            if row.get("id") == request_id:
                if row.get("status") != "pending":
                    raise ValueError("richiesta già gestita")
                row["status"] = status
                row["updated_at"] = now_iso()
                row["result"] = _clean(result, 1000)
                row["error"] = _clean(error, 1000)
                target = row
                break
        if target is None:
            raise ValueError("richiesta non trovata")
        _write_unlocked(rows)
        return target


def pending_count() -> int:
    with _locked():
        return sum(1 for row in _read_unlocked() if row.get("status") == "pending")
