#!/usr/bin/env python3
"""Diagnosi completa dell'installazione della segreteria digitale.

Controlla ciò che serve davvero perché preventivi, riepiloghi e promemoria
funzionino: file installati, scheda Preventivi nella dashboard, persona,
policy anti-raffica, tool MCP, motore PDF, servizi systemd e API locali.

Di default non modifica nulla e non stampa segreti. Ogni problema ha un'azione
concreta; con ``--fix`` applica le riparazioni sicure, una alla volta e con conferma.

    python3 doctor.py            # report completo
    python3 doctor.py --short    # solo problemi
    python3 doctor.py --json     # per la dashboard
    python3 doctor.py --offline  # salta systemd e HTTP
    python3 doctor.py --fix      # ripara ciò che può, con conferma

``--fix`` non tocca mai i segreti e non reinstalla file: esegue solo le
riparazioni locali sicure (allowlist MCP, policy anti-raffica, servizi inattivi)
e chiede conferma prima di ogni modifica. ``--yes`` salta le domande per un uso
non interattivo.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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
    # Piattaforma Gina: dashboard, MCP e plugin li importano dalla copia installata.
    "lead_intel.py",
    "memory.py",
    "cache.py",
    "identity.py",
    "quotas.py",
    "router.py",
    "webhooks.py",
    "stats.py",
    "conversations.py",
    "gdpr.py",
    "backup.py",
    "live.py",
    "i18n.json",
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


OPENCLAW_MINIMUM = "2026.4.22"

# Dipendenze runtime oltre alla standard library. Senza questi moduli la
# memoria clienti e i backup cifrati (AES-256-GCM) falliscono con
# ModuleNotFoundError: il controllo deve fermarsi subito, con la via d'uscita.
RUNTIME_DEPENDENCIES = {
    "cryptography": "memoria clienti e backup cifrati (AES-256-GCM)",
}


def _missing_dependencies() -> list[str]:
    """Moduli runtime non importabili dall'interprete che esegue la diagnosi."""
    missing: list[str] = []
    for module in RUNTIME_DEPENDENCIES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return missing


def _linger_state() -> bool | None:
    """Lingering utente: senza di esso i servizi non partono al boot.

    Restituisce True/False quando `loginctl` è utilizzabile, None quando lo
    stato non è verificabile (Windows o sessione senza systemd).
    """
    loginctl = shutil.which("loginctl")
    if not loginctl:
        return None
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return None
    try:
        result = subprocess.run(
            [loginctl, "show-user", user, "-p", "Linger", "--value"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip().lower() == "yes"
    except Exception:
        return None


def _openclaw_argv(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", command, *args]
    return [command, *args]


def _probe_openclaw(command: str = "openclaw", timeout: float = 8.0) -> dict:
    """Legge versione e feature senza modificare la configurazione."""
    info = {
        "present": False,
        "version": "",
        "version_ok": False,
        "accept_capabilities": False,
        "conversation_access": False,
        "minimum": OPENCLAW_MINIMUM,
        "error": "",
        "probe_errors": [],
    }
    resolved = shutil.which(command) or command
    try:
        result = subprocess.run(
            _openclaw_argv(resolved, ["--version"]), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        info["error"] = "comando openclaw non trovato"
        return info
    except Exception as exc:
        info["error"] = f"openclaw --version non eseguibile: {str(exc)[:120]}"
        return info
    info["present"] = True
    version_text = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(r"\b(\d{4})\.(\d{1,2})\.(\d{1,2})\b", version_text)
    if match:
        info["version"] = ".".join(match.groups())
        info["version_ok"] = tuple(int(part) for part in match.groups()) >= (2026, 4, 22)
    else:
        info["error"] = "versione OpenClaw non riconosciuta"
    if result.returncode != 0:
        info["version_ok"] = False
        info["error"] = f"openclaw --version ha restituito {result.returncode}"

    probes = [
        ("accept_capabilities", _openclaw_argv(resolved, ["plugins", "install", "--help"]), "--accept-capabilities"),
        ("conversation_access", _openclaw_argv(resolved, ["config", "schema"]), "allowConversationAccess"),
    ]
    for key, argv, needle in probes:
        try:
            probe = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
            output = (probe.stdout or "") + "\n" + (probe.stderr or "")
            info[key] = needle in output
        except Exception:
            info["probe_errors"].append(key)
    return info


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

    # 1b. Dipendenze Python: senza cryptography memoria e backup cifrati
    # falliscono a runtime, quindi il blocco va segnalato prima di tutto il resto.
    missing_deps = _missing_dependencies()
    if missing_deps:
        checks.append(_check(
            "deps", "Dipendenze Python", "fail",
            "Manca " + ", ".join(missing_deps) + ": "
            + "; ".join(RUNTIME_DEPENDENCIES[name] for name in missing_deps)
            + " non funzionano (ModuleNotFoundError).",
            "python3 -m pip install -r requirements.txt (Debian/Ubuntu: nota venv/PEP 668 nel README)",
        ))
    else:
        checks.append(_check(
            "deps", "Dipendenze Python", "ok",
            "cryptography presente: memoria clienti e backup cifrati attivi.",
        ))

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
            "client_brief · remember_fact · list_quotes · request_email · "
            "request_calendar · request_quote · request_quote_resend",
        ))

    # 6b. Runtime MCP: la copia installata risponde e genera un PDF.
    # La sola presenza del file non basta: qui eseguiamo davvero il server
    # installato, così un file vecchio o rotto non passa inosservato.
    runtime_script = base / "approval_mcp.py"
    if not runtime_script.is_file():
        checks.append(_check(
            "mcp_runtime", "Runtime approvazioni", "fail",
            "approval_mcp.py non è installato nella cartella della segreteria.",
            "git pull && bash scripts/install.sh",
        ))
    else:
        child_env = dict(os.environ)
        child_env.update({
            "SECRETARY_BASE": str(base),
            "SECRETARY_CONFIG": str(config_path),
            "OPENCLAW_CONFIG": str(openclaw_config),
            "PYTHONPATH": str(base),
        })
        try:
            result = subprocess.run(
                [sys.executable, str(runtime_script), "--selftest"],
                capture_output=True, text=True, timeout=60, env=child_env, check=False,
            )
            output = (result.stdout or "").strip()
            if result.returncode == 0 and "MCP SELF-TEST OK" in output:
                checks.append(_check("mcp_runtime", "Runtime approvazioni", "ok", output[-200:]))
            else:
                detail = ((result.stderr or "") or output or "nessun output").strip()[-200:]
                checks.append(_check(
                    "mcp_runtime", "Runtime approvazioni", "fail",
                    f"Il server MCP installato non risponde: {detail}",
                    "git pull && bash scripts/install.sh, poi riavvia il gateway.",
                ))
        except Exception as exc:
            checks.append(_check(
                "mcp_runtime", "Runtime approvazioni", "fail",
                f"Self-test MCP non eseguibile: {str(exc)[:160]}",
                "git pull && bash scripts/install.sh",
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
    guard_module = base / "plugins" / "secretary-guard" / "guard.js"
    plugin_text = _read(plugin)
    plugin_ok = (
        "duplicate_suppressed" in plugin_text
        and "duplicate_inbound_suppressed" in plugin_text
        and guard_module.is_file()
    )
    if plugin_ok:
        checks.append(_check(
            "plugin", "Guardia anti-raffica", "ok",
            "Plugin aggiornato installato (duplicati inbound prima del modello + outbound).",
        ))
    else:
        checks.append(_check(
            "plugin", "Guardia anti-raffica", "warn",
            "Il plugin installato non contiene la guardia anti-raffica completa.",
            "bash scripts/install.sh e riavvia il gateway.",
        ))

    # Compatibilità OpenClaw: versione minima e feature usate dall'installer.
    openclaw = _probe_openclaw()
    if not openclaw["present"]:
        checks.append(_check(
            "openclaw_compat", "Compatibilità OpenClaw", "info",
            openclaw["error"] or "OpenClaw non rilevato: controlli compatibilità saltati.",
            "Installa OpenClaw >= " + OPENCLAW_MINIMUM + (
                " e rilancia bash scripts/install.sh" if os.name != "nt" else ""
            ),
        ))
    else:
        if openclaw["version_ok"]:
            checks.append(_check(
                "openclaw_version", "Versione OpenClaw", "ok",
                f"OpenClaw {openclaw['version']} >= {OPENCLAW_MINIMUM}.",
            ))
        else:
            checks.append(_check(
                "openclaw_version", "Versione OpenClaw", "fail",
                openclaw["error"] or (
                    f"OpenClaw {openclaw['version'] or 'sconosciuto'} < {OPENCLAW_MINIMUM}."
                ),
                f"Aggiorna OpenClaw a >= {OPENCLAW_MINIMUM}, poi rilancia bash scripts/install.sh.",
            ))
        if openclaw["accept_capabilities"]:
            checks.append(_check(
                "openclaw_accept_capabilities", "Feature --accept-capabilities", "ok",
                "Supportata: l'installer può accettare le capability del plugin.",
            ))
        else:
            checks.append(_check(
                "openclaw_accept_capabilities", "Feature --accept-capabilities", "warn",
                "Non rilevata: l'installer userà il percorso legacy e potrebbe rifiutare "
                "il plugin se richiede consenso.",
                f"Aggiorna OpenClaw a >= {OPENCLAW_MINIMUM} se l'installazione richiede consenso.",
            ))
        if openclaw["conversation_access"]:
            checks.append(_check(
                "openclaw_conversation_access", "Feature allowConversationAccess", "ok",
                "Supportata: agent_end può rilasciare il retry dopo un errore.",
            ))
        else:
            checks.append(_check(
                "openclaw_conversation_access", "Feature allowConversationAccess", "warn",
                "Non rilevata: la guardia resta in modalità burst-only, senza retry dopo un errore.",
                f"Aggiorna OpenClaw a >= {OPENCLAW_MINIMUM} per abilitare il retry.",
            ))

    # Il retry dopo un errore usa agent_end, che per i plugin non bundled richiede
    # un permesso esplicito. before_dispatch non lo richiede.
    if plugin_ok and "releaseInboundForScope" in plugin_text:
        plugins = gateway.get("plugins") if isinstance(gateway.get("plugins"), dict) else {}
        entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
        guard_entry = entries.get("secretary-guard") if isinstance(entries.get("secretary-guard"), dict) else {}
        hooks = guard_entry.get("hooks") if isinstance(guard_entry.get("hooks"), dict) else {}
        if hooks.get("allowConversationAccess") is True:
            checks.append(_check(
                "plugin_hook", "Retry dopo un errore", "ok",
                "agent_end autorizzato: un messaggio fallito non resta soppresso.",
            ))
        else:
            checks.append(_check(
                "plugin_hook", "Retry dopo un errore", "warn",
                "Manca allowConversationAccess: agent_end è bloccato e il retry può "
                "restare soppresso mentre è in coda (fino a 120 secondi).",
                "bash scripts/install.sh, oppure: openclaw config set "
                "'plugins.entries.secretary-guard.hooks.allowConversationAccess' true",
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
                    "systemd non disponibile in questo ambiente (Windows o sistema senza systemd).",
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

        # 24/7: senza lingering i servizi utente si fermano al logout e non
        # ripartono dopo un riavvio finché qualcuno non accede.
        linger = _linger_state()
        if linger is None:
            checks.append(_check(
                "linger", "Avvio automatico al boot", "info",
                "Stato lingering non verificabile in questo ambiente.",
            ))
        elif linger:
            checks.append(_check(
                "linger", "Avvio automatico al boot", "ok",
                "Lingering attivo: i servizi ripartono anche senza login.",
            ))
        else:
            checks.append(_check(
                "linger", "Avvio automatico al boot", "warn",
                "Lingering disattivo: dopo un riavvio i servizi partono solo al login.",
                "sudo loginctl enable-linger $USER",
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


def _script_command(base: Path, name: str) -> list[str] | None:
    path = Path(base) / name
    if not path.is_file():
        return None
    return [sys.executable or "python3", str(path)]


def _fix_env(base: Path, config_path: Path, openclaw_config: Path) -> dict[str, str]:
    """Ambiente esplicito: le riparazioni usano gli stessi percorsi della diagnosi."""
    env = os.environ.copy()
    env["SECRETARY_BASE"] = str(base)
    env["SECRETARY_CONFIG"] = str(config_path)
    env["OPENCLAW_CONFIG"] = str(openclaw_config)
    env["OPENCLAW_CONFIG_PATH"] = str(openclaw_config)
    return env


def plan_fixes(
    base: Path,
    checks: list[dict],
    config_path: Path,
    openclaw_config: Path,
    *,
    can_restart: bool = True,
) -> list[dict]:
    """Elenca le riparazioni sicure, nell'ordine in cui vanno applicate.

    Non reinstalla file e non inventa configurazione: richiama gli script già
    usati dall'installer oppure riavvia un'unità systemd. Le correzioni che
    richiedono ``git pull`` + ``install.sh`` restano manuali.
    """
    base = Path(base)
    config_path = Path(config_path)
    openclaw_config = Path(openclaw_config)
    by_id = {str(check.get("id")): check for check in checks if check.get("id")}
    systemd_missing = any(
        check.get("id") == "services" and check.get("status") == "info"
        for check in checks
    )
    restart = can_restart and not systemd_missing
    plan: list[dict] = []
    seen: set[str] = set()

    def add(
        key: str,
        check_id: str,
        title: str,
        detail: str,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        mkdir: str = "",
        done: str = "",
    ) -> None:
        if key in seen:
            return
        seen.add(key)
        plan.append({
            "key": key,
            "check_id": check_id,
            "title": title,
            "detail": detail,
            "command": command,
            "env": env,
            "mkdir": mkdir,
            "done": done,
            "prompt": "Applicare questa correzione? [s/N] ",
        })

    config_changed = False
    mcp = by_id.get("mcp_config")
    if mcp and mcp.get("status") == "fail":
        command = _script_command(base, "approval_setup.py")
        if command:
            add(
                "approval_setup", "mcp_config",
                "Ricollega i preventivi al bot",
                "approval_setup.py reinserisce request_quote nell'allowlist MCP e salva "
                "openclaw.json con un backup.",
                command=command,
                env=_fix_env(base, config_path, openclaw_config),
                done="Approvazioni aggiornate (backup creato).",
            )
            config_changed = True

    hook = by_id.get("plugin_hook")
    if hook and hook.get("status") == "warn":
        add(
            "plugin_hook", "plugin_hook",
            "Abilita il retry del plugin",
            "Imposta allowConversationAccess per agent_end: senza questo permesso "
            "un messaggio fallito può restare soppresso mentre è in coda (fino a 120 secondi).",
            command=[
                "openclaw", "config", "set",
                "plugins.entries.secretary-guard.hooks.allowConversationAccess",
                "true",
            ],
            env=_fix_env(base, config_path, openclaw_config),
            done="Permesso retry del plugin aggiornato.",
        )

    policy = by_id.get("policy")
    if policy and policy.get("status") == "warn":
        command = _script_command(base, "gateway_policy.py")
        if command:
            add(
                "gateway_policy", "policy",
                "Applica l'anti-raffica WhatsApp",
                "gateway_policy.py aggiunge solo le chiavi mancanti (debounce e coda "
                "WhatsApp): le scelte già presenti non vengono sovrascritte.",
                command=command,
                env=_fix_env(base, config_path, openclaw_config),
                done="Policy anti-raffica aggiornata.",
            )
            config_changed = True

    if restart:
        for check in checks:
            check_id = str(check.get("id") or "")
            if not check_id.startswith("service:"):
                continue
            if check.get("status") not in {"fail", "warn"}:
                continue
            unit = check_id.split(":", 1)[1]
            if check.get("status") == "warn":
                add(
                    "enable:" + unit, check_id, "Abilita " + unit,
                    "Il timer non è attivo: enable --now lo avvia subito e lo mantiene "
                    "attivo dopo i riavvii.",
                    command=["systemctl", "--user", "enable", "--now", unit],
                    done=unit + " abilitato e avviato.",
                )
            else:
                add(
                    "restart:" + unit, check_id, "Riavvia " + unit,
                    "Il servizio non è attivo: un riavvio lo riporta in funzione.",
                    command=["systemctl", "--user", "restart", unit],
                    done=unit + " riavviato.",
                )

        if config_changed:
            add(
                "restart:openclaw-gateway.service", "mcp_config",
                "Riavvia il gateway",
                "Il gateway deve rileggere openclaw.json per esporre i tool e la nuova "
                "policy a WhatsApp.",
                command=["systemctl", "--user", "restart", "openclaw-gateway.service"],
                done="Gateway riavviato: WhatsApp si riconnette in qualche decina di secondi.",
            )

        for check_id in ("http_dashboard", "http_quotes", "http_pdf"):
            check = by_id.get(check_id)
            if check and check.get("status") == "fail":
                add(
                    "restart:openclaw-dashboard.service", check_id,
                    "Riavvia la dashboard",
                    "La dashboard installata non risponde o serve una versione vecchia "
                    "delle API.",
                    command=["systemctl", "--user", "restart", "openclaw-dashboard.service"],
                    done="Dashboard riavviata.",
                )

    return plan


def apply_fix(item: dict, *, runner=subprocess.run) -> tuple[bool, str]:
    """Esegue una sola riparazione. Restituisce (riuscita, messaggio)."""
    if item.get("mkdir"):
        path = Path(item["mkdir"])
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(path, 0o700)
        except OSError as exc:
            return False, f"cartella non creata: {exc}"
        return True, item.get("done") or f"creata {path}"
    command = item.get("command")
    if not command:
        return False, "nessun comando associato"
    try:
        result = runner(
            command, capture_output=True, text=True, timeout=120, env=item.get("env")
        )
    except FileNotFoundError:
        return False, f"comando non trovato: {command[0]}"
    except Exception as exc:
        return False, f"esecuzione fallita: {str(exc)[:160]}"
    if result.returncode == 0:
        return True, item.get("done") or "completato"
    lines = ((result.stderr or "").strip() or (result.stdout or "").strip()).splitlines()
    return False, (lines[-1][:200] if lines else f"exit {result.returncode}")


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"s", "si", "sì", "y", "yes"}


def run_fixes(
    plan: list[dict],
    *,
    assume_yes: bool = False,
    confirm=None,
    runner=subprocess.run,
    out=print,
) -> dict:
    """Applica le riparazioni una alla volta, chiedendo conferma per ognuna."""
    applied: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for index, item in enumerate(plan):
        out("")
        out("▸ " + item["title"])
        if item.get("detail"):
            out("  " + item["detail"])
        if item.get("command"):
            out("  Comando: " + shlex.join(item["command"]))
        if item.get("mkdir"):
            out("  Cartella: " + item["mkdir"])
        if assume_yes:
            approved = True
        else:
            ask = confirm or _confirm
            try:
                approved = bool(ask(item.get("prompt") or "Applicare? [s/N] "))
            except KeyboardInterrupt:
                out("  Interrotto: le correzioni restanti sono state saltate.")
                skipped.extend(other["key"] for other in plan[index:])
                break
        if not approved:
            out("  Saltata.")
            skipped.append(item["key"])
            continue
        ok, message = apply_fix(item, runner=runner)
        out(("  ✅ " if ok else "  ❌ ") + message)
        (applied if ok else failed).append(item["key"])
    return {"applied": applied, "failed": failed, "skipped": skipped}


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
    # Il controllo runtime esegue davvero il server MCP: la fixture usa le
    # copie reali dei moduli, altrimenti fallirebbe per costruzione.
    import approval_mcp as _approval_mcp

    source_root = Path(_approval_mcp.__file__).parent
    for name in (
        "approval_mcp.py", "approval_store.py", "quote_pdf.py", "email_template.py", "config.py",
        "live.py", "lead_intel.py", "memory.py", "cache.py",
    ):
        (base / name).write_text((source_root / name).read_text(encoding="utf-8"), encoding="utf-8")
    (base / "dashboard.html").write_text('data-tab="quotes" id="quotesBody" data-tab="contacts"', encoding="utf-8")
    plugin_dir = base / "plugins" / "secretary-guard"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "index.js").write_text(
        "duplicate_suppressed duplicate_inbound_suppressed releaseInboundForScope\n",
        encoding="utf-8",
    )
    (plugin_dir / "guard.js").write_text("export const guard = true;\n", encoding="utf-8")
    (base / "secrets.env").write_text("GEMINI_API_KEY=placeholder\n", encoding="utf-8")
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
        "plugins": {"entries": {"secretary-guard": {"hooks": {"allowConversationAccess": True}}}},
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

    broken_gateway = tmp / "openclaw-broken.json"
    broken_gateway.write_text(json.dumps({
        "messages": {"queue": {"mode": "steer"}},
        "plugins": {"entries": {"secretary-guard": {"hooks": {"allowConversationAccess": True}}}},
        "mcp": {"servers": {"secretary": {"toolFilter": {"include": [
            "request_email", "request_calendar",
        ]}}}},
    }), encoding="utf-8")
    broken_checks = run_checks(
        base, config_path, broken_gateway, workspace, services=False, http=False
    )
    plan = plan_fixes(
        base, broken_checks, config_path, broken_gateway, can_restart=False
    )
    assert [item["key"] for item in plan] == ["approval_setup", "gateway_policy"], plan
    assert all("systemctl" not in (item.get("command") or []) for item in plan), plan
    restart_plan = plan_fixes(
        base, broken_checks, config_path, broken_gateway, can_restart=True
    )
    assert "restart:openclaw-gateway.service" in [item["key"] for item in restart_plan], restart_plan
    assert "dashboard_ui" not in [item["check_id"] for item in restart_plan], restart_plan

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    outcome = run_fixes(
        plan[:1], assume_yes=True, runner=fake_run, out=lambda *_args: None
    )
    assert outcome["applied"] == ["approval_setup"], outcome
    assert calls and calls[0][-1].endswith("approval_setup.py"), calls
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
    parser.add_argument(
        "--fix", action="store_true",
        help="ripara i problemi risolvibili, chiedendo conferma per ogni modifica",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="applica tutte le correzioni senza chiedere (richiede --fix)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.json and args.fix:
        parser.error("--json non può essere combinato con --fix")
    if args.yes and not args.fix:
        parser.error("--yes richiede --fix")
    if args.self_test:
        self_test()
        return 0

    base = Path(args.base)
    config_path = Path(args.config)
    openclaw_config = Path(args.openclaw)

    def collect() -> list[dict]:
        return run_checks(
            base, config_path, openclaw_config, Path(args.workspace),
            services=not args.offline, http=not args.offline,
        )

    checks = collect()
    report = summarize(checks)
    if args.json:
        print(json.dumps({"ok": report["counts"]["fail"] == 0, **report}, ensure_ascii=False))
    else:
        print(render(report, short=args.short))

    plan = plan_fixes(
        base, checks, config_path, openclaw_config, can_restart=not args.offline
    )
    if args.fix:
        if not plan:
            if report["counts"]["fail"] or report["counts"]["warn"]:
                print("\nNessuna correzione automatica: i problemi restanti richiedono "
                      "git pull e bash scripts/install.sh.")
            else:
                print("\nNessuna correzione necessaria.")
        elif not args.yes and not sys.stdin.isatty():
            print("\nInput non interattivo: nessuna modifica eseguita. "
                  "Rilancia con --yes per applicare le correzioni.")
        else:
            if args.yes:
                print(f"\nApplico {len(plan)} correzioni (--yes).")
            else:
                print(f"\n{len(plan)} correzioni proposte; conferma richiesta per ognuna.")
            outcome = run_fixes(plan, assume_yes=args.yes)
            print(
                "\nRiepilogo correzioni: "
                f"{len(outcome['applied'])} applicate · "
                f"{len(outcome['failed'])} fallite · "
                f"{len(outcome['skipped'])} saltate"
            )
            if any(
                key.startswith(("restart:", "enable:")) for key in outcome["applied"]
            ):
                time.sleep(2)
            checks = collect()
            report = summarize(checks)
            print("\nDopo le correzioni:")
            print(render(report))
    elif not args.json and plan:
        print(f"\n{len(plan)} correzioni automatiche disponibili. "
              "Eseguile con: python3 doctor.py --fix")

    if report["counts"]["fail"]:
        return 1
    if args.strict and report["counts"]["warn"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
