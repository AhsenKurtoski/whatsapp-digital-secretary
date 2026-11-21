#!/usr/bin/env python3
"""Dashboard privata della segreteria digitale.

Gira solo su 127.0.0.1. Richiede un token locale e un cookie HttpOnly.
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hmac
import importlib.util
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

HOME = os.path.expanduser("~")
CFG = config.load()
BASE = str(config.BASE)
CONFIG = str(config.OPENCLAW_CONFIG)
DB = config.db_path()
NODE = config.node_bin()
CLI = config.openclaw_cli()
TOKEN_FILE = os.path.join(BASE, "dashboard_token")
SECURITY_STATE = os.path.join(BASE, "security_state.json")
WATCHDOG_STATE = os.path.join(BASE, "watchdog_state.json")
HEALTH_STATE = os.path.join(BASE, "health_state.json")
SECRETS = os.path.join(BASE, "secrets.env")
GOG = config.gog_bin()
GOOGLE_ACCOUNT = str(CFG.get("google_account") or "")
MCP_SERVER = "google-workspace"
sys.path.insert(0, BASE)
try:
    import approval_store
except Exception:
    approval_store = None
try:
    import email_template
except Exception:
    email_template = None
try:
    import bridge_client
except Exception:
    bridge_client = None
try:
    import approval_mcp
except Exception:
    approval_mcp = None
try:
    import quote_pdf
except Exception:
    quote_pdf = None
try:
    import contacts as contacts_history
except Exception:
    contacts_history = None
try:
    import conversations as conversations_view
except Exception:
    conversations_view = None
try:
    import stats as stats_view
except Exception:
    stats_view = None
try:
    import identity as identity_store
except Exception:
    identity_store = None
try:
    import quotas as quotas_view
except Exception:
    quotas_view = None
try:
    import webhooks as webhooks_view
except Exception:
    webhooks_view = None
try:
    import gdpr as gdpr_tools
except Exception:
    gdpr_tools = None
try:
    import live as live_view
except Exception:
    live_view = None
try:
    import router as router_view
except Exception:
    router_view = None
CONNECTOR_CACHE = {"ts": 0.0, "data": None}
BRIDGE_CONFIG = os.path.join(BASE, "google_bridge.json")
LOG = os.path.join(BASE, "dashboard.log")
HTML_FILE = os.path.join(BASE, "dashboard.html")
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(CFG.get("dashboard_port") or 8787)
ROME = config.resolve_timezone()
OWNER = str(CFG.get("owner_number") or "")
START = time.time()
STATUS_CACHE = {"ts": 0.0, "data": None}
CHANNEL_CACHE = {"ts": 0.0, "data": None}
LOGIN_FAILS: dict[str, list[float]] = {}
ACTION_LIMIT: dict[str, float] = {}
# Sessioni lato server: il cookie è un id casuale, non il token, e scade.
SESSIONS: dict[str, dict] = {}
SESSION_TTL = 12 * 3600
LAST_LOGIN: str | None = None
TOKEN_ROTATED_AT: str | None = None
MAX_POOL_KEYS = 7

# Rate limiting configurabile da config.json ("rate_limit"): i valori fuori
# scala vengono limitati, così una configurazione sbagliata non disattiva le
# protezioni né blocca il titolare per un giorno intero.
RATE_CFG = CFG.get("rate_limit") if isinstance(CFG.get("rate_limit"), dict) else {}


def _rate_value(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(RATE_CFG.get(name, default))
    except (TypeError, ValueError):
        return float(default)
    return max(float(minimum), min(value, float(maximum)))


LOGIN_MAX_FAILS = int(_rate_value("login_max_fails", 10, 1, 1000))
LOGIN_WINDOW = _rate_value("login_window_seconds", 300, 30, 86400)
ACTION_SECONDS = _rate_value("action_seconds", 15, 0, 3600)
SEND_SECONDS = _rate_value("send_seconds", 1, 0, 60)


def _throttled(key: str, seconds: float | None = None) -> bool:
    if seconds is None:
        seconds = ACTION_SECONDS
    """Limita azioni costose (invii, demo) senza dipendere da servizi esterni."""
    now = time.time()
    if len(ACTION_LIMIT) > 500:
        for stale in [k for k, v in ACTION_LIMIT.items() if now - v > 3600]:
            ACTION_LIMIT.pop(stale, None)
    last = ACTION_LIMIT.get(key, 0.0)
    if now - last < seconds:
        return True
    ACTION_LIMIT[key] = now
    return False


def now_iso() -> str:
    return datetime.now(ROME).isoformat(timespec="seconds")


def log(msg: str) -> None:
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def ensure_token() -> str:
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(TOKEN_FILE) or os.path.getsize(TOKEN_FILE) < 16:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(24))
    os.chmod(TOKEN_FILE, 0o600)
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return f.read().strip()


DASH_TOKEN = ensure_token()


def run(cmd: list[str], timeout: int = 20, extra_env: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def read_jsonl(path: str, limit: int = 50) -> list[dict]:
    q: deque[dict] = deque(maxlen=max(1, min(limit, 5000)))
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    q.append(item)
    except FileNotFoundError:
        pass
    return list(reversed(q))


def today_date():
    return datetime.now(ROME).date()


def item_day(item: dict):
    raw = item.get("ts") or ""
    try:
        return datetime.fromisoformat(str(raw)).astimezone(ROME).date()
    except Exception:
        return None


def fmt_ms(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, ROME).strftime("%d/%m %H:%M")
    except Exception:
        return "-"


def health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18789/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def is_active(unit: str) -> bool:
    return run(["systemctl", "--user", "is-active", unit], timeout=8)[1] == "active"


SPAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(us|ms|s|min|h|d|w)")
SPAN_UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "h": 3600, "d": 86400, "w": 604800}


def interval_seconds(props: str) -> float:
    """Intervallo di un timer monotono, letto da ``TimersMonotonic``.

    Esempio: ``{ OnUnitActiveUSec=2min ; next_elapse=26min 1.9s }`` → 120.
    """
    for part in str(props or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip("{} \t\n")
        if not key.endswith("USec") or "next_elapse" in key:
            continue
        total = 0.0
        found = False
        for amount, span in SPAN_RE.findall(value):
            total += float(amount) * SPAN_UNITS.get(span, 0)
            found = True
        if found and total > 0:
            return total
    return 0.0


def next_elapse(unit: str) -> str:
    """Ora della prossima esecuzione del timer, anche per i timer monotoni.

    Con ``OnUnitActiveSec`` systemd lascia vuoto ``NextElapseUSecRealtime``:
    per questo usiamo ``list-timers --output=json``. Subito dopo un'esecuzione
    systemd può riportare ``next=0`` finché non ricalcola: in quel caso
    ricostruiamo l'istante da ultima esecuzione + intervallo.
    """
    _, out, _ = run(["systemctl", "--user", "list-timers", "--all", "--no-pager", "--output=json"], timeout=8)
    try:
        for item in json.loads(out or "[]"):
            if str(item.get("unit") or "") != unit:
                continue
            micros = int(item.get("next") or 0)
            if micros > 0:
                return datetime.fromtimestamp(micros / 1_000_000, ROME).strftime("%d/%m %H:%M")
    except Exception:
        pass
    _, realtime, _ = run(["systemctl", "--user", "show", unit, "-p", "NextElapseUSecRealtime", "--value"], timeout=8)
    if realtime.strip():
        return realtime.strip()
    _, props, _ = run(["systemctl", "--user", "show", unit, "-p", "TimersMonotonic", "--value"], timeout=8)
    seconds = interval_seconds(props)
    if seconds:
        _, last, _ = run(["systemctl", "--user", "show", unit, "-p", "LastTriggerUSec", "--value"], timeout=8)
        try:
            # systemd aggiunge l'abbreviazione del fuso (CET/CEST/UTC). strptime
            # accetta %Z solo per UTC/GMT/local time, quindi la togliamo e usiamo
            # il fuso configurato: lo stesso codice funziona su ogni Python.
            raw = re.sub(r"\s+[A-Za-z][A-Za-z0-9/_+-]{1,9}$", "", " ".join(last.split()))
            stamp = datetime.strptime(raw, "%a %Y-%m-%d %H:%M:%S").replace(tzinfo=ROME)
            return (stamp + timedelta(seconds=seconds)).strftime("%d/%m %H:%M")
        except Exception:
            pass
    return "-"


def uptime_text() -> str:
    _, pid, _ = run(["systemctl", "--user", "show", "openclaw-gateway", "-p", "MainPID", "--value"], timeout=8)
    if not pid.isdigit() or int(pid) <= 0:
        return "-"
    _, secs, _ = run(["ps", "-p", pid, "-o", "etimes="], timeout=8)
    try:
        s = int(secs.strip())
    except Exception:
        return "-"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}g {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _watchdog_whatsapp() -> dict | None:
    """Ultimo stato WhatsApp rilevato dal watchdog.

    La CLI di OpenClaw impiega ~18 secondi ad avviarsi: interrogarla dentro una
    richiesta HTTP rendeva la dashboard lentissima. Il watchdog gira ogni due
    minuti e salva lo stato qui, quindi la dashboard legge un file locale.
    """
    saved = read_json(WATCHDOG_STATE, {})
    if not isinstance(saved, dict):
        return None
    last = saved.get("last_check")
    if not last:
        return None
    try:
        stamp = datetime.fromisoformat(str(last))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ROME)
        age = (datetime.now(ROME) - stamp).total_seconds()
    except Exception:
        return None
    if age < -30 or age > 300:
        return None
    result = saved.get("last_result") or {}
    state = str(result.get("whatsapp") or "unknown")
    lifecycle = "ready" if state in {"healthy", "connected", "ready"} else "-"
    return {
        "state": state,
        "lifecycle": lifecycle,
        "reconnect": 0,
        "lastEvent": last,
        "busy": False,
        "source": "watchdog",
        "age": max(0, int(age)),
    }


def health_state() -> dict:
    """Legge l'ultimo report scritto da healthcheck.py.

    La scansione completa interroga Google, MCP e systemd: non deve bloccare
    il caricamento della dashboard. Il timer la esegue ogni 30 minuti e il
    pulsante Health la esegue su richiesta.
    """
    data = read_json(HEALTH_STATE, {})
    if not isinstance(data, dict) or not data:
        return {"available": False, "overall": "unknown", "counts": {}, "checks": [], "tokens": [], "alerts": []}
    data = dict(data)
    data["available"] = True
    try:
        stamp = datetime.fromisoformat(str(data.get("generated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ROME)
        data["age_seconds"] = max(0, int((datetime.now(ROME) - stamp).total_seconds()))
    except Exception:
        data["age_seconds"] = None
    return data


def health_summary() -> dict:
    data = health_state()
    tokens = [
        t for t in (data.get("tokens") or [])
        if isinstance(t, dict) and str(t.get("status")) in {"critical", "warn"}
    ]
    return {
        "available": bool(data.get("available")),
        "overall": str(data.get("overall") or "unknown"),
        "counts": data.get("counts") or {},
        "generated_at": data.get("generated_at"),
        "age_seconds": data.get("age_seconds"),
        "tokens": tokens[:8],
        "expiring": data.get("expiring") or [],
    }


def channel_status() -> dict:
    """Stato WhatsApp istantaneo, senza avviare la CLI nel thread HTTP."""
    snap = _watchdog_whatsapp()
    if snap is not None:
        CHANNEL_CACHE["ts"] = time.time()
        CHANNEL_CACHE["data"] = snap
        return snap
    if CHANNEL_CACHE["data"] is not None:
        cached = dict(CHANNEL_CACHE["data"])
        cached["stale"] = True
        return cached
    return {
        "state": "starting",
        "lifecycle": "-",
        "reconnect": 0,
        "lastEvent": None,
        "busy": False,
        "source": "waiting-watchdog",
    }


def pool_values(env: dict[str, str], kind: str) -> list[str]:
    if kind == "gemini":
        raw = [env.get("GEMINI_API_KEY", "")]
        raw += [env.get(f"GEMINI_API_KEY_{i}", "") for i in range(2, MAX_POOL_KEYS + 1)]
    else:
        raw = [env.get(f"GROQ_API_KEY_{i}", "") for i in range(2, MAX_POOL_KEYS + 1)]
    out: list[str] = []
    for value in raw:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out


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


def keys_status() -> dict:
    env = read_secrets()
    cfg = read_json(CONFIG, {})
    fallbacks = ((cfg.get("agents") or {}).get("defaults", {}).get("model", {}) or {}).get("fallbacks") or []
    gem = pool_values(env, "gemini")
    groq = pool_values(env, "groq")
    providers = (((cfg.get("models") or {}).get("providers")) or {})
    return {
        "gemini": {
            "count": len(gem),
            "configured": bool(gem),
            "fallback": any(str(x).startswith("google/") for x in fallbacks),
            "max": MAX_POOL_KEYS,
        },
        "groq_backup": {
            "count": len(groq),
            "configured": bool(groq),
            "providers": [f"groq{i}" for i in range(2, 2 + len(groq)) if f"groq{i}" in providers],
            "fallback": any(re.match(r"^groq[2-5]/", str(x)) for x in fallbacks),
            "max": MAX_POOL_KEYS,
        },
        "total": len(gem) + len(groq),
        "fallback_models": fallbacks,
    }


def gog_env() -> dict:
    env = read_secrets()
    out = {"GOG_KEYRING_BACKEND": "file"}
    if env.get("GOG_KEYRING_PASSWORD"):
        out["GOG_KEYRING_PASSWORD"] = env["GOG_KEYRING_PASSWORD"]
    return out


RENEW_SERVICES = "gmail,calendar"
GOOGLE_REDIRECT_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/")


def google_renew_start() -> dict:
    """Genera l'URL di consenso Google senza far uscire nulla dal PC.

    Usa il flusso ``--remote --step 1`` di gog: stampa solo l'URL di consenso e
    termina. Il codice viene scambiato da :func:`google_renew_finish`, così
    nessun processo resta appeso e la dashboard non blocca un thread.
    """
    if not GOOGLE_ACCOUNT:
        return {"ok": False, "error": "google_account non configurato"}
    if not (os.path.isfile(GOG) and os.access(GOG, os.X_OK)):
        return {"ok": False, "error": f"CLI gog non trovata in {GOG}"}
    cmd = [
        GOG, "auth", "add", GOOGLE_ACCOUNT,
        "--services", RENEW_SERVICES,
        "--gmail-scope", "read-send",
        "--force-consent",
        "--remote", "--step", "1", "--json",
    ]
    rc, out, err = run(cmd, timeout=60, extra_env=gog_env())
    data = {}
    try:
        data = json.loads(out[out.find("{"):])
    except Exception:
        data = {}
    url = str(data.get("auth_url") or "")
    if rc != 0 or not url.startswith("https://accounts.google.com/"):
        detail = str(data.get("error") or err or out or f"gog exit {rc}")[:300]
        log("google renew start failed: " + detail[:160])
        return {"ok": False, "error": detail}
    log("google renew url generated")
    return {"ok": True, "auth_url": url, "account": GOOGLE_ACCOUNT}


def google_renew_finish(auth_url: str) -> dict:
    """Scambia il codice del redirect con un refresh token e verifica Gmail."""
    auth_url = str(auth_url or "").strip()
    if not auth_url or len(auth_url) > 4096:
        return {"ok": False, "error": "incolla l'indirizzo completo del browser"}
    if not GOOGLE_REDIRECT_RE.match(auth_url) or "code=" not in auth_url:
        return {"ok": False, "error": "indirizzo non valido: serve quello con ?code=..."}
    cmd = [
        GOG, "auth", "add", GOOGLE_ACCOUNT,
        "--services", RENEW_SERVICES,
        "--gmail-scope", "read-send",
        "--remote", "--step", "2",
        "--auth-url", auth_url,
        "--json",
    ]
    rc, out, err = run(cmd, timeout=120, extra_env=gog_env())
    data = {}
    try:
        data = json.loads(out[out.find("{"):])
    except Exception:
        data = {}
    if rc != 0 or (isinstance(data.get("error"), dict)):
        detail = str(data.get("error") or err or out or f"gog exit {rc}")[:400]
        log("google renew finish failed: " + detail[:160])
        return {"ok": False, "error": detail}
    probe_rc, probe_out, probe_err = run(
        [GOG, "--account", GOOGLE_ACCOUNT, "--json", "gmail", "labels", "list"],
        timeout=40,
        extra_env=gog_env(),
    )
    if probe_rc != 0:
        log("google renew probe failed")
        return {
            "ok": False,
            "error": "autorizzazione salvata ma Gmail non risponde: " + str(probe_err or probe_out)[:300],
        }
    log("google renew ok")
    return {"ok": True, "account": GOOGLE_ACCOUNT, "result": "Gmail e Calendar riautorizzati."}


def bridge_status() -> dict:
    """Return only non-secret bridge metadata; never expose the secret."""
    if bridge_client is None:
        return {"configured": False, "error": "client bridge non installato"}
    try:
        cfg = bridge_client.load_config(BRIDGE_CONFIG)
    except Exception as exc:
        return {"configured": False, "error": str(exc)[:180]}
    if not cfg:
        return {"configured": False}
    host = urllib.parse.urlparse(cfg["url"]).netloc
    return {"configured": True, "host": host, "url": cfg["url"]}


def connector_status() -> dict:
    now = time.time()
    if CONNECTOR_CACHE["data"] is not None and now - CONNECTOR_CACHE["ts"] < 15:
        return CONNECTOR_CACHE["data"]
    installed = os.path.isfile(GOG) and os.access(GOG, os.X_OK)
    version = run([GOG, "--version"], timeout=8)[1] if installed else ""
    accounts: list[str] = []
    auth_error = ""
    if installed:
        rc, out, err = run([GOG, "auth", "list", "--json"], timeout=15, extra_env=gog_env())
        try:
            raw = json.loads(out[out.find("{"):])
            accounts = [str(x.get("email") or x.get("account") or "") for x in (raw.get("accounts") or [])]
            accounts = [x for x in accounts if x]
        except Exception:
            auth_error = (err or out or "auth non disponibile")[:180]
            accounts = []
    mcp_ok = False
    mcp_tools: list[str] = []
    mcp_error = ""
    rc, out, err = run([NODE, CLI, "mcp", "probe", MCP_SERVER, "--json"], timeout=35, extra_env=gog_env())
    try:
        raw = json.loads(out[out.find("{"):])
        server = (raw.get("servers") or {}).get(MCP_SERVER) or {}
        mcp_ok = bool(server) and not (server.get("error") or "")
        mcp_tools = [str(x).rsplit("__", 1)[-1] for x in (raw.get("tools") or [])]
    except Exception:
        mcp_error = (err or out or "probe MCP fallito")[:180]
    cfg = read_json(CONFIG, {})
    defaults = ((cfg.get("agents") or {}).get("defaults") or {})
    messages = cfg.get("messages") or {}
    data = {
        "google": {
            "installed": installed,
            "version": version,
            "authorized": bool(accounts),
            "accounts": accounts,
            "error": auth_error,
        },
        "bridge": bridge_status(),
        "mcp": {
            "name": MCP_SERVER,
            "ok": mcp_ok,
            "tools": mcp_tools,
            "error": mcp_error,
        },
        "policy": {
            "max_concurrent": int(defaults.get("maxConcurrent") or 1),
            "queue": str((messages.get("queue") or {}).get("mode") or "steer"),
            "debounce_ms": int((messages.get("inbound") or {}).get("debounceMs") or 0),
            "whatsapp_debounce_ms": int(((messages.get("inbound") or {}).get("byChannel") or {}).get("whatsapp") or 0),
        },
        "owner": (OWNER[:3] + " *** " + OWNER[-4:]) if len(OWNER) >= 7 else OWNER,
    }
    CONNECTOR_CACHE["ts"] = now
    CONNECTOR_CACHE["data"] = data
    return data


def status() -> dict:
    now = time.time()
    if STATUS_CACHE["data"] is not None and now - STATUS_CACHE["ts"] < 8:
        return STATUS_CACHE["data"]
    cfg = read_json(CONFIG, {})
    model = ((cfg.get("agents") or {}).get("defaults", {}).get("model", {}) or {})
    leads = read_jsonl(os.path.join(BASE, "leads.jsonl"), 2000)
    alerts = read_jsonl(os.path.join(BASE, "alerts.jsonl"), 2000)
    gw_health = health()
    day = today_date()
    counts_by_day: dict[str, int] = {}
    for item in leads:
        d = item_day(item)
        if d:
            counts_by_day[d.isoformat()] = counts_by_day.get(d.isoformat(), 0) + 1
    activity = []
    weekdays = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    for offset in range(6, -1, -1):
        d = day - timedelta(days=offset)
        activity.append({"date": d.isoformat(), "label": weekdays[d.weekday()], "count": counts_by_day.get(d.isoformat(), 0)})
    leads_today = [x for x in leads if item_day(x) == day]
    alerts_today = [x for x in alerts if item_day(x) == day]
    senders = {x.get("sender") for x in leads_today if x.get("sender")}
    data = {
        "now": now_iso(),
        "gateway": {
            "active": is_active("openclaw-gateway"),
            "health": gw_health,
            "uptime": uptime_text(),
            "pid": run(["systemctl", "--user", "show", "openclaw-gateway", "-p", "MainPID", "--value"], timeout=8)[1].strip(),
        },
        "whatsapp": channel_status() if gw_health else {
            "state": "gateway-down", "lifecycle": "-", "reconnect": 0, "lastEvent": None, "busy": False,
        },
        "model": {
            "primary": model.get("primary") or "-",
            "fallbacks": model.get("fallbacks") or [],
            "thinking": "off",
            "tools": "email · calendario · preventivi PDF",
        },
        "capabilities": bot_capabilities(),
        "timers": {
            "monitor": {"active": is_active("openclaw-secretary-monitor.timer"), "next": next_elapse("openclaw-secretary-monitor.timer")},
            "digest": {"active": is_active("openclaw-secretary-digest.timer"), "next": next_elapse("openclaw-secretary-digest.timer")},
            "watchdog": {"active": is_active("openclaw-watchdog.timer"), "next": next_elapse("openclaw-watchdog.timer")},
            "health": {"active": is_active("openclaw-secretary-health.timer"), "next": next_elapse("openclaw-secretary-health.timer")},
        },
        "counts": {
            "leads_today": len(leads_today),
            "alerts_today": len(alerts_today),
            "senders_today": len(senders),
            "leads_total": len(leads),
            "alerts_total": len(alerts),
            "scams_today": sum(1 for x in alerts_today if x.get("kind") == "scam"),
            "hot_today": sum(1 for x in alerts_today if x.get("kind") == "hot"),
            "approvals_pending": approval_store.pending_count() if approval_store else 0,
        },
        "keys": keys_status(),
        "activity": activity,
        "security": security_status(),
        "recent": {
            "leads": leads[:12],
            "alerts": alerts[:12],
        },
        "dashboard": {"uptime": int(now - START), "host": HOST, "port": PORT},
        "health": health_summary(),
    }
    STATUS_CACHE["ts"] = now
    STATUS_CACHE["data"] = data
    return data


def normalize_key_list(value) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[\s,]+", value)
    elif isinstance(value, list):
        raw = [str(x) for x in value]
    else:
        raw = []
    out: list[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key and key not in out:
            out.append(key)
    return out


def save_keys(payload: dict) -> dict:
    global TOKEN_ROTATED_AT
    env = read_secrets()
    if payload.get("clear_gemini"):
        for k in ["GEMINI_API_KEY", "GEMINI_API_KEYS"] + [f"GEMINI_API_KEY_{i}" for i in range(2, MAX_POOL_KEYS + 1)]:
            env.pop(k, None)
    if payload.get("clear_groq_backup") or payload.get("clear_groq2"):
        for i in range(2, MAX_POOL_KEYS + 1):
            env.pop(f"GROQ_API_KEY_{i}", None)

    if "gemini_keys" in payload:
        for k in ["GEMINI_API_KEY", "GEMINI_API_KEYS"] + [f"GEMINI_API_KEY_{i}" for i in range(2, MAX_POOL_KEYS + 1)]:
            env.pop(k, None)
        gem = normalize_key_list(payload.get("gemini_keys"))
    else:
        gem = normalize_key_list(payload.get("gemini_key"))
    if "groq_backup_keys" in payload:
        for i in range(2, MAX_POOL_KEYS + 1):
            env.pop(f"GROQ_API_KEY_{i}", None)
        groq = normalize_key_list(payload.get("groq_backup_keys"))
    else:
        groq = normalize_key_list(payload.get("groq2_key"))

    if len(gem) > MAX_POOL_KEYS:
        return {"ok": False, "error": f"Massimo {MAX_POOL_KEYS} chiavi Gemini."}
    if len(groq) > MAX_POOL_KEYS:
        return {"ok": False, "error": f"Massimo {MAX_POOL_KEYS} chiavi Groq di backup."}
    for idx, key in enumerate(gem, 1):
        if len(key) < 20:
            return {"ok": False, "error": f"Chiave Gemini {idx} troppo corta."}
    for idx, key in enumerate(groq, 2):
        if len(key) < 20:
            return {"ok": False, "error": f"Chiave Groq {idx} troppo corta."}

    if gem:
        env["GEMINI_API_KEY"] = gem[0]
        env["GEMINI_API_KEYS"] = ",".join(gem)
        for idx, key in enumerate(gem[1:], 2):
            env[f"GEMINI_API_KEY_{idx}"] = key
    if groq:
        for idx, key in enumerate(groq, 2):
            env[f"GROQ_API_KEY_{idx}"] = key

    os.makedirs(BASE, exist_ok=True)
    tmp = SECRETS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# Chiavi private della segreteria. Non condividere.\n")
        for k in sorted(env):
            if env[k]:
                f.write(f"{k}={env[k]}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SECRETS)
    rc, out, err = run(["python3", os.path.join(BASE, "sync_fallbacks.py")], timeout=30)
    if rc != 0:
        log(f"sync_fallbacks failed rc={rc} err={err[:200]}")
    subprocess.Popen(
        ["systemctl", "--user", "restart", "openclaw-gateway"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log(f"keys saved gemini={len(gem)} groq_backup={len(groq)}; gateway restart requested")
    return {"ok": True, "restarting": True, "sync": out[-500:], "keys": keys_status()}


def _test_gemini_key(key: str) -> str:
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?key=" + urllib.parse.quote(key),
            headers={"Accept": "application/json", "User-Agent": "openclaw-secretary/1.0"},
        )
        with urllib.request.urlopen(req, timeout=14) as r:
            return "ok" if r.status == 200 else f"http {r.status}"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "limite"
        return "invalida" if e.code in (400, 401, 403) else f"http {e.code}"
    except Exception:
        return "rete"


def _test_groq_key(key: str) -> str:
    body = json.dumps({
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 8,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "openclaw-secretary/1.0"},
        )
        with urllib.request.urlopen(req, timeout=14) as r:
            return "ok" if r.status == 200 else f"http {r.status}"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "limite"
        return "invalida" if e.code in (400, 401, 403) else f"http {e.code}"
    except Exception:
        return "rete"


def test_keys() -> dict:
    env = read_secrets()
    gem = pool_values(env, "gemini")
    groq = pool_values(env, "groq")
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_POOL_KEYS, 7))) as ex:
        gem_status = list(ex.map(_test_gemini_key, gem)) if gem else []
        groq_status = list(ex.map(_test_groq_key, groq)) if groq else []
    def rows(values, statuses, start):
        return [{"index": start + i, "status": statuses[i] if i < len(statuses) else "missing"} for i in range(len(values))]
    return {
        "gemini": rows(gem, gem_status, 1),
        "groq_backup": rows(groq, groq_status, 2),
        "summary": {
            "gemini_ok": sum(1 for x in gem_status if x == "ok"),
            "gemini_total": len(gem),
            "groq_ok": sum(1 for x in groq_status if x == "ok"),
            "groq_total": len(groq),
        },
    }


def save_security_state() -> None:
    try:
        os.makedirs(BASE, exist_ok=True)
        tmp = SECURITY_STATE + ".tmp"
        saved = read_json(SECURITY_STATE, {})
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "last_login": LAST_LOGIN or saved.get("last_login"),
                "token_rotated_at": TOKEN_ROTATED_AT or saved.get("token_rotated_at"),
            }, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SECURITY_STATE)
    except Exception:
        pass


def security_status() -> dict:
    st = os.stat(TOKEN_FILE) if os.path.exists(TOKEN_FILE) else None
    saved = read_json(SECURITY_STATE, {})
    return {
        "bind": f"{HOST}:{PORT}",
        "token_file": TOKEN_FILE,
        "token_perms": oct(st.st_mode & 0o777) if st else None,
        "last_login": LAST_LOGIN or saved.get("last_login"),
        "token_rotated_at": TOKEN_ROTATED_AT or saved.get("token_rotated_at"),
        "failed_logins_5m": sum(len(v) for v in LOGIN_FAILS.values()),
        "max_login_fails": LOGIN_MAX_FAILS,
        "login_window_seconds": int(LOGIN_WINDOW),
        "lock_window_seconds": 300,
        "session_hours": SESSION_TTL // 3600,
        "sessions_active": len(_live_sessions()),
        "two_factor": bool(identity_store is not None and identity_store.totp_status().get("enabled")),
    }


PAGE_CACHE: dict[str, object] = {"key": None, "html": ""}


def rendered_page() -> str:
    """Dashboard pronta da servire, ricostruita solo se i file cambiano.

    Leggere 161 KiB di HTML e 37 KiB di traduzioni a ogni richiesta costava
    ~1,3 ms di CPU per un dato che cambia solo quando aggiorni la dashboard.
    """
    i18n_path = os.path.join(os.path.dirname(HTML_FILE), "i18n.json")
    try:
        key = (
            os.path.getmtime(HTML_FILE), os.path.getsize(HTML_FILE),
            os.path.getmtime(i18n_path), os.path.getsize(i18n_path),
            GOOGLE_ACCOUNT, OWNER,
            str(CFG.get("business_name") or ""), str(CFG.get("owner_name") or ""),
            str(CFG.get("bot_name") or ""),
        )
    except OSError:
        key = None
    if key is not None and PAGE_CACHE["key"] == key:
        return str(PAGE_CACHE["html"])
    with open(HTML_FILE, encoding="utf-8") as f:
        page = f.read()
    try:
        with open(i18n_path, encoding="utf-8") as f:
            i18n_text = f.read().strip() or "{}"
    except OSError:
        i18n_text = "{}"
    page = (page.replace("{{I18N_TEXT}}", i18n_text)
                .replace("{{BUSINESS_NAME}}", str(CFG.get("business_name") or "Nome Attività"))
                .replace("{{OWNER_NAME}}", str(CFG.get("owner_name") or "Titolare"))
                .replace("{{BOT_NAME}}", str(CFG.get("bot_name") or "Gina"))
                .replace("{{BOT_INITIAL}}", (str(CFG.get("bot_name") or "Gina").strip()[:1] or "G").upper())
                .replace("{{GOOGLE_ACCOUNT}}", GOOGLE_ACCOUNT)
                .replace("{{OWNER_NUMBER}}", OWNER))
    if key is not None:
        PAGE_CACHE["key"] = key
        PAGE_CACHE["html"] = page
    return page


def _token_matches(candidate: str) -> bool:
    """Confronto a tempo costante che non esplode su un token non ASCII.

    ``hmac.compare_digest`` accetta ``str`` solo se è ASCII: un POST con
    ``token=città`` sollevava ``TypeError`` e chiudeva la richiesta con un 500
    invece di un 401. Il confronto su UTF-8 è equivalente e non solleva.
    """
    try:
        expected = DASH_TOKEN.encode("utf-8")
        return hmac.compare_digest(str(candidate or "").encode("utf-8"), expected)
    except (TypeError, ValueError, AttributeError):
        return False


def _live_sessions() -> dict[str, dict]:
    now = time.time()
    for sid in [key for key, value in SESSIONS.items() if float(value.get("exp") or 0) <= now]:
        SESSIONS.pop(sid, None)
    return SESSIONS


def create_session(name: str, role: str) -> str:
    sid = secrets.token_urlsafe(32)
    _live_sessions()
    SESSIONS[sid] = {"name": name, "role": role, "exp": time.time() + SESSION_TTL}
    return sid


def revoke_sessions() -> int:
    count = len(SESSIONS)
    SESSIONS.clear()
    return count


def rotate_token() -> str:
    global DASH_TOKEN, TOKEN_ROTATED_AT
    new = secrets.token_hex(24)
    os.makedirs(BASE, exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)
    DASH_TOKEN = new
    TOKEN_ROTATED_AT = now_iso()
    save_security_state()
    log("dashboard token rotated")
    return new


def approvals_data(limit: int = 100) -> dict:
    if approval_store is None:
        return {"pending": [], "recent": [], "counts": {"pending": 0}, "error": "coda non disponibile"}
    return approval_store.list_requests(limit)


def bot_capabilities() -> dict:
    """Verifica i tool che il bot ha davvero a disposizione.

    Non basta che il file esista: carica la copia installata e legge il suo
    elenco tool. Se `request_quote` non c'è, la dashboard lo dice subito al
    titolare invece di lasciare che sia il cliente a scoprirlo.
    """
    expected = {"request_email", "request_calendar", "request_quote"}
    tools: set[str] = set()
    error = ""
    script = os.path.join(BASE, "approval_mcp.py")
    try:
        spec = importlib.util.spec_from_file_location("secretary_installed_mcp", script)
        module = importlib.util.module_from_spec(spec) if spec and spec.loader else None
        if module is None:
            raise RuntimeError("modulo non caricabile")
        spec.loader.exec_module(module)
        tools = {str(tool.get("name") or "") for tool in getattr(module, "TOOLS", [])}
    except Exception as exc:
        error = str(exc)[:160]
    if not tools and approval_mcp is not None:
        tools = {str(tool.get("name") or "") for tool in approval_mcp.TOOLS}
        error = ""
    missing = sorted(expected - tools)
    ok = not missing and bool(tools)
    detail = (
        "Tool attivi: " + ", ".join(sorted(tools))
        if ok else
        (
            "Mancano: " + ", ".join(missing)
            if missing else
            f"Server MCP non leggibile: {error or 'file assente'}"
        )
    )
    return {
        "ok": ok,
        "tools": sorted(tools),
        "missing": missing,
        "detail": detail,
        "demo": bool(CFG.get("demo_mode")),
    }


def quotes_data(limit: int = 60) -> dict:
    """Elenco dei preventivi con stato, scadenza, PDF e azioni."""
    if approval_store is None:
        return {"quotes": [], "counts": {}, "error": "coda non disponibile"}
    limit = max(1, min(int(limit or 60), 200))
    data = approval_store.list_requests(max(limit, 120))
    rows = [row for row in (data.get("recent") or []) if row.get("type") == "quote"]
    today = datetime.now().date()
    quotes: list[dict] = []
    for row in rows[:limit]:
        payload = row.get("payload") or {}
        valid_until = str(payload.get("valid_until") or "")
        days_left = None
        try:
            days_left = (datetime.strptime(valid_until, "%Y-%m-%d").date() - today).days
        except Exception:
            days_left = None
        quotes.append({
            "id": row.get("id"),
            "number": payload.get("number") or "",
            "client": payload.get("client_name") or row.get("client_name") or "",
            "email": payload.get("to") or "",
            "subject": payload.get("subject") or "",
            "total": (payload.get("totals") or {}).get("total"),
            "currency": payload.get("currency") or "EUR",
            "status": row.get("status") or "",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "valid_until": valid_until,
            "days_left": days_left,
            "expired": bool(days_left is not None and days_left < 0),
            "result": row.get("result") or "",
            "error": row.get("error") or "",
            "has_pdf": approval_store.quote_path(row) is not None,
            "items": payload.get("items") or [],
        })
    return {"quotes": quotes, "counts": data.get("counts") or {}}


def create_demo_quote() -> dict:
    if approval_store is None or quote_pdf is None:
        return {"ok": False, "error": "modulo preventivi non disponibile"}
    try:
        quote = quote_pdf.demo_quote()
        request = approval_store.append_request(
            "quote",
            quote,
            client_name=quote.get("client_name") or "Cliente Demo",
            reason="Preventivo dimostrativo generato dalla dashboard.",
        )
    except Exception as exc:
        log(f"demo quote failed: {str(exc)[:180]}")
        return {"ok": False, "error": str(exc)[:400]}
    payload = request.get("payload") or {}
    log(f"demo quote created {request.get('id')}")
    return {
        "ok": True,
        "id": request.get("id"),
        "number": payload.get("number"),
        "data": quotes_data(),
    }


def create_manual_quote(data: dict) -> dict:
    """Crea un preventivo dalla dashboard, senza passare dal modello.

    Serve quando il bot non è raggiungibile, quando il cliente è al telefono o
    quando il titolare vuole correggere i dati a mano. I totali vengono sempre
    ricalcolati da quote_pdf: la dashboard non può inventare cifre.
    """
    if approval_store is None or quote_pdf is None:
        return {"ok": False, "error": "modulo preventivi non disponibile"}
    data = data if isinstance(data, dict) else {}
    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    items: list[dict] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        description = str(row.get("description") or "").strip()
        price = row.get("unit_price")
        if price is None:
            price = row.get("price")
        price_text = str(price if price is not None else "").strip().replace(",", ".")
        if not description and not price_text:
            continue
        items.append({
            "description": description,
            "qty": row.get("qty") or 1,
            "unit": str(row.get("unit") or "").strip(),
            "unit_price": price_text or "0",
        })
    email = str(data.get("client_email") or data.get("to") or "").strip()
    payload = {
        "to": email,
        "client_name": str(data.get("client_name") or "").strip(),
        "client_email": email,
        "client_company": str(data.get("client_company") or "").strip(),
        "client_address": str(data.get("client_address") or "").strip(),
        "client_phone": str(data.get("client_phone") or "").strip(),
        "items": items,
        "discount_pct": data.get("discount_pct") or 0,
        "notes": str(data.get("notes") or "").strip(),
        "subject": str(data.get("subject") or "").strip(),
        "body": str(data.get("body") or "").strip(),
    }
    for key in ("vat_pct", "valid_days"):
        value = data.get(key)
        if value not in (None, ""):
            payload[key] = value
    try:
        request = approval_store.append_request(
            "quote",
            payload,
            client_name=payload["client_name"],
            client_phone=payload["client_phone"],
            reason="Preventivo creato dalla dashboard.",
            dedupe=False,
        )
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)[:400]}
    except Exception as exc:
        log(f"manual quote failed: {str(exc)[:180]}")
        return {"ok": False, "error": "impossibile creare il preventivo"}
    saved = request.get("payload") or {}
    log(f"manual quote created {request.get('id')}")
    return {
        "ok": True,
        "id": request.get("id"),
        "number": saved.get("number"),
        "data": quotes_data(),
    }


def quote_email_preview(request_id: str) -> dict:
    """HTML dell'email di un preventivo reale, generato in locale."""
    if approval_store is None:
        return {"ok": False, "error": "coda non disponibile"}
    request = approval_store.get_request(str(request_id or ""))
    if not request or request.get("type") != "quote":
        return {"ok": False, "error": "preventivo non trovato"}
    payload = request.get("payload") or {}
    html = str(payload.get("html") or "")
    if not html:
        return {"ok": False, "error": "anteprima non disponibile: rigenera il preventivo"}
    return {
        "ok": True,
        "html": html,
        "subject": payload.get("subject") or "",
        "to": payload.get("to") or "",
        "number": payload.get("number") or "",
    }


def quotes_csv(limit: int = 200) -> str:
    """Esporta i preventivi in CSV per Excel e per il commercialista."""
    data = quotes_data(limit)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "numero", "creato", "cliente", "email", "totale", "valuta",
        "stato", "scadenza", "pdf", "esito",
    ])

    def cell(value) -> str:
        text = str(value if value is not None else "")
        # Evita l'esecuzione di formule quando il CSV si apre in Excel.
        if text[:1] in {"=", "+", "-", "@"}:
            text = "'" + text
        return text

    for row in data.get("quotes") or []:
        writer.writerow([
            cell(row.get("number")), cell(row.get("created_at")), cell(row.get("client")),
            cell(row.get("email")), cell(row.get("total")), cell(row.get("currency")),
            cell(row.get("status")), cell(row.get("valid_until")),
            "sì" if row.get("has_pdf") else "no", cell(row.get("result")),
        ])
    return out.getvalue()


def _pending_by_phone() -> dict[str, int]:
    counts: dict[str, int] = {}
    if approval_store is None:
        return counts
    for row in approval_store.list_requests(300).get("recent") or []:
        if row.get("status") != "pending":
            continue
        phone = str(row.get("client_phone") or "").strip()
        if phone:
            counts[phone] = counts.get(phone, 0) + 1
    return counts


def contacts_data(limit: int = 60, lang: str = "it") -> dict:
    """Storico reale dei contatti WhatsApp, unito a lead e richieste aperte."""
    if contacts_history is None:
        return {"contacts": [], "total": 0, "messages": 0, "error": "modulo contatti non disponibile"}
    contacts = contacts_history.load_contacts(limit=limit)
    pending = _pending_by_phone()
    return {
        "contacts": contacts,
        "total": len(contacts),
        "messages": sum(int(c.get("messages_in") or 0) for c in contacts),
        "pending_by_phone": pending,
        "summary": contacts_history.summary_text(contacts, pending_by_phone=pending, lang=lang),
    }


def send_contacts_summary(dry: bool = False) -> dict:
    if contacts_history is None:
        return {"ok": False, "error": "modulo contatti non disponibile"}
    data = contacts_data(120)
    text = contacts_history.summary_text(
        data.get("contacts") or [], pending_by_phone=data.get("pending_by_phone") or {}
    )
    ok = contacts_history.send_whatsapp(text, dry=dry)
    if not ok:
        return {"ok": False, "error": "invio non riuscito: controlla owner_number e il gateway WhatsApp"}
    log("contacts summary sent to owner")
    return {"ok": True, "sent": not dry, "text": text[:1600]}


def run_reminders(dry: bool = False, force: bool = False) -> dict:
    """Esegue reminders.py senza bloccare il resto della dashboard."""
    cmd = [sys.executable, os.path.join(BASE, "reminders.py"), "--json"]
    if dry:
        cmd.append("--dry-run")
    if force:
        cmd.append("--force")
    rc, out, err = run(cmd, timeout=120)
    data: dict = {}
    try:
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except Exception:
        data = {}
    if rc != 0:
        return {"ok": False, "error": (err or out or f"reminders exit {rc}")[-400:]}
    return {"ok": True, **(data if isinstance(data, dict) else {})}


def run_doctor() -> dict:
    """Esegue doctor.py e restituisce i controlli in forma leggibile."""
    cmd = [sys.executable, os.path.join(BASE, "doctor.py"), "--json"]
    rc, out, err = run(cmd, timeout=120)
    data: dict = {}
    try:
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except Exception:
        data = {}
    if not data:
        return {"ok": False, "error": (err or out or f"doctor exit {rc}")[-400:], "checks": []}
    return data


MAX_ATTACHMENT_BYTES = 1_200_000


def _quote_attachments(payload: dict) -> list[str]:
    """Percorso del PDF del preventivo, confinato alla cartella privata."""
    if approval_store is None or not str(payload.get("pdf") or ""):
        return []
    path = approval_store.quote_path({"payload": payload})
    return [str(path)] if path else []


def _bridge_attachments(paths: list[str]) -> list[dict]:
    out: list[dict] = []
    for path in paths:
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_ATTACHMENT_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            log("attachment too large, skipped")
            continue
        out.append({
            "filename": os.path.basename(path),
            "mimeType": "application/pdf",
            "data": base64.b64encode(data).decode("ascii"),
        })
    return out


def _execute_email(payload: dict, attachments: list[str] | None = None) -> str:
    bridge = None
    if bridge_client is not None:
        try:
            bridge = bridge_client.load_config(BRIDGE_CONFIG)
        except Exception as exc:
            log(f"bridge config error: {str(exc)[:180]}")
    if attachments is None:
        attachments = _quote_attachments(payload)
    suffix = " con PDF allegato" if attachments else ""
    if bridge:
        request = dict(payload)
        if attachments:
            request["attachments"] = _bridge_attachments(attachments)
        result = bridge_client.send_email(request, bridge)
        message_id = str((result or {}).get("id") or "")
        return (
            f"Email inviata a {payload.get('to')} via bridge Google{suffix}"
            + (f" (id {message_id})" if message_id else "")
        )

    cmd = [
        GOG, "--account", GOOGLE_ACCOUNT, "--json", "--no-input",
        "gmail", "send",
        "--to", str(payload.get("to") or ""),
        "--subject", str(payload.get("subject") or ""),
        "--body", str(payload.get("body") or ""),
    ]
    html_body = str(payload.get("html") or "")
    html_path = ""
    if html_body.strip():
        fd, html_path = tempfile.mkstemp(prefix="secretary-mail-", suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html_body)
        cmd += ["--body-html-file", html_path]
    for path in attachments:
        cmd += ["--attach", path]
    try:
        rc, out, err = run(cmd, timeout=120, extra_env=gog_env())
    finally:
        if html_path:
            try:
                os.unlink(html_path)
            except OSError:
                pass
    if rc != 0:
        raise RuntimeError((err or out or f"gog exit {rc}")[:600])
    try:
        raw = json.loads(out[out.find("{"):]) if "{" in out else {}
    except Exception:
        raw = {}
    message_id = raw.get("id") or raw.get("messageId") or raw.get("message_id") or ""
    return (
        f"Email inviata a {payload.get('to')}{suffix}"
        + (f" (id {message_id})" if message_id else "")
    )


def _demo_quote_message() -> dict:
    """Costruisce preventivo demo e HTML senza inviare né accodare nulla."""
    if email_template is None or quote_pdf is None:
        raise RuntimeError("template email o generatore PDF non disponibile")
    owner = str(CFG.get("owner_name") or "titolare").strip()
    brand = str(CFG.get("business_name") or "Nome Attività").strip()
    demo = quote_pdf.demo_quote(CFG)
    demo["number"] = "DEMO-" + datetime.now(ROME).strftime("%Y%m%d")
    subject = f"Preventivo demo — {brand}"
    if approval_store is not None and hasattr(approval_store, "_quote_email_body"):
        body = approval_store._quote_email_body(demo)
    else:
        body = (
            f"Ciao {owner},\n\n"
            "in allegato trovi un preventivo dimostrativo con il layout dell'attività.\n\n"
            f"Totale: {quote_pdf.money_text(demo['totals']['total'])}.\n\n"
            "Serve solo a verificare colori, link e allegato PDF.\n\n"
            f"A presto,\n{owner}"
        )
    try:
        html = email_template.render_email(
            subject, body, client_name=owner, quote=demo
        )
    except TypeError:  # template vecchio durante un aggiornamento parziale
        html = email_template.render_email(subject, body, client_name=owner)
    return {"quote": demo, "subject": subject, "body": body, "html": html}


def _demo_quote_pdf(message: dict | None = None) -> tuple[bytes, str]:
    """Genera il PDF demo e il suo nome senza toccare la coda."""
    message = message or _demo_quote_message()
    quote = message["quote"]
    return quote_pdf.render_quote_pdf(quote), quote_pdf.quote_filename(quote)


def demo_quote_preview() -> dict:
    """Anteprima locale del preventivo demo: nessuna email, nessuna coda."""
    try:
        message = _demo_quote_message()
        pdf, pdf_filename = _demo_quote_pdf(message)
    except Exception as exc:
        log(f"demo quote preview failed: {str(exc)[:180]}")
        return {"ok": False, "error": str(exc)[:400]}
    quote = message["quote"]
    return {
        "ok": True,
        "sent": False,
        "subject": message["subject"],
        "html": message["html"],
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "pdf_filename": pdf_filename,
        "number": quote.get("number") or "",
        "total": (quote.get("totals") or {}).get("total"),
        "currency": quote.get("currency") or "EUR",
        "items": len(quote.get("items") or []),
        "valid_until": quote.get("valid_until") or "",
    }


def send_test_quote_email() -> dict:
    """Invia al titolare il preventivo demo già usato dall'anteprima."""
    if not GOOGLE_ACCOUNT:
        return {"ok": False, "error": "configura google_account o GOOGLE_ACCOUNT"}
    try:
        message = _demo_quote_message()
        pdf = quote_pdf.render_quote_pdf(message["quote"])
        fd, pdf_path = tempfile.mkstemp(prefix="secretary-quote-demo-", suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(pdf)
            result = _execute_email({
                "to": GOOGLE_ACCOUNT,
                "subject": message["subject"],
                "body": message["body"],
                "html": message["html"],
            }, attachments=[pdf_path])
        finally:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass
    except Exception as exc:
        log(f"test quote email failed: {str(exc)[:180]}")
        return {"ok": False, "error": str(exc)[:400]}
    log("test quote email sent")
    return {"ok": True, "result": result, "to": GOOGLE_ACCOUNT, "pdf": True}


def send_test_email() -> dict:
    if email_template is None:
        return {"ok": False, "error": "template email non disponibile"}
    if not GOOGLE_ACCOUNT:
        return {"ok": False, "error": "configura google_account o GOOGLE_ACCOUNT"}
    owner = str(CFG.get("owner_name") or "titolare").strip()
    brand = str(CFG.get("business_name") or "Nome Attività").strip()
    body = (
        f"Ciao {owner},\n\n"
        "questa è un'email di prova inviata dalla dashboard della segreteria.\n\n"
        "Serve a controllare subito il template grafico, i pulsanti e la resa su Gmail, senza aspettare un cliente.\n\n"
        "Se ti piace, è già attivo per tutte le email di follow-up.\n\n"
        f"A presto,\n{owner}"
    )
    try:
        subject = f"Email di prova — {brand}"
        html = email_template.render_email(subject, body, client_name=owner)
        result = _execute_email({
            "to": GOOGLE_ACCOUNT,
            "subject": subject,
            "body": body,
            "html": html,
        })
    except Exception as exc:
        log(f"test email failed: {str(exc)[:180]}")
        return {"ok": False, "error": str(exc)[:400]}
    log("test email sent")
    return {"ok": True, "result": result, "to": GOOGLE_ACCOUNT}


def _execute_calendar(payload: dict) -> str:
    bridge = None
    if bridge_client is not None:
        try:
            bridge = bridge_client.load_config(BRIDGE_CONFIG)
        except Exception as exc:
            log(f"bridge config error: {str(exc)[:180]}")
    if bridge:
        result = bridge_client.create_event(payload, bridge)
        event_id = str((result or {}).get("id") or "")
        return f"Evento creato: {payload.get('summary')} via bridge Google" + (f" (id {event_id})" if event_id else "")

    cmd = [
        GOG, "--account", GOOGLE_ACCOUNT, "--json", "--no-input",
        "calendar", "create", "primary",
        "--summary", str(payload.get("summary") or ""),
        "--from", str(payload.get("start") or ""),
        "--to", str(payload.get("end") or ""),
        "--timezone", str(payload.get("timezone") or "Europe/Rome"),
        "--send-updates", "none",
    ]
    if payload.get("attendees"):
        cmd += ["--attendees", ",".join(payload["attendees"])]
    if payload.get("location"):
        cmd += ["--location", str(payload["location"])]
    if payload.get("description"):
        cmd += ["--description", str(payload["description"])]
    rc, out, err = run(cmd, timeout=90, extra_env=gog_env())
    if rc != 0:
        raise RuntimeError((err or out or f"gog exit {rc}")[:600])
    try:
        raw = json.loads(out[out.find("{"):]) if "{" in out else {}
    except Exception:
        raw = {}
    event_id = raw.get("id") or raw.get("eventId") or raw.get("event_id") or ""
    return f"Evento creato: {payload.get('summary')}" + (f" (id {event_id})" if event_id else "")


def approve_request(request_id: str) -> dict:
    if approval_store is None:
        return {"ok": False, "error": "coda approvazioni non disponibile"}
    request = approval_store.get_request(request_id)
    if not request:
        return {"ok": False, "error": "richiesta non trovata"}
    if request.get("status") != "pending":
        return {"ok": False, "error": "richiesta già gestita"}
    try:
        if request.get("type") in {"email", "quote"}:
            result = _execute_email(request.get("payload") or {})
        elif request.get("type") == "calendar":
            result = _execute_calendar(request.get("payload") or {})
        else:
            raise RuntimeError("tipo richiesta non valido")
    except Exception as exc:
        approval_store.set_status(request_id, "failed", error=str(exc)[:600])
        log(f"approval failed {request_id}: {str(exc)[:180]}")
        return {"ok": False, "error": str(exc)[:600], "id": request_id}
    approval_store.set_status(request_id, "approved", result=result)
    log(f"approval approved {request_id} type={request.get('type')}")
    if webhooks_view is not None:
        event = {"quote": "quote.approved", "calendar": "appointment.created"}.get(
            str(request.get("type") or "")
        )
        if event:
            try:
                payload = request.get("payload") or {}
                webhooks_view.emit(event, {
                    "number": payload.get("number", ""),
                    "client": request.get("client_name", ""),
                    "total": (payload.get("totals") or {}).get("total", 0),
                })
            except Exception:
                pass
    return {"ok": True, "id": request_id, "result": result, "data": approvals_data(80)}


def reject_request(request_id: str) -> dict:
    if approval_store is None:
        return {"ok": False, "error": "coda approvazioni non disponibile"}
    try:
        approval_store.set_status(request_id, "rejected")
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    log(f"approval rejected {request_id}")
    return {"ok": True, "id": request_id, "data": approvals_data(80)}


def audit_lines(limit: int = 80) -> list[str]:
    try:
        with open(LOG, encoding="utf-8") as f:
            lines = f.readlines()
        return [x.rstrip() for x in lines[-max(10, min(limit, 300)):]]
    except FileNotFoundError:
        return []


ThreadingHTTPServer.allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "DigitalSecretary/1.0"

    def log_message(self, fmt, *args):
        return

    def _headers(self, code=200, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("Vary", "Cookie")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if extra:
            for k, v in extra:
                self.send_header(k, v)
        self.end_headers()

    def _send(self, body: bytes, ctype: str, code=200, extra=None):
        """Invia il corpo compresso quando il browser lo accetta.

        La dashboard è una pagina locale da ~161 KiB: senza compressione ogni
        caricamento trasferisce tutto il markup. Sotto i 1400 byte la
        compressione non conviene e il corpo resta in chiaro.
        """
        headers = list(extra or [])
        if len(body) >= 1400 and "gzip" in self.headers.get("Accept-Encoding", "").lower():
            body = gzip.compress(body, 6)
            headers += [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")]
        headers.append(("Content-Length", str(len(body))))
        self._headers(code, ctype, headers)
        self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", code, extra)

    def _html(self, body: str, code=200, extra=None):
        self._send(body.encode("utf-8"), "text/html; charset=utf-8", code, extra)

    def _pdf(self, path: str, filename: str):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            self._json({"ok": False, "error": "PDF non disponibile"}, 404)
            return
        self._pdf_bytes(data, filename)

    def _pdf_bytes(self, data: bytes, filename: str, disposition: str = "attachment"):
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(filename or ""))[:120] or "preventivo.pdf"
        self._headers(200, "application/pdf", [
            ("Content-Length", str(len(data))),
            ("Content-Disposition", f'{disposition}; filename="{safe_name}"'),
        ])
        self.wfile.write(data)

    def _csv(self, text: str, filename: str):
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(filename or ""))[:120] or "export.csv"
        data = text.encode("utf-8-sig")
        self._headers(200, "text/csv; charset=utf-8", [
            ("Content-Length", str(len(data))),
            ("Content-Disposition", f'attachment; filename="{safe_name}"'),
        ])
        self.wfile.write(data)

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return ""
        m = c.get("ocdash")
        return m.value if m else ""

    def _identity(self) -> dict | None:
        val = self._cookie_token()
        if not val:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                val = auth[7:].strip()
        if not val:
            return None
        session = _live_sessions().get(val)
        if session:
            return {"name": str(session.get("name") or "owner"), "role": str(session.get("role") or "viewer")}
        if _token_matches(val):
            return {"name": "owner", "role": "admin"}
        if identity_store is not None:
            try:
                return identity_store.authenticate(val)
            except Exception:
                return None
        return None

    def _authed(self) -> bool:
        return self._identity() is not None

    def _audit(self, action: str, detail: str = "", ok: bool = True) -> None:
        if identity_store is None:
            return
        try:
            identity_store.audit(getattr(self, "actor", "owner"), action, detail, ok)
        except Exception:
            pass

    def _discard_body(self, max_bytes: int = 1_000_000) -> None:
        """Consuma il body prima di una risposta anticipata.

        Se il server risponde 401/403 senza leggere il body, su Windows il
        client può ricevere un reset (WinError 10053) prima della risposta.
        """
        if getattr(self, "_body_consumed", False):
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            self._body_consumed = True
            self.close_connection = True
            return
        if n <= 0:
            self._body_consumed = True
            return
        if n > max_bytes:
            self._body_consumed = True
            self.close_connection = True
            return
        try:
            self.rfile.read(n)
            self._body_consumed = True
        except Exception:
            self._body_consumed = True
            self.close_connection = True

    def _require(self, role: str = "viewer") -> bool:
        who = self._identity()
        if not who:
            self._discard_body()
            self._json({"ok": False, "error": "auth"}, 401)
            return False
        self.actor = str(who.get("name") or "")
        self.role = str(who.get("role") or "viewer")
        if identity_store is not None and not identity_store.role_allows(self.role, role):
            self._discard_body()
            self._json({"ok": False, "error": "permesso negato", "role": self.role}, 403)
            return False
        return True

    def _body(self, max_bytes: int = 1_000_000) -> bytes:
        if getattr(self, "_body_consumed", False):
            return getattr(self, "_raw_body", b"")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            self.close_connection = True
            self._body_consumed = True
            self._raw_body = b""
            return b""
        if n <= 0:
            self._body_consumed = True
            self._raw_body = b""
            return b""
        if n > max_bytes:
            self._body_consumed = True
            self._raw_body = b""
            self.close_connection = True
            return b""
        self._raw_body = self.rfile.read(n)
        self._body_consumed = True
        return self._raw_body

    def _json_body(self) -> dict:
        try:
            raw = self._body().decode("utf-8")
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _post_ok(self) -> bool:
        if self.headers.get("X-Requested-With", "") != "dashboard":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin not in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}:
            return False
        # I browser moderni inviano Sec-Fetch-Site: se c'è e non è same-origin,
        # la richiesta non arriva dalla nostra dashboard.
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if not origin and fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            try:
                self._html(rendered_page())
            except Exception:
                self._html("<h1>Dashboard non installata</h1>", 500)
            return
        if path == "/logout":
            self._html(
                "Sessione chiusa. <a href='/'>Accedi</a>",
                200,
                [
                    ("Set-Cookie", "ocdash=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"),
                    ("Clear-Site-Data", '"cache", "cookies", "storage"'),
                ],
            )
            return
        if path == "/api/status":
            if not self._require():
                return
            self._json({"ok": True, "data": status()})
            return
        if path == "/api/health":
            if not self._require():
                return
            self._json({"ok": True, "data": health_state()})
            return
        if path == "/api/leads":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["100"])[0])
            except Exception:
                limit = 100
            self._json({"ok": True, "data": read_jsonl(os.path.join(BASE, "leads.jsonl"), limit)})
            return
        if path == "/api/alerts":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["100"])[0])
            except Exception:
                limit = 100
            self._json({"ok": True, "data": read_jsonl(os.path.join(BASE, "alerts.jsonl"), limit)})
            return
        if path == "/api/connectors":
            if not self._require():
                return
            self._json({"ok": True, "data": connector_status()})
            return
        if path == "/api/approvals":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["100"])[0])
            except Exception:
                limit = 100
            self._json({"ok": True, "data": approvals_data(limit)})
            return
        if path == "/api/security":
            if not self._require():
                return
            self._json({"ok": True, "data": security_status()})
            return
        if path == "/api/audit/verify":
            if not self._require("admin"):
                return
            if identity_store is None:
                self._json({"ok": False, "error": "identità non disponibile"}, 503)
                return
            try:
                report = identity_store.verify_audit()
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)[:160]}, 500)
                return
            self._json({"ok": True, "data": report})
            return
        if path == "/api/audit":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                lines = int((q.get("lines") or ["80"])[0])
            except Exception:
                lines = 80
            self._json({"ok": True, "lines": audit_lines(lines)})
            return
        if path == "/api/logs":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                lines = max(10, min(int((q.get("lines") or ["60"])[0]), 300))
            except Exception:
                lines = 60
            _, out, _ = run(["journalctl", "--user", "-u", "openclaw-gateway", "-n", str(lines), "--no-pager", "-o", "cat"], timeout=20)
            self._json({"ok": True, "lines": out.splitlines()})
            return
        if path == "/api/capabilities":
            if not self._require():
                return
            self._json({"ok": True, "data": bot_capabilities()})
            return
        if path == "/api/quotes.csv":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["200"])[0])
            except Exception:
                limit = 200
            self._csv(quotes_csv(limit), "preventivi.csv")
            return
        if path == "/api/quotes":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["60"])[0])
            except Exception:
                limit = 60
            self._json({"ok": True, "data": quotes_data(limit)})
            return
        quote_email_match = re.match(r"^/api/quotes/([A-Za-z0-9-]{6,80})/email$", path)
        if quote_email_match:
            if not self._require():
                return
            self._json(quote_email_preview(quote_email_match.group(1)))
            return
        if path == "/api/quotes/demo/preview":
            if not self._require():
                return
            self._json(demo_quote_preview())
            return
        if path == "/api/quotes/demo.pdf":
            if not self._require():
                return
            try:
                pdf, filename = _demo_quote_pdf()
            except Exception as exc:
                log(f"demo quote pdf failed: {str(exc)[:180]}")
                self._json({"ok": False, "error": str(exc)[:400]})
                return
            self._pdf_bytes(pdf, filename, disposition="inline")
            return
        pdf_match = re.match(r"^/api/quotes/([A-Za-z0-9-]{6,80})\.pdf$", path)
        if pdf_match:
            if not self._require():
                return
            if approval_store is None:
                self._json({"ok": False, "error": "coda non disponibile"}, 404)
                return
            request = approval_store.get_request(pdf_match.group(1))
            if not request or request.get("type") != "quote":
                self._json({"ok": False, "error": "preventivo non trovato"}, 404)
                return
            path_pdf = approval_store.quote_path(request)
            if path_pdf is None:
                self._json({"ok": False, "error": "PDF non disponibile"}, 404)
                return
            payload = request.get("payload") or {}
            filename = quote_pdf.quote_filename(payload) if quote_pdf is not None else "preventivo.pdf"
            log(f"quote pdf downloaded {request.get('id')}")
            self._pdf(str(path_pdf), filename)
            return
        if path == "/api/doctor":
            if not self._require():
                return
            self._json(run_doctor())
            return
        if path == "/api/contacts":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["60"])[0])
            except Exception:
                limit = 60
            lang = "en" if (q.get("lang") or ["it"])[0].lower().startswith("en") else "it"
            self._json({"ok": True, "data": contacts_data(limit, lang=lang)})
            return
        if path == "/api/me":
            if not self._require():
                return
            can_write = True
            if identity_store is not None:
                can_write = identity_store.role_allows(getattr(self, "role", "viewer"), "operator")
            self._json({"ok": True, "user": {
                "name": getattr(self, "actor", "owner"),
                "role": getattr(self, "role", "admin"),
                "can_write": can_write,
            }})
            return
        if path == "/api/stats":
            if not self._require():
                return
            data = stats_view.compute() if stats_view is not None else {"kpis": {}, "daily": [], "empty": True}
            self._json({"ok": True, "data": data})
            return
        if path == "/api/conversations":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["60"])[0])
            except Exception:
                limit = 60
            rows = conversations_view.list_conversations(limit) if conversations_view is not None else []
            self._json({"ok": True, "data": rows})
            return
        conversation_match = re.match(r"^/api/conversations/(%2B|\+)?(\d{6,20})$", path)
        if conversation_match:
            if not self._require():
                return
            phone = "+" + conversation_match.group(2)
            q = parse_qs(urlparse(self.path).query)
            redact = (q.get("redact") or ["1"])[0] != "0"
            try:
                data = conversations_view.transcript(phone, redact=redact) if conversations_view is not None else None
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            if data is None:
                self._json({"ok": False, "error": "modulo conversazioni non disponibile"}, 503)
                return
            self._json({"ok": True, "data": data})
            return
        if path == "/api/usage":
            if not self._require():
                return
            q = parse_qs(urlparse(self.path).query)
            lang = "en" if (q.get("lang") or ["it"])[0].lower().startswith("en") else "it"
            data = quotas_view.status(lang=lang) if quotas_view is not None else {"providers": [], "alerts": []}
            model = router_view.choose(load=len(conversations_view.list_conversations(50)), lang=lang) if (router_view and conversations_view) else {}
            self._json({"ok": True, "data": data, "model": model})
            return
        if path == "/api/users":
            if not self._require("admin"):
                return
            rows = identity_store.list_users() if identity_store is not None else []
            self._json({"ok": True, "data": rows})
            return
        if path == "/api/audit/users":
            if not self._require("admin"):
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                limit = int((q.get("limit") or ["100"])[0])
            except Exception:
                limit = 100
            rows = identity_store.recent_audit(limit) if identity_store is not None else []
            self._json({"ok": True, "data": rows})
            return
        if path == "/api/webhooks":
            if not self._require("admin"):
                return
            rows = webhooks_view.list_endpoints() if webhooks_view is not None else []
            log_rows = webhooks_view.deliveries(40) if webhooks_view is not None else []
            self._json({"ok": True, "data": rows, "deliveries": log_rows, "events": list(webhooks_view.EVENTS) if webhooks_view else []})
            return
        if path == "/api/gdpr/inventory":
            if not self._require("admin"):
                return
            data = gdpr_tools.inventory() if gdpr_tools is not None else {}
            self._json({"ok": True, "data": data})
            return
        if path == "/api/live/summary":
            if not self._require("admin"):
                return
            data = live_view.stats() if live_view is not None else {}
            self._json({"ok": True, "data": data})
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        # Un handler che risponde senza leggere il body può causare un reset
        # della connessione su Windows; consumalo sempre a fine richiesta.
        self._body_consumed = False
        self._raw_body = b""
        try:
            self._handle_post()
        finally:
            self._discard_body()

    def _handle_live(self, path: str) -> None:
        """Ingest server-to-server del plugin: token dedicato, solo loopback."""
        data = self._json_body()
        if live_view is None:
            self._json({"ok": False, "error": "live non disponibile"}, 503)
            return
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._json({"ok": False, "error": "solo loopback"}, 403)
            return
        if not live_view.check_token(self.headers.get("X-Gina-Live", "")):
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        phone = str(data.get("phone") or "").strip()
        text = str(data.get("text") or "")[:1200]
        if not phone or not text:
            self._json({"ok": False, "error": "phone e text obbligatori"}, 400)
            return
        try:
            if path == "/api/live/inbound":
                result = live_view.record_inbound(phone, text, str(data.get("name") or ""))
            elif path == "/api/live/outbound":
                result = live_view.record_outbound(phone, text, str(data.get("question") or ""))
            else:
                self._json({"ok": False, "error": "not found"}, 404)
                return
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)[:160]}, 400)
            return
        self._json({"ok": True, "data": result})

    def _handle_post(self):
        path = urlparse(self.path).path
        if path == "/login":
            raw = self._body(4096).decode("utf-8", "replace")
            form = parse_qs(raw)
            token = (form.get("token") or [""])[0].strip()
            code = (form.get("code") or [""])[0].strip()
            ip = self.client_address[0]
            now = time.time()
            fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < LOGIN_WINDOW]
            LOGIN_FAILS[ip] = fails
            if len(fails) >= LOGIN_MAX_FAILS:
                minutes = max(1, int(LOGIN_WINDOW // 60))
                self._json(
                    {"ok": False, "error": f"Troppi tentativi. Riprova tra {minutes} minuti."},
                    429,
                )
                return
            valid_user = None
            if token and identity_store is not None:
                try:
                    valid_user = identity_store.authenticate(token)
                except Exception:
                    valid_user = None
            if token and (_token_matches(token) or valid_user):
                if identity_store is not None and not identity_store.check_totp(code):
                    LOGIN_FAILS[ip].append(now)
                    log("login failed (2fa)")
                    if identity_store is not None:
                        identity_store.audit("owner", "login.2fa", self.client_address[0], False)
                    self._json({"ok": False, "error": "Codice 2FA non valido."}, 401)
                    return
                global LAST_LOGIN
                LOGIN_FAILS[ip] = []
                LAST_LOGIN = now_iso()
                save_security_state()
                actor = valid_user.get("name") if valid_user else "owner"
                role = str(valid_user.get("role") if valid_user else "admin")
                sid = create_session(str(actor), role)
                if identity_store is not None:
                    identity_store.audit(actor, "login", self.client_address[0], True)
                log(f"login ok ({actor})")
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"ocdash={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict")
                self.end_headers()
                return
            LOGIN_FAILS[ip].append(now)
            log("login failed")
            self._json({"ok": False, "error": "Token non valido."}, 401)
            return
        if not path.startswith("/api/"):
            self._discard_body()
            self._json({"ok": False, "error": "not found"}, 404)
            return
        if path.startswith("/api/live/"):
            self._handle_live(path)
            return
        if not self._require("operator"):
            return
        if not self._post_ok():
            self._discard_body()
            self._json({"ok": False, "error": "csrf"}, 403)
            return
        admin_only = {
            "/api/users", "/api/users/revoke", "/api/webhooks", "/api/webhooks/remove",
            "/api/webhooks/test", "/api/gdpr/delete", "/api/gdpr/export", "/api/model/apply",
        }
        if path in admin_only and identity_store is not None and not identity_store.role_allows(
            getattr(self, "role", "viewer"), "admin"
        ):
            self._audit(path, "permesso negato", False)
            self._json({"ok": False, "error": "permesso negato"}, 403)
            return
        if path in {"/api/contacts/summary", "/api/reminders/run", "/api/quotes/demo"}:
            if _throttled(f"{self.client_address[0]}:{path}", ACTION_SECONDS):
                self._json({"ok": False, "error": "Attendi qualche secondo prima di ripetere."}, 429)
                return
        if path == "/api/restart":
            subprocess.Popen(["systemctl", "--user", "restart", "openclaw-gateway"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log("restart requested")
            self._json({"ok": True, "restarting": True})
            return
        if path == "/api/monitor":
            rc, out, err = run(["python3", os.path.join(BASE, "monitor.py")], timeout=90)
            self._json({"ok": rc == 0, "output": out[-1200:], "error": err[-400:]})
            return
        if path == "/api/health/scan":
            rc, out, err = run([sys.executable, os.path.join(BASE, "healthcheck.py"), "--json"], timeout=120)
            data = None
            try:
                data = json.loads(out[out.find("{"):]) if "{" in out else None
            except Exception:
                data = None
            self._json({"ok": rc == 0 and isinstance(data, dict), "data": data, "error": (err or out)[-500:] if rc != 0 else ""})
            return
        if path == "/api/health/notify":
            rc, out, err = run([sys.executable, os.path.join(BASE, "healthcheck.py"), "--notify", "--force", "--summary"], timeout=120)
            self._json({"ok": rc == 0, "output": out[-500:], "error": err[-400:]})
            return
        if path == "/api/google/renew/start":
            self._json(google_renew_start())
            return
        if path == "/api/google/renew/finish":
            data = self._json_body()
            self._json(google_renew_finish(str(data.get("auth_url") or "")))
            return
        if path == "/api/keys/status":
            self._json({"ok": True, "keys": keys_status()})
            return
        if path == "/api/keys/save":
            data = self._json_body()
            self._json(save_keys(data))
            return
        if path == "/api/keys/test":
            self._json({"ok": True, "results": test_keys()})
            return
        if path == "/api/email/test":
            if _throttled("test-email", 15):
                self._json({"ok": False, "error": "attendi 15 secondi prima di un altro invio"})
                return
            self._json(send_test_email())
            return
        if path == "/api/email/test-quote":
            if _throttled("test-email", 15):
                self._json({"ok": False, "error": "attendi 15 secondi prima di un altro invio"})
                return
            self._json(send_test_quote_email())
            return
        if path == "/api/approvals/approve":
            data = self._json_body()
            result = approve_request(str(data.get("id") or ""))
            self._audit("approval.approve", str(data.get("id") or ""), bool(result.get("ok")))
            self._json(result)
            return
        if path == "/api/approvals/reject":
            data = self._json_body()
            result = reject_request(str(data.get("id") or ""))
            self._audit("approval.reject", str(data.get("id") or ""), bool(result.get("ok")))
            self._json(result)
            return
        if path == "/api/connectors/test":
            CONNECTOR_CACHE["ts"] = 0.0
            data = connector_status()
            if data.get("bridge", {}).get("configured"):
                try:
                    health = bridge_client.health()
                    # I bridge >= 1.2.0 restituiscono healthy/services; un deployment
                    # vecchio non deve sembrare sano solo perché risponde.
                    data["bridge"]["healthy"] = health.get("healthy") is True
                    data["bridge"]["version"] = health.get("version")
                except Exception as exc:
                    data["bridge"]["healthy"] = False
                    data["bridge"]["health_error"] = str(exc)[:180]
            self._json({"ok": True, "data": data})
            return
        if path == "/api/security/rotate":
            rotate_token()
            revoke_sessions()
            sid = create_session("owner", "admin")
            self._json({"ok": True, "rotated": True}, 200, [("Set-Cookie", f"ocdash={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict")])
            return
        if path == "/api/sessions/revoke":
            if not self._require("admin"):
                return
            count = revoke_sessions()
            rotate_token()
            self._audit("sessions.revoke", f"{count} sessioni", True)
            self._json(
                {"ok": True, "revoked": count},
                200,
                [("Set-Cookie", "ocdash=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")],
            )
            return
        if path == "/api/quotes/demo":
            self._json(create_demo_quote())
            return
        if path == "/api/quotes":
            data = self._json_body()
            result = create_manual_quote(data)
            self._audit("quote.create", str(data.get("client_name") or "")[:80], bool(result.get("ok")))
            if result.get("ok") and webhooks_view is not None:
                try:
                    webhooks_view.emit("quote.created", {
                        "number": (result.get("request") or {}).get("payload", {}).get("number", ""),
                        "total": ((result.get("request") or {}).get("payload", {}).get("totals") or {}).get("total", 0),
                    })
                except Exception:
                    pass
            self._json(result)
            return
        if path == "/api/conversations/handoff":
            data = self._json_body()
            phone = str(data.get("phone") or "").strip()
            if not phone:
                self._json({"ok": False, "error": "numero mancante"}, 400)
                return
            active = bool(data.get("active"))
            try:
                result = conversations_view.set_handoff(
                    phone, active, str(data.get("operator") or getattr(self, "actor", ""))[:60]
                ) if conversations_view is not None else {"ok": False}
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._audit("handoff", f"{phone} active={active}")
            self._json({"ok": True, **result})
            return
        if path == "/api/conversations/send":
            data = self._json_body()
            phone = str(data.get("phone") or "").strip()
            text = str(data.get("text") or "")
            if conversations_view is None:
                self._json({"ok": False, "error": "modulo conversazioni non disponibile"}, 503)
                return
            if _throttled(f"{self.client_address[0]}:chat-send", SEND_SECONDS):
                self._json({"ok": False, "error": "Attendi prima di inviare un altro messaggio."}, 429)
                return
            try:
                result = conversations_view.send_message(
                    phone, text, str(getattr(self, "actor", "") or "operatore")
                )
            except PermissionError as exc:
                self._json({"ok": False, "error": str(exc), "handoff": False}, 409)
                return
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)[:200]}, 502)
                return
            # Il testo del messaggio non finisce nei log: solo numero ed esito.
            self._audit("chat.send", phone, bool(result.get("ok")))
            log(f"chat.send {phone}: {'ok' if result.get('ok') else 'failed'}")
            self._json(result, 200 if result.get("ok") else 502)
            return
        if path == "/api/users":
            data = self._json_body()
            if identity_store is None:
                self._json({"ok": False, "error": "modulo utenti non disponibile"}, 503)
                return
            try:
                created = identity_store.create_user(
                    str(data.get("name") or ""), str(data.get("role") or "operator")
                )
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._audit("user.create", f"{created['name']} {created['role']}")
            self._json({"ok": True, "user": created})
            return
        if path == "/api/users/revoke":
            data = self._json_body()
            name = str(data.get("name") or "").strip()
            changed = identity_store.revoke_user(name) if identity_store is not None else False
            self._audit("user.revoke", name, changed)
            self._json({"ok": changed, "error": "" if changed else "utente non trovato"})
            return
        if path == "/api/webhooks":
            data = self._json_body()
            if webhooks_view is None:
                self._json({"ok": False, "error": "modulo webhook non disponibile"}, 503)
                return
            try:
                entry = webhooks_view.add_endpoint(
                    str(data.get("url") or ""),
                    [str(e) for e in (data.get("events") or [])] or None,
                )
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._audit("webhook.add", str(entry.get("url"))[:120])
            self._json({"ok": True, "endpoint": entry})
            return
        if path == "/api/webhooks/remove":
            data = self._json_body()
            removed = webhooks_view.remove_endpoint(int(data.get("id") or 0)) if webhooks_view is not None else False
            self._audit("webhook.remove", str(data.get("id")), removed)
            self._json({"ok": removed, "error": "" if removed else "endpoint non trovato"})
            return
        if path == "/api/webhooks/test":
            data = self._json_body()
            result = webhooks_view.test_endpoint(int(data.get("id") or 0)) if webhooks_view is not None else {"ok": False}
            self._audit("webhook.test", str(data.get("id")), bool(result.get("ok")))
            self._json(result)
            return
        if path == "/api/gdpr/export":
            data = self._json_body()
            phone = str(data.get("phone") or "").strip()
            if gdpr_tools is None or not phone:
                self._json({"ok": False, "error": "numero mancante"}, 400)
                return
            self._audit("gdpr.export", phone)
            self._json({"ok": True, "data": gdpr_tools.export_contact(phone)})
            return
        if path == "/api/gdpr/delete":
            data = self._json_body()
            phone = str(data.get("phone") or "").strip()
            if gdpr_tools is None or not phone:
                self._json({"ok": False, "error": "numero mancante"}, 400)
                return
            if str(data.get("confirm") or "") != "DELETE":
                self._json({"ok": False, "error": "conferma richiesta: invia confirm=DELETE"}, 400)
                return
            result = gdpr_tools.delete_contact(phone)
            self._audit("gdpr.delete", phone)
            self._json({"ok": True, **result})
            return
        if path == "/api/model/apply":
            data = self._json_body()
            if router_view is None:
                self._json({"ok": False, "error": "router non disponibile"}, 503)
                return
            model = str(data.get("model") or "").strip()
            if not model:
                decision = router_view.choose(load=0)
                model = decision["primary"]
            result = router_view.apply_choice(model)
            self._audit("model.apply", model, bool(result.get("ok")))
            self._json(result)
            return
        if path == "/api/contacts/summary":
            data = self._json_body()
            self._json(send_contacts_summary(dry=bool(data.get("dry_run"))))
            return
        if path == "/api/reminders/run":
            data = self._json_body()
            self._json(run_reminders(dry=bool(data.get("dry_run")), force=bool(data.get("force"))))
            return
        self._json({"ok": False, "error": "not found"}, 404)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Dashboard privata di Gina. Ascolta solo su 127.0.0.1 e richiede il token locale.",
    )
    parser.add_argument("--port", type=int, default=None,
                        help="porta locale (default: dashboard_port della configurazione o 8787)")
    args = parser.parse_args(argv)
    global PORT
    if args.port is not None:
        PORT = max(1024, min(int(args.port), 65535))
    os.makedirs(BASE, exist_ok=True)
    log(f"dashboard start on {HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
