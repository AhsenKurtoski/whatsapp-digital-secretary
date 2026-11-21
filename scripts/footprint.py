#!/usr/bin/env python3
"""Impronta di memoria reale della segreteria su macchine leggere.

Misura, senza dipendenze esterne, quanta RAM usano davvero i processi della
segreteria (dashboard, gateway OpenClaw, processi Node collegati) e quanta ne
resta al sistema. Serve a rispondere a una domanda concreta: «gira su una
macchina da 2 GB?» con numeri, non con promesse.

Uso:
    python3 scripts/footprint.py
    python3 scripts/footprint.py --json
    python3 scripts/footprint.py --budget-mb 1200
    python3 scripts/footprint.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

MEMINFO = "/proc/meminfo"
PROC = Path("/proc")
DEFAULT_BUDGET_MB = 1500
MATCH = ("dashboard.py", "openclaw", "gateway", "secretary")


def parse_meminfo(text: str) -> dict:
    """Estrae i valori utili da /proc/meminfo, in MB."""
    values: dict[str, int] = {}
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        try:
            kb = int(parts[1])
        except ValueError:
            continue
        values[key] = kb // 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "total_mb": total,
        "available_mb": available,
        "used_mb": max(0, total - available),
        "swap_total_mb": swap_total,
        "swap_used_mb": max(0, swap_total - swap_free),
        "used_percent": round((total - available) * 100 / total, 1) if total else 0.0,
    }


def system_memory(path: str = MEMINFO) -> dict:
    try:
        return parse_meminfo(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return parse_meminfo("")


def _rss_mb(pid: int) -> float:
    try:
        for line in (PROC / str(pid) / "status").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        return 0.0
    return 0.0


def _cmdline(pid: int) -> str:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def processes(limit: int = 40) -> list[dict]:
    """Processi della segreteria con la loro RSS, dal più pesante."""
    own = os.getpid()
    found = []
    try:
        entries = list(PROC.iterdir())
    except Exception:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own:
            continue
        cmd = _cmdline(pid)
        if not cmd or not any(word in cmd.lower() for word in MATCH):
            continue
        # La shell che ha lanciato il comando contiene le stesse parole nella
        # propria cmdline: non è un processo della segreteria.
        if Path(cmd.split()[0]).name in {"bash", "sh", "dash", "zsh"}:
            continue
        found.append({"pid": pid, "rss_mb": _rss_mb(pid), "cmd": cmd[:160]})
    found.sort(key=lambda item: item["rss_mb"], reverse=True)
    return found[:limit]


def verdict(used_mb: float, budget_mb: float) -> dict:
    ratio = used_mb / budget_mb if budget_mb else 0.0
    if used_mb > budget_mb:
        return {
            "status": "over",
            "detail": f"{used_mb:.0f} MB oltre il budget di {budget_mb:.0f} MB",
        }
    if ratio >= 0.8:
        return {
            "status": "near",
            "detail": f"{used_mb:.0f} MB, vicino al budget di {budget_mb:.0f} MB",
        }
    return {
        "status": "ok",
        "detail": f"{used_mb:.0f} MB su un budget di {budget_mb:.0f} MB",
    }


def report(budget_mb: float = DEFAULT_BUDGET_MB) -> dict:
    memory = system_memory()
    procs = processes()
    used = round(sum(item["rss_mb"] for item in procs), 1)
    return {
        "system": memory,
        "processes": procs,
        "process_total_mb": used,
        "budget_mb": budget_mb,
        "verdict": verdict(used, budget_mb),
        "low_memory_suggested": 0 < memory["total_mb"] < 4096,
    }


def human(data: dict) -> str:
    memory = data["system"]
    lines = [
        f"RAM sistema: {memory['used_mb']:.0f} MB usati su {memory['total_mb']:.0f} MB "
        f"({memory['used_percent']:.0f}%), {memory['available_mb']:.0f} MB disponibili",
    ]
    if memory["swap_total_mb"]:
        lines.append(
            f"Swap: {memory['swap_used_mb']:.0f} MB usati su {memory['swap_total_mb']:.0f} MB"
        )
    lines.append(f"Processi della segreteria: {len(data['processes'])}")
    for item in data["processes"]:
        lines.append(f"  {item['rss_mb']:7.1f} MB  pid {item['pid']:<7} {item['cmd']}")
    lines.append(
        f"Totale segreteria: {data['process_total_mb']:.1f} MB  "
        f"({data['verdict']['detail']})"
    )
    if data["low_memory_suggested"]:
        lines.append("Suggerimento: macchina sotto i 4 GB, imposta \"low_memory\": true in config.json.")
    return "\n".join(lines)


def self_test() -> int:
    sample = (
        "MemTotal:        2048000 kB\n"
        "MemFree:          200000 kB\n"
        "MemAvailable:     800000 kB\n"
        "SwapTotal:       1048576 kB\n"
        "SwapFree:         524288 kB\n"
    )
    parsed = parse_meminfo(sample)
    assert parsed["total_mb"] == 2000, parsed
    assert parsed["available_mb"] == 781, parsed
    assert parsed["swap_used_mb"] == 512, parsed
    assert 0 < parsed["used_percent"] < 100, parsed
    assert parse_meminfo("") == {
        "total_mb": 0, "available_mb": 0, "used_mb": 0,
        "swap_total_mb": 0, "swap_used_mb": 0, "used_percent": 0.0,
    }
    assert verdict(500, 1000)["status"] == "ok"
    assert verdict(850, 1000)["status"] == "near"
    assert verdict(1200, 1000)["status"] == "over"
    data = report()
    assert isinstance(data["processes"], list), data
    assert data["budget_mb"] == DEFAULT_BUDGET_MB
    assert "MB" in human(data)
    print("SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Impronta di memoria della segreteria")
    parser.add_argument("--json", action="store_true", help="stampa JSON")
    parser.add_argument("--budget-mb", type=float, default=DEFAULT_BUDGET_MB,
                        help=f"budget RAM dei processi (default {DEFAULT_BUDGET_MB} MB)")
    parser.add_argument("--self-test", action="store_true", help="test interni")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    data = report(args.budget_mb)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(human(data))
    return 0 if data["verdict"]["status"] != "over" else 1


if __name__ == "__main__":
    raise SystemExit(main())
