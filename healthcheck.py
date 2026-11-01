#!/usr/bin/env python3
"""Scansione completa della segreteria e avvisi di scadenza.

Il comando viene eseguito dal timer systemd ogni 30 minuti e dalla dashboard.
Controlla configurazione, persona, segreti, gateway, WhatsApp, dashboard,
timer, Google (OAuth e bridge), MCP, chiavi AI, coda approvazioni, disco, log e
avvio Windows. Non stampa mai token, chiavi o contenuti dei clienti.

Uso:
    python3 healthcheck.py --json
    python3 healthcheck.py --notify
    python3 healthcheck.py --notify --dry-run
    python3 healthcheck.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "google-bridge"))

import config

VERSION = "1.1.0"
HOME = os.path.expanduser("~")
CFG = config.load()
BASE = Path(config.BASE)
CONFIG = Path(config.OPENCLAW_CONFIG)
SECRETS = BASE / "secrets.env"
TOKEN_FILE = BASE / "dashboard_token"
WATCHDOG_STATE = BASE / "watchdog_state.json"
HEALTH_STATE = BASE / "health_state.json"
ALERT_STATE = BASE / "health_alerts_state.json"
BRIDGE_CONFIG = BASE / "google_bridge.json"
LOG_FILES = [BASE / "watchdog.log", BASE / "dashboard.log", BASE / "monitor.log"]
ROME = config.resolve_timezone()
GOG = str(config.gog_bin())
OWNER = str(CFG.get("owner_number") or "").strip()
MCP_SERVER = "google-workspace"

STATUS_RANK = {"ok": 0, "info": 1, "warn": 2, "critical": 3}
STATUS_ICON = {"ok": "✅", "info": "ℹ️", "warn": "🟡", "critical": "🔴"}

# Windows consoles can be cp1252; never crash while printing a check.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass


def now_iso() -> str:
    return datetime.now(ROME).isoformat(timespec="seconds")


def log(msg: str) -> None:
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with open(BASE / "healthcheck.log", "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
        path = BASE / "healthcheck.log"
        if path.stat().st_size > 512_000:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _run(cmd, timeout: int = 20, extra_env: dict | None = None):
    env = os.environ.copy()
    if hasattr(os, "getuid"):
        uid = os.getuid()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _write_json(path: Path, data: dict) -> None:
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception as exc:
        log(f"state write failed: {str(exc)[:160]}")


def _json_from(text: str) -> dict:
    if not text:
        return {}
    start = text.find("{")
    if start < 0:
        return {}
    try:
        data = json.loads(text[start:])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_dt(value):
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except Exception:
        return None


def _http_json(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "DigitalSecretaryHealth/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(256_000)
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _http_status(url: str, timeout: int = 6) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DigitalSecretaryHealth/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def _check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    action: str = "",
    expires: str = "",
    group: str = "core",
    token: dict | None = None,
    meta: dict | None = None,
) -> dict:
    status = status if status in STATUS_RANK else "warn"
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": str(detail or "")[:900],
        "action": str(action or "")[:500],
        "expires": str(expires or "")[:120],
        "group": group,
        "token": token,
        "meta": meta or {},
    }


def overall(checks: list[dict]) -> str:
    worst = "ok"
    for item in checks:
        status = str(item.get("status") or "warn")
        # "info" is context, not a problem: an optional bridge not configured
        # must not turn the whole dashboard yellow.
        if status not in {"warn", "critical"}:
            continue
        if STATUS_RANK.get(status, 2) > STATUS_RANK.get(worst, 0):
            worst = status
    return worst


def secret_values() -> dict:
    out: dict[str, str] = {}
    try:
        with open(SECRETS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def gog_env() -> dict:
    env = secret_values()
    out = {"GOG_KEYRING_BACKEND": "file"}
    if env.get("GOG_KEYRING_PASSWORD"):
        out["GOG_KEYRING_PASSWORD"] = env["GOG_KEYRING_PASSWORD"]
    return out


def pool_count(env: dict, kind: str) -> int:
    values = []
    if kind == "gemini":
        values.append(env.get("GEMINI_API_KEY", ""))
        values.extend(env.get(f"GEMINI_API_KEY_{i}", "") for i in range(2, 8))
    else:
        values.extend(env.get(f"GROQ_API_KEY_{i}", "") for i in range(2, 8))
    return len({str(value).strip() for value in values if str(value).strip()})


def _systemctl_active(unit: str) -> bool:
    return _run(["systemctl", "--user", "is-active", unit], timeout=8)[1] == "active"


def _systemctl_next(unit: str) -> str:
    out = _run(
        ["systemctl", "--user", "show", unit, "-p", "NextElapseUSecRealtime", "--value"],
        timeout=8,
    )[1]
    return out.strip() or "-"


def _uptime_text() -> str:
    pid = _run(
        ["systemctl", "--user", "show", "openclaw-gateway", "-p", "MainPID", "--value"],
        timeout=8,
    )[1].strip()
    if not pid.isdigit() or int(pid) <= 0:
        return "-"
    raw = _run(["ps", "-p", pid, "-o", "etimes="], timeout=8)[1].strip()
    try:
        seconds = int(raw)
    except Exception:
        return "-"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}g {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def check_config() -> dict:
    path = Path(config.DEFAULT_CONFIG_PATH)
    if not path.exists():
        return _check(
            "config",
            "Configurazione attività",
            "critical",
            f"File non trovato: {path}",
            "Esegui: python3 setup.py",
        )
    required = ("business_name", "owner_name", "bot_name", "owner_number")
    missing = [key for key in required if not str(CFG.get(key) or "").strip()]
    if missing:
        return _check(
            "config",
            "Configurazione attività",
            "critical",
            "Campi mancanti: " + ", ".join(missing),
            "Completa la configurazione dalla dashboard o con setup.py",
        )
    placeholders = []
    if str(CFG.get("business_name") or "").strip() in {"Nome Attività", "Nome Attivita"}:
        placeholders.append("business_name")
    if str(CFG.get("owner_name") or "").strip() in {"Titolare", "Owner"}:
        placeholders.append("owner_name")
    if placeholders:
        return _check(
            "config",
            "Configurazione attività",
            "warn",
            "Valori demo ancora presenti: " + ", ".join(placeholders),
            "Sostituisci i dati demo con quelli reali della tua attività",
        )
    warnings = []
    if not str(CFG.get("google_account") or "").strip():
        warnings.append("google_account vuoto")
    if not str(CFG.get("owner_number") or "").startswith("+"):
        warnings.append("owner_number senza prefisso internazionale")
    detail = "Configurazione presente; numero admin e account Google impostati."
    if warnings:
        detail = "Configurazione presente; " + ", ".join(warnings) + "."
    return _check(
        "config",
        "Configurazione attività",
        "warn" if warnings else "ok",
        detail,
        "Completa i campi segnalati" if warnings else "",
    )


def check_persona() -> dict:
    path = Path(config.workspace_dir()) / "AGENTS.md"
    if not path.exists():
        return _check(
            "persona",
            "Persona del bot",
            "critical",
            f"File non trovato: {path}",
            "Esegui: python3 setup.py",
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _check("persona", "Persona del bot", "critical", f"Non leggibile: {exc}")
    if "{{" in text:
        return _check(
            "persona",
            "Persona del bot",
            "warn",
            "La persona contiene segnaposto non renderizzati.",
            "Riesegui: python3 setup.py",
        )
    if len(text.strip()) < 200:
        return _check(
            "persona",
            "Persona del bot",
            "warn",
            "La persona è molto corta: il bot potrebbe non avere abbastanza contesto.",
        )
    return _check("persona", "Persona del bot", "ok", f"Persona attiva ({len(text)} caratteri).")


def check_secrets() -> dict:
    if not SECRETS.exists():
        return _check(
            "secrets",
            "Segreti e chiavi AI",
            "critical",
            "File segreti non trovato.",
            "Salva almeno una chiave AI dalla scheda Chiavi",
        )
    try:
        mode = stat.S_IMODE(os.stat(SECRETS).st_mode)
    except Exception:
        mode = 0
    env = secret_values()
    gemini = pool_count(env, "gemini")
    groq = pool_count(env, "groq")
    total = gemini + groq
    if total == 0:
        return _check(
            "secrets",
            "Segreti e chiavi AI",
            "critical",
            "Nessuna chiave AI configurata.",
            "Apri la scheda Chiavi e salva una chiave Gemini o Groq",
        )
    perm_warn = bool(mode & 0o077) if os.name != "nt" else False
    warnings = []
    if perm_warn:
        warnings.append(f"permessi {oct(mode)} invece di 600")
    if total == 1:
        warnings.append("una sola chiave: possibili 429 sotto carico")
    detail = f"{gemini} Gemini · {groq} Groq backup. Nessun valore mostrato."
    if warnings:
        detail += " " + "; ".join(warnings) + "."
    return _check(
        "secrets",
        "Segreti e chiavi AI",
        "warn" if warnings else "ok",
        detail,
        "Aggiungi una seconda chiave o correggi i permessi" if warnings else "",
        token={
            "id": "ai_keys",
            "label": "Chiavi AI Gemini/Groq",
            "status": "warn" if warnings else "ok",
            "expires": "nessuna scadenza (solo quota)",
            "detail": detail,
            "action": "",
        },
    )


def check_gateway() -> dict:
    active = _systemctl_active("openclaw-gateway.service")
    health = _http_status("http://127.0.0.1:18789/health", 4) == 200
    if not active:
        return _check(
            "gateway",
            "Gateway OpenClaw",
            "critical",
            "Il servizio gateway non è attivo.",
            "Riavvia dalla dashboard o esegui: systemctl --user restart openclaw-gateway",
        )
    if not health:
        return _check(
            "gateway",
            "Gateway OpenClaw",
            "critical",
            "Servizio attivo ma /health non risponde.",
            "Controlla i log del gateway e riavvialo",
        )
    return _check("gateway", "Gateway OpenClaw", "ok", f"Attivo e sano · uptime {_uptime_text()}.")


def check_dashboard() -> dict:
    active = _systemctl_active("openclaw-dashboard.service")
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(CFG.get("dashboard_port") or 8787)
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    token_ok = TOKEN_FILE.exists() and TOKEN_FILE.stat().st_size >= 16
    mode = stat.S_IMODE(os.stat(TOKEN_FILE).st_mode) if TOKEN_FILE.exists() else 0
    http = _http_status(f"http://127.0.0.1:{port}/", 5)
    if not active:
        return _check(
            "dashboard",
            "Dashboard locale",
            "critical",
            "Il servizio dashboard non è attivo.",
            "Riavvia dalla dashboard o esegui: systemctl --user restart openclaw-dashboard",
        )
    if not loopback:
        return _check(
            "dashboard",
            "Dashboard locale",
            "critical",
            f"Ascolta su {host}:{port}, non solo su localhost.",
            "Imposta DASHBOARD_HOST=127.0.0.1 e riavvia",
        )
    if not token_ok:
        return _check(
            "dashboard",
            "Dashboard locale",
            "critical",
            "Token della dashboard mancante.",
            "Riavvia la dashboard per rigenerarlo",
        )
    if http != 200:
        return _check(
            "dashboard",
            "Dashboard locale",
            "warn",
            f"Pagina di login non risponde (HTTP {http or 'timeout'}).",
            "Riavvia openclaw-dashboard",
        )
    token_warn = os.name != "nt" and bool(mode & 0o077)
    detail = f"Attiva su {host}:{port}; token presente."
    if token_warn:
        detail += f" Permessi token {oct(mode)} invece di 600."
    return _check(
        "dashboard",
        "Dashboard locale",
        "warn" if token_warn else "ok",
        detail,
        "Correggi i permessi del token" if token_warn else "",
        token={
            "id": "dashboard_token",
            "label": "Token dashboard",
            "status": "warn" if token_warn else "ok",
            "expires": "nessuna scadenza (cookie 7 giorni)",
            "detail": detail,
            "action": "",
        },
    )


def check_whatsapp() -> dict:
    state = _read_json(WATCHDOG_STATE, {})
    last = state.get("last_check")
    if not last:
        return _check(
            "whatsapp",
            "Connessione WhatsApp",
            "warn",
            "Il watchdog non ha ancora salvato uno stato.",
            "Attendi due minuti o esegui watchdog.py",
        )
    stamp = _parse_dt(last)
    age = int((datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()) if stamp else 9999
    result = state.get("last_result") or {}
    wa = str(result.get("whatsapp") or "unknown")
    healthy = wa in {"healthy", "connected", "ready"}
    if healthy and age <= 300:
        return _check(
            "whatsapp",
            "Connessione WhatsApp",
            "ok",
            f"Connesso · ultimo controllo {age}s fa.",
            token={
                "id": "whatsapp",
                "label": "Sessione WhatsApp",
                "status": "ok",
                "expires": "nessuna scadenza fissa (si riconnette)",
                "detail": "Sessione attiva e healthy.",
                "action": "",
            },
        )
    if healthy and age > 300:
        return _check(
            "whatsapp",
            "Connessione WhatsApp",
            "warn",
            f"Stato healthy ma watchdog fermo da {age // 60} minuti.",
            "Controlla openclaw-watchdog.timer",
        )
    return _check(
        "whatsapp",
        "Connessione WhatsApp",
        "critical",
        f"Stato {wa}; il watchdog riprova la riconnessione.",
        "Controlla il telefono del bot e i log del gateway",
        token={
            "id": "whatsapp",
            "label": "Sessione WhatsApp",
            "status": "critical",
            "expires": "da riconnettere",
            "detail": f"Stato {wa}.",
            "action": "Controlla il telefono del bot",
        },
    )


def check_timers() -> dict:
    units = {
        "monitor": "openclaw-secretary-monitor.timer",
        "watchdog": "openclaw-watchdog.timer",
        "digest": "openclaw-secretary-digest.timer",
        "health": "openclaw-secretary-health.timer",
    }
    states = {key: _systemctl_active(unit) for key, unit in units.items()}
    inactive = [key for key, active in states.items() if not active]
    if inactive:
        return _check(
            "timers",
            "Timer automatici",
            "critical",
            "Timer non attivi: " + ", ".join(inactive),
            "Esegui: bash scripts/install.sh",
            meta={"states": states},
        )
    return _check(
        "timers",
        "Timer automatici",
        "ok",
        "Monitor, watchdog, digest e health attivi.",
        meta={"states": states, "next": _systemctl_next(units["health"])},
    )


def check_approvals() -> dict:
    try:
        import approval_store
    except Exception as exc:
        return _check("approvals", "Coda approvazioni", "warn", f"Modulo non disponibile: {exc}")
    try:
        data = approval_store.list_requests(500)
    except Exception as exc:
        return _check("approvals", "Coda approvazioni", "warn", f"Non leggibile: {exc}")
    pending = data.get("pending") or []
    recent = data.get("recent") or []
    failed = [item for item in recent if item.get("status") == "failed"]
    oldest = None
    for item in pending:
        stamp = _parse_dt(item.get("ts") or item.get("created_at"))
        if stamp and (oldest is None or stamp < oldest):
            oldest = stamp
    age_hours = 0
    if oldest:
        age_hours = int((datetime.now(timezone.utc) - oldest.astimezone(timezone.utc)).total_seconds() // 3600)
    if len(pending) >= 10 or age_hours >= 24 or failed:
        detail = f"{len(pending)} in attesa"
        if age_hours >= 24:
            detail += f"; la più vecchia da {age_hours}h"
        if failed:
            detail += f"; {len(failed)} fallite recenti"
        return _check(
            "approvals",
            "Coda approvazioni",
            "warn",
            detail + ".",
            "Apri Approvazioni e gestisci le richieste",
            meta={"pending": len(pending), "failed": len(failed), "age_hours": age_hours},
        )
    return _check(
        "approvals",
        "Coda approvazioni",
        "ok",
        f"{len(pending)} in attesa; nessuna bloccata.",
        meta={"pending": len(pending), "failed": 0, "age_hours": 0},
    )


def check_disk() -> dict:
    try:
        usage = shutil.disk_usage(str(BASE))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
    except Exception as exc:
        return _check("disk", "Spazio disco", "warn", f"Non verificabile: {exc}")
    status = "critical" if free_gb < 0.2 else ("warn" if free_gb < 1 else "ok")
    return _check(
        "disk",
        "Spazio disco",
        status,
        f"{free_gb:.1f} GB liberi su {total_gb:.0f} GB.",
        "Libera spazio" if status != "ok" else "",
        meta={"free_gb": round(free_gb, 2)},
    )


def check_logs() -> dict:
    tracebacks = 0
    errors = 0
    for path in LOG_FILES:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-300:]
        except Exception:
            continue
        for line in lines:
            if "Traceback (most recent call last)" in line:
                tracebacks += 1
            if re.search(r"\b(CRITICAL|ERROR)\b", line):
                errors += 1
    if tracebacks:
        return _check(
            "logs",
            "Log recenti",
            "warn",
            f"{tracebacks} traceback e {errors} errori nelle ultime righe dei log.",
            "Apri Sistema → Log e controlla il primo errore",
        )
    return _check(
        "logs",
        "Log recenti",
        "ok",
        f"Nessun traceback; {errors} righe di errore normali nei log.",
    )


def check_runtime() -> dict:
    node = Path(config.node_bin())
    cli = Path(config.openclaw_cli())
    if not node.exists():
        return _check("runtime", "Runtime Node/OpenClaw", "critical", f"Node non trovato: {node}")
    if not cli.exists() and not shutil.which("openclaw"):
        return _check("runtime", "Runtime Node/OpenClaw", "critical", "CLI OpenClaw non trovata.")
    rc, out, _ = _run([str(node), "--version"], timeout=8)
    return _check(
        "runtime",
        "Runtime Node/OpenClaw",
        "ok" if rc == 0 else "warn",
        f"Node {out or 'ok'} · CLI OpenClaw raggiungibile.",
    )


def check_ai() -> dict:
    env = secret_values()
    gemini = pool_count(env, "gemini")
    groq = pool_count(env, "groq")
    cfg = _read_json(CONFIG, {})
    model = (((cfg.get("agents") or {}).get("defaults") or {}).get("model") or {})
    primary = str(model.get("primary") or "-")
    fallbacks = model.get("fallbacks") or []
    total = gemini + groq
    if total == 0:
        return _check(
            "ai",
            "Provider AI",
            "critical",
            "Nessuna chiave AI: il bot non può rispondere.",
            "Apri Chiavi e configura Gemini o Groq",
        )
    detail = f"{gemini} Gemini · {groq} Groq backup · primario {primary}."
    status = "warn" if total == 1 else "ok"
    return _check(
        "ai",
        "Provider AI",
        status,
        detail,
        "Aggiungi una chiave di fallback" if status == "warn" else "",
    )


def check_google_oauth() -> dict:
    account = str(CFG.get("google_account") or "ACCOUNT")
    if not (os.path.isfile(GOG) and os.access(GOG, os.X_OK)):
        return _check(
            "google_oauth",
            "Google OAuth (gog)",
            "critical",
            f"CLI gog non trovata in {GOG}.",
            "Installa gog o attiva il bridge Apps Script",
            token={
                "id": "google_oauth",
                "label": "Google OAuth (gog)",
                "status": "critical",
                "expires": "non disponibile",
                "detail": "CLI gog non installata.",
                "action": "Attiva il bridge Apps Script",
            },
        )
    rc, out, err = _run([GOG, "auth", "list", "--json"], timeout=20, extra_env=gog_env())
    accounts = (_json_from(out).get("accounts") or []) if rc == 0 else []
    created = _parse_dt(accounts[0].get("created_at")) if accounts else None
    expires = created + timedelta(days=7) if created else None
    days_left = None
    if expires:
        days_left = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    probe_rc, probe_out, probe_err = _run(
        [GOG, "--json", "gmail", "labels", "list"],
        timeout=25,
        extra_env=gog_env(),
    )
    probe_text = (probe_out + "\n" + probe_err).lower()
    probe_data = _json_from(probe_out)
    probe_error = str(probe_data.get("error") or "")
    valid = probe_rc == 0 and not probe_error
    expired = any(
        marker in (probe_text + " " + probe_error.lower())
        for marker in ("invalid_grant", "expired or revoked", "token expired")
    )
    if expired:
        detail = "Token OAuth scaduto o revocato (invalid_grant): Gmail/Calendar via gog non funzionano."
        status = "critical"
    elif not accounts:
        detail = "Nessun account Google autorizzato per gog."
        status = "critical"
    elif not valid:
        detail = "Token non verificabile in questo momento: " + (probe_error or probe_err or "errore API")[:180]
        status = "warn"
    elif days_left is not None and days_left <= 2:
        detail = f"Token valido, ma la stima Testing scade tra {max(0, int(days_left))} giorni."
        status = "warn"
    else:
        detail = "Token OAuth valido."
        status = "ok"
    if created:
        detail += f" Autorizzato il {created.astimezone(ROME).strftime('%d/%m %H:%M')}."
    if expires:
        detail += f" Stima scadenza {expires.astimezone(ROME).strftime('%d/%m %H:%M')} (app Testing)."
    return _check(
        "google_oauth",
        "Google OAuth (gog)",
        status,
        detail,
        "Riautorizza gog oppure attiva il bridge Apps Script",
        expires=expires.astimezone(ROME).strftime("%d/%m/%Y %H:%M") if expires else "sconosciuta",
        token={
            "id": "google_oauth",
            "label": "Google OAuth (gog)",
            "status": status,
            "expires": expires.astimezone(ROME).strftime("%d/%m/%Y %H:%M") if expires else "sconosciuta",
            "detail": detail,
            "action": f"gog-secretary auth add {account} --services gmail,calendar --gmail-scope read-send --manual",
        },
        meta={"valid": valid, "expired": expired, "days_left": days_left, "created": created.isoformat() if created else None},
    )


def check_google_bridge() -> dict:
    try:
        import bridge_client
    except Exception as exc:
        return _check(
            "google_bridge",
            "Bridge Google Apps Script",
            "warn",
            f"Client bridge non disponibile: {str(exc)[:140]}",
            "Riesegui l'installer",
            meta={"configured": False},
        )
    try:
        bridge = bridge_client.load_config(str(BRIDGE_CONFIG))
    except Exception as exc:
        return _check(
            "google_bridge",
            "Bridge Google Apps Script",
            "warn",
            f"Configurazione bridge non valida: {str(exc)[:160]}",
            "Controlla google_bridge.json",
            meta={"configured": False},
        )
    if not bridge:
        return _check(
            "google_bridge",
            "Bridge Google Apps Script",
            "info",
            "Bridge non configurato (opzionale se OAuth funziona).",
            "Attivalo per evitare la scadenza dei 7 giorni",
            expires="nessuna scadenza",
            token={
                "id": "google_bridge",
                "label": "Bridge Google Apps Script",
                "status": "info",
                "expires": "nessuna scadenza (HMAC)",
                "detail": "Non configurato.",
                "action": "bash ~/.openclaw/secretary/google-bridge/installa-bridge.sh",
            },
            meta={"configured": False},
        )
    url = str(bridge.get("url") or "")
    health_url = url + ("&" if "?" in url else "?") + "action=health"
    try:
        data = _http_json(health_url, 10)
        healthy = data.get("ok") is True
        detail = "Bridge configurato e raggiungibile." if healthy else "Risposta health non valida."
    except Exception as exc:
        healthy = False
        detail = "Bridge non raggiungibile: " + str(exc)[:160]
    return _check(
        "google_bridge",
        "Bridge Google Apps Script",
        "ok" if healthy else "warn",
        detail,
        "" if healthy else "Verifica il deployment Apps Script e l'autorizzazione Google",
        expires="nessuna scadenza (HMAC)",
        token={
            "id": "google_bridge",
            "label": "Bridge Google Apps Script",
            "status": "ok" if healthy else "warn",
            "expires": "nessuna scadenza (HMAC)",
            "detail": detail,
            "action": "" if healthy else "Verifica Apps Script",
        },
        meta={"configured": True, "healthy": healthy},
    )


def check_mcp() -> dict:
    rc, out, err = _run(
        [config.node_bin(), config.openclaw_cli(), "mcp", "probe", MCP_SERVER, "--json"],
        timeout=25,
        extra_env=gog_env(),
    )
    data = _json_from(out)
    server = (data.get("servers") or {}).get(MCP_SERVER) or {}
    tools = [str(x).split("__", 1)[-1] for x in (data.get("tools") or [])]
    ok = bool(server) and not (server.get("error") or "")
    if ok:
        return _check(
            "mcp",
            "MCP Google Workspace",
            "ok",
            f"Connesso · {len(tools)} tool disponibili.",
            meta={"tools": len(tools)},
        )
    detail = str((server or {}).get("error") or err or "probe non riuscito")[:180]
    return _check(
        "mcp",
        "MCP Google Workspace",
        "warn",
        "Non connesso: " + detail,
        "Verifica Google OAuth o attiva il bridge",
    )


def check_site() -> dict:
    url = str(CFG.get("site_url") or "").strip()
    if not url:
        return _check("site", "Sito pubblico", "info", "Nessun site_url configurato.")
    if not url.startswith(("http://", "https://")):
        return _check("site", "Sito pubblico", "warn", "site_url non è un URL HTTP valido.")
    status = _http_status(url, 8)
    if status == 200:
        return _check(
            "site",
            "Sito pubblico",
            "ok",
            f"Risponde HTTP 200.",
            expires="certificato gestito dall'hosting",
            token={
                "id": "site",
                "label": "Sito pubblico / HTTPS",
                "status": "ok",
                "expires": "gestito da GitHub Pages",
                "detail": "HTTP 200.",
                "action": "",
            },
        )
    return _check(
        "site",
        "Sito pubblico",
        "warn",
        f"Risponde HTTP {status or 'timeout'}.",
        "Controlla l'hosting del sito",
        expires="certificato gestito dall'hosting",
    )


def check_windows_autostart() -> dict:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return _check(
            "windows",
            "Avvio automatico Windows",
            "info",
            "Non verificabile da questo ambiente (powershell.exe assente).",
        )
    command = "(Get-ScheduledTask -TaskName 'OpenClaw WSL Gateway' -ErrorAction SilentlyContinue).State"
    rc, out, err = _run([powershell, "-NoProfile", "-Command", command], timeout=12)
    state = (out or err).strip().splitlines()[-1].strip() if (out or err).strip() else ""
    if rc == 0 and state in {"Ready", "Running"}:
        return _check("windows", "Avvio automatico Windows", "ok", f"Task '{state}': riparte al login.")
    if rc == 0 and not state:
        return _check(
            "windows",
            "Avvio automatico Windows",
            "warn",
            "Task 'OpenClaw WSL Gateway' non trovato.",
            "Crea il task di avvio o avvia manualmente il gateway",
        )
    return _check(
        "windows",
        "Avvio automatico Windows",
        "warn",
        f"Task non verificabile: {state or err or 'errore'}",
        "Controlla l'Utilità di pianificazione di Windows",
    )


def _safe_check(fn) -> dict:
    try:
        return fn()
    except Exception as exc:
        label = fn.__name__.replace("check_", "").replace("_", " ").title()
        return _check(fn.__name__.replace("check_", ""), label, "warn", f"Controllo non eseguito: {str(exc)[:180]}")


def collect() -> dict:
    started = time.monotonic()
    checks = [
        _safe_check(check_config),
        _safe_check(check_persona),
        _safe_check(check_secrets),
        _safe_check(check_gateway),
        _safe_check(check_dashboard),
        _safe_check(check_whatsapp),
        _safe_check(check_timers),
        _safe_check(check_approvals),
        _safe_check(check_disk),
        _safe_check(check_logs),
        _safe_check(check_runtime),
        _safe_check(check_ai),
    ]
    external = [
        check_google_oauth,
        check_google_bridge,
        check_mcp,
        check_site,
        check_windows_autostart,
    ]
    with ThreadPoolExecutor(max_workers=5) as pool:
        checks.extend(pool.map(_safe_check, external))
    tokens = [item["token"] for item in checks if item.get("token")]
    data = {
        "version": VERSION,
        "generated_at": now_iso(),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "overall": overall(checks),
        "counts": {
            "ok": sum(1 for x in checks if x["status"] == "ok"),
            "info": sum(1 for x in checks if x["status"] == "info"),
            "warn": sum(1 for x in checks if x["status"] == "warn"),
            "critical": sum(1 for x in checks if x["status"] == "critical"),
        },
        "checks": checks,
        "tokens": tokens,
        "alerts": build_alerts(checks),
    }
    _write_json(HEALTH_STATE, data)
    return data


def build_alerts(checks: list[dict]) -> list[dict]:
    by = {str(item.get("id")): item for item in checks}
    alerts: list[dict] = []

    def add(key: str, severity: str, title: str, message: str, action: str = "") -> None:
        alerts.append({
            "key": key,
            "severity": severity,
            "title": title,
            "message": str(message)[:700],
            "action": str(action)[:320],
        })

    if by.get("config", {}).get("status") == "critical":
        add("config", "critical", "Configurazione incompleta", by["config"]["detail"], by["config"].get("action", ""))
    if by.get("secrets", {}).get("status") == "critical":
        add("secrets", "critical", "Chiavi AI mancanti", by["secrets"]["detail"], by["secrets"].get("action", ""))
    if by.get("runtime", {}).get("status") == "critical":
        add("runtime", "critical", "Runtime non disponibile", by["runtime"]["detail"], by["runtime"].get("action", ""))
    if by.get("gateway", {}).get("status") == "critical":
        add("gateway", "critical", "Gateway offline", by["gateway"]["detail"], by["gateway"].get("action", ""))
    if by.get("dashboard", {}).get("status") == "critical":
        add("dashboard", "critical", "Dashboard offline", by["dashboard"]["detail"], by["dashboard"].get("action", ""))
    if by.get("whatsapp", {}).get("status") == "critical":
        add("whatsapp", "critical", "WhatsApp non connesso", by["whatsapp"]["detail"], by["whatsapp"].get("action", ""))
    if by.get("timers", {}).get("status") == "critical":
        add("timers", "critical", "Timer automatici fermi", by["timers"]["detail"], by["timers"].get("action", ""))

    google = by.get("google_oauth", {})
    bridge = by.get("google_bridge", {})
    bridge_ok = bridge.get("status") == "ok"
    if google.get("status") == "critical":
        if bridge_ok:
            add(
                "google_oauth_expired",
                "warn",
                "Google OAuth scaduto · bridge attivo",
                "Il token gog è scaduto, ma Gmail/Calendar passano dal bridge Apps Script. Rinnova il token solo se vuoi il fallback.",
                "Il bridge è attivo: nessuna urgenza",
            )
        else:
            add(
                "google_oauth_expired",
                "critical",
                "Gmail/Calendar non funzionano",
                "Token Google scaduto o revocato e bridge Apps Script non configurato.",
                "Attiva il bridge: bash ~/.openclaw/secretary/google-bridge/installa-bridge.sh",
            )
    elif google.get("status") == "warn":
        add(
            "google_oauth_expiring",
            "warn",
            "Google OAuth in scadenza",
            google.get("detail", ""),
            "Riautorizza gog o attiva il bridge Apps Script",
        )
    if bridge.get("meta", {}).get("configured") and bridge.get("status") == "warn":
        add("google_bridge", "warn", "Bridge Google non risponde", bridge["detail"], bridge.get("action", ""))

    if by.get("mcp", {}).get("status") == "warn" and google.get("status") == "ok" and not bridge_ok:
        add("mcp", "warn", "MCP Google non connesso", by["mcp"]["detail"], by["mcp"].get("action", ""))
    if by.get("approvals", {}).get("status") == "warn":
        add("approvals", "warn", "Approvazioni da gestire", by["approvals"]["detail"], by["approvals"].get("action", ""))
    if by.get("disk", {}).get("status") in {"warn", "critical"}:
        add("disk", by["disk"]["status"], "Spazio disco basso", by["disk"]["detail"], by["disk"].get("action", ""))
    if by.get("logs", {}).get("status") == "warn":
        add("logs", "warn", "Errori nei log", by["logs"]["detail"], by["logs"].get("action", ""))
    if by.get("windows", {}).get("status") == "warn":
        add("windows", "warn", "Avvio automatico da verificare", by["windows"]["detail"], by["windows"].get("action", ""))
    return alerts


def alert_cooldown(alert: dict) -> int:
    key = str(alert.get("key") or "")
    severity = str(alert.get("severity") or "warn")
    if key.startswith("google"):
        return 21600
    if key == "whatsapp":
        return 3600
    if key in {"disk", "approvals", "logs", "windows"}:
        return 21600
    return 3600 if severity == "critical" else 43200


def render_message(data: dict, alerts: list[dict]) -> str:
    counts = data.get("counts") or {}
    stamp = _parse_dt(data.get("generated_at"))
    when = stamp.astimezone(ROME).strftime("%d/%m %H:%M") if stamp else now_iso()
    lines = [
        f"🩺 *Health Segreteria* · {when}",
        f"{counts.get('critical', 0)} critici · {counts.get('warn', 0)} avvisi · {counts.get('ok', 0)} ok",
        "",
    ]
    for item in alerts[:6]:
        icon = "🔴" if item.get("severity") == "critical" else "🟡"
        lines.append(f"{icon} *{item.get('title') or 'Problema'}*")
        lines.append(str(item.get("message") or ""))
        if item.get("action"):
            lines.append(f"→ {item['action']}")
        lines.append("")
    if not alerts:
        lines.append("✅ Nessun problema rilevato.")
    else:
        lines.append("✅ Gateway, dashboard e WhatsApp restano monitorati.")
    lines.append("Dashboard → Health per il report completo.")
    return "\n".join(lines)[:1800]


def send_whatsapp(text: str) -> bool:
    if not OWNER:
        log("alert skipped: owner_number missing")
        return False
    cmd = [
        config.node_bin(),
        config.openclaw_cli(),
        "message",
        "send",
        "--channel",
        "whatsapp",
        "--target",
        OWNER,
        "--message",
        text,
        "--json",
    ]
    rc, out, err = _run(cmd, timeout=90)
    if rc != 0:
        log(f"alert send failed rc={rc} err={str(err or out)[:160]}")
        return False
    data = _json_from(out)
    if data.get("ok") is False:
        log("alert send returned ok=false")
        return False
    return True


def _alert_state() -> dict:
    data = _read_json(ALERT_STATE, {})
    sent = data.get("sent")
    return {"sent": sent if isinstance(sent, dict) else {}}


def notify(data: dict | None = None, force: bool = False, summary: bool = False, dry_run: bool = False) -> int:
    data = data or collect()
    alerts = data.get("alerts") or []
    state = _alert_state()
    sent = state["sent"]
    now_ts = time.time()
    if summary:
        selected = alerts or [{
            "key": "summary",
            "severity": "info",
            "title": "Tutto in ordine",
            "message": "Nessun problema rilevato nella scansione completa.",
            "action": "",
        }]
    elif force:
        selected = alerts
    else:
        selected = []
        for item in alerts:
            last = sent.get(item["key"]) or {}
            last_ts = float(last.get("ts") or 0)
            last_sev = str(last.get("severity") or "")
            changed_to_worse = STATUS_RANK.get(item.get("severity"), 2) > STATUS_RANK.get(last_sev, 0)
            if changed_to_worse or now_ts - last_ts >= alert_cooldown(item):
                selected.append(item)
    if not selected:
        return 0
    text = render_message(data, selected)
    if dry_run:
        print(text)
        return 0
    if not send_whatsapp(text):
        return 1
    for item in selected:
        sent[item["key"]] = {"ts": now_ts, "severity": item.get("severity")}
    _write_json(ALERT_STATE, {
        "sent": sent,
        "last_notification": now_iso(),
        "last_keys": [item["key"] for item in selected],
    })
    log("alert sent keys=" + ",".join(item["key"] for item in selected))
    return 0


def human_summary(data: dict) -> str:
    counts = data.get("counts") or {}
    lines = [
        f"Health {data.get('overall', 'unknown').upper()} · {counts.get('critical', 0)} critici, {counts.get('warn', 0)} avvisi, {counts.get('ok', 0)} ok",
        f"Scansione: {data.get('generated_at')} ({data.get('duration_ms', 0)} ms)",
    ]
    for item in data.get("checks") or []:
        if item.get("status") in {"critical", "warn"}:
            lines.append(f"{STATUS_ICON.get(item['status'], '•')} {item.get('label')}: {item.get('detail')}")
    return "\n".join(lines)


def self_test() -> int:
    checks = [
        {"id": "a", "status": "ok"},
        {"id": "b", "status": "warn"},
    ]
    assert overall(checks) == "warn"
    assert overall([{"id": "c", "status": "critical"}, {"id": "d", "status": "warn"}]) == "critical"
    created = datetime(2026, 10, 25, 14, 53, 14, tzinfo=timezone.utc)
    assert created + timedelta(days=7) == datetime(2026, 11, 1, 14, 53, 14, tzinfo=timezone.utc)
    alerts = build_alerts([
        {"id": "google_oauth", "status": "critical", "detail": "token scaduto", "action": ""},
        {"id": "google_bridge", "status": "info", "detail": "non configurato", "meta": {"configured": False}},
    ])
    assert any(item["key"] == "google_oauth_expired" for item in alerts)
    message = render_message(
        {"generated_at": "2026-11-01T20:00:00+01:00", "counts": {"critical": 1, "warn": 0, "ok": 4}},
        alerts,
    )
    assert "Health Segreteria" in message and len(message) <= 1800
    assert alert_cooldown({"key": "google_oauth_expired", "severity": "critical"}) == 21600
    print("SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Health check della segreteria digitale")
    parser.add_argument("--json", action="store_true", help="stampa il report JSON")
    parser.add_argument("--notify", action="store_true", help="invia gli avvisi su WhatsApp")
    parser.add_argument("--summary", action="store_true", help="invia anche un riepilogo senza problemi")
    parser.add_argument("--force", action="store_true", help="ignora il cooldown degli avvisi")
    parser.add_argument("--dry-run", action="store_true", help="mostra l'avviso senza inviarlo")
    parser.add_argument("--self-test", action="store_true", help="esegue i test interni")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    data = collect()
    rc = 0
    if args.notify:
        rc = notify(data, force=args.force, summary=args.summary, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif not args.notify:
        print(human_summary(data))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
