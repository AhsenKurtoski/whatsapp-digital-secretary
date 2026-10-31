#!/usr/bin/env python3
"""MCP locale per la segreteria.

Espone solo due tool che creano una richiesta in attesa di approvazione:
- request_email
- request_calendar

Non esiste un tool per inviare email o creare eventi: l'azione la esegue
solo la dashboard privata dopo il login e l'approvazione del titolare.
"""
from __future__ import annotations

import json
import sys

from approval_store import append_request

PROTOCOL_VERSION = "2024-11-05"
TOOLS = [
    {
        "name": "request_email",
        "description": (
            "Crea una richiesta di email di follow-up da far approvare al titolare. "
            "Non invia nulla. Usalo solo quando il cliente ha chiesto esplicitamente "
            "un follow-up e ha confermato il suo indirizzo email."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "to": {"type": "string", "description": "Indirizzo email del cliente, uno solo o separati da virgola."},
                "subject": {"type": "string", "description": "Oggetto breve e professionale."},
                "body": {
                    "type": "string",
                    "description": (
                        "Testo semplice dell'email, in italiano corretto, senza HTML o markdown: "
                        "il sistema applica il template grafico dell'attività. Inizia con 'Ciao [nome],' "
                        "e riporta solo dati che il cliente ha confermato."
                    ),
                },
                "client_name": {"type": "string", "description": "Nome del cliente, se noto."},
                "client_phone": {"type": "string", "description": "Numero WhatsApp del cliente, se noto."},
                "reason": {"type": "string", "description": "Perché il titolare dovrebbe approvare questa email."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "request_calendar",
        "description": (
            "Crea una richiesta di appuntamento da far approvare al titolare. "
            "Non crea l'evento e non invia inviti. Usa date ISO 8601 con fuso Europe/Rome."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "description": "Titolo dell'appuntamento."},
                "start": {"type": "string", "description": "Inizio ISO 8601, es. 2026-10-28T10:00:00+01:00."},
                "end": {"type": "string", "description": "Fine ISO 8601."},
                "timezone": {"type": "string", "description": "Fuso orario IANA, default Europe/Rome."},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email degli invitati, massimo 10.",
                },
                "location": {"type": "string", "description": "Luogo o link della call."},
                "description": {"type": "string", "description": "Note per l'appuntamento."},
                "client_name": {"type": "string", "description": "Nome del cliente, se noto."},
                "client_phone": {"type": "string", "description": "Numero WhatsApp del cliente, se noto."},
                "reason": {"type": "string", "description": "Perché il titolare dovrebbe approvare l'appuntamento."},
            },
            "required": ["summary", "start", "end"],
        },
    },
]


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(request_id: str, kind: str) -> dict:
    label = "email" if kind == "email" else "appuntamento"
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Richiesta {request_id} creata per {label}. "
                    "È in attesa di approvazione del titolare: non dire al cliente che è già stato inviato o creato. "
                    "Rispondi che hai preparato la richiesta e che il titolare la approva."
                ),
            }
        ],
        "structuredContent": {"request_id": request_id, "status": "pending", "type": kind},
        "isError": False,
    }


def _handle_tool(name: str, arguments: dict) -> dict:
    arguments = arguments if isinstance(arguments, dict) else {}
    if name == "request_email":
        request = append_request(
            "email",
            {
                "to": arguments.get("to"),
                "subject": arguments.get("subject"),
                "body": arguments.get("body"),
            },
            client_name=str(arguments.get("client_name") or ""),
            client_phone=str(arguments.get("client_phone") or ""),
            reason=str(arguments.get("reason") or ""),
        )
        return _result(request["id"], "email")
    if name == "request_calendar":
        request = append_request(
            "calendar",
            {
                "summary": arguments.get("summary"),
                "start": arguments.get("start"),
                "end": arguments.get("end"),
                "timezone": arguments.get("timezone") or "Europe/Rome",
                "attendees": arguments.get("attendees") or [],
                "location": arguments.get("location") or "",
                "description": arguments.get("description") or "",
            },
            client_name=str(arguments.get("client_name") or ""),
            client_phone=str(arguments.get("client_phone") or ""),
            reason=str(arguments.get("reason") or ""),
        )
        return _result(request["id"], "calendar")
    raise ValueError(f"tool sconosciuto: {name}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            continue
        if not isinstance(message, dict):
            continue
        msg_id = message.get("id")
        method = message.get("method")
        if msg_id is None and method and str(method).startswith("notifications/"):
            continue
        try:
            if method == "initialize":
                params = message.get("params") or {}
                requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
                protocol = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18"} else PROTOCOL_VERSION
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": protocol,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "secretary-approvals", "version": "1.0.0"},
                    },
                })
            elif method == "ping":
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            elif method == "tools/list":
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = message.get("params") or {}
                name = str(params.get("name") or "")
                result = _handle_tool(name, params.get("arguments") or {})
                _send({"jsonrpc": "2.0", "id": msg_id, "result": result})
            else:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": "metodo non supportato"},
                })
        except Exception as exc:
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": str(exc)[:300]},
            })


if __name__ == "__main__":
    main()
