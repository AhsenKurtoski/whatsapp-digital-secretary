#!/usr/bin/env python3
"""MCP locale per la segreteria.

Espone solo tre tool che creano una richiesta in attesa di approvazione:
- request_email
- request_calendar
- request_quote  (preventivo PDF generato in locale e allegato all'email)

Non esiste un tool per inviare email, creare eventi o scaricare file: l'azione
la esegue solo la dashboard privata dopo il login e l'approvazione del titolare.
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
        "name": "request_quote",
        "description": (
            "Prepara un preventivo professionale in PDF e l'email con l'allegato, da far "
            "approvare al titolare. Non invia nulla e non mostra prezzi non confermati: usa "
            "solo voci, quantità e prezzi che il cliente ha approvato o che il titolare ha "
            "indicato. Il sistema ricalcola subtotale, sconto, IVA e totale."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "client_name": {"type": "string", "description": "Nome del cliente."},
                "client_email": {"type": "string", "description": "Email del cliente: obbligatoria per inviare il preventivo."},
                "client_company": {"type": "string", "description": "Azienda del cliente, se nota."},
                "client_phone": {"type": "string", "description": "Numero WhatsApp del cliente, se noto."},
                "client_address": {"type": "string", "description": "Indirizzo o città del cliente, se nota."},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 40,
                    "description": "Voci del preventivo. I totali vengono calcolati dal sistema.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "description": {"type": "string", "description": "Descrizione della voce."},
                            "qty": {"type": "number", "description": "Quantità, default 1."},
                            "unit": {"type": "string", "description": "Unità, es. forfait, ora, mese."},
                            "unit_price": {"type": "number", "description": "Prezzo unitario in euro, IVA esclusa."},
                        },
                        "required": ["description", "unit_price"],
                    },
                },
                "discount_pct": {"type": "number", "description": "Sconto percentuale, 0-100."},
                "vat_pct": {"type": "number", "description": "IVA percentuale; se assente usa quella dell'attività."},
                "valid_days": {"type": "integer", "description": "Validità del preventivo in giorni."},
                "notes": {"type": "string", "description": "Note o esclusioni riportate nel PDF."},
                "subject": {"type": "string", "description": "Oggetto dell'email di accompagnamento."},
                "body": {"type": "string", "description": "Testo semplice dell'email; se assente viene generato dal sistema."},
                "reason": {"type": "string", "description": "Perché il titolare dovrebbe approvare questo preventivo."},
            },
            "required": ["client_name", "client_email", "items"],
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


def _result(request_id: str, kind: str, extra: dict | None = None) -> dict:
    label = {"email": "email", "calendar": "appuntamento", "quote": "preventivo PDF"}.get(kind, kind)
    detail = ""
    if extra:
        if extra.get("number"):
            detail = f" Numero {extra['number']}"
            if extra.get("total") is not None:
                detail += f", totale {extra['total']} {extra.get('currency') or 'EUR'}"
            detail += "."
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Richiesta {request_id} creata per {label}.{detail} "
                    "È in attesa di approvazione del titolare: non dire al cliente che è già stato inviato, "
                    "creato o consegnato. Rispondi che hai preparato la richiesta e che il titolare la approva."
                ),
            }
        ],
        "structuredContent": {"request_id": request_id, "status": "pending", "type": kind, **(extra or {})},
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
    if name == "request_quote":
        # I campi opzionali assenti non devono sovrascrivere i default del sistema.
        arguments = {key: value for key, value in arguments.items() if value is not None}
        request = append_request(
            "quote",
            {
                "to": arguments.get("client_email") or arguments.get("to"),
                "client_name": arguments.get("client_name"),
                "client_email": arguments.get("client_email"),
                "client_company": arguments.get("client_company"),
                "client_address": arguments.get("client_address"),
                "items": arguments.get("items") or [],
                "discount_pct": arguments.get("discount_pct") or 0,
                "vat_pct": arguments.get("vat_pct"),
                "valid_days": arguments.get("valid_days"),
                "notes": arguments.get("notes") or "",
                "subject": arguments.get("subject") or "",
                "body": arguments.get("body") or "",
            },
            client_name=str(arguments.get("client_name") or ""),
            client_phone=str(arguments.get("client_phone") or ""),
            reason=str(arguments.get("reason") or ""),
        )
        payload = request.get("payload") or {}
        return _result(
            request["id"],
            "quote",
            {
                "number": payload.get("number"),
                "total": (payload.get("totals") or {}).get("total"),
                "currency": payload.get("currency"),
                "pdf": bool(payload.get("pdf")),
            },
        )
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
