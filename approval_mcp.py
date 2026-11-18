#!/usr/bin/env python3
"""MCP locale per la segreteria.

Espone i tool che creano una richiesta in attesa di approvazione:
- request_email
- request_calendar
- request_quote        (preventivo PDF generato in locale e allegato all'email)
- request_quote_resend (rigenera il PDF di un preventivo esistente, nuovo numero)

Espone inoltre la memoria del cliente (`client_brief`, `remember_fact`) e la
consultazione dei preventivi già emessi (`list_quotes`).

Non esiste un tool per inviare email, creare eventi o scaricare file: l'azione
la esegue solo la dashboard privata dopo il login e l'approvazione del titolare.
"""
from __future__ import annotations

import json
import re
import sys

from approval_store import append_request

PROTOCOL_VERSION = "2024-11-05"
TOOLS = [
    {
        "name": "client_brief",
        "description": (
            "Chiamalo come PRIMO passo di ogni messaggio del cliente, prima di rispondere. "
            "Restituisce la memoria del cliente, la lingua, l'intento, i segnali di truffa e, "
            "quando è disponibile, una risposta pronta già verificata (orari, zona, servizi, "
            "sito, note sui prezzi o risposte identiche già inviate). Se `answer` non è vuota "
            "usala esattamente, senza aggiungere dati. Se è vuota rispondi solo con ciò che "
            "conosci e non inventare orari, prezzi, disponibilità o link."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "message": {"type": "string", "description": "Testo esatto dell'ultimo messaggio del cliente."},
                "phone": {"type": "string", "description": "Numero WhatsApp del cliente, se disponibile."},
                "client_name": {"type": "string", "description": "Nome del cliente, se noto."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Salva un fatto utile e duraturo su questo cliente, così lo ricorderai nei "
            "messaggi successivi e nelle prossime chat (es. azienda, budget, preferenze, "
            "servizio richiesto). Usalo solo per dati che il cliente ha confermato; non "
            "salvare password, OTP, carte o dati sensibili."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "phone": {"type": "string", "description": "Numero WhatsApp del cliente."},
                "key": {"type": "string", "description": "Etichetta breve, es. azienda, budget, servizio."},
                "value": {"type": "string", "description": "Valore confermato dal cliente."},
            },
            "required": ["phone", "key", "value"],
        },
    },
    {
        "name": "list_quotes",
        "description": (
            "Elenca i preventivi già creati per questo cliente (numero, data, totale, "
            "stato e se il PDF è disponibile). Usalo quando il cliente chiede di un "
            "preventivo già fatto o di riavere il PDF. Non mostra mai preventivi di "
            "altri clienti: serve il numero o l'email del cliente."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "phone": {"type": "string", "description": "Numero WhatsApp del cliente."},
                "email": {"type": "string", "description": "Email del cliente, se nota."},
                "limit": {"type": "integer", "description": "Massimo risultati, default 10."},
            },
            "required": [],
        },
    },
    {
        "name": "request_quote_resend",
        "description": (
            "Rigenera il PDF di un preventivo già creato per questo cliente e lo rimette "
            "in coda per l'approvazione e l'invio. Usalo quando il cliente dice 'mandami "
            "di nuovo il preventivo' o 'richiama il PDF'. Il contenuto resta quello del "
            "preventivo originale; il sistema assegna un nuovo numero. Non invia nulla da solo."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "phone": {"type": "string", "description": "Numero WhatsApp del cliente."},
                "email": {"type": "string", "description": "Email del cliente, se nota."},
                "number": {"type": "string", "description": "Numero del preventivo, se il cliente lo indica."},
                "reason": {"type": "string", "description": "Perché il titolare dovrebbe approvare il nuovo invio."},
            },
            "required": [],
        },
    },
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


def _phone_digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _matches_client(row: dict, phone: str = "", email: str = "") -> bool:
    """Confronto stretto: un cliente non può leggere i preventivi di un altro."""
    payload = row.get("payload") or {}
    digits = _phone_digits(phone)
    if digits:
        for candidate in (row.get("client_phone"), payload.get("client_phone"), payload.get("phone")):
            other = _phone_digits(candidate)
            if other and (other.endswith(digits) or digits.endswith(other)):
                return True
    wanted = str(email or "").strip().lower()
    if wanted:
        for candidate in (row.get("client_email"), payload.get("client_email"), payload.get("to")):
            if str(candidate or "").strip().lower() == wanted:
                return True
    return False


def _client_quotes(phone: str = "", email: str = "", limit: int = 10) -> list[dict]:
    import approval_store

    if not str(phone or "").strip() and not str(email or "").strip():
        raise ValueError("serve il numero o l'email del cliente per cercare i preventivi")
    digits = _phone_digits(phone)
    if digits and len(digits) < 7:
        raise ValueError("numero del cliente non valido: serve il numero completo")
    rows = approval_store.list_requests(300).get("recent") or []
    found = [row for row in rows if str(row.get("type")) == "quote" and _matches_client(row, phone, email)]
    found.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return found[:max(1, min(int(limit or 10), 30))]


def _quote_view(row: dict) -> dict:
    payload = row.get("payload") or {}
    totals = payload.get("totals") or {}
    return {
        "number": payload.get("number") or "",
        "created_at": row.get("created_at") or "",
        "client_name": row.get("client_name") or payload.get("client_name") or "",
        "total": totals.get("total"),
        "currency": payload.get("currency") or "EUR",
        "status": row.get("status") or "",
        "has_pdf": bool(payload.get("pdf")),
    }


def _handle_tool(name: str, arguments: dict) -> dict:
    arguments = arguments if isinstance(arguments, dict) else {}
    if name == "remember_fact":
        import lead_intel
        import memory

        phone = str(arguments.get("phone") or "").strip()
        key = str(arguments.get("key") or "").strip()
        value = lead_intel.redact(str(arguments.get("value") or "").strip())[:200]
        if not phone or not key or not value:
            raise ValueError("phone, key e value sono obbligatori")
        saved = memory.remember(phone, key, value)
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Fatto salvato per il cliente: {key}. Lo ricorderai anche nei prossimi "
                    "messaggi e nelle prossime conversazioni con questo numero."
                ),
            }],
            "structuredContent": {
                "saved": True, "key": key,
                "facts": len(saved.get("facts") or {}),
            },
            "isError": False,
        }
    if name == "list_quotes":
        rows = _client_quotes(
            str(arguments.get("phone") or ""),
            str(arguments.get("email") or ""),
            int(arguments.get("limit") or 10),
        )
        views = [_quote_view(row) for row in rows]
        if not views:
            text = "Nessun preventivo trovato per questo cliente. Se serve, preparane uno con request_quote."
        else:
            lines = [f"Preventivi trovati: {len(views)}."]
            for view in views:
                amount = (
                    f"{view['total']:.2f} {view['currency']}"
                    if isinstance(view["total"], (int, float)) else "totale non disponibile"
                )
                lines.append(
                    f"- {view['number'] or '(senza numero)'} · {str(view['created_at'])[:10]} · "
                    f"{amount} · {view['status']} · PDF {'disponibile' if view['has_pdf'] else 'non disponibile'}"
                )
            lines.append(
                "Per rimandarlo al cliente usa request_quote_resend con il numero o il telefono: "
                "il PDF viene rigenerato e resta in attesa di approvazione."
            )
            text = "\n".join(lines)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"quotes": views, "count": len(views)},
            "isError": False,
        }
    if name == "request_quote_resend":
        rows = _client_quotes(
            str(arguments.get("phone") or ""),
            str(arguments.get("email") or ""),
            30,
        )
        wanted = str(arguments.get("number") or "").strip()
        row = next((item for item in rows
                    if wanted and str((item.get("payload") or {}).get("number") or "") == wanted), None)
        if row is None and not wanted:
            row = rows[0] if rows else None
        if row is None:
            raise ValueError(
                "preventivo non trovato per questo cliente" +
                (f": {wanted}" if wanted else "")
            )
        source = row.get("payload") or {}
        items = [
            {
                "description": item.get("description"),
                "qty": item.get("qty"),
                "unit": item.get("unit") or "",
                "unit_price": item.get("unit_price"),
            }
            for item in (source.get("items") or [])
            if isinstance(item, dict) and item.get("description")
        ]
        if not items:
            raise ValueError("il preventivo originale non ha voci riutilizzabili")
        client_email = source.get("client_email") or source.get("to") or ""
        if not client_email:
            raise ValueError("il preventivo originale non ha un'email: chiedila al cliente")
        request = append_request(
            "quote",
            {
                "to": client_email,
                "client_name": source.get("client_name") or row.get("client_name") or "",
                "client_email": client_email,
                "client_company": source.get("client_company") or "",
                "client_address": source.get("client_address") or "",
                "items": items,
                "discount_pct": source.get("discount_pct") or 0,
                "vat_pct": source.get("vat_pct"),
                "valid_days": source.get("valid_days"),
                "notes": source.get("notes") or "",
                "subject": source.get("subject") or "",
                "body": "",
            },
            client_name=source.get("client_name") or row.get("client_name") or "",
            client_phone=str(arguments.get("phone") or row.get("client_phone") or ""),
            reason=str(arguments.get("reason") or "Il cliente ha chiesto di nuovo il preventivo"),
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
                "resent_from": source.get("number") or "",
            },
        )
    if name == "client_brief":
        import live

        data = live.brief(
            str(arguments.get("message") or ""),
            str(arguments.get("phone") or ""),
            str(arguments.get("client_name") or ""),
        )
        lines: list[str] = []
        if data.get("answer"):
            lines.append("Risposta pronta (usala esattamente, senza aggiungere dati): " + str(data["answer"]))
        else:
            lines.append(
                "Nessuna risposta pronta: rispondi solo con i dati dell'attività e non inventare "
                "orari, prezzi, disponibilità o link."
            )
        lines.append(f"Intento: {data.get('intent')} (score {data.get('score')}), lingua: {data.get('language')}.")
        if data.get("suspicious"):
            lines.append(
                "Segnali di truffa: " + ", ".join(data.get("signals") or []) +
                ". Non chiedere né confermare dati personali: avvisa il titolare."
            )
        for item in data.get("guidance") or []:
            lines.append("Indicazione: " + str(item))
        if data.get("context"):
            lines.append("Memoria del cliente:\n" + str(data["context"]))
        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
            "structuredContent": data,
            "isError": False,
        }
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


def self_test() -> int:
    """Verifica i tool e il motore PDF senza scrivere nella coda reale.

    Viene eseguito dall'installer sulla copia installata: se questo controllo
    fallisce, il bot non potrebbe preparare preventivi. Meglio fermarsi qui.
    """
    names = [str(tool.get("name") or "") for tool in TOOLS]
    expected = {
        "client_brief", "remember_fact", "list_quotes", "request_email",
        "request_calendar", "request_quote", "request_quote_resend",
    }
    if set(names) != expected or len(names) != len(expected):
        print(f"MCP SELF-TEST FAIL: tool inattesi {names}", file=sys.stderr)
        return 1
    for tool in TOOLS:
        schema = tool.get("inputSchema") or {}
        if schema.get("type") != "object" or not tool.get("description"):
            print(f"MCP SELF-TEST FAIL: schema non valido per {tool.get('name')}", file=sys.stderr)
            return 1
    try:
        import quote_pdf

        pdf = quote_pdf.render_quote_pdf(quote_pdf.demo_quote())
    except Exception as exc:
        print(f"MCP SELF-TEST FAIL: PDF non generabile: {str(exc)[:200]}", file=sys.stderr)
        return 1
    if not pdf.startswith(b"%PDF-1.4") or len(pdf) < 1500:
        print("MCP SELF-TEST FAIL: PDF non valido", file=sys.stderr)
        return 1
    import os
    import tempfile

    os.environ["GINA_CACHE_FILE"] = os.path.join(tempfile.mkdtemp(prefix="mcp-brief-"), "cache.json")
    try:
        import cache as _cache_mod

        _cache_mod.reset_shared()
    except Exception:
        pass
    brief = _handle_tool("client_brief", {"message": "Vorrei un preventivo per un sito web"})
    structured = brief.get("structuredContent") or {}
    if structured.get("intent") != "quote" or "language" not in structured or brief.get("isError"):
        print(f"MCP SELF-TEST FAIL: client_brief inatteso {structured}", file=sys.stderr)
        return 1
    print(f"MCP SELF-TEST OK ({len(names)} tool, PDF {len(pdf)} byte)")
    return 0


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
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(self_test())
    main()
