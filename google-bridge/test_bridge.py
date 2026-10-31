#!/usr/bin/env python3
"""Test locale del protocollo firmato del bridge, senza usare Google."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_client  # noqa: E402

SECRET = "test-" + "x" * 48
RECEIVED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            envelope = json.loads(raw)
            signed = envelope["data"]
            signature = envelope["sig"]
            expected = hmac.new(SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError("bad signature")
            data = json.loads(signed)
            if abs(time.time() - float(data["ts"])) > 300:
                raise ValueError("expired")
            RECEIVED.append(data)
            if data["action"] == "sendEmail":
                result = {"id": "msg-1", "to": data["payload"]["to"]}
            elif data["action"] == "createEvent":
                result = {"id": "evt-1", "summary": data["payload"]["summary"]}
            else:
                raise ValueError("unknown")
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}).encode()
            self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {"url": f"http://127.0.0.1:{server.server_port}/exec", "secret": SECRET, "enabled": True}
    try:
        sent = bridge_client.send_email(
            {"to": "cliente@example.com", "subject": "Ciao", "body": "Test", "html": "<p>Test</p>"},
            config,
        )
        assert sent.get("id") == "msg-1", sent
        event = bridge_client.create_event(
            {
                "summary": "Call",
                "start": "2026-10-28T10:00:00+01:00",
                "end": "2026-10-28T10:30:00+01:00",
                "attendees": ["cliente@example.com"],
            },
            config,
        )
        assert event.get("id") == "evt-1", event
        assert RECEIVED[-1]["action"] == "createEvent"
        assert RECEIVED[-1]["payload"]["summary"] == "Call"

        bad = dict(config)
        bad["secret"] = "wrong-" + "y" * 48
        try:
            bridge_client.send_email({"to": "cliente@example.com", "subject": "x", "body": "x"}, bad)
        except bridge_client.BridgeError:
            pass
        else:
            raise AssertionError("una firma errata deve essere rifiutata")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bridge.json")
            bridge_client.write_config("https://script.google.com/macros/s/abc/exec", SECRET, path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            assert mode == 0o600, oct(mode)
            loaded = bridge_client.load_config(path)
            assert loaded and loaded["secret"] == SECRET

        print("bridge protocol: OK")
        print("requests:", [x["action"] for x in RECEIVED])
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
