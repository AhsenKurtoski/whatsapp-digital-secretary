#!/usr/bin/env python3
"""Configurazione condivisa della segreteria digitale.

Tutto ciò che riguarda l'attività vive in un unico file JSON:
`~/.config/digital-secretary/config.json` (percorso sovrascrivibile con
`SECRETARY_CONFIG`). Le variabili d'ambiente hanno precedenza sui valori
salvati, così i segreti possono restare fuori dal file di configurazione.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("SECRETARY_CONFIG", str(Path.home() / ".config" / "digital-secretary" / "config.json"))
).expanduser()
BASE = Path(os.environ.get("SECRETARY_BASE", str(Path.home() / ".openclaw" / "secretary"))).expanduser()
OPENCLAW_CONFIG = Path(os.environ.get("OPENCLAW_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json"))).expanduser()

DEFAULTS: dict[str, object] = {
    "business_name": "Nome Attività",
    "owner_name": "Titolare",
    "bot_name": "Segreteria Digitale",
    "owner_number": "",
    "google_account": "",
    "language": "it",
    "timezone": "Europe/Rome",
    "site_url": "",
    "city": "",
    "services": [
        "informazioni su prodotti e servizi",
        "preventivi e disponibilità",
        "prenotazioni e appuntamenti",
    ],
    "hours": "Lun–Ven 9:00–18:00",
    "pricing_note": "I prezzi e le condizioni li conferma il titolare.",
    "dashboard_port": 8787,
    "max_concurrent": 3,
    "max_reply_lines": 6,
    "whatsapp_number": "",
}

_ENV_KEYS = {
    "business_name": "BUSINESS_NAME",
    "owner_name": "OWNER_NAME",
    "bot_name": "BOT_NAME",
    "owner_number": "OWNER_NUMBER",
    "google_account": "GOOGLE_ACCOUNT",
    "language": "BOT_LANGUAGE",
    "timezone": "BOT_TIMEZONE",
    "site_url": "SITE_URL",
    "city": "BUSINESS_CITY",
    "hours": "BUSINESS_HOURS",
    "dashboard_port": "DASHBOARD_PORT",
}


def _read_file() -> dict:
    try:
        raw = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # malformed config: fail loudly, don't invent data
        raise SystemExit(f"Configurazione non valida ({DEFAULT_CONFIG_PATH}): {exc}") from exc


def load() -> dict:
    data = dict(DEFAULTS)
    data.update(_read_file())
    for key, env in _ENV_KEYS.items():
        value = os.environ.get(env)
        if value:
            data[key] = value
    try:
        data["dashboard_port"] = int(data.get("dashboard_port") or 8787)
    except (TypeError, ValueError):
        data["dashboard_port"] = 8787
    if not str(data.get("owner_number") or "").strip():
        data["owner_number"] = ""
    return data


def get(key: str, default=None):
    value = load().get(key, default)
    return default if value is None else value


def timezone_name() -> str:
    return str(get("timezone", "Europe/Rome"))


def resolve_timezone(name: str | None = None):
    """Restituisce il fuso configurato, con fallback sicuro su Windows.

    Windows non include il database IANA: senza il pacchetto ``tzdata``
    ``ZoneInfo("Europe/Rome")`` fallisce. In quel caso usiamo l'offset locale
    corrente del sistema, così l'app resta operativa; per date future oltre un
    cambio di ora legale installa ``tzdata``. Su Linux il database di sistema è
    presente, quindi una chiave davvero invalida continua a sollevare l'errore
    invece di essere nascosta.
    """
    key = str(name or timezone_name() or "Europe/Rome")
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        if available_timezones():
            raise
        return datetime.now().astimezone().tzinfo or dt_timezone.utc


def wa_link() -> str:
    digits = "".join(ch for ch in str(get("owner_number", "")) if ch.isdigit())
    return f"https://wa.me/{digits}" if digits else ""


def node_bin() -> str:
    env = os.environ.get("OPENCLAW_NODE")
    if env:
        return env
    found = shutil.which("node")
    if found:
        return found
    candidates = sorted(glob.glob(str(Path.home() / ".nvm" / "versions" / "node" / "*" / "bin" / "node")))
    return candidates[-1] if candidates else "/usr/bin/node"


def openclaw_cli() -> str:
    env = os.environ.get("OPENCLAW_CLI")
    if env:
        return env
    candidates = sorted(
        glob.glob(str(Path.home() / ".nvm" / "versions" / "node" / "*" / "lib" / "node_modules" / "openclaw" / "dist" / "index.js"))
    )
    if candidates:
        return candidates[-1]
    for root in ("/usr/local/lib/node_modules", "/usr/lib/node_modules", str(Path.home() / ".npm-global" / "lib" / "node_modules")):
        path = Path(root) / "openclaw" / "dist" / "index.js"
        if path.exists():
            return str(path)
    which = shutil.which("openclaw")
    return which or "openclaw"


def workspace_dir() -> Path:
    return Path(os.environ.get("SECRETARY_WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))).expanduser()


def db_path() -> str:
    return os.environ.get(
        "OPENCLAW_DB",
        str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "openclaw-agent.sqlite"),
    )


def gog_bin() -> str:
    return os.environ.get("GOG_BIN", str(Path.home() / ".local" / "bin" / "gog"))


def ensure_base() -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    try:
        BASE.chmod(0o700)
    except OSError:
        pass
    return BASE
