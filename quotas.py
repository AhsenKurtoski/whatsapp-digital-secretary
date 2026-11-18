#!/usr/bin/env python3
"""Uso delle quote AI per provider, con soglie e alert proattivi.

I piani gratuiti di Groq e Gemini non espongono una API di quota affidabile:
qui si contano le richieste realmente fatte da questa installazione, i 429
ricevuti, la latenza e i token dichiarati dalla risposta. Le soglie sono
configurabili e l'healthcheck le legge per avvisare *prima* del blocco.

Uso:
    python3 quotas.py --json
    python3 quotas.py --record groq --ok --latency 820 --tokens 640
    python3 quotas.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import config

PROVIDERS = ("groq", "gemini")
DEFAULT_LIMITS = {"groq": 1000, "gemini": 1000}
DEFAULT_WARN = 0.8
DEFAULT_CRITICAL = 0.95


def _base(base: Path | None = None) -> Path:
    folder = Path(base or config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def store_path(base: Path | None = None) -> Path:
    return _base(base) / "ai_usage.json"


def _today() -> str:
    return datetime.now(config.resolve_timezone()).date().isoformat()


def _limits() -> dict:
    cfg = config.load()
    raw = cfg.get("ai_daily_limits")
    limits = dict(DEFAULT_LIMITS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                limits[str(key).lower()] = max(1, int(value))
            except (TypeError, ValueError):
                continue
    return limits


def _thresholds() -> tuple[float, float]:
    cfg = config.load()
    try:
        warn = float(cfg.get("ai_alert_ratio") or DEFAULT_WARN)
    except (TypeError, ValueError):
        warn = DEFAULT_WARN
    try:
        critical = float(cfg.get("ai_alert_critical_ratio") or DEFAULT_CRITICAL)
    except (TypeError, ValueError):
        critical = DEFAULT_CRITICAL
    warn = min(max(warn, 0.1), 1.0)
    critical = min(max(critical, warn), 1.0)
    return warn, critical


def _empty_provider() -> dict:
    return {
        "requests": 0,
        "ok": 0,
        "errors": 0,
        "rate_limited": 0,
        "tokens": 0,
        "latency_ms_sum": 0,
        "latency_count": 0,
        "last_error": "",
        "last_error_at": "",
        "last_request_at": "",
    }


def load(base: Path | None = None) -> dict:
    path = store_path(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    today = _today()
    if data.get("date") != today:
        # Giorno nuovo: conserva solo lo storico sintetico.
        raw_history = data.get("history")
        history = raw_history if isinstance(raw_history, list) else []
        data = {"date": today, "providers": {}, "history": history[-30:]}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        data["providers"] = providers
    for name in PROVIDERS:
        entry = providers.get(name)
        if not isinstance(entry, dict):
            providers[name] = _empty_provider()
        else:
            base_entry = _empty_provider()
            base_entry.update(entry)
            providers[name] = base_entry
    return data


def _save(data: dict, base: Path | None = None) -> None:
    path = store_path(base)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def record(provider: str, ok: bool = True, status: int | None = None,
           tokens: int | None = None, latency_ms: float | None = None,
           error: str = "", base: Path | None = None) -> dict:
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"provider non supportato: {provider}")
    with _guard():
        data = load(base)
        entry = data["providers"][provider]
        entry["requests"] += 1
        entry["last_request_at"] = datetime.now(config.resolve_timezone()).isoformat(timespec="seconds")
        if ok:
            entry["ok"] += 1
        else:
            entry["errors"] += 1
            entry["last_error"] = str(error or f"http {status}")[:200]
            entry["last_error_at"] = entry["last_request_at"]
        if status == 429:
            entry["rate_limited"] += 1
        if tokens:
            try:
                entry["tokens"] += max(0, int(tokens))
            except (TypeError, ValueError):
                pass
        if latency_ms is not None:
            try:
                entry["latency_ms_sum"] += max(0.0, float(latency_ms))
                entry["latency_count"] += 1
            except (TypeError, ValueError):
                pass
        _save(data, base)
        return entry


def status(base: Path | None = None, lang: str = "it") -> dict:
    data = load(base)
    limits = _limits()
    warn, critical = _thresholds()
    en = str(lang or "").strip().lower().startswith("en")
    rows = []
    alerts = []
    for name in PROVIDERS:
        entry = data["providers"][name]
        limit = limits.get(name, 1000)
        ratio = round(entry["requests"] / limit, 4) if limit else 0.0
        avg_latency = round(entry["latency_ms_sum"] / entry["latency_count"], 1) if entry["latency_count"] else 0.0
        level = "ok"
        if ratio >= critical or (entry["rate_limited"] and entry["requests"] >= 3):
            level = "critical"
        elif ratio >= warn or entry["rate_limited"]:
            level = "warn"
        row = {
            "provider": name,
            "requests": entry["requests"],
            "limit": limit,
            "ratio": ratio,
            "remaining": max(0, limit - entry["requests"]),
            "tokens": entry["tokens"],
            "rate_limited": entry["rate_limited"],
            "errors": entry["errors"],
            "avg_latency_ms": avg_latency,
            "level": level,
            "last_error": entry["last_error"],
        }
        rows.append(row)
        if level == "critical":
            if en:
                alerts.append(
                    f"{name} quota almost exhausted: {entry['requests']}/{limit} requests today"
                    + (f", {entry['rate_limited']} 429 responses" if entry["rate_limited"] else "")
                )
            else:
                alerts.append(
                    f"Quota {name} quasi esaurita: {entry['requests']}/{limit} richieste oggi"
                    + (f", {entry['rate_limited']} risposte 429" if entry["rate_limited"] else "")
                )
        elif level == "warn":
            alerts.append(
                f"{name} quota at {int(ratio * 100)}%: {entry['requests']}/{limit} requests today"
                if en else
                f"Quota {name} all'{int(ratio * 100)}%: {entry['requests']}/{limit} richieste oggi"
            )
    return {
        "date": data["date"],
        "providers": rows,
        "alerts": alerts,
        "thresholds": {"warn": warn, "critical": critical},
    }


class _guard:
    """Lock fra thread e processi, senza dipendenze esterne."""

    def __enter__(self):
        import time
        self._deadline = time.monotonic() + 5
        self._handle = None
        path = _base() / "ai_usage.lock"
        while True:
            try:
                self._handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                return self
            except FileExistsError:
                if time.monotonic() >= self._deadline:
                    return self  # meglio contare che bloccare una risposta
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self._handle is not None:
            try:
                os.close(self._handle)
                (_base() / "ai_usage.lock").unlink()
            except OSError:
                pass
        return False


def prune(days: int = 30, base: Path | None = None) -> dict:
    data = load(base)
    if len(data.get("history") or []) > days:
        data["history"] = data["history"][-days:]
        _save(data, base)
    return data


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["SECRETARY_BASE"] = tmp
        try:
            record("groq", ok=True, latency_ms=900, tokens=500, base=base)
            record("groq", ok=True, latency_ms=700, tokens=300, base=base)
            entry = record("groq", ok=False, status=429, error="rate limit", base=base)
            assert entry["requests"] == 3 and entry["rate_limited"] == 1, entry
            assert entry["tokens"] == 800, entry

            data = status(base)
            groq = next(p for p in data["providers"] if p["provider"] == "groq")
            assert groq["avg_latency_ms"] == 800.0, groq
            assert groq["level"] in {"warn", "critical"}, groq
            assert data["alerts"], data

            # Soglia critica configurabile: 1 richiesta su limite 1 = 100%.
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"ai_daily_limits": {"groq": 1}}), encoding="utf-8")
            os.environ["SECRETARY_CONFIG"] = str(cfg_path)
            config.DEFAULT_CONFIG_PATH = cfg_path
            critical = status(base)
            row = next(p for p in critical["providers"] if p["provider"] == "groq")
            assert row["level"] == "critical" and row["remaining"] == 0, row
        finally:
            os.environ.pop("SECRETARY_BASE", None)
            os.environ.pop("SECRETARY_CONFIG", None)
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Uso quote AI")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--record", default="")
    parser.add_argument("--ok", action="store_true")
    parser.add_argument("--status", type=int, default=None)
    parser.add_argument("--tokens", type=int, default=None)
    parser.add_argument("--latency", type=float, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.record:
        try:
            record(args.record, ok=args.ok or args.status in (None, 200), status=args.status,
                   tokens=args.tokens, latency_ms=args.latency)
        except ValueError as exc:
            print(f"quotas: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
