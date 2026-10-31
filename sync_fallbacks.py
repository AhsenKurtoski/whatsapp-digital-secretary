#!/usr/bin/env python3
"""Allinea i fallback del gateway alle chiavi presenti.

Legge ~/.openclaw/secretary/secrets.env e aggiorna openclaw.json:
- con una chiave Gemini usa google/gemini-3.5-flash come motore principale
  (contesto ampio e quota generosa) e mette Groq nei fallback;
- con GROQ_API_KEY_2..7 crea i provider groq2..groq7;
- senza una chiave rimuove i fallback e i provider corrispondenti.

Non stampa mai il valore delle chiavi.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import config

HOME = os.path.expanduser("~")
BASE = str(config.BASE)
SECRETS = os.path.join(BASE, "secrets.env")
CONFIG = str(config.OPENCLAW_CONFIG)

GEMINI_PRIMARY = "google/gemini-3.5-flash"
GEMINI_MODELS = [
    "google/gemini-3.5-flash",
    "google/gemini-3.5-flash-lite",
    "google/gemini-3.8-flash",
    "google/gemini-2.5-flash-lite",
]
GROQ_PRIMARY = "groq/openai/gpt-oss-120b"
GROQ_BACKUP_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
QWEN_MODEL = "groq/qwen/qwen3.8-27b"
MAX_POOL_KEYS = 7
MANAGED_PREFIXES = ("google/", "groq/llama-", "groq2/", "groq3/", "groq4/", "groq5/", "groq6/", "groq7/")


def read_secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(SECRETS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def read_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("openclaw.json non è un oggetto JSON")
    return data


def write_config(data: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    if os.path.exists(CONFIG):
        shutil.copy2(CONFIG, CONFIG + ".bak")
    fd, tmp = tempfile.mkstemp(prefix=".openclaw-", suffix=".json", dir=os.path.dirname(CONFIG))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pool_values(env: dict[str, str], kind: str) -> list[str]:
    if kind == "gemini":
        values = [env.get("GEMINI_API_KEY", "")]
        values += [env.get(f"GEMINI_API_KEY_{i}", "") for i in range(2, MAX_POOL_KEYS + 1)]
    else:
        values = [env.get(f"GROQ_API_KEY_{i}", "") for i in range(2, MAX_POOL_KEYS + 1)]
    out: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def main() -> int:
    env = read_secrets()
    gemini_keys = pool_values(env, "gemini")
    groq_keys = pool_values(env, "groq")
    has_gemini = bool(gemini_keys)
    has_groq2 = bool(groq_keys)

    try:
        cfg = read_config()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"config non leggibile: {exc}"}))
        return 1

    agents = cfg.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    model = defaults.setdefault("model", {})
    current_primary = str(model.get("primary") or "")
    if has_gemini:
        # Gemini 3.5 Flash è stabile e veloce; regge il contesto della
        # segreteria. Groq gratuito ha 8.000 token/minuto e andava in 413.
        primary = GEMINI_PRIMARY
    elif not current_primary or current_primary.startswith("google/"):
        primary = GROQ_PRIMARY
    else:
        primary = current_primary
    model["primary"] = primary

    current = model.get("fallbacks")
    current = [str(x) for x in current] if isinstance(current, list) else []
    kept = [x for x in current if not str(x).startswith(MANAGED_PREFIXES)]

    managed: list[str] = []
    if has_gemini:
        managed.extend(GEMINI_MODELS)
    for i in range(2, 2 + len(groq_keys)):
        managed.extend([f"groq{i}/{model_id}" for model_id in GROQ_BACKUP_MODELS])
    managed.append(GROQ_PRIMARY)
    managed.append("groq/openai/gpt-oss-20b")
    managed.append(QWEN_MODEL)

    out: list[str] = []
    for ref in managed + kept:
        if ref != primary and ref not in out:
            out.append(ref)
    model["fallbacks"] = out

    providers = cfg.setdefault("models", {}).setdefault("providers", {})
    for idx in range(2, MAX_POOL_KEYS + 1):
        name = f"groq{idx}"
        if idx < 2 + len(groq_keys):
            providers[name] = {
                "baseUrl": "https://api.groq.com/openai/v1",
                "apiKey": f"${{GROQ_API_KEY_{idx}}}",
                "api": "openai-completions",
                "models": [
                    {
                        "id": model_id,
                        "name": f"Groq Backup {idx} " + ("GPT-OSS 120B" if model_id.endswith("120b") else "GPT-OSS 20B"),
                        "contextWindow": 131072,
                        "maxTokens": 600,
                        "reasoning": False,
                        "input": ["text"],
                    }
                    for model_id in GROQ_BACKUP_MODELS
                ],
            }
        else:
            providers.pop(name, None)

    try:
        write_config(cfg)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"config non scrivibile: {exc}"}))
        return 1

    print(json.dumps({
        "ok": True,
        "gemini_keys": len(gemini_keys),
        "groq_backup_keys": len(groq_keys),
        "primary": primary,
        "fallbacks": out,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
