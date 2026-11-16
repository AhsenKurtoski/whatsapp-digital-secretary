#!/usr/bin/env python3
"""Demo locale in 60 secondi: dashboard con dati finti, senza chiavi.

    python3 demo.py

Crea una cartella temporanea, genera un preventivo con PDF e una richiesta di
email, poi avvia la dashboard su http://127.0.0.1:8799. Non tocca la tua
installazione, non usa WhatsApp, Gmail o chiavi AI e non invia nulla.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def seed(base: Path) -> None:
    """Popola lead e alert demo per rendere viva la panoramica."""
    now = datetime.now()
    leads = [
        ("+393331112233", "Mario Bianchi", "Buongiorno, vorrei un preventivo per un sito web.", "preventivo", 85),
        ("+393331112244", "Giulia Neri", "Quanto costa la manutenzione mensile?", "info", 55),
        ("+393331112255", "Luca Verdi", "Potete fissare un appuntamento giovedì?", "call", 70),
    ]
    with open(base / "leads.jsonl", "w", encoding="utf-8") as handle:
        for index, (phone, name, text, intent, score) in enumerate(leads):
            handle.write(json.dumps({
                "ts": (now - timedelta(minutes=13 * index + 4)).isoformat(timespec="seconds"),
                "sender": phone,
                "name": name,
                "text": text,
                "intent": intent,
                "score": score,
            }, ensure_ascii=False) + "\n")
    with open(base / "alerts.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts": now.isoformat(timespec="seconds"),
            "kind": "hot",
            "sender": "+393331112255",
            "text": "Richiesta di appuntamento da Luca Verdi",
            "sent": False,
        }, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo locale della segreteria, senza chiavi e senza invii.")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--no-open", action="store_true", help="non aprire il browser")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="gina-demo-"))
    base = root / "base"
    workspace = root / "workspace"
    base.mkdir(parents=True)
    workspace.mkdir(parents=True)
    # La dashboard serve il file dalla cartella base: nella demo va copiato.
    shutil.copy2(ROOT / "dashboard.html", base / "dashboard.html")
    config_path = root / "config.json"
    config_path.write_text(json.dumps({
        "business_name": "Studio Aurora",
        "owner_name": "Mario Rossi",
        "bot_name": "Gina",
        "owner_number": "+393331112222",
        "google_account": "demo@example.com",
        "language": "it",
        "timezone": "Europe/Rome",
        "site_url": "https://example.com",
        "city": "Milano",
        "services": ["siti web", "manutenzione mensile", "consulenza"],
        "hours": "Lun–Ven 9:00–18:00",
        "pricing_note": "I prezzi li conferma il titolare.",
        "vat_rate": 22,
        "quote_prefix": "DEMO",
        "quote_valid_days": 30,
        "payment_terms": "Pagamento: 50% alla conferma, saldo alla consegna.",
        "dashboard_port": args.port,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    os.environ.update({
        "SECRETARY_BASE": str(base),
        "SECRETARY_CONFIG": str(config_path),
        "SECRETARY_WORKSPACE": str(workspace),
        "DASHBOARD_PORT": str(args.port),
    })
    sys.path.insert(0, str(ROOT))

    import approval_store
    import quote_pdf

    seed(base)
    quote = quote_pdf.demo_quote()
    quote_request = approval_store.append_request(
        "quote",
        quote,
        client_name=quote.get("client_name") or "Cliente Demo",
        reason="Preventivo dimostrativo: nessun invio.",
        dedupe=False,
    )
    approval_store.append_request(
        "email",
        {
            "to": "cliente@example.com",
            "subject": "Follow-up preventivo",
            "body": "Ciao Cliente Demo,\n\nti ricordo il preventivo inviato. Se ti serve altro sono qui.",
        },
        client_name="Cliente Demo",
        reason="Email dimostrativa: nessun invio.",
        dedupe=False,
    )

    import dashboard

    token = dashboard.ensure_token()
    url = f"http://127.0.0.1:{args.port}"
    print("Gina — demo locale (dati finti, nessun invio)", flush=True)
    print(f"Dashboard: {url}", flush=True)
    print(f"Token:     {token}", flush=True)
    print(f"Preventivo demo: {quote_request.get('payload', {}).get('number') or 'creato'}", flush=True)
    print(f"Dati demo: {root}", flush=True)
    print("Premi Ctrl+C per fermare la demo.", flush=True)
    if not args.no_open:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    try:
        dashboard.main()
    except KeyboardInterrupt:
        print("\nDemo terminata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
