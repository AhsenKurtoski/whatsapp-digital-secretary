#!/usr/bin/env python3
"""Client locale per il bridge Google Apps Script della segreteria.

Il file di configurazione contiene URL e segreto HMAC e deve avere permessi 0600.
Non stampa mai il segreto.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".openclaw", "secretary", "google_bridge.json")
MAX_RESPONSE = 1_500_000
TIMEOUT = 35


class BridgeError(RuntimeError):
    pass


def load_config(path: str | None = None) -> dict | None:
    """Return a validated bridge config, or None when the bridge is not enabled."""
    path = path or DEFAULT_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise BridgeError(f"configurazione bridge non leggibile: {exc}") from exc
    if not isinstance(data, dict) or data.get("enabled", True) is False:
        return None
    url = str(data.get("url") or "").strip()
    secret = str(data.get("secret") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "script.google.com":
        raise BridgeError("URL bridge non valido")
    if not parsed.path.startswith("/macros/s/") or not parsed.path.endswith("/exec"):
        raise BridgeError("URL bridge non riconosciuto")
    if len(secret) < 32:
        raise BridgeError("segreto bridge mancante o troppo corto")
    return {"url": url, "secret": secret, "enabled": True}


def write_config(url: str, secret: str, path: str | None = None) -> str:
    """Write the bridge config atomically with owner-only permissions."""
    path = path or DEFAULT_CONFIG
    data = {"url": str(url).strip(), "secret": str(secret).strip(), "enabled": True}
    # Validate before writing.
    if not data["url"].startswith("https://script.google.com/macros/s/"):
        raise BridgeError("URL bridge non valido")
    if len(data["secret"]) < 32:
        raise BridgeError("segreto bridge troppo corto")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    old_umask = os.umask(0o077)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        os.umask(old_umask)
    return path


def _request(config: dict, body: bytes) -> dict:
    req = urllib.request.Request(
        config["url"],
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "DigitalSecretaryBridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2000).decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise BridgeError(f"bridge HTTP {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"bridge non raggiungibile: {exc.reason}") from exc
    except Exception as exc:
        raise BridgeError(f"bridge errore di rete: {exc}") from exc
    if len(raw) > MAX_RESPONSE:
        raise BridgeError("risposta bridge troppo grande")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BridgeError("risposta bridge non JSON") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        error = (data or {}).get("error") if isinstance(data, dict) else "errore sconosciuto"
        detail = (data or {}).get("detail") if isinstance(data, dict) else ""
        raise BridgeError(f"bridge: {error or 'errore'}{(': ' + str(detail)) if detail else ''}")
    return data


def _post(config: dict, action: str, payload: dict) -> dict:
    signed = json.dumps(
        {"ts": int(time.time()), "action": action, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    signature = hmac.new(
        config["secret"].encode("utf-8"), signed.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    body = json.dumps(
        {"data": signed, "sig": signature}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return _request(config, body)


def health(config: dict | None = None) -> dict:
    config = config or load_config()
    if not config:
        raise BridgeError("bridge non configurato")
    url = config["url"] + ("&" if "?" in config["url"] else "?") + "action=health"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DigitalSecretaryBridge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read(64_000)
    except Exception as exc:
        raise BridgeError(f"health bridge fallita: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BridgeError("health bridge non JSON") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise BridgeError("health bridge non valida")
    return data


def send_email(payload: dict, config: dict | None = None) -> dict:
    config = config or load_config()
    if not config:
        raise BridgeError("bridge non configurato")
    return (_post(config, "sendEmail", payload).get("result") or {})


def create_event(payload: dict, config: dict | None = None) -> dict:
    config = config or load_config()
    if not config:
        raise BridgeError("bridge non configurato")
    return (_post(config, "createEvent", payload).get("result") or {})


def new_secret() -> str:
    return secrets.token_urlsafe(36)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Client bridge Google Digital Secretary")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    setup = sub.add_parser("setup")
    setup.add_argument("--url", required=True)
    setup.add_argument("--secret", default="")
    args = parser.parse_args()
    path = args.config
    try:
        if args.command == "health":
            print(json.dumps(health(load_config(path)), ensure_ascii=False, indent=2))
        elif args.command == "setup":
            secret = args.secret or new_secret()
            print(write_config(args.url, secret, path))
    except BridgeError as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
