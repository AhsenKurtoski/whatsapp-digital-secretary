#!/usr/bin/env python3
"""Watchdog della segreteria: gateway, WhatsApp, dashboard e timer.

Eseguito dal timer systemd ogni 5 minuti. Non stampa mai chiavi o token.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import config

HOME = os.path.expanduser("~")
CFG = config.load()
BASE = str(config.BASE)
STATE = os.path.join(BASE, "watchdog_state.json")
LOG = os.path.join(BASE, "watchdog.log")
ROME = ZoneInfo(str(CFG.get("timezone") or "Europe/Rome"))
UNITS = {
    "gateway": "openclaw-gateway.service",
    "dashboard": "openclaw-dashboard.service",
    "monitor": "openclaw-secretary-monitor.timer",
    "digest": "openclaw-secretary-digest.timer",
}


def now_iso() -> str:
    return datetime.now(ROME).isoformat(timespec="seconds")


def log(msg: str) -> None:
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
        # Keep the log bounded without touching the rest of the system.
        if os.path.getsize(LOG) > 512_000:
            with open(LOG, encoding="utf-8") as f:
                lines = f.readlines()[-2000:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(data: dict) -> None:
    try:
        os.makedirs(BASE, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


def is_active(unit: str) -> bool:
    return run(["systemctl", "--user", "is-active", unit], timeout=10)[1] == "active"


def gateway_health() -> bool:
    for _ in range(2):
        try:
            with urllib.request.urlopen("http://127.0.0.1:18789/health", timeout=4) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def dashboard_health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def recent_whatsapp_activity(minutes: int = 8) -> bool:
    """True se il gateway ha scambiato messaggi WhatsApp di recente.

    La CLI `channels status` impiega ~18 secondi e sotto carico può andare in
    timeout anche quando il canale è connesso. I log del gateway sono la prova
    migliore: un messaggio inviato o ricevuto negli ultimi minuti significa che
    WhatsApp è vivo.
    """
    rc, out, _ = run(
        ["journalctl", "--user", "-u", "openclaw-gateway", "--since", f"-{minutes} min", "--no-pager"],
        timeout=15,
    )
    if rc != 0 or not out:
        return False
    low = out.lower()
    return any(
        marker in low
        for marker in (
            "listening for whatsapp inbound",
            "sending message ->",
            "sent message ",
            "inbound message +",
        )
    )


def whatsapp_state() -> str:
    node = os.path.join(HOME, ".nvm", "versions", "node", "v24.22.1", "bin", "node")
    cli = os.path.join(HOME, ".nvm", "versions", "node", "v24.22.1", "lib", "node_modules", "openclaw", "dist", "index.js")
    rc, out, _ = run([node, cli, "channels", "status", "--json"], timeout=40)
    if rc != 0:
        if recent_whatsapp_activity():
            log("channels status timeout; recent WhatsApp traffic -> healthy")
            return "healthy"
        return "unreachable"
    try:
        data = json.loads(out[out.find("{"):])
        ch = (data.get("channels") or {}).get("whatsapp") or {}
        return str(ch.get("healthState") or "unknown")
    except Exception:
        if recent_whatsapp_activity():
            log("channels status parse error; recent WhatsApp traffic -> healthy")
            return "healthy"
        return "parse-error"


def restart(unit: str) -> bool:
    rc, _, err = run(["systemctl", "--user", "restart", unit], timeout=45)
    log(f"restart {unit} rc={rc} err={err[:160]}")
    return rc == 0


def start(unit: str) -> bool:
    rc, _, err = run(["systemctl", "--user", "start", unit], timeout=30)
    log(f"start {unit} rc={rc} err={err[:160]}")
    return rc == 0


def check(dry: bool = False) -> dict:
    state = load_state()
    result = {
        "ts": now_iso(),
        "gateway_active": is_active(UNITS["gateway"]),
        "gateway_health": gateway_health(),
        "dashboard_active": is_active(UNITS["dashboard"]),
        "dashboard_health": dashboard_health(),
        "monitor_active": is_active(UNITS["monitor"]),
        "digest_active": is_active(UNITS["digest"]),
        "whatsapp": "unknown",
        "actions": [],
    }

    if result["gateway_health"]:
        result["whatsapp"] = whatsapp_state()
    elif result["gateway_active"]:
        result["whatsapp"] = "gateway-unhealthy"
    else:
        result["whatsapp"] = "gateway-down"

    # Gateway: due controlli falliti consecutivi prima del riavvio.
    if not result["gateway_health"]:
        state["gateway_fail"] = int(state.get("gateway_fail", 0)) + 1
        if state["gateway_fail"] >= 2:
            log(f"gateway unhealthy x{state['gateway_fail']}; restarting")
            if not dry:
                restart(UNITS["gateway"])
                result["actions"].append("gateway-restart")
            state["gateway_fail"] = 0
    else:
        state["gateway_fail"] = 0

    # WhatsApp: un reconnect momentaneo non deve causare riavvii a raffica
    # (WhatsApp risponde 408 se il client si riconnette troppo spesso).
    # Riavvio solo dopo 30 minuti di stato non sano e al massimo ogni 30 minuti.
    if result["whatsapp"] not in ("healthy", "connected", "ready"):
        state["wa_fail"] = int(state.get("wa_fail", 0)) + 1
        last_wa_restart = float(state.get("wa_restart_ts", 0) or 0)
        if state["wa_fail"] >= 6 and time.time() - last_wa_restart > 1800:
            log(f"whatsapp state={result['whatsapp']} x{state['wa_fail']}; restarting gateway")
            if not dry:
                restart(UNITS["gateway"])
                result["actions"].append("whatsapp-restart")
            state["wa_fail"] = 0
            state["wa_restart_ts"] = time.time()
    else:
        state["wa_fail"] = 0

    # Dashboard: riavvio immediato se il servizio è spento o non risponde.
    if not result["dashboard_active"]:
        log(f"dashboard active={result['dashboard_active']} health={result['dashboard_health']}; starting")
        if not dry:
            start(UNITS["dashboard"])
            result["actions"].append("dashboard-start")
    elif not result["dashboard_health"]:
        log(f"dashboard active but unhealthy; restarting")
        if not dry:
            restart(UNITS["dashboard"])
            result["actions"].append("dashboard-restart")

    # Timer: li riattiva se qualcuno li ha fermati.
    for key in ("monitor", "digest"):
        if not result[key + "_active"]:
            log(f"{key} timer inactive; starting")
            if not dry:
                start(UNITS[key])
                result["actions"].append(key + "-start")

    state["last_check"] = result["ts"]
    state["last_result"] = {
        "gateway": result["gateway_health"],
        "dashboard": result["dashboard_health"],
        "whatsapp": result["whatsapp"],
        "actions": result["actions"],
    }
    if not dry:
        save_state(state)

    log(json.dumps(result, ensure_ascii=False))
    return result


def self_test() -> int:
    print(json.dumps({
        "ok": True,
        "units": {k: is_active(v) for k, v in UNITS.items()},
        "gateway_health": gateway_health(),
        "dashboard_health": dashboard_health(),
        "state_file": os.path.exists(STATE),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    result = check(dry=args.dry_run)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result["gateway_health"] or result["actions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
