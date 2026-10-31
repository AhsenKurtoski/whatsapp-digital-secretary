#!/usr/bin/env python3
"""Primo avvio guidato: crea config.json, secrets.env e la persona del bot.

Non installa OpenClaw e non avvia servizi: prepara la configurazione, poi
`scripts/install.sh` collega dashboard, timer e plugin.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import config

TEMPLATE = Path(__file__).with_name("persona_template.md")


def ask(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{label}{suffix}: ").strip()
        except EOFError:
            value = ""
        value = value or default
        if value or not required:
            return value
        print("  Questo campo è obbligatorio.")


def ask_list(label: str, default: list[str]) -> list[str]:
    current = "; ".join(default)
    value = ask(label, current)
    return [item.strip() for item in value.split(";") if item.strip()]


def validate_phone(value: str) -> str:
    clean = value.replace(" ", "").replace("-", "")
    if clean and not re.fullmatch(r"\+?[1-9]\d{6,15}", clean):
        raise SystemExit("Numero non valido: usa il formato internazionale, es. +393331234567")
    return clean


def validate_timezone(value: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    except Exception as exc:
        raise SystemExit(f"Fuso orario non valido: {value}") from exc
    return value


def render_persona(data: dict) -> str:
    owner_contact = ""
    if data.get("owner_number"):
        owner_contact = f", numero verificato {data['owner_number']}"
    services = "; ".join(data.get("services") or []) or "informazioni e richieste"
    replacements = {
        "BUSINESS_NAME": str(data.get("business_name") or "Nome Attività"),
        "BOT_NAME": str(data.get("bot_name") or "Segreteria Digitale"),
        "OWNER_NAME": str(data.get("owner_name") or "Titolare"),
        "OWNER_CONTACT": owner_contact,
        "LANGUAGE": str(data.get("language") or "italiano"),
        "MAX_LINES": str(data.get("max_reply_lines") or 6),
        "HOURS": str(data.get("hours") or "da definire"),
        "CITY": str(data.get("city") or "da definire"),
        "SERVICES": services,
        "SITE_URL": str(data.get("site_url") or "non indicato"),
        "PRICING_NOTE": str(data.get("pricing_note") or "I prezzi li conferma il titolare."),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Configura la segreteria digitale")
    parser.add_argument("--non-interactive", action="store_true", help="usa solo i flag, senza domande")
    parser.add_argument("--business-name", default="")
    parser.add_argument("--owner-name", default="")
    parser.add_argument("--bot-name", default="")
    parser.add_argument("--owner-number", default="")
    parser.add_argument("--google-account", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--timezone", default="")
    parser.add_argument("--city", default="")
    parser.add_argument("--hours", default="")
    parser.add_argument("--site-url", default="")
    parser.add_argument("--services", default="")
    parser.add_argument("--pricing-note", default="")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    existing = {}
    try:
        if config.DEFAULT_CONFIG_PATH.exists():
            existing = json.loads(config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    if args.non_interactive:
        data = {
            "business_name": args.business_name or str(existing.get("business_name") or "Nome Attività"),
            "owner_name": args.owner_name or str(existing.get("owner_name") or "Titolare"),
            "bot_name": args.bot_name or str(existing.get("bot_name") or "Segreteria Digitale"),
            "owner_number": validate_phone(args.owner_number or str(existing.get("owner_number") or "")),
            "google_account": args.google_account or str(existing.get("google_account") or ""),
            "language": args.language or str(existing.get("language") or "italiano"),
            "timezone": validate_timezone(args.timezone or str(existing.get("timezone") or "Europe/Rome")),
            "city": args.city or str(existing.get("city") or ""),
            "hours": args.hours or str(existing.get("hours") or "Lun–Ven 9:00–18:00"),
            "site_url": args.site_url or str(existing.get("site_url") or ""),
            "services": [x.strip() for x in args.services.split(";") if x.strip()]
            or existing.get("services")
            or config.DEFAULTS["services"],
            "pricing_note": args.pricing_note or str(existing.get("pricing_note") or "I prezzi e le condizioni li conferma il titolare."),
            "dashboard_port": args.port if args.port is not None else int(existing.get("dashboard_port") or 8787),
        }
    else:
        print("\nConfigurazione della segreteria digitale")
        print("Premi Invio per accettare il valore tra parentesi.\n")
        data = {
            "business_name": ask("Nome attività", str(existing.get("business_name") or ""), required=True),
            "owner_name": ask("Nome del titolare", str(existing.get("owner_name") or ""), required=True),
            "bot_name": ask("Nome del bot", str(existing.get("bot_name") or "Segreteria Digitale")),
            "owner_number": validate_phone(ask("Numero WhatsApp del titolare (formato internazionale)", str(existing.get("owner_number") or ""))),
            "google_account": ask("Account Gmail per le email (opzionale)", str(existing.get("google_account") or "")),
            "language": ask("Lingua principale", str(existing.get("language") or "italiano")),
            "timezone": validate_timezone(ask("Fuso orario IANA", str(existing.get("timezone") or "Europe/Rome"))),
            "city": ask("Città o zona", str(existing.get("city") or "")),
            "hours": ask("Orari", str(existing.get("hours") or "Lun–Ven 9:00–18:00")),
            "site_url": ask("Sito web (opzionale)", str(existing.get("site_url") or "")),
            "services": ask_list("Servizi, separati da ;", existing.get("services") or config.DEFAULTS["services"]),
            "pricing_note": ask("Nota sui prezzi", str(existing.get("pricing_note") or "I prezzi e le condizioni li conferma il titolare.")),
            "dashboard_port": int(ask("Porta dashboard", str(existing.get("dashboard_port") or 8787))),
        }

    # An update must not drop settings that the wizard does not ask about
    # (max_concurrent, max_reply_lines, whatsapp_number, future keys).
    data = {**existing, **data}

    config.DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config.DEFAULT_CONFIG_PATH.parent, 0o700)
    tmp = config.DEFAULT_CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, config.DEFAULT_CONFIG_PATH)

    base = config.ensure_base()
    persona = render_persona(data)
    (base / "persona.md").write_text(persona, encoding="utf-8")
    os.chmod(base / "persona.md", 0o600)

    workspace = config.workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    agents = workspace / "AGENTS.md"
    if agents.exists():
        backup = agents.with_name(f"AGENTS.md.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(agents, backup)
        os.chmod(backup, 0o600)
    agents.write_text(persona, encoding="utf-8")
    os.chmod(agents, 0o600)

    secrets = base / "secrets.env"
    if not secrets.exists():
        secrets.write_text(
            "# Chiavi API: non committare questo file.\n"
            "# GROQ_API_KEY=\n# GROQ_API_KEY_2=\n# GEMINI_API_KEY=\n",
            encoding="utf-8",
        )
        os.chmod(secrets, 0o600)

    print(f"\nConfigurazione: {config.DEFAULT_CONFIG_PATH}")
    print(f"Persona:        {workspace / 'AGENTS.md'}")
    print(f"Segreti:        {secrets}")
    print("\nProssimo passo: bash scripts/install.sh")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAnnullato.", file=sys.stderr)
        raise SystemExit(130)
