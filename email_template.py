#!/usr/bin/env python3
"""Template HTML delle email della segreteria.

Design scuro e colorato in ogni sezione: hero viola, card indaco, riepilogo
preventivo, pulsanti e footer. Usa solo tabelle e stili inline (più gli
attributi ``bgcolor``) per far arrivare i colori anche a Gmail e Outlook, che
tendono a ignorare parte del CSS. Nessuna immagine, font remoto, pixel di
tracciamento o CSS esterno: l'email è autosufficiente e non chiama terzi.

Il testo piano resta il fallback: lo stesso contenuto viene passato a gog e al
bridge, quindi un client che blocca l'HTML mostra comunque il messaggio.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import config

CFG = config.load()
SITE = str(CFG.get("site_url") or "").strip()
SITE_LABEL = SITE.replace("https://", "").replace("http://", "").rstrip("/")
WHATSAPP = config.wa_link()
EMAIL = str(CFG.get("google_account") or "").strip()
OWNER = str(CFG.get("owner_name") or "Titolare").strip()
CITY = str(CFG.get("city") or "").strip()
BRAND = str(CFG.get("business_name") or "Nome Attività").strip()

# Palette: lo stesso blu/viola della dashboard, ma con più contrasto per i
# client di posta. Ogni blocco ha un bgcolor esplicito.
INK = "#07050D"
HERO = "#3B0F70"
HERO_2 = "#4C1D95"
CARD = "#120D1E"
CARD_2 = "#1B1330"
ROW = "#251A40"
LINE = "#3B2B59"
TEXT = "#FBF7FF"
MUTED = "#B9A7D9"
SOFT = "#E9DEFA"
WHITE = "#FFFFFF"
VIOLET = "#8B5CF6"
FUCHSIA = "#D946EF"
CYAN = "#22D3EE"

_URL_RE = re.compile(r"(https?://[^\s<]+)")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s*")


def _safe_url(value: str) -> str:
    url = str(value or "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def _money(value, currency: str = "EUR") -> str:
    try:
        amount = Decimal(str(value if value is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        return ""
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {text}" if str(currency or "EUR").upper() == "EUR" else f"{text} {str(currency).upper()}"


def _date_it(value) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d/%m/%Y")
        except Exception:
            continue
    return text


def _rich_text(text: str) -> str:
    """Converte il testo piano in paragrafi, liste e link, senza HTML esterno."""
    escaped = html.escape(str(text or "").strip())
    escaped = _URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:{CYAN};text-decoration:none">{m.group(1)}</a>',
        escaped,
    )
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", escaped):
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        if not lines:
            continue
        if all(ln.startswith(("- ", "• ")) for ln in lines):
            items = "".join(
                f'<li style="margin:0 0 8px 0;padding-left:2px">{ln[2:].strip()}</li>'
                for ln in lines
            )
            blocks.append(
                f'<ul style="margin:0 0 18px 0;padding-left:20px;color:{SOFT};font-size:15px;line-height:1.75">{items}</ul>'
            )
            continue
        if len(lines) > 1 and all(_NUMBERED_RE.match(ln) for ln in lines):
            items = "".join(
                f'<li style="margin:0 0 8px 0;padding-left:2px">{_NUMBERED_RE.sub("", ln)}</li>'
                for ln in lines
            )
            blocks.append(
                f'<ol style="margin:0 0 18px 0;padding-left:20px;color:{SOFT};font-size:15px;line-height:1.75">{items}</ol>'
            )
            continue
        body = "<br/>".join(lines)
        blocks.append(
            f'<p style="margin:0 0 16px 0;color:{SOFT};font-size:15px;line-height:1.75">{body}</p>'
        )
    return "".join(blocks) or f'<p style="margin:0;color:{SOFT};font-size:15px;line-height:1.75"></p>'


def _quote_panel(quote: dict) -> str:
    """Riquadro riepilogativo per le email che accompagnano un preventivo."""
    if not isinstance(quote, dict):
        return ""
    number = html.escape(str(quote.get("number") or "Preventivo"))
    totals = quote.get("totals") if isinstance(quote.get("totals"), dict) else {}
    total = html.escape(_money(totals.get("total"), quote.get("currency")))
    valid = html.escape(_date_it(quote.get("valid_until")))
    items = quote.get("items") if isinstance(quote.get("items"), list) else []
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:4px 0 24px 0;border-collapse:separate">
        <tr>
          <td width="5" bgcolor="{VIOLET}" style="width:5px;background-color:{VIOLET};font-size:0;line-height:0">&nbsp;</td>
          <td bgcolor="{CARD_2}" style="background-color:{CARD_2};padding:20px 22px 18px 22px;border-radius:0 16px 16px 0">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font-family:Inter,'Segoe UI',Arial,sans-serif">
                  <div style="color:{CYAN};font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">Preventivo pronto</div>
                  <div style="color:{TEXT};font-size:20px;font-weight:800;letter-spacing:-0.3px;line-height:1.25">{number}</div>
                </td>
                <td align="right" style="font-family:Inter,'Segoe UI',Arial,sans-serif">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="right">
                    <tr><td bgcolor="{VIOLET}" style="background-color:{VIOLET};border-radius:999px;padding:7px 13px;color:{WHITE};font-size:10px;font-weight:800;letter-spacing:1.4px">PDF ALLEGATO</td></tr>
                  </table>
                </td>
              </tr>
            </table>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px">
              <tr>
                <td bgcolor="{ROW}" style="background-color:{ROW};border-radius:12px;padding:14px 16px;font-family:Inter,'Segoe UI',Arial,sans-serif">
                  <div style="color:{MUTED};font-size:9px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;margin-bottom:4px">Totale</div>
                  <div style="color:{TEXT};font-size:26px;font-weight:800;letter-spacing:-0.6px;line-height:1.1">{total}</div>
                </td>
                <td width="10" style="width:10px;font-size:0;line-height:0">&nbsp;</td>
                <td bgcolor="{CARD}" style="background-color:{CARD};border-radius:12px;padding:14px 16px;font-family:Inter,'Segoe UI',Arial,sans-serif">
                  <div style="color:{MUTED};font-size:9px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;margin-bottom:4px">Valido fino al</div>
                  <div style="color:{TEXT};font-size:15px;font-weight:700;line-height:1.3">{valid or "—"}</div>
                  <div style="color:{MUTED};font-size:11px;line-height:1.4;margin-top:4px">{len(items)} voci · dettaglio nel PDF</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>"""


def render_email(
    subject: str,
    body: str,
    client_name: str = "",
    quote: dict | None = None,
) -> str:
    safe_subject = html.escape(str(subject or f"Un messaggio da {BRAND}").strip())[:180]
    heading = safe_subject or f"Un messaggio da {BRAND}"
    name = str(client_name or "").strip()
    body_head = str(body or "").strip().lower()
    has_salutation = body_head.startswith(
        ("ciao", "buongiorno", "buonasera", "salve", "gentile", "egregio", "hello", "hi", "dear")
    )
    greeting = ""
    if name and not has_salutation:
        greeting = (
            f'<p style="margin:0 0 14px 0;color:{TEXT};font-size:16px;line-height:1.7">'
            f"Ciao {html.escape(name)},</p>"
        )

    quote_panel = _quote_panel(quote) if isinstance(quote, dict) else ""
    if quote_panel:
        preheader = html.escape(
            f"Preventivo {quote.get('number') or ''} · totale "
            f"{_money((quote.get('totals') or {}).get('total'), quote.get('currency'))}"
        )[:160]
        eyebrow = "Il tuo preventivo"
        badge = "PREVENTIVO"
    else:
        preheader = html.escape(str(body or "").strip().replace("\n", " ")[:120])
        eyebrow = "Un messaggio per te"
        badge = "MESSAGGIO"
    content = _rich_text(body)

    site_url = _safe_url(SITE)
    whatsapp_url = _safe_url(WHATSAPP)
    email_href = f"mailto:{EMAIL}" if meta_email_link(f"mailto:{EMAIL}") else ""
    phone_link = (
        f'<a href="{html.escape(email_href, quote=True)}" style="color:{CYAN};text-decoration:none">{html.escape(EMAIL)}</a>'
        if email_href else html.escape(EMAIL)
    )
    site_link = f'<a href="{html.escape(site_url, quote=True)}" style="color:{MUTED};text-decoration:none">{html.escape(SITE_LABEL)}</a>' if site_url else ""
    footer_bits = " · ".join(bit for bit in (html.escape(OWNER), html.escape(CITY)) if bit)
    contact_line = footer_bits
    if phone_link:
        contact_line += ("<br/>" if contact_line else "") + phone_link
    if site_link:
        contact_line += (" · " if phone_link else ("<br/>" if contact_line else "")) + site_link

    cta = ""
    if whatsapp_url:
        cta += f"""
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px 0">
                <tr>
                  <td bgcolor="{VIOLET}" style="background-color:{VIOLET};border-radius:999px">
                    <a class="cta" href="{html.escape(whatsapp_url, quote=True)}" style="display:inline-block;padding:13px 24px;color:{WHITE};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:14px;font-weight:800;text-decoration:none;border-radius:999px;letter-spacing:-0.1px">Parliamone su WhatsApp</a>
                  </td>
                </tr>
              </table>"""
    if site_url:
        cta += f"""
              <div style="font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:13px;margin-top:4px">
                <a href="{html.escape(site_url, quote=True)}" style="color:{CYAN};text-decoration:none;font-weight:700">Visita il sito &rarr;</a>
              </div>"""

    return f"""<!DOCTYPE html>
<html lang="it" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="light only"/>
<meta name="supported-color-schemes" content="light"/>
<title>{safe_subject}</title>
<style>
  :root {{ color-scheme: light only; supported-color-schemes: light; }}
  a {{ text-decoration: none; }}
  @media (max-width:620px) {{
    .wrap {{ padding:16px 8px !important; }}
    .card {{ border-radius:16px !important; }}
    .pad {{ padding:24px 20px !important; }}
    .h1 {{ font-size:22px !important; line-height:1.3 !important; }}
    .cta {{ display:block !important; width:100% !important; box-sizing:border-box !important; }}
    .quote-cell {{ display:block !important; width:100% !important; }}
    .quote-gap {{ display:none !important; }}
  }}
</style>
</head>
<body bgcolor="{INK}" style="margin:0;padding:0;background-color:{INK};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{INK}" style="background-color:{INK};border-collapse:collapse;">
<tr><td align="center" class="wrap" style="padding:32px 14px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;border-collapse:separate;">
    <tr><td class="card" bgcolor="{CARD}" style="background-color:{CARD};border:1px solid {LINE};border-radius:22px;overflow:hidden;padding:0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{VIOLET}" style="background-color:{VIOLET};border-collapse:collapse;">
        <tr>
          <td width="25%" height="6" bgcolor="{VIOLET}" style="height:6px;background-color:{VIOLET};font-size:0;line-height:0">&nbsp;</td>
          <td width="25%" height="6" bgcolor="{FUCHSIA}" style="height:6px;background-color:{FUCHSIA};font-size:0;line-height:0">&nbsp;</td>
          <td width="25%" height="6" bgcolor="{CYAN}" style="height:6px;background-color:{CYAN};font-size:0;line-height:0">&nbsp;</td>
          <td width="25%" height="6" bgcolor="{HERO_2}" style="height:6px;background-color:{HERO_2};font-size:0;line-height:0">&nbsp;</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{HERO}" style="background-color:{HERO};border-collapse:collapse;">
        <tr><td class="pad" bgcolor="{HERO}" style="background-color:{HERO};padding:26px 34px 26px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                  <tr>
                    <td width="44" height="44" align="center" bgcolor="{VIOLET}" style="width:44px;height:44px;background-color:{VIOLET};border-radius:14px;color:{WHITE};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:21px;font-weight:800;line-height:44px;">{(html.escape(BRAND)[:1] or "A").upper()}</td>
                    <td style="padding-left:12px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
                      <div style="color:{WHITE};font-size:15px;font-weight:800;letter-spacing:-0.2px;line-height:1.2">{html.escape(BRAND)}</div>
                      <div style="color:#DDD0FF;font-size:10px;letter-spacing:2px;text-transform:uppercase;line-height:1.5">Segreteria digitale</div>
                    </td>
                  </tr>
                </table>
              </td>
              <td align="right">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="right">
                  <tr><td bgcolor="{FUCHSIA}" style="background-color:{FUCHSIA};border-radius:999px;padding:7px 12px;color:{WHITE};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:800;letter-spacing:1.6px">{badge}</td></tr>
                </table>
              </td>
            </tr>
          </table>
          <div style="color:{CYAN};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:10px;font-weight:800;letter-spacing:2.2px;text-transform:uppercase;margin:24px 0 8px 0">{eyebrow}</div>
          <h1 class="h1" style="margin:0;color:{WHITE};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:27px;line-height:1.25;font-weight:800;letter-spacing:-0.6px">{heading}</h1>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD}" style="background-color:{CARD};border-collapse:collapse;">
        <tr><td class="pad" bgcolor="{CARD}" style="background-color:{CARD};padding:28px 34px 8px 34px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
          {greeting}
          {quote_panel}
          <div style="color:{SOFT}">{content}</div>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD}" style="background-color:{CARD};border-collapse:collapse;">
        <tr><td class="pad" bgcolor="{CARD}" style="background-color:{CARD};padding:6px 34px 0 34px;">
          <div style="border-top:1px solid {LINE};padding-top:22px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
            <div style="color:{MUTED};font-size:13px;line-height:1.6;margin-bottom:14px">Vuoi parlarne subito? Rispondi a questa email o scrivimi su WhatsApp.</div>
            {cta}
          </div>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD}" style="background-color:{CARD};border-collapse:collapse;">
        <tr><td class="pad" bgcolor="{CARD}" style="background-color:{CARD};padding:24px 34px 28px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {LINE}">
            <tr><td style="padding-top:18px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
              <div style="color:{TEXT};font-size:13px;font-weight:800;margin-bottom:3px">{html.escape(OWNER)}</div>
              <div style="color:{MUTED};font-size:12px;line-height:1.7">{contact_line}</div>
              <div style="color:#8B7AA8;font-size:11px;line-height:1.6;margin-top:14px">Ricevi questa email perché hai chiesto un preventivo o un recapito. Nessuna newsletter, nessun dato condiviso.</div>
            </td></tr>
          </table>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{HERO_2}" style="background-color:{HERO_2};border-collapse:collapse;">
        <tr>
          <td width="33%" height="5" bgcolor="{VIOLET}" style="height:5px;background-color:{VIOLET};font-size:0;line-height:0">&nbsp;</td>
          <td width="34%" height="5" bgcolor="{CYAN}" style="height:5px;background-color:{CYAN};font-size:0;line-height:0">&nbsp;</td>
          <td width="33%" height="5" bgcolor="{FUCHSIA}" style="height:5px;background-color:{FUCHSIA};font-size:0;line-height:0">&nbsp;</td>
        </tr>
      </table>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


def meta_email_link(value: str) -> bool:
    """True per un link mailto già validato (evita href vuoti o javascript:)."""
    return bool(re.match(r"^mailto:[^@\s]+@[^@\s]+\.[^@\s]+$", str(value or "")))


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--demo-quote":
        import quote_pdf

        sample = quote_pdf.demo_quote()
        sample["number"] = "PREV-DEMO-001"
        print(render_email(
            "Il tuo preventivo è pronto",
            "Ciao,\n\nin allegato trovi il preventivo richiesto.\n\nSe ti torna tutto, rispondi a questa email.",
            client_name="Cliente Demo",
            quote=sample,
        ))
    else:
        print(render_email(
            "Il tuo sito, pronto a partire",
            sys.argv[1] if len(sys.argv) > 1 else "Ciao,\n\nquesta è una prova.\n\n- Primo punto\n- Secondo punto",
        ))
