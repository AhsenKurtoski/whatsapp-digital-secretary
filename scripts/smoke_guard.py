#!/usr/bin/env python3
"""Smoke test isolato per la guardia anti-raffica e il retry.

Avvia un gateway OpenClaw reale su loopback con un modello OpenAI-compatible
locale, senza WhatsApp e senza il profilo dell'utente. Verifica:

1. sei messaggi identici -> una sola chiamata al modello e cinque soppressioni;
2. primo turno fallito -> chiave rilasciata e retry accettato.

Uso:
    python3 scripts/smoke_guard.py
    python3 scripts/smoke_guard.py --keep   # conserva log e config temporanei
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "secretary-guard"
COMPAT = ROOT / "scripts" / "openclaw_compat.py"


def openclaw_command(executable: str) -> list[str]:
    path = shutil.which(executable)
    if not path:
        raise RuntimeError(f"openclaw non trovato: {executable}")
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path]
    return [path]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ModelState:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_status = 0
        self.lock = threading.Lock()

    def record(self) -> int:
        with self.lock:
            self.calls += 1
            return self.calls

    @property
    def fail(self) -> int:
        with self.lock:
            return self.fail_status

    def set_fail(self, status: int) -> None:
        with self.lock:
            self.fail_status = status


class MockModelHandler(BaseHTTPRequestHandler):
    state: ModelState

    def log_message(self, *_args) -> None:
        return

    def _json(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            self._json(200, {
                "object": "list",
                "data": [{"id": "mock-test", "object": "model", "owned_by": "smoke"}],
            })
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            self._json(400, {"error": {"message": "invalid json"}})
            return
        self.state.record()
        fail = self.state.fail
        if fail:
            self._json(fail, {"error": {"message": "smoke forced failure"}})
            return
        model = str(body.get("model") or "mock-test")
        if body.get("stream") is True:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            base = {
                "id": "chatcmpl-smoke",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
            }
            for delta, finish in (
                ({"role": "assistant", "content": "ok"}, None),
                ({}, "stop"),
            ):
                chunk = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        self._json(200, {
            "id": "chatcmpl-smoke",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def start_model(port: int, state: ModelState) -> ThreadingHTTPServer:
    handler = type("SmokeModelHandler", (MockModelHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_http(url: str, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    return False


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def call(openclaw: list[str], env: dict[str, str], *args: str, timeout: float = 40.0):
    result = subprocess.run(
        [*openclaw, *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"openclaw {' '.join(args)} fallito (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:800]}"
        )
    return result


def chat_send(
    openclaw: list[str],
    env: dict[str, str],
    port: int,
    session: str,
    message: str,
    idempotency_key: str,
    timeout: float = 35.0,
) -> str:
    params = json.dumps({
        "sessionKey": session,
        "message": message,
        "idempotencyKey": idempotency_key,
    })
    cli_timeout = max(10, int(timeout))
    result = call(
        openclaw, env,
        "gateway", "call", "chat.send",
        "--port", str(port), "--json", "--timeout", str(cli_timeout * 1000),
        "--params", params,
        timeout=cli_timeout + 10,
    )
    return result.stdout


def count_lines(path: Path, needle: str) -> int:
    return sum(1 for line in read_log(path).splitlines() if needle in line)


def wait_for(predicate, timeout: float, interval: float = 0.4) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    """Invia un segnale all'intero gruppo del gateway, non solo al padre."""
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.send_signal(sig)
    except Exception:
        pass


def stop_process(process: subprocess.Popen, log_path: Path) -> None:
    """Chiude il gateway senza poter bloccare la verifica.

    Il gateway avvia processi figli: terminare solo il padre lascia vivo
    l'albero, tiene occupate le porte e faceva restare appeso il teardown
    (il secondo `wait` rilanciava dentro il `finally`). Qui si chiude l'intero
    gruppo, con SIGKILL come ultima risorsa, e nessuna eccezione esce da questa
    funzione: il teardown non deve mai trasformare un test riuscito in un blocco.
    """
    try:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=False,
            )
        else:
            _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                try:
                    process.kill()
                except Exception:
                    pass
            else:
                _signal_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            print(f"warning: gateway ancora attivo; log: {log_path}", file=sys.stderr)
    except Exception as exc:  # il teardown non deve mai propagare errori
        print(f"warning: teardown gateway: {str(exc)[:160]}", file=sys.stderr)
    finally:
        for stream in (getattr(process, "stdin", None),):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass


def build_config(workspace: Path, model_port: int, gateway_port: int) -> dict:
    return {
        "gateway": {
            "bind": "loopback",
            "port": gateway_port,
            "auth": {"mode": "none"},
        },
        "agents": {
            "defaults": {
                "workspace": str(workspace),
                "model": {"primary": "mock/mock-test"},
                "utilityModel": "",
            },
        },
        "models": {
            "mode": "merge",
            "providers": {
                "mock": {
                    "baseUrl": f"http://127.0.0.1:{model_port}/v1",
                    "apiKey": "smoke-local",
                    "api": "openai-completions",
                    "models": [{
                        "id": "mock-test",
                        "name": "Mock Test",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 200000,
                        "maxTokens": 8192,
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    }],
                },
            },
        },
        "plugins": {
            "load": {"paths": [str(PLUGIN)]},
            "entries": {
                "secretary-guard": {
                    "enabled": True,
                    "hooks": {"allowConversationAccess": True},
                },
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test guardia secretary")
    parser.add_argument("--keep", action="store_true", help="conserva i file temporanei")
    parser.add_argument("--openclaw", default="openclaw")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--retry-timeout",
        type=float,
        default=90.0,
        help="secondi massimi dedicati alla verifica del retry",
    )
    args = parser.parse_args()

    openclaw = openclaw_command(args.openclaw)
    compat = subprocess.run(
        [sys.executable, str(COMPAT), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    if compat.returncode != 0:
        print(f"compatibilità OpenClaw: {compat.stderr.strip()}", file=sys.stderr)
        return 1
    info = json.loads(compat.stdout)
    if not info.get("present"):
        print("OpenClaw non trovato: installalo prima dello smoke test.", file=sys.stderr)
        return 1
    if not info.get("version_ok"):
        print(
            f"OpenClaw {info.get('version') or 'sconosciuto'} non supportato: "
            f"serve >= {info.get('minimum')}.",
            file=sys.stderr,
        )
        return 1
    if not info.get("conversation_access"):
        print(
            "OpenClaw non espone allowConversationAccess: aggiorna per testare il retry.",
            file=sys.stderr,
        )
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="secretary-smoke-"))
    state = ModelState()
    model = None
    gateway = None
    log_handle = None
    ok = False
    try:
        model_port = free_port()
        gateway_port = free_port()
        model = start_model(model_port, state)
        workspace = tmp / "workspace"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text(
            "# Smoke workspace\nSegretaria di prova. Non usare dati reali.\n",
            encoding="utf-8",
        )
        config_path = tmp / "openclaw.json"
        config_path.write_text(
            json.dumps(build_config(workspace, model_port, gateway_port), indent=2) + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({
            "OPENCLAW_HOME": str(tmp / "home"),
            "OPENCLAW_STATE_DIR": str(tmp / "state"),
            "OPENCLAW_CONFIG_PATH": str(config_path),
        })
        (tmp / "home").mkdir()
        (tmp / "state").mkdir()
        log_path = tmp / "gateway.log"
        log_handle = log_path.open("w", encoding="utf-8")
        # Sessione separata: il teardown può chiudere tutto il gruppo senza
        # coinvolgere il processo che esegue i test.
        popen_extra = {} if os.name == "nt" else {"start_new_session": True}
        gateway = subprocess.Popen(
            [*openclaw, "gateway", "run", "--port", str(gateway_port),
             "--auth", "none", "--allow-unconfigured"],
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_extra,
        )
        health = f"http://127.0.0.1:{gateway_port}/health"
        if not wait_http(health, timeout=args.timeout):
            tail = "\n".join(read_log(log_path).splitlines()[-30:])
            raise RuntimeError(f"gateway non pronto ({health}).\n{tail}")
        # /health può rispondere prima che la manutenzione di avvio abbia
        # liberato la coda: aspettare il marker evita che sei messaggi concorrenti
        # superino la finestra anti-raffica solo perché il gateway è ancora lento.
        if not wait_for(
            lambda: "post-ready.startup-maintenance" in read_log(log_path),
            timeout=args.timeout,
        ):
            tail = "\n".join(read_log(log_path).splitlines()[-30:])
            raise RuntimeError(f"gateway non ha completato la manutenzione di avvio.\n{tail}")

        guard_log = tmp / "state" / "guard.log"
        burst_session = "agent:main:smoke-burst"
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(
                    chat_send, openclaw, env, gateway_port,
                    burst_session, "1", f"smoke-burst-{index}",
                )
                for index in range(1, 7)
            ]
            for future in futures:
                future.result()
        if not wait_for(lambda: state.calls >= 1 and count_lines(guard_log, "duplicate_inbound_suppressed") >= 5, args.timeout):
            raise RuntimeError(
                f"burst incompleto: model_calls={state.calls}, "
                f"suppressions={count_lines(guard_log, 'duplicate_inbound_suppressed')}"
            )
        time.sleep(2)
        burst_calls = state.calls
        burst_suppressed = count_lines(guard_log, "duplicate_inbound_suppressed")
        if burst_calls != 1 or burst_suppressed != 5:
            raise RuntimeError(
                f"burst errato: model_calls={burst_calls}, suppressions={burst_suppressed}"
            )

        retry_session = "agent:main:smoke-retry"
        retry_token = f"smoke-retry-{int(time.time())}"
        retry_timeout = max(35.0, args.retry_timeout)
        before_retry = state.calls
        state.set_fail(400)
        chat_send(
            openclaw, env, gateway_port, retry_session, retry_token,
            "smoke-retry-first", timeout=retry_timeout,
        )
        if not wait_for(
            lambda: "inbound_released_after_agent_failure" in read_log(guard_log),
            retry_timeout,
        ):
            raise RuntimeError("retry non rilasciato: agent_end non ha liberato la chiave")
        state.set_fail(0)
        chat_send(
            openclaw, env, gateway_port, retry_session, retry_token,
            "smoke-retry-second", timeout=retry_timeout,
        )
        if not wait_for(
            lambda: state.calls >= before_retry + 2,
            retry_timeout,
        ):
            tail = "\n".join(read_log(guard_log).splitlines()[-20:])
            raise RuntimeError(
                f"retry non arrivato al modello: calls={state.calls}, "
                f"atteso>={before_retry + 2}\nguard log:\n{tail}"
            )
        time.sleep(1)
        retry_calls = state.calls - before_retry
        final_suppressed = count_lines(guard_log, "duplicate_inbound_suppressed")
        if retry_calls != 2 or final_suppressed != 5:
            raise RuntimeError(
                f"retry errato: model_calls={retry_calls}, suppressions={final_suppressed}"
            )

        print("SMOKE OK")
        print(f"burst: 6 messaggi -> {burst_calls} chiamata, {burst_suppressed} soppressioni")
        print(f"retry: primo turno 400 -> {retry_calls} chiamate (1 fallita + 1 riuscita)")
        ok = True
        return 0
    except Exception as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        print(f"artefatti: {tmp}", file=sys.stderr)
        print("gateway log:", file=sys.stderr)
        print("\n".join(read_log(tmp / "gateway.log").splitlines()[-40:]), file=sys.stderr)
        return 1
    finally:
        if gateway is not None:
            stop_process(gateway, tmp / "gateway.log")
        if model is not None:
            try:
                model.shutdown()
                model.server_close()
            except Exception:
                pass
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:
                pass
        if model is not None:
            model.shutdown()
            model.server_close()
        if ok and not args.keep:
            try:
                shutil.rmtree(tmp)
            except OSError as exc:
                print(f"warning: artefatti non rimossi ({exc}): {tmp}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
