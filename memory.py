#!/usr/bin/env python3
"""Memoria per cliente: contesto persistente, lingua, tono, fatti e handoff.

Un file per contatto sotto `memory/contacts/`, scrittura atomica e dimensione
limitata. Non contiene segreti; i transcript sono redatti prima di essere
salvati. `forget()` cancella tutto per un contatto (diritto all'oblio).

Uso:
    python3 memory.py --profile +393331112222
    python3 memory.py --context +393331112222
    python3 memory.py --list
    python3 memory.py --forget +393331112222 --yes
    python3 memory.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import config
import lead_intel

MAX_MESSAGES = 300
MAX_FACTS = 40
MAX_TEXT = 1200
_LOCK = threading.Lock()


def _base(base: Path | None = None) -> Path:
    folder = Path(base or config.BASE) / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass
    return folder


def _dir(base: Path | None = None) -> Path:
    folder = _base(base) / "contacts"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass
    return folder


def _phone_key(phone: str) -> str:
    clean = re.sub(r"[^0-9+]", "", str(phone or ""))
    if not clean:
        raise ValueError("numero mancante")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:32]


def _path(phone: str, base: Path | None = None) -> Path:
    return _dir(base) / f"{_phone_key(phone)}.json"


def _empty(phone: str) -> dict:
    now = _now()
    return {
        "phone": str(phone or "")[:40],
        "name": "",
        "language": "",
        "tone": "",
        "facts": {},
        "fact_meta": {},
        "first_seen": now,
        "last_seen": now,
        "messages_in": 0,
        "messages_out": 0,
        "intents": {},
        "max_score": 0,
        "handoff": {"active": False, "operator": "", "since": ""},
        "transcript": [],
    }


def _now() -> str:
    return datetime.now(config.resolve_timezone()).isoformat(timespec="seconds")


def load(phone: str, base: Path | None = None) -> dict:
    path = _path(phone, base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    profile = _empty(phone)
    profile.update({k: v for k, v in data.items() if k in profile})
    profile.setdefault("handoff", {"active": False, "operator": "", "since": ""})
    profile.setdefault("facts", {})
    profile.setdefault("fact_meta", {})
    profile.setdefault("transcript", [])
    profile.setdefault("intents", {})
    return profile


def _save(profile: dict, base: Path | None = None) -> None:
    path = _path(profile.get("phone", ""), base)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def record(phone: str, role: str, text: str, intent: str | None = None,
           score: int | None = None, name: str | None = None,
           base: Path | None = None) -> dict:
    """Registra un messaggio redatto e aggiorna lingua, intenti e punteggio."""
    role = "assistant" if str(role).lower() in {"assistant", "bot", "out"} else "user"
    clean = lead_intel.redact(str(text or "")[:MAX_TEXT])
    with _LOCK:
        profile = load(phone, base)
        if name and not profile.get("name"):
            profile["name"] = str(name)[:60]
        if role == "user":
            profile["messages_in"] = int(profile.get("messages_in") or 0) + 1
            detected = lead_intel.detect_language(clean)
            profile["language"] = detected
            if intent:
                intents = profile.get("intents") or {}
                intents[str(intent)] = int(intents.get(str(intent)) or 0) + 1
                profile["intents"] = intents
            if score is not None:
                profile["max_score"] = max(int(profile.get("max_score") or 0), int(score))
        else:
            profile["messages_out"] = int(profile.get("messages_out") or 0) + 1
        profile["last_seen"] = _now()
        entries = profile.get("transcript") or []
        entry = {"ts": _now(), "role": role, "text": clean}
        if entries and entries[-1].get("role") == role and entries[-1].get("text") == clean:
            # Un retry o un doppio invio non deve duplicare il contesto.
            entries[-1]["ts"] = entry["ts"]
        else:
            entries.append(entry)
        profile["transcript"] = entries[-MAX_MESSAGES:]
        _save(profile, base)
        return profile


def remember(phone: str, key: str, value: str, base: Path | None = None) -> dict:
    """Salva un fatto utile e duraturo (es. "servizio": "sito web")."""
    key = re.sub(r"[^a-z0-9_]+", "_", str(key or "").strip().lower())[:40]
    if not key:
        raise ValueError("chiave mancante")
    value = str(value or "").strip()[:200]
    with _LOCK:
        profile = load(phone, base)
        facts = profile.get("facts") or {}
        meta = profile.get("fact_meta") or {}
        if value:
            # Un fatto aggiornato torna in cima: senza pop resterebbe nella
            # vecchia posizione e verrebbe scartato al posto di uno obsoleto.
            facts.pop(key, None)
            facts[key] = value
            meta.pop(key, None)
            meta[key] = {"updated_at": _now()}
            keep = list(facts)[-MAX_FACTS:]
            profile["facts"] = {name: facts[name] for name in keep}
            profile["fact_meta"] = {name: meta[name] for name in keep if name in meta}
            _save(profile, base)
        return profile


def set_handoff(phone: str, active: bool, operator: str = "", base: Path | None = None) -> dict:
    with _LOCK:
        profile = load(phone, base)
        profile["handoff"] = {
            "active": bool(active),
            "operator": str(operator or "")[:60],
            "since": _now() if active else "",
        }
        _save(profile, base)
        return profile


def is_handoff(phone: str, base: Path | None = None) -> bool:
    return bool((load(phone, base).get("handoff") or {}).get("active"))


def profile(phone: str, base: Path | None = None) -> dict:
    data = load(phone, base)
    data.pop("transcript", None)
    data["messages_total"] = int(data.get("messages_in") or 0) + int(data.get("messages_out") or 0)
    return data


def transcript(phone: str, limit: int = 50, redact: bool = True,
               base: Path | None = None) -> list[dict]:
    rows = (load(phone, base).get("transcript") or [])[-max(1, min(int(limit or 50), MAX_MESSAGES)):]
    if redact:
        return [{"ts": r.get("ts"), "role": r.get("role"), "text": lead_intel.redact(r.get("text", ""))} for r in rows]
    return list(rows)


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z\u00e0-\u00ff0-9]{3,}", str(text or "").lower())}


def _rank_facts(data: dict, query: str = "") -> list[tuple[str, str]]:
    """Fatti ordinati per attinenza al messaggio corrente, poi per recenza."""
    facts = data.get("facts") or {}
    meta = data.get("fact_meta") or {}
    words = _tokens(query)
    if not words:
        for item in reversed(data.get("transcript") or []):
            if item.get("role") == "user":
                words = _tokens(item.get("text"))
                break

    def rank(item: tuple[str, str]) -> tuple[int, str]:
        key, value = item
        overlap = len(words & _tokens(f"{key} {value}"))
        stamp = str((meta.get(key) or {}).get("updated_at") or "")
        return (overlap, stamp)

    return sorted(facts.items(), key=rank, reverse=True)


def search(text: str, limit: int = 20, base: Path | None = None) -> list[dict]:
    """Cerca fatti e messaggi in tutte le memorie, con dati personali redatti."""
    needle = str(text or "").strip().lower()
    if not needle:
        raise ValueError("ricerca mancante")
    rows: list[dict] = []
    for path in _dir(base).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("phone"):
            continue
        phone = str(data.get("phone"))
        name = str(data.get("name") or "")
        meta = data.get("fact_meta") or {}
        for key, value in (data.get("facts") or {}).items():
            if needle in f"{key} {value}".lower():
                rows.append({
                    "phone": phone,
                    "name": name,
                    "type": "fact",
                    "key": str(key),
                    "value": lead_intel.redact(str(value))[:200],
                    "at": str((meta.get(key) or {}).get("updated_at") or data.get("last_seen") or ""),
                })
        for item in data.get("transcript") or []:
            message = str(item.get("text") or "")
            if needle in message.lower():
                rows.append({
                    "phone": phone,
                    "name": name,
                    "type": "message",
                    "role": str(item.get("role") or ""),
                    "text": lead_intel.redact(message)[:200],
                    "at": str(item.get("ts") or ""),
                })
    rows.sort(key=lambda row: str(row.get("at") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 20), 200))]


def context(phone: str, max_chars: int = 900, base: Path | None = None, query: str = "") -> str:
    """Contesto compatto per il prompt: lingua, fatti pertinenti e ultimi scambi."""
    data = load(phone, base)
    if not data.get("messages_in") and not data.get("facts"):
        return ""
    lines = []
    if data.get("language"):
        lines.append(f"Lingua del cliente: {data['language']}")
    if data.get("name"):
        lines.append(f"Nome: {data['name']}")
    facts = data.get("facts") or {}
    if facts:
        ranked = _rank_facts(data, query)
        lines.append("Fatti: " + "; ".join(f"{key}={value}" for key, value in ranked[:10]))
    intents = data.get("intents") or {}
    if intents:
        top = max(intents, key=lambda key: intents[key])
        lines.append(f"Intento prevalente: {top}")
    recent = (data.get("transcript") or [])[-6:]
    for item in recent:
        who = "Cliente" if item.get("role") == "user" else "Gina"
        lines.append(f"{who}: {lead_intel.redact(item.get('text', ''))[:160]}")
    text = "\n".join(lines)
    return text[:max_chars]


def conversations(limit: int = 100, base: Path | None = None) -> list[dict]:
    folder = _dir(base)
    rows = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("phone"):
            continue
        last = (data.get("transcript") or [{}])[-1]
        rows.append({
            "phone": data.get("phone"),
            "name": data.get("name", ""),
            "language": data.get("language", ""),
            "messages_in": data.get("messages_in", 0),
            "messages_out": data.get("messages_out", 0),
            "last_seen": data.get("last_seen", ""),
            "handoff": bool((data.get("handoff") or {}).get("active")),
            "max_score": data.get("max_score", 0),
            "preview": lead_intel.redact(str(last.get("text") or ""))[:160],
        })
    rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 100), 500))]


def forget(phone: str, base: Path | None = None) -> bool:
    """Diritto all'oblio: elimina memoria e transcript del contatto."""
    path = _path(phone, base)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def export_contact(phone: str, base: Path | None = None) -> dict:
    return load(phone, base)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        phone = "+393331112222"
        record(phone, "user", "Ciao, vorrei un preventivo per il sito", intent="quote", score=72,
               name="Mario", base=base)
        record(phone, "assistant", "Certo Mario, a che email lo mando?", base=base)
        assert is_handoff(phone, base) is False

        data = profile(phone, base)
        assert data["messages_in"] == 1 and data["messages_out"] == 1, data
        assert data["language"] == "it", data
        assert data["intents"]["quote"] == 1, data
        assert data["max_score"] == 72, data
        assert data["name"] == "Mario", data

        remember(phone, "servizio", "sito web", base=base)
        remember(phone, "budget", "3000 euro", base=base)
        ctx = context(phone, base=base)
        assert "sito web" in ctx and "Lingua" in ctx, ctx

        remember(phone, "servizio", "e-commerce", base=base)
        ranked = context(phone, base=base, query="quanto costa l'e-commerce")
        assert "e-commerce" in ranked and "sito web" not in ranked, ranked
        hits = search("e-commerce", base=base)
        assert hits and hits[0]["type"] == "fact" and hits[0]["phone"] == phone, hits

        before = len(transcript(phone, base=base))
        record(phone, "user", "Stessa domanda", base=base)
        record(phone, "user", "Stessa domanda", base=base)
        assert len(transcript(phone, base=base)) == before + 1, "i doppioni consecutivi non si duplicano"

        record(phone, "user", "Scrivimi a mario@example.com", base=base)
        last = transcript(phone, 1, base=base)[0]
        assert "[email]" in last["text"], last

        set_handoff(phone, True, "Anna", base=base)
        assert is_handoff(phone, base) is True
        assert profile(phone, base)["handoff"]["operator"] == "Anna"

        assert conversations(base=base)[0]["phone"] == phone
        assert export_contact(phone, base)["phone"] == phone
        assert forget(phone, base) is True
        assert forget(phone, base) is False
        assert conversations(base=base) == []
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Memoria per cliente")
    parser.add_argument("--profile", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--search", default="")
    parser.add_argument("--forget", default="")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.forget:
        if not args.yes:
            print("conferma con --yes", file=sys.stderr)
            return 2
        print("eliminato" if forget(args.forget) else "nessun dato per questo numero")
        return 0
    if args.profile:
        print(json.dumps(profile(args.profile), ensure_ascii=False, indent=2))
        return 0
    if args.context:
        print(context(args.context))
        return 0
    if args.search:
        print(json.dumps(search(args.search), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(conversations(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
