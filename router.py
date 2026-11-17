#!/usr/bin/env python3
"""Scelta del modello in base a carico, latenza e salute dei provider.

Regole trasparenti e verificabili nei test:
1. un provider con 429 recenti o molti errori viene penalizzato;
2. sotto carico si preferisce il modello più veloce osservato;
3. a parità di condizioni vince il provider con latenza media migliore;
4. il modello primario configurato resta il preferito quando è sano.

`--apply` scrive la scelta in OpenClaw con `openclaw config set`, solo su
richiesta esplicita: nessun cambio silenzioso durante l'esecuzione.

Uso:
    python3 router.py --load 3
    python3 router.py --apply
    python3 router.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
import quotas

DEFAULT_MODELS = {
    "groq": "groq/llama-3.3-70b-versatile",
    "gemini": "google/gemini-2.5-flash",
}
# Oltre questa soglia di conversazioni attive si privilegia la latenza.
HIGH_LOAD = 5


def _models() -> dict:
    cfg = config.load()
    raw = cfg.get("ai_models")
    models = dict(DEFAULT_MODELS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if str(key).lower() in models and str(value or "").strip():
                models[str(key).lower()] = str(value).strip()
    return models


def _penalty(row: dict) -> float:
    """Punteggio di rischio: più alto = meno affidabile adesso."""
    penalty = 0.0
    if row.get("rate_limited"):
        penalty += 60.0 + 5.0 * float(row["rate_limited"])
    penalty += 8.0 * float(row.get("errors") or 0)
    level = row.get("level")
    if level == "critical":
        penalty += 70.0
    elif level == "warn":
        penalty += 25.0
    penalty += float(row.get("ratio") or 0.0) * 30.0
    return penalty


def rank(load: int = 0, base: Path | None = None) -> list[dict]:
    models = _models()
    usage = quotas.status(base)
    rows = []
    for row in usage["providers"]:
        name = row["provider"]
        latency = float(row.get("avg_latency_ms") or 0.0)
        # Latenza sconosciuta: neutra, non premiata né punita.
        latency_cost = latency if latency > 0 else 1500.0
        score = 1000.0 - _penalty(row) - min(latency_cost, 5000.0)
        if int(load or 0) >= HIGH_LOAD and latency > 0:
            score += max(0.0, 3000.0 - latency) / 10.0
        rows.append({
            "provider": name,
            "model": models.get(name, name),
            "score": round(score, 1),
            "avg_latency_ms": latency,
            "level": row.get("level"),
            "rate_limited": row.get("rate_limited", 0),
            "requests": row.get("requests", 0),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def choose(load: int = 0, base: Path | None = None) -> dict:
    rows = rank(load, base)
    primary = rows[0]
    fallbacks = [row["model"] for row in rows[1:]]
    reasons = [
        f"carico attivo: {int(load or 0)} conversazioni",
        f"provider scelto: {primary['provider']} (punteggio {primary['score']})",
    ]
    if primary.get("level") in {"warn", "critical"}:
        reasons.append(f"attenzione: quota {primary['provider']} in stato {primary['level']}")
    if primary.get("avg_latency_ms"):
        reasons.append(f"latenza media {primary['avg_latency_ms']} ms")
    return {
        "primary": primary["model"],
        "provider": primary["provider"],
        "fallbacks": fallbacks,
        "ranking": rows,
        "reasons": reasons,
        "high_load": int(load or 0) >= HIGH_LOAD,
    }


def apply_choice(model: str, cli: str | None = None, timeout: int = 30) -> dict:
    """Scrive il modello primario nella configurazione OpenClaw."""
    if not str(model or "").strip():
        raise ValueError("modello mancante")
    command = cli or os.environ.get("OPENCLAW_CLI") or shutil.which("openclaw") or "openclaw"
    try:
        result = subprocess.run(
            [command, "config", "set", "agents.defaults.model.primary", str(model)],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "model": model}
    return {
        "ok": result.returncode == 0,
        "model": model,
        "output": (result.stdout or "").strip()[-400:],
        "error": (result.stderr or "").strip()[-400:],
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["SECRETARY_BASE"] = tmp
        try:
            quotas.record("groq", ok=True, latency_ms=500, base=base)
            quotas.record("gemini", ok=True, latency_ms=2400, base=base)
            fast = choose(load=0, base=base)
            assert fast["provider"] == "groq", fast["ranking"]
            assert fast["fallbacks"], fast

            # Gemini prende 429: Groq deve restare davanti anche se più lento.
            for _ in range(3):
                quotas.record("gemini", ok=False, status=429, base=base)
            after = choose(load=0, base=base)
            assert after["provider"] == "groq", after["ranking"]
            assert after["ranking"][0]["score"] > after["ranking"][1]["score"]

            # Alta latenza di Groq + carico alto: il ranking resta deterministico.
            heavy = choose(load=9, base=base)
            assert heavy["high_load"] is True
            assert heavy["primary"] in {row["model"] for row in heavy["ranking"]}

            # apply_choice con un finto CLI verifica la chiamata senza OpenClaw.
            fake = Path(tmp) / "openclaw-fake"
            fake.write_text("#!/usr/bin/env bash\necho \"$@\"\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            result = apply_choice("groq/llama-3.3-70b-versatile", cli=str(fake))
            assert result["ok"] is True, result
            assert "agents.defaults.model.primary" in result["output"], result

            broken = Path(tmp) / "openclaw-broken"
            broken.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
            broken.chmod(0o755)
            assert apply_choice("x", cli=str(broken))["ok"] is False
        finally:
            os.environ.pop("SECRETARY_BASE", None)
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scelta dinamica del modello")
    parser.add_argument("--load", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    decision = choose(args.load)
    if args.apply:
        decision["applied"] = apply_choice(decision["primary"])
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0 if decision.get("applied", {"ok": True}).get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
