#!/usr/bin/env python3
"""Rileva le funzionalità di OpenClaw usate dall'installer.

L'installer non deve assumere che ogni versione conosca
``--accept-capabilities`` o ``plugins.entries.<id>.hooks.allowConversationAccess``.
Questo modulo esegue solo probe in lettura (``--version``, help e schema) e
stampa un blocco shell sicuro con i risultati. Non modifica la configurazione.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

MIN_OPENCLAW = "2026.4.22"


def parse_version(text: str) -> tuple[int, ...]:
    """Estrae la versione da ``OpenClaw 2026.10.7 (hash)``."""
    match = re.search(r"OpenClaw\s+(\d+(?:\.\d+)*)", text or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def version_at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    # Le versioni hanno lunghezze diverse: 2026.4.22 e 2026.10.7.
    length = max(len(version), len(minimum))
    left = version + (0,) * (length - len(version))
    right = minimum + (0,) * (length - len(minimum))
    return left >= right


def supports_accept_capabilities(help_text: str) -> bool:
    return "--accept-capabilities" in (help_text or "")


def supports_conversation_access(schema_text: str) -> bool:
    return "allowConversationAccess" in (schema_text or "")


@dataclass
class Compatibility:
    present: bool
    version: str
    version_ok: bool
    accept_capabilities: bool
    conversation_access: bool
    minimum: str = MIN_OPENCLAW
    error: str = ""


@dataclass
class InstallPlan:
    allowed: bool
    refuse: bool
    reason: str
    accept_capabilities: bool
    set_conversation_access: bool
    burst_only: bool
    warnings: list[str]


def make_install_plan(value: Compatibility) -> InstallPlan:
    """Decide se procedere, con quali flag e con quale funzionalità ridotta."""
    if not value.present:
        return InstallPlan(
            False, False, "openclaw non trovato", False, False, False,
            ["openclaw non trovato; il plugin secretary-guard non verrà installato."],
        )
    if not value.version_ok:
        reason = (
            f"OpenClaw {value.version or 'sconosciuto'} non supportato: "
            f"serve >= {value.minimum} per la guardia prima del modello."
        )
        return InstallPlan(False, True, reason, False, False, False, [reason])
    warnings: list[str] = []
    if not value.accept_capabilities:
        warnings.append(
            "questa versione non conosce --accept-capabilities; "
            "tento l'installazione legacy, aggiorna se richiede consenso."
        )
    if not value.conversation_access:
        warnings.append(
            f"questa versione non espone allowConversationAccess; "
            f"burst attivo ma retry disattivato. Aggiorna OpenClaw >= {value.minimum}."
        )
    return InstallPlan(
        True, False, "", value.accept_capabilities, value.conversation_access,
        not value.conversation_access, warnings,
    )


def resolve_openclaw(openclaw: str) -> list[str]:
    path = shutil.which(openclaw)
    if not path:
        return [openclaw]
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path]
    return [path]


def _run(command: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def inspect(openclaw: str = "openclaw") -> Compatibility:
    executable = resolve_openclaw(openclaw)
    code, version_text = _run([*executable, "--version"], timeout=10)
    if code != 0:
        return Compatibility(False, "", False, False, False, error=version_text.strip()[:300])
    version = parse_version(version_text)
    version_text_value = ".".join(str(part) for part in version) if version else ""

    _, install_help = _run([*executable, "plugins", "install", "--help"])
    _, schema = _run([*executable, "config", "schema"], timeout=30)
    minimum = tuple(int(part) for part in MIN_OPENCLAW.split("."))
    return Compatibility(
        present=True,
        version=version_text_value,
        version_ok=bool(version) and version_at_least(version, minimum),
        accept_capabilities=supports_accept_capabilities(install_help),
        conversation_access=supports_conversation_access(schema),
    )


def _shell_text(text: str) -> str:
    return text.replace("'", " ").replace("\n", " ").replace("\r", " ")[:500]


def as_shell(value: Compatibility, plan: InstallPlan | None = None) -> str:
    version = re.sub(r"[^0-9.]", "", value.version)
    error = _shell_text(value.error)
    plan = plan or make_install_plan(value)
    warning = _shell_text("; ".join(plan.warnings))
    return "\n".join([
        f"OC_PRESENT={int(value.present)}",
        f"OC_VERSION='{version}'",
        f"OC_VERSION_OK={int(value.version_ok)}",
        f"OC_ACCEPT_CAPABILITIES={int(value.accept_capabilities)}",
        f"OC_CONVERSATION_ACCESS={int(value.conversation_access)}",
        f"OC_MIN_VERSION='{value.minimum}'",
        f"OC_COMPAT_ERROR='{error}'",
        f"OC_INSTALL_ALLOWED={int(plan.allowed)}",
        f"OC_REFUSE={int(plan.refuse)}",
        f"OC_SET_CONVERSATION_ACCESS={int(plan.set_conversation_access)}",
        f"OC_BURST_ONLY={int(plan.burst_only)}",
        f"OC_PLAN_WARNING='{warning}'",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Compatibilità OpenClaw")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("--require-min", action="store_true")
    parser.add_argument("--install-plan", action="store_true")
    args = parser.parse_args()
    result = inspect()
    plan = make_install_plan(result)
    if args.install_plan:
        print(json.dumps(asdict(plan), ensure_ascii=False))
        return 0 if plan.allowed else 1
    if args.require_min and not result.version_ok:
        if args.json:
            data = asdict(result)
            data["plan"] = asdict(plan)
            print(json.dumps(data, ensure_ascii=False))
        else:
            message = plan.reason or (plan.warnings[0] if plan.warnings else "compatibilità insufficiente")
            print(message, file=sys.stderr)
        return 1
    if args.json:
        data = asdict(result)
        data["plan"] = asdict(plan)
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(as_shell(result, plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
