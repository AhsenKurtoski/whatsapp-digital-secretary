#!/usr/bin/env python3
"""Template HTML per le email della segreteria.

Fondo scuro, accenti viola/ciano, tipografia Inter/system, card arrotondata e
CTA a pillola. Usa solo tabelle e stili inline per essere compatibile con Gmail,
Outlook e client mobili. Il testo piano resta il fallback.
"""
from __future__ import annotations

import html
import re

import config

CFG = config.load()
SITE = str(CFG.get("site_url") or "").strip()
SITE_LABEL = SITE.replace("https://", "").replace("http://", "").rstrip("/") or "Sito"
WHATSAPP = config.wa_link() or SITE or "#"
EMAIL = str(CFG.get("google_account") or "").strip()
OWNER = str(CFG.get("owner_name") or "Titolare").strip()
CITY = str(CFG.get("city") or "").strip()
BRAND = str(CFG.get("business_name") or "Nome Attività").strip()

INK = "#09090b"
CARD = "#111116"
CARD_2 = "#17171d"
LINE = "#2a2a33"
TEXT = "#f4f4f5"
MUTED = "#a1a1aa"
SOFT = "#d4d4d8"
VIOLET = "#8b5cf6"
INDIGO = "#6366f1"
CYAN = "#06b6d4"

_URL_RE = re.compile(r"(https?://[^\s<]+)")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s*")


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
                f'<li style="margin:0 0 8px 0;padding-left:2px">{ln[2:].strip()}</li>' for ln in lines
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


def render_email(subject: str, body: str, client_name: str = "") -> str:
    safe_subject = html.escape(str(subject or f"Un messaggio da {BRAND}").strip())[:180]
    preheader = html.escape(str(body or "").strip().replace("\n", " ")[:120])
    content = _rich_text(body)
    greeting = ""
    name = str(client_name or "").strip()
    body_head = str(body or "").strip().lower()
    has_salutation = body_head.startswith(
        ("ciao", "buongiorno", "buonasera", "salve", "gentile", "egregio", "hello", "hi", "dear")
    )
    if name and not has_salutation:
        greeting = (
            f'<p style="margin:0 0 14px 0;color:{TEXT};font-size:16px;line-height:1.7">'
            f"Ciao {html.escape(name)},</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="it" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="dark light"/>
<meta name="supported-color-schemes" content="dark light"/>
<title>{safe_subject}</title>
<style>
  @media (max-width:620px) {{
    .wrap {{ padding:18px 10px !important; }}
    .card {{ border-radius:18px !important; }}
    .pad {{ padding:26px 20px !important; }}
    .h1 {{ font-size:23px !important; line-height:1.25 !important; }}
    .cta {{ display:block !important; width:100% !important; box-sizing:border-box !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{INK};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{INK}" style="background:{INK};">
<tr><td align="center" class="wrap" style="padding:34px 14px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
    <tr><td class="card" style="background:{CARD};border:1px solid {LINE};border-radius:22px;overflow:hidden;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="50%" height="4" bgcolor="{VIOLET}" style="height:4px;background:{VIOLET};font-size:0;line-height:0">&nbsp;</td>
          <td width="50%" height="4" bgcolor="{CYAN}" style="height:4px;background:{CYAN};font-size:0;line-height:0">&nbsp;</td>
        </tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="pad" style="padding:30px 34px 0 34px;">
        <tr>
          <td>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="42" height="42" align="center" bgcolor="{VIOLET}" style="width:42px;height:42px;background:{VIOLET};border-radius:13px;color:#ffffff;font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:20px;font-weight:800;line-height:42px;">A</td>
                <td style="padding-left:12px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
                  <div style="color:{TEXT};font-size:15px;font-weight:700;letter-spacing:-0.2px;line-height:1.2">{BRAND}</div>
                  <div style="color:{MUTED};font-size:11px;letter-spacing:2px;text-transform:uppercase;line-height:1.5">Segreteria digitale</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="pad" style="padding:24px 34px 0 34px;">
        <tr><td>
          <div style="color:{CYAN};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:2.2px;text-transform:uppercase;margin-bottom:10px">Un messaggio per te</div>
          <h1 class="h1" style="margin:0 0 20px 0;color:{TEXT};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:27px;line-height:1.25;font-weight:750;letter-spacing:-0.6px">{safe_subject}</h1>
          {greeting}
          <div style="font-family:Inter,'Segoe UI',Arial,sans-serif;color:{SOFT}">{content}</div>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="pad" style="padding:8px 34px 0 34px;">
        <tr><td style="border-top:1px solid {LINE};padding-top:24px;">
          <div style="color:{MUTED};font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:13px;line-height:1.6;margin-bottom:14px">Vuoi parlarne subito? Rispondi a questa email o scrivimi su WhatsApp.</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px">
            <tr>
              <td bgcolor="{VIOLET}" style="background:{VIOLET};border-radius:999px;">
                <a class="cta" href="{WHATSAPP}" style="display:inline-block;padding:13px 24px;color:#ffffff;font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:14px;font-weight:700;text-decoration:none;border-radius:999px">Parliamone su WhatsApp</a>
              </td>
            </tr>
          </table>
          <div style="font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:13px">
            <a href="{SITE}" style="color:{CYAN};text-decoration:none;font-weight:600">Visita il sito &rarr;</a>
          </div>
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="pad" style="padding:26px 34px 30px 34px;">
        <tr><td style="border-top:1px solid {LINE};padding-top:20px;font-family:Inter,'Segoe UI',Arial,sans-serif;">
          <div style="color:{TEXT};font-size:13px;font-weight:700;margin-bottom:3px">{OWNER}</div>
          <div style="color:{MUTED};font-size:12px;line-height:1.7">Sviluppatore web &middot; {CITY}<br/>
            <a href="mailto:{EMAIL}" style="color:{MUTED};text-decoration:none">{EMAIL}</a> &middot;
            <a href="{SITE}" style="color:{MUTED};text-decoration:none">{SITE_LABEL}</a>
          </div>
          <div style="color:#52525b;font-size:11px;line-height:1.6;margin-top:14px">Ricevi questa email perché hai chiesto un recapito. Nessuna newsletter, nessun dato condiviso.</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


if __name__ == "__main__":
    import sys

    print(render_email("Il tuo sito, pronto a partire", sys.argv[1] if len(sys.argv) > 1 else "Ciao,\n\nquesta è una prova.\n\n- Primo punto\n- Secondo punto"))
