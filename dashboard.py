#!/usr/bin/env python3
"""Dashboard privata della segreteria digitale.

Gira solo su 127.0.0.1. Richiede un token locale e un cookie HttpOnly.
"""
from __future__ import annotations

import hmac
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
LAST_LOGIN: str | None = None
TOKEN_ROTATED_AT: str | None = None
MAX_POOL_KEYS = 7


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


def next_elapse(unit: str) -> str:
    _, out, _ = run(["systemctl", "--user", "show", unit, "-p", "NextElapseUSecRealtime", "--value"], timeout=8)
    return out.strip() or "-"


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
        mcp_tools = [str(x).split("__", 1)[-1] for x in (raw.get("tools") or [])]
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
            "whatsapp_debounce_ms": int((((messages.get("inbound") or {}).get("byChannel") or {}).get("whatsapp") or 0)),
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
            "tools": "approvazioni email/calendario",
        },
        "timers": {
            "monitor": {"active": is_active("openclaw-secretary-monitor.timer"), "next": next_elapse("openclaw-secretary-monitor.timer")},
            "digest": {"active": is_active("openclaw-secretary-digest.timer"), "next": next_elapse("openclaw-secretary-digest.timer")},
            "watchdog": {"active": is_active("openclaw-watchdog.timer"), "next": next_elapse("openclaw-watchdog.timer")},
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
        "max_login_fails": 10,
        "lock_window_seconds": 300,
        "session_days": 7,
    }


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


def _execute_email(payload: dict) -> str:
    bridge = None
    if bridge_client is not None:
        try:
            bridge = bridge_client.load_config(BRIDGE_CONFIG)
        except Exception as exc:
            log(f"bridge config error: {str(exc)[:180]}")
    if bridge:
        result = bridge_client.send_email(payload, bridge)
        message_id = str((result or {}).get("id") or "")
        return f"Email inviata a {payload.get('to')} via bridge Google" + (f" (id {message_id})" if message_id else "")

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
    try:
        rc, out, err = run(cmd, timeout=90, extra_env=gog_env())
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
    return f"Email inviata a {payload.get('to')}" + (f" (id {message_id})" if message_id else "")


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
        if request.get("type") == "email":
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
    return {"ok": True, "id": request_id, "result": result, "data": approvals_data(80)}


def reject_request(request_id: str) -> dict:
    if approval_store is None:
        return {"ok": False, "error": "coda approvazioni non disponibile"}
    try:
        request = approval_store.set_status(request_id, "rejected")
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

    def _json(self, obj, code=200, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        headers = [("Content-Length", str(len(body)))] + (extra or [])
        self._headers(code, "application/json; charset=utf-8", headers)
        self.wfile.write(body)

    def _html(self, body: str, code=200, extra=None):
        data = body.encode("utf-8")
        self._headers(code, "text/html; charset=utf-8", [("Content-Length", str(len(data)))] + (extra or []))
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

    def _authed(self) -> bool:
        val = self._cookie_token()
        if not val:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                val = auth[7:].strip()
        return bool(val) and hmac.compare_digest(val, DASH_TOKEN)

    def _require(self) -> bool:
        if self._authed():
            return True
        self._json({"ok": False, "error": "auth"}, 401)
        return False

    def _body(self, max_bytes: int = 1_000_000) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0 or n > max_bytes:
            return b""
        return self.rfile.read(n)

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
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            try:
                with open(HTML_FILE, encoding="utf-8") as f:
                    page = f.read()
                page = (page.replace("{{BUSINESS_NAME}}", str(CFG.get("business_name") or "Nome Attività"))
                            .replace("{{OWNER_NAME}}", str(CFG.get("owner_name") or "Titolare"))
                            .replace("{{BOT_NAME}}", str(CFG.get("bot_name") or "Segreteria Digitale"))
                            .replace("{{GOOGLE_ACCOUNT}}", GOOGLE_ACCOUNT)
                            .replace("{{OWNER_NUMBER}}", OWNER))
                self._html(page)
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
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login":
            raw = self._body(4096).decode("utf-8", "replace")
            form = parse_qs(raw)
            token = (form.get("token") or [""])[0].strip()
            ip = self.client_address[0]
            now = time.time()
            fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 300]
            LOGIN_FAILS[ip] = fails
            if len(fails) >= 10:
                self._json({"ok": False, "error": "Troppi tentativi. Riprova tra 5 minuti."}, 429)
                return
            if token and hmac.compare_digest(token, DASH_TOKEN):
                global LAST_LOGIN
                LOGIN_FAILS[ip] = []
                LAST_LOGIN = now_iso()
                save_security_state()
                log("login ok")
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"ocdash={DASH_TOKEN}; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict")
                self.end_headers()
                return
            LOGIN_FAILS[ip].append(now)
            log("login failed")
            self._json({"ok": False, "error": "Token non valido."}, 401)
            return
        if not path.startswith("/api/"):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        if not self._require():
            return
        if not self._post_ok():
            self._json({"ok": False, "error": "csrf"}, 403)
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
            self._json(send_test_email())
            return
        if path == "/api/approvals/approve":
            data = self._json_body()
            self._json(approve_request(str(data.get("id") or "")))
            return
        if path == "/api/approvals/reject":
            data = self._json_body()
            self._json(reject_request(str(data.get("id") or "")))
            return
        if path == "/api/connectors/test":
            CONNECTOR_CACHE["ts"] = 0.0
            data = connector_status()
            if data.get("bridge", {}).get("configured"):
                try:
                    health = bridge_client.health()
                    data["bridge"]["healthy"] = bool(health.get("ok"))
                    data["bridge"]["version"] = health.get("version")
                except Exception as exc:
                    data["bridge"]["healthy"] = False
                    data["bridge"]["health_error"] = str(exc)[:180]
            self._json({"ok": True, "data": data})
            return
        if path == "/api/security/rotate":
            new = rotate_token()
            self._json({"ok": True, "rotated": True}, 200, [("Set-Cookie", f"ocdash={new}; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict")])
            return
        self._json({"ok": False, "error": "not found"}, 404)


def main():
    os.makedirs(BASE, exist_ok=True)
    log("dashboard start")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
