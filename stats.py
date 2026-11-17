#!/usr/bin/env python3
"""KPI reali della segreteria, calcolati sui dati già presenti.

Nessun dato inventato: se un file manca, il KPI è 0 e la dashboard lo dichiara.
Le serie coprono gli ultimi 14 giorni.

Uso:
    python3 stats.py --json
    python3 stats.py --self-test
"""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config
import memory

DAYS = 14


def _base(base: Path | None = None) -> Path:
    return Path(base or config.BASE)


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
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
                    rows.append(item)
    except OSError:
        pass
    return rows


def _day(value) -> str:
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            config.resolve_timezone()
        ).date().isoformat()
    except Exception:
        return ""


def _leads(base: Path) -> list[dict]:
    rows = []
    for item in _jsonl(base / "leads.jsonl"):
        rows.append({
            "day": _day(item.get("ts") or item.get("at") or item.get("created_at")),
            "phone": str(item.get("phone") or item.get("sender") or item.get("from") or ""),
            "score": int(item.get("score") or 0),
            "intent": str(item.get("intent") or "info"),
            "suspicious": str(item.get("intent") or "") == "sospetto" or bool(item.get("scam")),
        })
    return rows


def _approvals(base: Path) -> list[dict]:
    return [row for row in _jsonl(base / "approvals.jsonl") if isinstance(row, dict)]


def compute(base: Path | None = None) -> dict:
    folder = _base(base)
    leads = _leads(folder)
    approvals = _approvals(folder)
    profiles = memory.conversations(500, folder)

    quotes = [row for row in approvals if str(row.get("type")) == "quote"]
    approved_quotes = [row for row in quotes if row.get("status") == "approved"]

    def value_of(row: dict) -> float:
        try:
            return float(((row.get("payload") or {}).get("totals") or {}).get("total") or 0)
        except (TypeError, ValueError):
            return 0.0

    quote_value = round(sum(value_of(row) for row in approved_quotes), 2)

    # Tempi di risposta reali: coppia user -> assistant nella stessa memoria.
    deltas: list[float] = []
    for row in profiles:
        try:
            data = memory.load(row["phone"], folder)
        except Exception:
            continue
        previous_user = None
        for item in data.get("transcript") or []:
            try:
                stamp = datetime.fromisoformat(str(item.get("ts")))
            except Exception:
                stamp = None
            if item.get("role") == "user":
                previous_user = stamp
            elif item.get("role") == "assistant" and previous_user and stamp:
                delta = (stamp - previous_user).total_seconds()
                if 0 <= delta <= 86400:
                    deltas.append(delta)
                previous_user = None
    avg_response = round(sum(deltas) / len(deltas), 1) if deltas else 0.0

    today = datetime.now(config.resolve_timezone()).date()
    days = [(today - timedelta(days=offset)).isoformat() for offset in range(DAYS - 1, -1, -1)]
    daily = []
    for day in days:
        messages = 0
        conversations = 0
        for row in profiles:
            data = memory.load(row["phone"], folder) if row.get("phone") else {}
            count = sum(1 for m in (data.get("transcript") or []) if _day(m.get("ts")) == day)
            if count:
                conversations += 1
                messages += count
        daily.append({
            "day": day,
            "messages": messages,
            "conversations": conversations,
            "leads": sum(1 for lead in leads if lead["day"] == day),
            "quotes": sum(1 for row in quotes if _day(row.get("created_at")) == day),
        })

    leads_total = len(leads)
    quotes_total = len(quotes)
    conversion = round(quotes_total / leads_total, 4) if leads_total else 0.0
    scored = [lead["score"] for lead in leads if lead["score"]]
    return {
        "kpis": {
            "conversations": len(profiles),
            "messages": sum(int(row.get("messages_in") or 0) + int(row.get("messages_out") or 0) for row in profiles),
            "leads": leads_total,
            "quotes_issued": quotes_total,
            "quotes_approved": len(approved_quotes),
            "lead_conversion_rate": conversion,
            "avg_response_seconds": avg_response,
            "avg_lead_score": round(sum(scored) / len(scored), 1) if scored else 0.0,
            "quotes_value_approved": quote_value,
            "handoffs_active": sum(1 for row in profiles if row.get("handoff")),
            "suspicious_messages": sum(1 for lead in leads if lead["suspicious"]),
        },
        "daily": daily,
        "generated_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
        "empty": not profiles and not leads and not approvals,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "leads.jsonl").write_text(
            json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "phone": "+393331112222",
                        "score": 80, "intent": "preventivo"}) + "\n"
            + json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "phone": "+393334445555",
                          "score": 20, "intent": "info"}) + "\n",
            encoding="utf-8",
        )
        (base / "approvals.jsonl").write_text(
            json.dumps({"type": "quote", "status": "approved", "created_at": datetime.now().isoformat(timespec="seconds"),
                        "payload": {"totals": {"total": 1220.0}}}) + "\n"
            + json.dumps({"type": "quote", "status": "pending", "created_at": datetime.now().isoformat(timespec="seconds"),
                          "payload": {"totals": {"total": 500.0}}}) + "\n",
            encoding="utf-8",
        )
        memory.record("+393331112222", "user", "vorrei un preventivo", intent="quote", base=base)
        memory.record("+393331112222", "assistant", "certo, dimmi", base=base)

        data = compute(base)
        kpi = data["kpis"]
        assert kpi["leads"] == 2, kpi
        assert kpi["quotes_issued"] == 2 and kpi["quotes_approved"] == 1, kpi
        assert kpi["lead_conversion_rate"] == 1.0, kpi
        assert kpi["quotes_value_approved"] == 1220.0, kpi
        assert kpi["conversations"] == 1, kpi
        assert kpi["avg_lead_score"] == 50.0, kpi
        assert kpi["avg_response_seconds"] >= 0, kpi
        assert len(data["daily"]) == DAYS, data["daily"]
        assert sum(day["leads"] for day in data["daily"]) == 2, data["daily"]
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="KPI della segreteria")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    data = compute()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for key, value in data["kpis"].items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
