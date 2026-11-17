#!/usr/bin/env python3
"""Webhook verso Zapier, Make, HubSpot o endpoint propri.

Ogni consegna è firmata `X-Gina-Signature: sha256=<hmac>` sul corpo esatto,
così il ricevente può verificare che arrivi da questa installazione. Gli
endpoint sono solo HTTPS, oppure HTTP su loopback per i test. Le consegne non
bloccano la dashboard: timeout breve, due tentativi, log senza segreti.

Uso:
    python3 webhooks.py --list
    python3 webhooks.py --add https://hooks.zapier.com/... --events lead.created,quote.created
    python3 webhooks.py --test 1
    python3 webhooks.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config

EVENTS = ("lead.created", "quote.created", "quote.approved", "appointment.created", "handoff.started")
MAX_ENDPOINTS = 20
TIMEOUT = 5.0
RETRIES = 1


def _base(base: Path | None = None) -> Path:
    folder = Path(base or config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def config_path(base: Path | None = None) -> Path:
    return _base(base) / "webhooks.json"


def log_path(base: Path | None = None) -> Path:
    return _base(base) / "webhooks.log"


def _load(base: Path | None = None) -> dict:
    try:
        data = json.loads(config_path(base).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    endpoints = data.get("endpoints")
    data["endpoints"] = endpoints if isinstance(endpoints, list) else []
    return data


def _save(data: dict, base: Path | None = None) -> None:
    path = config_path(base)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _valid_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    return False


def add_endpoint(url: str, events: list[str] | None = None, secret: str | None = None,
                 base: Path | None = None) -> dict:
    url = str(url or "").strip()
    if not _valid_url(url):
        raise ValueError("URL non valido: serve https (o http solo su loopback)")
    chosen = [str(e).strip() for e in (events or list(EVENTS)) if str(e).strip() in EVENTS]
    if not chosen:
        raise ValueError("nessun evento valido")
    data = _load(base)
    if len(data["endpoints"]) >= MAX_ENDPOINTS:
        raise ValueError(f"massimo {MAX_ENDPOINTS} endpoint")
    if any(item.get("url") == url for item in data["endpoints"]):
        raise ValueError("endpoint già configurato")
    entry = {
        "id": len(data["endpoints"]) + 1,
        "url": url,
        "events": chosen,
        "secret": str(secret or secrets.token_hex(24)),
        "active": True,
        "created_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
    }
    data["endpoints"].append(entry)
    _save(data, base)
    return dict(entry)


def list_endpoints(base: Path | None = None) -> list[dict]:
    return [
        {
            "id": item.get("id"),
            "url": item.get("url"),
            "events": item.get("events"),
            "active": item.get("active", True),
            "signature": "sha256",
        }
        for item in _load(base)["endpoints"]
    ]


def remove_endpoint(endpoint_id: int, base: Path | None = None) -> bool:
    data = _load(base)
    before = len(data["endpoints"])
    data["endpoints"] = [item for item in data["endpoints"] if int(item.get("id") or 0) != int(endpoint_id)]
    if len(data["endpoints"]) != before:
        _save(data, base)
        return True
    return False


def _post(url: str, body: bytes, secret: str) -> tuple[bool, int, str]:
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Gina-Secretary/1.0",
            "X-Gina-Signature": signature,
            "X-Gina-Event": json.loads(body.decode("utf-8")).get("event", ""),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300, int(response.status), ""
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), f"http {exc.code}"
    except Exception as exc:
        return False, 0, str(exc)[:200]


def emit(event: str, data: dict | None = None, base: Path | None = None) -> dict:
    """Consegna un evento a tutti gli endpoint attivi interessati."""
    if event not in EVENTS:
        raise ValueError(f"evento sconosciuto: {event}")
    payload = {
        "event": event,
        "sent_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
        "data": data if isinstance(data, dict) else {},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    results = []
    for item in _load(base)["endpoints"]:
        if not item.get("active", True) or event not in (item.get("events") or []):
            continue
        ok = False
        status = 0
        error = ""
        for attempt in range(RETRIES + 1):
            ok, status, error = _post(str(item.get("url")), body, str(item.get("secret") or ""))
            if ok:
                break
            time.sleep(0.2 * (attempt + 1))
        results.append({"id": item.get("id"), "ok": ok, "status": status, "error": error})
        _log(base, event, item.get("url"), ok, status, error)
    return {"ok": all(r["ok"] for r in results) if results else True, "deliveries": results}


def _log(base: Path | None, event: str, url: str, ok: bool, status: int, error: str) -> None:
    try:
        path = log_path(base)
        entry = {
            "ts": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
            "event": event,
            "host": urllib.parse.urlparse(url).netloc[:120],
            "ok": ok,
            "status": status,
            "error": str(error or "")[:200],
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
        if path.stat().st_size > 500_000:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def deliveries(limit: int = 50, base: Path | None = None) -> list[dict]:
    try:
        lines = log_path(base).read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def test_endpoint(endpoint_id: int, base: Path | None = None) -> dict:
    data = _load(base)
    target = next((item for item in data["endpoints"] if int(item.get("id") or 0) == int(endpoint_id)), None)
    if target is None:
        raise ValueError("endpoint non trovato")
    payload = {
        "event": "test.ping",
        "sent_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
        "data": {"message": "Gina webhook test"},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ok, status, error = _post(str(target.get("url")), body, str(target.get("secret") or ""))
    _log(base, "test.ping", str(target.get("url")), ok, status, error)
    return {"ok": ok, "status": status, "error": error}


def self_test() -> None:
    received: list[dict] = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            received.append({
                "body": body,
                "signature": self.headers.get("X-Gina-Signature"),
                "event": self.headers.get("X-Gina-Event"),
            })
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            url = f"http://127.0.0.1:{server.server_port}/hook"
            created = add_endpoint(url, ["lead.created"], secret="s3cret", base=base)
            assert created["id"] == 1
            assert list_endpoints(base)[0]["url"] == url
            assert "secret" not in list_endpoints(base)[0]

            result = emit("lead.created", {"phone": "+39333", "score": 80}, base=base)
            assert result["ok"] is True, result
            assert len(received) == 1, received
            body = received[0]["body"]
            expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
            assert received[0]["signature"] == expected, received[0]["signature"]
            assert json.loads(body)["event"] == "lead.created"

            # Evento non sottoscritto: nessuna consegna.
            emit("quote.created", {}, base=base)
            assert len(received) == 1, received

            assert test_endpoint(1, base)["ok"] is True
            assert deliveries(10, base)
            assert remove_endpoint(1, base) is True
            assert remove_endpoint(1, base) is False
            try:
                add_endpoint("http://example.com/hook", base=base)
                raise AssertionError("http pubblico accettato")
            except ValueError:
                pass
    finally:
        server.shutdown()
        server.server_close()
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Webhook eventi Gina")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--add", default="")
    parser.add_argument("--events", default=",".join(EVENTS))
    parser.add_argument("--remove", type=int, default=0)
    parser.add_argument("--test", type=int, default=0)
    parser.add_argument("--deliveries", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        if args.add:
            entry = add_endpoint(args.add, [e.strip() for e in args.events.split(",")])
            print(json.dumps(entry, ensure_ascii=False))
            print("Salva il secret ora: serve per verificare la firma.", file=sys.stderr)
            return 0
        if args.remove:
            print("rimosso" if remove_endpoint(args.remove) else "endpoint non trovato")
            return 0
        if args.test:
            print(json.dumps(test_endpoint(args.test), ensure_ascii=False))
            return 0
    except ValueError as exc:
        print(f"webhooks: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(deliveries(50) if args.deliveries else list_endpoints(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
