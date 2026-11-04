#!/usr/bin/env python3
"""Diagnosi completa dell'installazione della segreteria digitale.

Controlla ciò che serve davvero perché preventivi, riepiloghi e promemoria
funzionino: file installati, scheda Preventivi nella dashboard, persona,
policy anti-raffica, tool MCP, motore PDF, servizi systemd e API locali.

Non modifica nulla e non stampa segreti. Ogni problema ha un'azione concreta.

    python3 doctor.py            # report completo
    python3 doctor.py --short    # solo problemi
    python3 doctor.py --json     # per la dashboard
    python3 doctor.py --offline  # salta systemd e HTTP
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import config

REQUIRED_FILES = [
    "dashboard.py",
    "dashboard.html",
    "quote_pdf.py",
    "contacts.py",
    "reminders.py",
    "gateway_policy.py",
    "approval_store.py",
    "approval_mcp.py",
    "approval_setup.py",
    "email_template.py",
    "bridge_client.py",
    "monitor.py",
    "watchdog.py",
    "digest.py",
    "healthcheck.py",
    "doctor.py",
]
DASHBOARD_MARKERS = ['data-tab="quotes"', 'id="quotesBody"', 'data-tab="contacts"']
PERSONA_MARKERS = ["Non sono abilitato", "Messaggi ripetuti", "request_quote"]
QUOTE_MARKERS = ["request_quote"]
UNITS_FAIL = ["openclaw-gateway.service", "openclaw-dashboard.service"]
UNITS_WARN = [
    "openclaw-watchdog.timer",
    "openclaw-secretary-monitor.timer",
    "openclaw-secretary-digest.timer",
    "openclaw-secretary-health.timer",
    "openclaw-secretary-reminders.timer",
]
ICONS = {"ok": "✅", "warn": "🟡", "fail": "❌", "info": "ℹ️"}


def _check(check_id: str, label: str, status: str, detail: str, action: str = "") -> dict:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "action": action,
    }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _http(url: str, token: str = "", timeout: float = 4.0) -> tuple[int, bytes]:
    headers = {"User-Agent": "DigitalSecretaryDoctor/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(4000)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(4000)
        except Exception:
            return exc.code, b""


def run_checks(
    base: Path,
    config_path: Path,
    openclaw_config: Path,
    workspace: Path,
    *,
    services: bool = True,
    http: bool = True,
) -> list[dict]:
    base = Path(base)
    config_path = Path(config_path)
    openclaw_config = Path(openclaw_config)
    workspace = Path(workspace)
    cfg = _read_json(config_path)
    checks: list[dict] = []

    # 1. File installati
    missing = [name for name in REQUIRED_FILES if not (base / name).is_file()]
    if missing:
        checks.append(_check(
            "files", "File installati", "fail",
            "Mancano: " + ", ".join(missing),
            "Aggiorna il repo (git pull) e rilancia: bash scripts/install.sh",
        ))
    else:
        checks.append(_check("files", "File installati", "ok", f"{len(REQUIRED_FILES)} file presenti."))

    # 2. Scheda Preventivi/Contatti davvero installata
    html = _read(base / "dashboard.html")
    if not html:
        checks.append(_check(
            "dashboard_ui", "Dashboard aggiornata", "fail",
            "dashboard.html non leggibile o assente.",
            "Rilancia bash scripts/install.sh dal repo aggiornato.",
        ))
    else:
        missing_markers = [marker for marker in DASHBOARD_MARKERS if marker not in html]
        if missing_markers:
            checks.append(_check(
                "dashboard_ui", "Dashboard aggiornata", "fail",
                "La dashboard installata non contiene le schede Preventivi/Contatti.",
                "git pull && bash scripts/install.sh, poi ricarica con Ctrl+Shift+R.",
            ))
        else:
            checks.append(_check(
                "dashboard_ui", "Dashboard aggiornata", "ok",
                "Schede Preventivi e Contatti presenti.",
            ))

    # 3. Persona: perimetro e anti-raffica
    persona = _read(workspace / "AGENTS.md")
    if not persona:
        checks.append(_check(
            "persona", "Persona del bot", "fail",
            "AGENTS.md non trovato: il bot non ha le regole di perimetro.",
            "python3 setup.py --non-interactive, poi riavvia il gateway.",
        ))
    else:
        missing_rules = [marker for marker in PERSONA_MARKERS if marker not in persona]
        if missing_rules:
            checks.append(_check(
                "persona", "Persona del bot", "fail",
                "Regole mancanti: " + ", ".join(missing_rules),
                "python3 setup.py --non-interactive, poi: systemctl --user restart openclaw-gateway",
            ))
        else:
            checks.append(_check(
                "persona", "Persona del bot", "ok",
                "Perimetro, anti-raffica e preventivi presenti.",
            ))

    # 4. Configurazione attività
    if not cfg:
        checks.append(_check(
            "config", "Configurazione attività", "fail",
            f"config.json non leggibile: {config_path}",
            "python3 setup.py",
        ))
    else:
        owner = str(cfg.get("owner_number") or "").strip()
        if not re.fullmatch(r"\+?[1-9]\d{6,15}", owner.replace(" ", "")):
            checks.append(_check(
                "config", "Configurazione attività", "fail",
                "owner_number assente o non valido: i riepiloghi non possono arrivare.",
                "python3 setup.py e inserisci il numero in formato +39…",
            ))
        elif not str(cfg.get("google_account") or "").strip():
            checks.append(_check(
                "config", "Configurazione attività", "warn",
                "google_account vuoto: i preventivi non possono essere inviati via email.",
                "Imposta google_account in config.json o attiva il bridge.",
            ))
        else:
            checks.append(_check(
                "config", "Configurazione attività", "ok",
                f"{cfg.get('business_name') or 'Attività'} · IVA {cfg.get('vat_rate', 22)}% · preventivi {cfg.get('quote_prefix', 'PREV')}",
            ))

    # 5. Policy anti-raffica
    gateway = _read_json(openclaw_config)
    if not gateway:
        checks.append(_check(
            "policy", "Anti-raffica WhatsApp", "fail",
            f"openclaw.json non leggibile: {openclaw_config}",
            "Verifica l'installazione di OpenClaw.",
        ))
    else:
        messages = gateway.get("messages") if isinstance(gateway.get("messages"), dict) else {}
        inbound = messages.get("inbound") if isinstance(messages.get("inbound"), dict) else {}
        by_channel = inbound.get("byChannel") if isinstance(inbound.get("byChannel"), dict) else {}
        queue = messages.get("queue") if isinstance(messages.get("queue"), dict) else {}
        queue_by_channel = queue.get("byChannel") if isinstance(queue.get("byChannel"), dict) else {}
        debounce = by_channel.get("whatsapp") or inbound.get("debounceMs")
        mode = queue_by_channel.get("whatsapp") or queue.get("mode")
        if not debounce:
            checks.append(_check(
                "policy", "Anti-raffica WhatsApp", "warn",
                "Nessun debounce per WhatsApp: una raffica può creare più turni.",
                "python3 ~/.openclaw/secretary/gateway_policy.py, poi riavvia il gateway.",
            ))
        else:
            checks.append(_check(
                "policy", "Anti-raffica WhatsApp", "ok",
                f"debounce {debounce} ms · coda {mode or 'default'}",
            ))

    # 6. Tool MCP: il preventivo deve essere esposto al bot
    try:
        import approval_mcp

        tool_names = {tool.get("name") for tool in approval_mcp.TOOLS}
    except Exception as exc:
        tool_names = set()
        checks.append(_check(
            "mcp_module", "Modulo approvazioni", "fail",
            f"approval_mcp non importabile: {str(exc)[:140]}",
            "git pull && bash scripts/install.sh",
        ))
    missing_tools = [name for name in QUOTE_MARKERS if name not in tool_names]
    if missing_tools:
        checks.append(_check(
            "mcp_tools", "Tool del bot", "fail",
            "Manca il tool: " + ", ".join(missing_tools),
            "bash scripts/install.sh (reinstalla approval_mcp.py).",
        ))
    else:
        checks.append(_check("mcp_tools", "Tool del bot", "ok", "request_quote disponibile."))

    servers = ((gateway.get("mcp") or {}).get("servers") or {}) if isinstance(gateway.get("mcp"), dict) else {}
    secretary = servers.get("secretary") if isinstance(servers.get("secretary"), dict) else {}
    include = ((secretary.get("toolFilter") or {}).get("include")) if isinstance(secretary.get("toolFilter"), dict) else None
    if not secretary:
        checks.append(_check(
            "mcp_config", "Approvazioni collegate a OpenClaw", "fail",
            "Il server MCP 'secretary' non è configurato in openclaw.json.",
            "python3 ~/.openclaw/secretary/approval_setup.py, poi riavvia il gateway.",
        ))
    elif isinstance(include, list) and "request_quote" not in include:
        checks.append(_check(
            "mcp_config", "Approvazioni collegate a OpenClaw", "fail",
            "request_quote è filtrato fuori dal server MCP: il bot non può creare il PDF.",
            "python3 ~/.openclaw/secretary/approval_setup.py, poi riavvia il gateway.",
        ))
    else:
        checks.append(_check(
            "mcp_config", "Approvazioni collegate a OpenClaw", "ok",
            "request_email · request_calendar · request_quote",
        ))

    # 7. Motore PDF: genera davvero un documento
    try:
        import quote_pdf

        sample = quote_pdf.demo_quote(cfg or None)
        data = quote_pdf.render_quote_pdf(sample)
        valid = data.startswith(b"%PDF-1.4") and b"PREVENTIVO" in data and len(data) > 1500
        if valid:
            checks.append(_check(
                "pdf_engine", "Generatore PDF", "ok",
                f"PDF demo valido ({len(data)} byte, totale {quote_pdf.money_text(sample['totals']['total'])}).",
            ))
        else:
            checks.append(_check(
                "pdf_engine", "Generatore PDF", "fail",
                "Il PDF generato non è valido.",
                "git pull && bash scripts/install.sh",
            ))
    except Exception as exc:
        checks.append(_check(
            "pdf_engine", "Generatore PDF", "fail",
            f"quote_pdf non funziona: {str(exc)[:160]}",
            "git pull && bash scripts/install.sh",
        ))

    quotes_dir = base / "quotes"
    if quotes_dir.is_dir():
        mode = oct(quotes_dir.stat().st_mode & 0o777) if os.name != "nt" else "windows"
        checks.append(_check("quotes_dir", "Cartella preventivi", "ok", f"{quotes_dir} · {mode}"))
    else:
        checks.append(_check(
            "quotes_dir", "Cartella preventivi", "info",
            "Non ancora creata: nasce al primo preventivo.",
        ))

    # 8. Bridge Google e plugin di guardia
    bridge = _read_json(base / "google_bridge.json")
    if bridge:
        url = str(bridge.get("url") or "")
        ok = url.startswith("https://script.google.com/macros/s/") and len(str(bridge.get("secret") or "")) >= 32
        checks.append(_check(
            "bridge", "Bridge Google", "ok" if ok else "fail",
            "Configurato (allegati PDF inclusi)." if ok else "Configurazione bridge non valida.",
            "" if ok else "Rilancia installa-bridge.sh o verifica google_bridge.json.",
        ))
    else:
        checks.append(_check(
            "bridge", "Bridge Google", "info",
            "Non configurato: le email usano gog (OAuth locale).",
        ))

    plugin = base / "plugins" / "secretary-guard" / "index.js"
    plugin_text = _read(plugin)
    if "duplicate_suppressed" in plugin_text:
        checks.append(_check("plugin", "Guardia anti-duplicati", "ok", "Plugin aggiornato installato."))
    else:
        checks.append(_check(
            "plugin", "Guardia anti-duplicati", "warn",
            "Il plugin installato non contiene l'anti-duplicati.",
            "bash scripts/install.sh e riavvia il gateway.",
        ))

    # 9. Chiavi AI presenti (senza mai leggerne il valore)
    secrets = _read(base / "secrets.env")
    keys = [
        line for line in secrets.splitlines()
        if "=" in line and not line.strip().startswith("#")
        and line.split("=", 1)[0].strip() in {
            "GEMINI_API_KEY", "GEMINI_API_KEYS", "GROQ_API_KEY",
            "GROQ_API_KEY_2", "GROQ_API_KEY_3",
        }
    ]
    checks.append(_check(
        "ai_keys", "Chiavi AI", "ok" if keys else "warn",
        f"{len(keys)} chiavi configurate." if keys else "Nessuna chiave AI trovata.",
        "" if keys else "Dashboard → Chiavi, oppure modifica ~/.openclaw/secretary/secrets.env.",
    ))

    # 10. Servizi systemd e timer
    if services:
        for unit in UNITS_FAIL + UNITS_WARN:
            severity = "fail" if unit in UNITS_FAIL else "warn"
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", unit],
                    capture_output=True, text=True, timeout=8,
                )
                state = (result.stdout or "").strip() or "unknown"
            except Exception:
                checks.append(_check(
                    "services", "Servizi systemd", "info",
                    "systemd non disponibile in questo ambiente (Docker/Windows).",
                ))
                break
            if state == "active":
                checks.append(_check("service:" + unit, unit, "ok", "attivo"))
            else:
                checks.append(_check(
                    "service:" + unit, unit, severity,
                    f"stato: {state}",
                    "systemctl --user restart " + unit if severity == "fail"
                    else "bash scripts/install.sh (abilita i timer)",
                ))

        reminders_unit = Path.home() / ".config" / "systemd" / "user" / "openclaw-secretary-reminders.timer"
        checks.append(_check(
            "reminders_unit", "Timer promemoria installato", "ok" if reminders_unit.exists() else "warn",
            str(reminders_unit) if reminders_unit.exists() else "Unità del timer non trovata.",
            "" if reminders_unit.exists() else "bash scripts/install.sh",
        ))

    # 11. API locali: dashboard, stato, preventivi
    if http:
        port = int(cfg.get("dashboard_port") or 8787)
        root = f"http://127.0.0.1:{port}"
        token = _read(base / "dashboard_token").strip()
        try:
            status, _ = _http(root + "/")
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            checks.append(_check("http_dashboard", "Dashboard locale", "ok", f"{root} risponde."))
        except Exception as exc:
            checks.append(_check(
                "http_dashboard", "Dashboard locale", "fail",
                f"{root} non raggiungibile ({str(exc)[:80]}).",
                "systemctl --user restart openclaw-dashboard",
            ))
        if token:
            try:
                status, body = _http(root + "/api/quotes", token)
                payload = json.loads(body.decode("utf-8", "replace") or "{}")
                if status == 200 and payload.get("ok"):
                    checks.append(_check(
                        "http_quotes", "API preventivi", "ok",
                        f"{len((payload.get('data') or {}).get('quotes') or [])} preventivi registrati.",
                    ))
                else:
                    checks.append(_check(
                        "http_quotes", "API preventivi", "fail",
                        f"Risposta inattesa (HTTP {status}).",
                        "Riavvia la dashboard dopo l'aggiornamento.",
                    ))
            except Exception as exc:
                checks.append(_check(
                    "http_quotes", "API preventivi", "fail",
                    f"Chiamata fallita: {str(exc)[:100]}",
                    "systemctl --user restart openclaw-dashboard",
                ))
            try:
                status, _ = _http(root + "/api/quotes/apr-00000000-000000-0000.pdf")
                if status == 401:
                    checks.append(_check("http_pdf", "Download PDF protetto", "ok", "Richiede il token."))
                else:
                    checks.append(_check(
                        "http_pdf", "Download PDF protetto", "fail",
                        f"HTTP {status}: la rotta PDF non è quella attesa.",
                        "Riavvia la dashboard dopo git pull && bash scripts/install.sh.",
                    ))
            except Exception:
                checks.append(_check("http_pdf", "Download PDF protetto", "warn", "Verifica non eseguibile."))

    return checks


def summarize(checks: list[dict]) -> dict:
    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
    for check in checks:
        counts[check.get("status", "info")] = counts.get(check.get("status", "info"), 0) + 1
    overall = "critical" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "overall": overall, "counts": counts, "checks": checks}


def render(report: dict, short: bool = False) -> str:
    lines = [f"Segreteria digitale — diagnosi {report['generated_at'][:16].replace('T', ' ')}"]
    for check in report["checks"]:
        if short and check["status"] in {"ok", "info"}:
            continue
        lines.append(f"{ICONS.get(check['status'], '·')} {check['label']}: {check['detail']}")
        if check.get("action"):
            lines.append(f"   → {check['action']}")
    counts = report["counts"]
    lines.append("")
    lines.append(
        f"Risultato: {counts['fail']} critici · {counts['warn']} avvisi · {counts['ok']} ok"
    )
    if counts["fail"] == 0:
        lines.append("Nessun blocco: preventivi, contatti e promemoria sono installati.")
    return "\n".join(lines)


def self_test() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="secretary-doctor-"))
    base = tmp / "base"
    workspace = tmp / "workspace"
    base.mkdir(parents=True)
    workspace.mkdir(parents=True)
    for name in REQUIRED_FILES:
        (base / name).write_text("# fixture\n", encoding="utf-8")
    (base / "dashboard.html").write_text('data-tab="quotes" id="quotesBody" data-tab="contacts"', encoding="utf-8")
    (workspace / "AGENTS.md").write_text(
        "Non sono abilitato · Messaggi ripetuti · request_quote", encoding="utf-8"
    )
    config_path = tmp / "config.json"
    config_path.write_text(json.dumps({
        "business_name": "Demo", "owner_number": "+393331112222",
        "google_account": "demo@example.com",
    }), encoding="utf-8")
    gateway_path = tmp / "openclaw.json"
    gateway_path.write_text(json.dumps({
        "messages": {"inbound": {"byChannel": {"whatsapp": 1800}}, "queue": {"byChannel": {"whatsapp": "collect"}}},
        "mcp": {"servers": {"secretary": {"toolFilter": {"include": ["request_email", "request_calendar", "request_quote"]}}}},
    }), encoding="utf-8")

    checks = run_checks(base, config_path, gateway_path, workspace, services=False, http=False)
    report = summarize(checks)
    fails = [check for check in checks if check["status"] == "fail"]
    assert not fails, fails

    (base / "dashboard.html").write_text("vecchia dashboard senza schede", encoding="utf-8")
    broken = summarize(run_checks(base, config_path, gateway_path, workspace, services=False, http=False))
    ui = next(check for check in broken["checks"] if check["id"] == "dashboard_ui")
    assert ui["status"] == "fail", ui
    assert "scripts/install.sh" in ui["action"], ui
    assert render(broken).startswith("Segreteria digitale — diagnosi")
    print("SELF-TEST OK")
    print(render(report))


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnosi installazione segreteria digitale")
    parser.add_argument("--base", default=str(config.BASE))
    parser.add_argument("--config", default=str(config.DEFAULT_CONFIG_PATH))
    parser.add_argument("--openclaw", default=str(config.OPENCLAW_CONFIG))
    parser.add_argument("--workspace", default=str(config.workspace_dir()))
    parser.add_argument("--offline", action="store_true", help="salta systemd e HTTP")
    parser.add_argument("--short", action="store_true", help="mostra solo problemi")
    parser.add_argument("--strict", action="store_true", help="esce 1 anche con avvisi")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    checks = run_checks(
        Path(args.base), Path(args.config), Path(args.openclaw), Path(args.workspace),
        services=not args.offline, http=not args.offline,
    )
    report = summarize(checks)
    if args.json:
        print(json.dumps({"ok": report["counts"]["fail"] == 0, **report}, ensure_ascii=False))
    else:
        print(render(report, short=args.short))
    if report["counts"]["fail"]:
        return 1
    if args.strict and report["counts"]["warn"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
