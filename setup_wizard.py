#!/usr/bin/env python3
"""Wizard di configurazione con verifiche reali prima di salvare.

Tre controlli, ognuno con una prova concreta e non con un "sembra ok":
1. la chiave AI viene usata subito contro il provider (Groq o Gemini);
2. lo stato WhatsApp viene letto dal gateway locale e dalla configurazione;
3. l'email di prova viene inviata davvero con lo stesso percorso delle
   approvazioni (gog o bridge Google), prima di dichiarare l'installazione pronta.

Le chiavi non vengono mai stampate né scritte in config.json: finiscono solo in
`secrets.env` con permessi 600, dopo la conferma.

Uso:
    python3 setup.py --wizard
    python3 setup_wizard.py --self-test
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

PROVIDERS = {
    "groq": {
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1/models",
        "header": "Authorization",
        "prefix": "Bearer ",
        "env": "GROQ_API_KEY",
    },
    "gemini": {
        "label": "Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "header": "",
        "prefix": "",
        "env": "GEMINI_API_KEY",
    },
}
GATEWAY_HEALTH = "http://127.0.0.1:18789/health"


def validate_ai_key(provider: str, key: str, timeout: float = 10.0,
                    url: str | None = None) -> dict:
    """Chiama davvero il provider: 200 = chiave valida, 401/403 = chiave errata."""
    provider = str(provider or "").strip().lower()
    key = str(key or "").strip()
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"provider sconosciuto: {provider}"}
    if len(key) < 12:
        return {"ok": False, "error": "chiave troppo corta"}
    spec = PROVIDERS[provider]
    endpoint = str(url or spec["url"])
    headers = {"User-Agent": "Gina-Setup/1.0"}
    if provider == "groq":
        headers["Authorization"] = "Bearer " + key
    else:
        endpoint = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode({"key": key})
    request = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            models = 0
            try:
                data = json.loads(body)
                models = len(data.get("data") or data.get("models") or [])
            except Exception:
                models = 0
            return {"ok": True, "provider": provider, "models": models}
    except urllib.error.HTTPError as exc:
        hint = "chiave non valida" if exc.code in (401, 403) else f"HTTP {exc.code}"
        if exc.code == 429:
            hint = "chiave valida ma quota momentaneamente esaurita (429)"
        return {"ok": exc.code == 429, "error": hint, "status": exc.code}
    except Exception as exc:
        return {"ok": False, "error": f"rete non raggiungibile: {str(exc)[:120]}"}


def whatsapp_status(url: str | None = None, timeout: float = 4.0) -> dict:
    """Legge lo stato reale del gateway e la presenza del canale WhatsApp."""
    result = {"gateway": False, "channel": False, "detail": ""}
    try:
        with urllib.request.urlopen(url or GATEWAY_HEALTH, timeout=timeout) as response:
            result["gateway"] = 200 <= response.status < 300
    except Exception as exc:
        result["detail"] = f"gateway non raggiungibile: {str(exc)[:120]}"
    try:
        data = json.loads(Path(config.OPENCLAW_CONFIG).read_text(encoding="utf-8"))
        channels = data.get("channels") or {}
        whatsapp = channels.get("whatsapp") if isinstance(channels, dict) else None
        if isinstance(whatsapp, dict):
            result["channel"] = bool(whatsapp.get("enabled", True)) and bool(
                whatsapp.get("session") or whatsapp.get("linked") or whatsapp.get("enabled")
            )
            result["linked"] = bool(whatsapp.get("linked") or whatsapp.get("session"))
    except Exception as exc:
        detail = str(result.get("detail") or "")
        result["detail"] = (detail + " · " if detail else "") + (
            f"configurazione OpenClaw non leggibile: {str(exc)[:100]}"
        )
    result["ok"] = bool(result["gateway"] and result["channel"])
    return result


def send_test_email(to: str, subject: str = "Gina · email di prova") -> dict:
    """Invia un'email reale con lo stesso codice delle approvazioni."""
    to = str(to or "").strip()
    if "@" not in to:
        return {"ok": False, "error": "indirizzo email non valido"}
    try:
        import dashboard

        body = (
            "Questa è l'email di prova di Gina.\n\n"
            "Se la leggi, Gmail è collegato e le approvazioni possono inviare "
            "preventivi con PDF allegato.\n"
        )
        result = dashboard._execute_email({"to": to, "subject": subject, "body": body})
        return {"ok": True, "detail": str(result)[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _write_secret(name: str, value: str) -> None:
    """Aggiorna una chiave in secrets.env senza mai stamparla."""
    folder = Path(config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "secrets.env"
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.strip().startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def run(data: dict, save_callback, ask=input, out=print) -> bool:
    """Esegue i tre controlli; salva solo dopo la conferma esplicita."""
    out("\nPasso 1/3 · Chiave AI")
    validated: dict[str, str] = {}
    for provider, spec in PROVIDERS.items():
        out(f"  {spec['label']}: lascia vuoto per saltare.")
        try:
            key = ask(f"  {spec['env']}: ").strip()
        except EOFError:
            key = ""
        if not key:
            continue
        result = validate_ai_key(provider, key)
        if result.get("ok"):
            validated[spec["env"]] = key
            out(f"  ✅ {spec['label']} risponde ({result.get('models', 0)} modelli).")
        else:
            out(f"  ❌ {spec['label']}: {result.get('error')}")

    out("\nPasso 2/3 · WhatsApp")
    status = whatsapp_status()
    if status.get("ok"):
        out("  ✅ Gateway attivo e canale WhatsApp configurato.")
    else:
        out(f"  ⚠️  WhatsApp non pronto: {status.get('detail') or 'gateway o canale assente'}")
        out("     Apri l'interfaccia OpenClaw e scansiona il QR prima di usare Gina.")
        answer = ask("  Continuo comunque? [s/N] ").strip().lower()
        if answer not in {"s", "si", "sì", "y", "yes"}:
            out("Configurazione annullata: nessun file è stato scritto.")
            return False

    out("\nPasso 3/3 · Email di prova")
    account = str(data.get("google_account") or "").strip()
    try:
        target = ask(f"  Invia a [{account or 'nessun indirizzo'}]: ").strip() or account
    except EOFError:
        target = account
    if target:
        email = send_test_email(target)
        if email.get("ok"):
            out(f"  ✅ Email inviata a {target}.")
        else:
            out(f"  ⚠️  Email non inviata: {email.get('error')}")
            out("     Potrai verificarla dalla dashboard (Chiavi → Email di prova).")
    else:
        out("  ℹ️  Nessun indirizzo: salto l'invio (test disponibile dalla dashboard).")

    try:
        confirm = ask("\nSalvo la configurazione? [S/n] ").strip().lower()
    except EOFError:
        confirm = ""
    if confirm in {"n", "no"}:
        out("Nessun file scritto.")
        return False

    save_callback(data)
    for name, value in validated.items():
        _write_secret(name, value)
    if validated:
        out("Chiavi salvate in secrets.env (permessi 600).")
    out("\nConfigurazione salvata. Prossimo passo: bash scripts/install.sh")
    return True


def self_test() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Ok(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            body = json.dumps({"data": [{"id": "modello"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Deny(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

    ok_server = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    deny_server = ThreadingHTTPServer(("127.0.0.1", 0), Deny)
    for server in (ok_server, deny_server):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ok_url = f"http://127.0.0.1:{ok_server.server_port}/models"
        deny_url = f"http://127.0.0.1:{deny_server.server_port}/models"
        good = validate_ai_key("groq", "chiave-lunga-abcdef", url=ok_url)
        assert good["ok"] is True and good["models"] == 1, good
        bad = validate_ai_key("groq", "chiave-lunga-abcdef", url=deny_url)
        assert bad["ok"] is False and bad["status"] == 401, bad
        assert validate_ai_key("groq", "corta")["ok"] is False

        status = whatsapp_status(url=ok_url)
        assert status["gateway"] is True, status
        assert whatsapp_status(url="http://127.0.0.1:1/health")["gateway"] is False

        with tempfile.TemporaryDirectory() as tmp:
            old_base = config.BASE
            config.BASE = Path(tmp)
            try:
                _write_secret("GROQ_API_KEY", "valore-segreto")
                text = (Path(tmp) / "secrets.env").read_text(encoding="utf-8")
                assert "GROQ_API_KEY=valore-segreto" in text
                assert oct((Path(tmp) / "secrets.env").stat().st_mode)[-3:] == "600"
                _write_secret("GROQ_API_KEY", "nuovo")
                text = (Path(tmp) / "secrets.env").read_text(encoding="utf-8")
                assert text.count("GROQ_API_KEY=") == 1, text
            finally:
                config.BASE = old_base
    finally:
        for server in (ok_server, deny_server):
            server.shutdown()
            server.server_close()
    print("SELF-TEST OK")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verifiche guidate della configurazione")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    print("Usa: python3 setup.py --wizard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
