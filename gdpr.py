#!/usr/bin/env python3
"""Diritti GDPR: accesso, cancellazione, retention e inventario dei dati.

Gina conserva i dati sul computer del titolare: WhatsApp/OpenClaw, memoria per
cliente, coda approvazioni e preventivi. Questo comando elimina davvero i dati
di un contatto da tutti questi archivi e produce un inventario leggibile.

Uso:
    python3 gdpr.py --report
    python3 gdpr.py --export +393331112222 --out contatto.json
    python3 gdpr.py --delete +393331112222 --yes
    python3 gdpr.py --retention 180 --apply
    python3 gdpr.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config
import memory

SOURCE_FILES = ("leads.jsonl", "lead_audit.jsonl", "approvals.jsonl", "alerts.jsonl")
RETENTION_DAYS = 180


def _base(base: Path | None = None) -> Path:
    return Path(base or config.BASE)


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _rows(path: Path) -> list[dict]:
    out = []
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
                if isinstance(item, dict):
                    out.append(item)
    except OSError:
        pass
    return out


def _write(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    tmp.write_text(text + ("\n" if text else ""), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _matches(row: dict, phone: str, keys=("phone", "client_phone", "sender", "from", "to")) -> bool:
    target = _digits(phone)
    if not target:
        return False
    for key in keys:
        if _digits(row.get(key) or "") == target:
            return True
    payload = row.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return _digits(payload.get("client_phone") or "") == target


def inventory(base: Path | None = None) -> dict:
    folder = _base(base)
    memory_dir = folder / "memory" / "contacts"
    profiles = list(memory_dir.glob("*.json")) if memory_dir.exists() else []
    quotes = folder / "quotes"
    return {
        "base": str(folder),
        "memory_profiles": len(profiles),
        "quote_pdfs": len(list(quotes.glob("*.pdf"))) if quotes.exists() else 0,
        "files": {
            name: (folder / name).exists() for name in SOURCE_FILES
        },
        "where": {
            "whatsapp": "~/.openclaw/agents (SQLite OpenClaw, sul computer del titolare)",
            "memory": "~/.openclaw/secretary/memory (JSON per contatto)",
            "approvals": "~/.openclaw/secretary/approvals.jsonl",
            "quotes": "~/.openclaw/secretary/quotes (PDF)",
            "ai": "Il testo dei messaggi può essere inviato al provider AI configurato (Groq/Gemini) per generare la risposta; non viene usato per addestrare modelli da questo progetto.",
        },
        "retention_days": int(config.get("data_retention_days") or RETENTION_DAYS),
    }


def export_contact(phone: str, base: Path | None = None) -> dict:
    folder = _base(base)
    data = {
        "exported_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
        "phone": phone,
        "memory": memory.export_contact(phone, folder),
        "leads": [row for row in _rows(folder / "leads.jsonl") if _matches(row, phone)],
        "audit": [row for row in _rows(folder / "lead_audit.jsonl") if _matches(row, phone)],
        "approvals": [row for row in _rows(folder / "approvals.jsonl") if _matches(row, phone)],
    }
    return data


def delete_contact(phone: str, base: Path | None = None) -> dict:
    """Elimina memoria, lead, audit e richieste del contatto, con i suoi PDF."""
    folder = _base(base)
    counts = {"memory": 0, "leads": 0, "audit": 0, "approvals": 0, "pdf": 0}

    if memory.forget(phone, folder):
        counts["memory"] = 1

    for name, key in (("leads.jsonl", "leads"), ("lead_audit.jsonl", "audit")):
        path = folder / name
        rows = _rows(path)
        kept = [row for row in rows if not _matches(row, phone)]
        if len(kept) != len(rows):
            counts[key] = len(rows) - len(kept)
            _write(path, kept)

    path = folder / "approvals.jsonl"
    rows = _rows(path)
    kept = []
    removed = []
    for row in rows:
        if _matches(row, phone):
            removed.append(row)
        else:
            kept.append(row)
    if removed:
        counts["approvals"] = len(removed)
        _write(path, kept)
        for row in removed:
            pdf = str((row.get("payload") or {}).get("pdf") or "")
            if pdf:
                target = (folder / "quotes" / Path(pdf).name)
                try:
                    target.unlink()
                    counts["pdf"] += 1
                except OSError:
                    pass

    return {"phone": phone, "deleted": counts, "ok": True}


def retention(days: int = RETENTION_DAYS, apply_changes: bool = False,
              base: Path | None = None) -> dict:
    folder = _base(base)
    cutoff = datetime.now(config.resolve_timezone()) - timedelta(days=max(1, int(days)))
    result = {"days": int(days), "cutoff": cutoff.isoformat(timespec="seconds"),
              "memory": 0, "audit": 0, "approvals": 0, "applied": bool(apply_changes)}

    memory_dir = folder / "memory" / "contacts"
    for path in (memory_dir.glob("*.json") if memory_dir.exists() else []):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            last = datetime.fromisoformat(str(data.get("last_seen")))
            if last.tzinfo is None:
                last = last.replace(tzinfo=cutoff.tzinfo)
        except Exception:
            continue
        if last < cutoff:
            result["memory"] = int(result["memory"] if isinstance(result["memory"], int) else 0) + 1
            if apply_changes:
                try:
                    path.unlink()
                except OSError:
                    pass

    for name, key in (("lead_audit.jsonl", "audit"), ("approvals.jsonl", "approvals")):
        path = folder / name
        rows = _rows(path)
        old = []
        kept = []
        for row in rows:
            stamp = str(row.get("ts") or row.get("created_at") or "")
            try:
                when = datetime.fromisoformat(stamp)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=cutoff.tzinfo)
            except Exception:
                kept.append(row)
                continue
            if when < cutoff:
                old.append(row)
            else:
                kept.append(row)
        result[key] = len(old)
        if apply_changes and old:
            _write(path, kept)
    return result


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        phone = "+393331112222"
        memory.record(phone, "user", "vorrei un preventivo", intent="quote", base=base)
        (base / "leads.jsonl").write_text(
            json.dumps({"phone": phone, "score": 70, "intent": "preventivo"}) + "\n"
            + json.dumps({"phone": "+393339998888", "score": 30}) + "\n", encoding="utf-8")
        (base / "lead_audit.jsonl").write_text(
            json.dumps({"phone": phone, "intent": "quote"}) + "\n", encoding="utf-8")
        (base / "quotes").mkdir()
        (base / "quotes" / "apr-1.pdf").write_bytes(b"%PDF-1.4")
        (base / "approvals.jsonl").write_text(
            json.dumps({"id": "apr-1", "type": "quote", "client_phone": phone,
                        "payload": {"pdf": "apr-1.pdf"}}) + "\n"
            + json.dumps({"id": "apr-2", "type": "email", "client_phone": "+393339998888",
                          "payload": {}}) + "\n", encoding="utf-8")

        exported = export_contact(phone, base)
        assert exported["memory"]["phone"] == phone
        assert len(exported["leads"]) == 1 and len(exported["approvals"]) == 1, exported

        result = delete_contact(phone, base)
        assert result["deleted"]["memory"] == 1, result
        assert result["deleted"]["leads"] == 1, result
        assert result["deleted"]["approvals"] == 1, result
        assert result["deleted"]["pdf"] == 1, result
        assert not (base / "quotes" / "apr-1.pdf").exists()
        assert not memory.profile(phone, base)["messages_in"]
        remaining = _rows(base / "leads.jsonl")
        assert len(remaining) == 1 and remaining[0]["phone"] == "+393339998888", remaining

        report = inventory(base)
        assert report["files"]["leads.jsonl"] is True
        assert "ai" in report["where"]

        old = datetime.now(config.resolve_timezone()) - timedelta(days=400)
        memory.record("+393330000000", "user", "vecchio", base=base)
        data = memory.load("+393330000000", base)
        data["last_seen"] = old.isoformat(timespec="seconds")
        (base / "memory" / "contacts" / f"{memory._phone_key('+393330000000')}.json").write_text(
            json.dumps(data), encoding="utf-8")
        dry = retention(180, False, base)
        assert dry["memory"] == 1 and dry["applied"] is False, dry
        applied = retention(180, True, base)
        assert applied["memory"] == 1, applied
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="GDPR: accesso, cancellazione, retention")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--export", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--delete", default="")
    parser.add_argument("--retention", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.delete:
        if not args.yes:
            print("La cancellazione è definitiva: conferma con --yes", file=sys.stderr)
            return 2
        print(json.dumps(delete_contact(args.delete), ensure_ascii=False, indent=2))
        return 0
    if args.export:
        data = export_contact(args.export)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            os.chmod(args.out, 0o600)
            print(f"esportato in {args.out}")
        else:
            print(text)
        return 0
    if args.retention:
        print(json.dumps(retention(args.retention, args.apply), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(inventory(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
