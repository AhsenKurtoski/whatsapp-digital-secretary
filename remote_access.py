#!/usr/bin/env python3
"""Accesso privato alla dashboard tramite Tailscale e tunnel SSH.

La dashboard resta in ascolto su 127.0.0.1. Questo helper costruisce un tunnel
locale SSH (nessuna porta pubblica, nessun servizio a pagamento) e verifica i
prerequisiti locali. Non modifica firewall, Tailscale o configurazione SSH.

Uso:
    python3 remote_access.py --host nome-macchina.tailnet.ts.net --user utente
    python3 remote_access.py --host nome-macchina.tailnet.ts.net --user utente --connect
    python3 remote_access.py --check
    python3 remote_access.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 8787
DEFAULT_SSH = "ssh"
DEFAULT_USER = ""

_HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^(?!-)[A-Za-z0-9._-]{1,64}$")


def _clean_host(host: str) -> str:
    value = str(host or "").strip()
    if not value or not _HOST_RE.fullmatch(value):
        raise ValueError(
            "host non valido: usa il nome MagicDNS Tailscale o un indirizzo IP, senza spazi o opzioni"
        )
    return value


def _clean_user(user: str) -> str:
    value = str(user or "").strip()
    if value and not _USER_RE.fullmatch(value):
        raise ValueError("utente SSH non valido")
    return value


def _clean_port(port: int) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("porta non valida") from exc
    if not 1 <= value <= 65535:
        raise ValueError("la porta deve essere tra 1 e 65535")
    return value


def build_ssh_command(
    host: str,
    user: str = DEFAULT_USER,
    port: int = DEFAULT_PORT,
    ssh: str = DEFAULT_SSH,
) -> list[str]:
    """Return the exact local-forward command, without invoking a shell.

    The local and remote ports must match: the dashboard's CSRF check compares
    the browser Origin with its own port, so changing only one side would make
    mutations fail even though the login page loaded.
    """
    target_host = _clean_host(host)
    target_user = _clean_user(user)
    target_port = _clean_port(port)
    ssh_bin = str(ssh or "").strip()
    if not ssh_bin:
        raise ValueError("comando ssh non valido")
    target = f"{target_user}@{target_host}" if target_user else target_host
    forward = f"127.0.0.1:{target_port}:127.0.0.1:{target_port}"
    return [
        ssh_bin,
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-L",
        forward,
        target,
    ]


def dashboard_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{_clean_port(port)}"


def check_environment(port: int = DEFAULT_PORT, timeout: float = 5.0) -> dict:
    """Check prerequisites without opening a tunnel or changing any state."""
    target_port = _clean_port(port)
    checks: list[dict] = []

    def add(check_id: str, label: str, ok: bool, detail: str, action: str = "") -> None:
        checks.append({
            "id": check_id,
            "label": label,
            "ok": ok,
            "detail": detail,
            "action": action,
        })

    ssh = shutil.which("ssh")
    add(
        "ssh",
        "Client SSH",
        bool(ssh),
        ssh or "comando ssh non trovato",
        "" if ssh else "Installa OpenSSH client (su Windows: Accessori facoltativi → OpenSSH).",
    )

    tailscale = shutil.which("tailscale")
    tailscale_ok = bool(tailscale)
    tailscale_detail = "comando tailscale non trovato"
    if tailscale:
        try:
            result = subprocess.run(
                [tailscale, "status", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            if result.returncode == 0:
                tailscale_detail = "Tailscale attivo"
            else:
                tailscale_ok = False
                tailscale_detail = (result.stderr or result.stdout or "Tailscale non connesso").strip()[:240]
        except Exception as exc:
            tailscale_ok = False
            tailscale_detail = f"Tailscale non verificabile: {str(exc)[:180]}"
    add(
        "tailscale",
        "Tailscale",
        tailscale_ok,
        tailscale_detail,
        "" if tailscale_ok else "Installa Tailscale, esegui 'tailscale up' e riprova.",
    )

    dashboard_ok = False
    dashboard_detail = "dashboard non raggiungibile su loopback"
    url = dashboard_url(target_port) + "/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            dashboard_ok = response.status == 200
            dashboard_detail = f"HTTP {response.status} su {url}"
    except urllib.error.HTTPError as exc:
        dashboard_ok = exc.code < 500
        dashboard_detail = f"HTTP {exc.code} su {url}"
    except Exception as exc:
        dashboard_detail = f"{url} non raggiungibile ({str(exc)[:160]})"
    add(
        "dashboard",
        "Dashboard locale",
        dashboard_ok,
        dashboard_detail,
        "" if dashboard_ok else "Avvia la dashboard con systemctl --user start openclaw-dashboard.",
    )

    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "command": build_ssh_command("host.tailnet.ts.net", port=target_port),
        "url": dashboard_url(target_port),
    }


def _print_check(result: dict) -> None:
    for item in result["checks"]:
        mark = "OK" if item["ok"] else "FAIL"
        print(f"[{mark}] {item['label']}: {item['detail']}")
        if not item["ok"] and item["action"]:
            print(f"       → {item['action']}")
    print(f"URL dopo il tunnel: {result['url']}")
    print(f"Comando: {' '.join(result['command'])}")


def _self_test() -> int:
    command = build_ssh_command("host.tailnet.ts.net", user="owner", port=8787)
    assert command[0] == "ssh"
    assert "-N" in command and "-T" in command
    assert command[command.index("-L") + 1] == "127.0.0.1:8787:127.0.0.1:8787"
    assert command[-1] == "owner@host.tailnet.ts.net"

    for bad in ("", "-oProxyCommand=evil", "host name", "host\nname", "host/path"):
        try:
            build_ssh_command(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"host non valido accettato: {bad!r}")
    for bad_port in (0, 65536, "abc"):
        try:
            build_ssh_command("host.tailnet.ts.net", port=bad_port)
        except ValueError:
            pass
        else:
            raise AssertionError(f"porta non valida accettata: {bad_port!r}")
    try:
        build_ssh_command("host.tailnet.ts.net", user="-oProxyCommand=evil")
    except ValueError:
        pass
    else:
        raise AssertionError("utente SSH non valido accettato")

    assert dashboard_url() == "http://127.0.0.1:8787"
    print("SELF-TEST OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tunnel SSH privato verso la dashboard, sopra Tailscale.",
        epilog="La dashboard deve restare in ascolto su 127.0.0.1; non usare porte pubbliche.",
    )
    parser.add_argument("--host", help="nome MagicDNS o IP Tailscale della macchina host")
    parser.add_argument("--user", default=DEFAULT_USER, help="utente SSH sull'host (opzionale)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="porta dashboard, uguale su entrambi i lati")
    parser.add_argument("--ssh", default=DEFAULT_SSH, help="comando client SSH")
    parser.add_argument("--check", action="store_true", help="verifica SSH, Tailscale e dashboard")
    parser.add_argument("--connect", action="store_true", help="apri il tunnel e resta in esecuzione")
    parser.add_argument("--json", action="store_true", help="output JSON per --check")
    parser.add_argument("--self-test", action="store_true", help="test interni senza rete")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.check:
        result = check_environment(args.port)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_check(result)
        return 0 if result["ok"] else 1

    if not args.host:
        parser.error("--host è obbligatorio (oppure usa --check o --self-test)")
    try:
        command = build_ssh_command(args.host, args.user, args.port, args.ssh)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.connect:
        print("Tunnel SSH privato (nessuna porta pubblica):")
        print("  " + " ".join(command))
        print(f"Poi apri: {dashboard_url(args.port)}")
        print("Aggiungi --connect per avviarlo; premi Ctrl+C per chiuderlo.")
        return 0

    print("Apro il tunnel SSH. La dashboard resta su loopback; premi Ctrl+C per chiudere.")
    try:
        return subprocess.run(command, check=False).returncode
    except FileNotFoundError:
        print("SSH non trovato: installa il client OpenSSH.", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
