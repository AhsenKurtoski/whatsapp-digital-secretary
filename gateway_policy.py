#!/usr/bin/env python3
"""Applica a OpenClaw la politica anti-raffica per WhatsApp.

Cinque o sei messaggi identici in pochi secondi non devono diventare cinque o
sei turni dell'agente. OpenClaw sa già raggruppare i messaggi in arrivo:

- ``messages.inbound.byChannel.whatsapp``: finestra di quiete in millisecondi
  prima di passare il testo all'agente;
- ``messages.queue.byChannel.whatsapp``: ``collect`` accoda i messaggi
  compatibili invece di sovrapporre le risposte.

Il file viene modificato solo se le chiavi mancano: una configurazione scelta
dall'utente non viene mai sovrascritta. Ogni scrittura è atomica e validata.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DEFAULT_POLICY = {
    "messages": {
        "inbound": {
            "debounceMs": 1200,
            "byChannel": {"whatsapp": 1800},
        },
        "queue": {
            "byChannel": {"whatsapp": "collect"},
        },
    }
}
QUEUE_MODES = {"steer", "followup", "collect", "interrupt"}


def _merge_missing(target: dict, defaults: dict, path: str = "") -> list[str]:
    """Inserisce solo le chiavi assenti; restituisce i percorsi aggiunti."""
    added: list[str] = []
    for key, value in defaults.items():
        current_path = f"{path}.{key}" if path else key
        if key not in target:
            target[key] = json.loads(json.dumps(value))
            added.append(current_path)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            added.extend(_merge_missing(target[key], value, current_path))
    return added


def validate(config: dict) -> None:
    messages = config.get("messages")
    if not isinstance(messages, dict):
        raise ValueError("messages non è un oggetto")
    inbound = messages.get("inbound")
    if inbound is not None:
        if not isinstance(inbound, dict):
            raise ValueError("messages.inbound non è un oggetto")
        debounce = inbound.get("debounceMs")
        if debounce is not None and (not isinstance(debounce, int) or debounce < 0):
            raise ValueError("messages.inbound.debounceMs deve essere un intero >= 0")
        by_channel = inbound.get("byChannel")
        if by_channel is not None:
            if not isinstance(by_channel, dict):
                raise ValueError("messages.inbound.byChannel non è un oggetto")
            for channel, value in by_channel.items():
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"messages.inbound.byChannel.{channel} non valido")
    queue = messages.get("queue")
    if queue is not None:
        if not isinstance(queue, dict):
            raise ValueError("messages.queue non è un oggetto")
        mode = queue.get("mode")
        if mode is not None and mode not in QUEUE_MODES:
            raise ValueError(f"messages.queue.mode non valido: {mode}")
        by_channel = queue.get("byChannel")
        if by_channel is not None:
            if not isinstance(by_channel, dict):
                raise ValueError("messages.queue.byChannel non è un oggetto")
            for channel, value in by_channel.items():
                if value not in QUEUE_MODES:
                    raise ValueError(f"messages.queue.byChannel.{channel} non valido: {value}")


def apply(config_path: str, dry_run: bool = False, policy: dict | None = None) -> dict:
    path = Path(config_path).expanduser()
    policy = policy or DEFAULT_POLICY
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"configurazione OpenClaw non trovata: {path}") from None
    except Exception as exc:
        raise ValueError(f"configurazione OpenClaw non leggibile: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("configurazione OpenClaw non valida")
    added = _merge_missing(config, policy)
    validate(config)
    result = {"ok": True, "changed": bool(added), "added": added, "path": str(path)}
    if dry_run or not added:
        return result
    backup = path.with_name(path.name + ".bak-secretary")
    try:
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return result


def self_test() -> None:
    tmp = tempfile.mkdtemp(prefix="secretary-policy-")
    path = Path(tmp) / "openclaw.json"
    path.write_text(json.dumps({
        "agents": {"defaults": {"model": {"primary": "google/gemini-3.5-flash"}}},
        "messages": {"queue": {"mode": "steer"}},
    }), encoding="utf-8")

    preview = apply(str(path), dry_run=True)
    assert preview["changed"] is True, preview
    assert json.loads(path.read_text(encoding="utf-8"))["messages"].get("inbound") is None

    result = apply(str(path))
    assert result["changed"] is True, result
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["messages"]["inbound"]["byChannel"]["whatsapp"] == 1800, saved
    assert saved["messages"]["queue"]["byChannel"]["whatsapp"] == "collect", saved
    assert saved["messages"]["queue"]["mode"] == "steer", "le scelte utente non vanno toccate"
    assert saved["agents"]["defaults"]["model"]["primary"] == "google/gemini-3.5-flash"

    again = apply(str(path))
    assert again["changed"] is False, again
    print("SELF-TEST OK")
    print(json.dumps(result, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Politica anti-raffica WhatsApp per OpenClaw")
    parser.add_argument("--config", default=os.environ.get(
        "OPENCLAW_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json")
    ))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        result = apply(args.config, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"gateway policy: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
