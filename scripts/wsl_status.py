#!/usr/bin/env python3
"""Dove è già installata la segreteria su Windows/WSL2 e qual è il prossimo comando.

Su Windows elenca le distro WSL e le interroga con un probe bash in sola lettura;
dentro Linux/WSL esegue lo stesso probe in locale. Non modifica niente: legge
percorsi, dipendenze, stato del gateway e, se disponibile, i tool MCP che il bot
vede davvero. Serve a rispondere alla domanda "ho già un'installazione, e adesso
cosa devo lanciare?" senza indovinare.

    python3 scripts/wsl_status.py            # report + prossimo comando
    python3 scripts/wsl_status.py --json
    python3 scripts/wsl_status.py --self-test

Su Windows serve PowerShell (o CMD): lo script usa ``wsl.exe`` e stampa il
comando da eseguire dentro la distro. Vedi ``docs/WSL.md``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_URL = "https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git"
REPO_NAME = "whatsapp-digital-secretary"
GATEWAY_UNIT = "openclaw-gateway.service"
REQUIRED_TOOLS = (
    "request_quote",
    "request_quote_resend",
    "request_email",
    "request_calendar",
)

# Probe POSIX in sola lettura: una riga key=value per ogni fatto. Non importa
# nulla dal repo e non scrive file, così funziona anche su una distro vuota.
PROBE = r"""
echo "wsl=$(grep -qi microsoft /proc/version 2>/dev/null && echo 1 || echo 0)"
repo="$(find "$HOME" -maxdepth 4 -type d -name whatsapp-digital-secretary 2>/dev/null | head -n 1)"
echo "repo=$repo"
echo "installed=$([ -f "$HOME/.openclaw/secretary/approval_mcp.py" ] && echo 1 || echo 0)"
echo "config=$([ -f "$HOME/.config/digital-secretary/config.json" ] && echo 1 || echo 0)"
echo "crypto=$(python3 -c 'import cryptography' >/dev/null 2>&1 && echo 1 || echo 0)"
echo "openclaw=$(command -v openclaw >/dev/null 2>&1 && echo 1 || echo 0)"
echo "node=$(command -v node >/dev/null 2>&1 && echo 1 || echo 0)"
echo "python=$(command -v python3 >/dev/null 2>&1 && echo 1 || echo 0)"
echo "gateway=$(systemctl --user is-active openclaw-gateway.service 2>/dev/null || echo unknown)"
tools=""
if command -v openclaw >/dev/null 2>&1; then
  tools="$(openclaw mcp probe secretary --json 2>/dev/null | python3 -c 'import json,sys
raw = sys.stdin.read()
start = raw.find("{")
if start < 0:
    raise SystemExit(0)
data = json.loads(raw[start:])
print(",".join(sorted({str(x).rsplit("__", 1)[-1] for x in (data.get("tools") or [])})))' 2>/dev/null)"
fi
echo "tools=$tools"
""".strip()

FLAGS = ("wsl", "installed", "config", "crypto", "openclaw", "node", "python")


def _decode(raw: bytes | str) -> str:
    """`wsl.exe` risponde in UTF-16LE; bash in UTF-8."""
    if isinstance(raw, str):
        return raw
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    return raw.decode("utf-8", errors="replace")


def parse_probe(text: str) -> dict:
    """Trasforma l'output del probe in uno stato booleano/leggibile."""
    state: dict = {
        "wsl": False,
        "repo": "",
        "installed": False,
        "config": False,
        "crypto": False,
        "openclaw": False,
        "node": False,
        "python": False,
        "gateway": "unknown",
        "tools": [],
    }
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key in FLAGS:
            state[key] = value == "1"
        elif key == "repo":
            state["repo"] = value
        elif key == "gateway":
            state["gateway"] = value or "unknown"
        elif key == "tools":
            state["tools"] = [name for name in value.split(",") if name]
    return state


def windows_distros(runner=subprocess.run) -> list[str]:
    """Nomi delle distro WSL installate (vuoto su Linux o senza WSL)."""
    if os.name != "nt":
        return []
    try:
        result = runner(
            ["wsl.exe", "-l", "-q"], capture_output=True, timeout=30, check=False
        )
    except Exception:
        return []
    names: list[str] = []
    for line in _decode(getattr(result, "stdout", b"") or b"").splitlines():
        name = line.strip().lstrip("*").strip()
        if not name or " " in name or name.endswith(":"):
            continue
        names.append(name)
    return names


def probe_distro(name: str = "", runner=subprocess.run) -> dict:
    """Esegue il probe dentro una distro WSL (o in locale, se name è vuoto)."""
    if os.name == "nt":
        if not name:
            return parse_probe("")
        command = ["wsl.exe", "-d", name, "-e", "bash", "-lc", PROBE]
    else:
        if not shutil.which("bash"):
            return probe_without_bash()
        command = ["bash", "-lc", PROBE]
    try:
        result = runner(
            command, capture_output=True, timeout=90, check=False
        )
    except Exception:
        return parse_probe("")
    output = _decode(getattr(result, "stdout", b"") or b"")
    if not output.strip():
        output = _decode(getattr(result, "stderr", b"") or b"")
    return parse_probe(output)


def probe_without_bash() -> dict:
    """Fallback minimo quando bash non c'è (raro: Linux ha sempre bash)."""
    home = Path.home()
    exists = (home / ".openclaw" / "secretary" / "approval_mcp.py").is_file()
    return parse_probe(
        "\n".join([
            "repo=" + str(home / REPO_NAME if (home / REPO_NAME).is_dir() else ""),
            f"installed={int(exists)}",
            f"config={int((home / '.config' / 'digital-secretary' / 'config.json').is_file())}",
        ])
    )


def local_repo(start: Path | None = None) -> str:
    """Repo del progetto partendo dalla cartella corrente (utile se ci sei già dentro)."""
    current = Path(start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "scripts" / "install.sh").is_file() and (
            candidate / "approval_mcp.py"
        ).is_file():
            return str(candidate)
    return ""


def missing_tools(state: dict) -> list[str]:
    tools = set(state.get("tools") or [])
    return [name for name in REQUIRED_TOOLS if name not in tools]


def next_step(state: dict, distro: str = "") -> dict:
    """Il prossimo comando da eseguire, con il motivo e dove lanciarlo."""
    repo = str(state.get("repo") or "")
    cd = f"cd {repo} && " if repo else ""

    if os.name == "nt" and not state:
        return {
            "command": "wsl --install -d Ubuntu",
            "where": "powershell",
            "reason": "Nessuna distro WSL: installa Ubuntu (serve un riavvio).",
        }
    if not state.get("python") and not state.get("wsl"):
        return {
            "command": "wsl --install -d Ubuntu",
            "where": "powershell",
            "reason": "WSL non rilevato: la segreteria non gira su Windows nativo.",
        }
    if not state.get("crypto"):
        return {
            "command": "sudo apt install python3-cryptography",
            "where": "wsl",
            "reason": "Manca cryptography: memoria clienti e backup cifrati non funzionano.",
        }
    if not repo:
        return {
            "command": f"git clone {REPO_URL} && cd {REPO_NAME}",
            "where": "wsl",
            "reason": "Cartella del repo non trovata nella home della distro.",
        }
    if not state.get("installed") and not state.get("config"):
        return {
            "command": cd + "python3 setup.py",
            "where": "wsl",
            "reason": "Configurazione dell'attività assente: il setup la crea.",
        }
    if not state.get("installed"):
        return {
            "command": cd + "bash scripts/install.sh",
            "where": "wsl",
            "reason": "Cartella ~/.openclaw/secretary incompleta: l'installer copia tutto.",
        }
    if state.get("gateway") != "active":
        return {
            "command": (
                f"systemctl --user restart {GATEWAY_UNIT} && "
                f"systemctl --user status {GATEWAY_UNIT} --no-pager"
            ),
            "where": "wsl",
            "reason": f"Gateway non attivo (stato: {state.get('gateway')}).",
        }
    tools_missing = missing_tools(state)
    if tools_missing:
        return {
            "command": (
                "python3 ~/.openclaw/secretary/approval_setup.py && "
                f"systemctl --user restart {GATEWAY_UNIT}"
            ),
            "where": "wsl",
            "reason": "Il bot non vede: " + ", ".join(tools_missing) + ".",
        }
    return {
        "command": "openclaw mcp probe secretary --json",
        "where": "wsl",
        "reason": "Tutto pronto: manda un messaggio di prova da un secondo telefono.",
    }


def _icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def render(state: dict, distro: str, step: dict, distros: list[str]) -> str:
    lines: list[str] = []
    if os.name == "nt":
        lines.append(
            "Distro WSL: " + (", ".join(distros) if distros else "nessuna")
        )
        if distro:
            lines.append(f"Interrogata: {distro}")
    else:
        lines.append("Ambiente: Linux" + (" (WSL)" if state.get("wsl") else ""))
    lines.append("")
    lines.append(_icon(bool(state.get("python"))) + " Python 3: " + ("presente" if state.get("python") else "assente"))
    lines.append(_icon(bool(state.get("crypto"))) + " cryptography: " + ("installata" if state.get("crypto") else "mancante"))
    lines.append(_icon(bool(state.get("node"))) + " Node.js: " + ("presente" if state.get("node") else "assente"))
    lines.append(_icon(bool(state.get("openclaw"))) + " OpenClaw: " + ("presente" if state.get("openclaw") else "assente"))
    lines.append(_icon(bool(state.get("repo"))) + " Repo: " + (str(state.get("repo")) or "non trovato"))
    lines.append(_icon(bool(state.get("config"))) + " Configurazione attività: " + ("presente" if state.get("config") else "assente"))
    lines.append(_icon(bool(state.get("installed"))) + " Installazione in ~/.openclaw/secretary: " + ("presente" if state.get("installed") else "assente"))
    lines.append(
        ("✅" if state.get("gateway") == "active" else "🟡")
        + f" Gateway: {state.get('gateway')}"
    )
    tools = state.get("tools") or []
    if tools:
        missing = missing_tools(state)
        label = "Tool del bot: " + ", ".join(tools)
        lines.append(("✅ " + label) if not missing else ("❌ " + label + f" (mancano {', '.join(missing)})"))
    elif state.get("installed"):
        lines.append("🟡 Tool del bot: probe non disponibile (gateway spento o OpenClaw assente)")
    lines.append("")
    lines.append(f"Prossimo comando ({step['where']}):")
    lines.append(f"  {step['command']}")
    lines.append("  motivo: " + step["reason"])
    return "\n".join(lines)


def collect(distro: str = "") -> tuple[dict, list[str], str]:
    """Stato delle distro (Windows) e della distro interrogata."""
    distros = windows_distros()
    if os.name == "nt":
        target = distro or (distros[0] if distros else "")
        state = probe_distro(target) if target else parse_probe("")
        return state, distros, target
    state = probe_distro()
    if not state.get("repo"):
        # Su Windows la cartella C:\ non serve alla distro: lo usa solo Linux.
        state["repo"] = local_repo()
    return state, distros, ""


def self_test() -> None:
    base = {
        "wsl": True, "repo": "/home/u/" + REPO_NAME, "installed": True,
        "config": True, "crypto": True, "openclaw": True, "node": True,
        "python": True, "gateway": "active", "tools": list(REQUIRED_TOOLS),
    }
    assert next_step(base)["command"].startswith("openclaw mcp probe")
    assert next_step({**base, "crypto": False})["command"].startswith("sudo apt")
    assert next_step({**base, "repo": ""})["command"].startswith("git clone")
    assert next_step({**base, "config": False, "installed": False})["command"].endswith("setup.py")
    assert next_step({**base, "installed": False, "gateway": "unknown"})["command"].endswith("install.sh")
    assert next_step({**base, "gateway": "inactive"})["command"].startswith("systemctl --user restart")
    tools_missing = next_step({**base, "tools": ["request_email"]})
    assert "approval_setup.py" in tools_missing["command"]
    assert "request_quote" in tools_missing["reason"]
    parsed = parse_probe(
        "repo=/home/u/x\ninstalled=1\nconfig=0\ngateway=active\ntools=request_quote,request_email\n"
    )
    assert parsed["installed"] is True and parsed["config"] is False
    assert parsed["tools"] == ["request_quote", "request_email"]
    assert render(base, "", next_step(base), ["Ubuntu"]).startswith(("Ambiente", "Distro"))
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stato installazione segreteria su WSL2")
    parser.add_argument("--distro", default="", help="distro WSL da interrogare (default: la prima)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    state, distros, target = collect(args.distro)
    step = next_step(state, target)
    if args.json:
        print(json.dumps(
            {"distros": distros, "distro": target, "state": state, "next": step},
            ensure_ascii=False,
        ))
        return 0
    print(render(state, target, step, distros))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
