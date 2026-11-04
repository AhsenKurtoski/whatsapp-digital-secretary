#!/usr/bin/env python3
"""Preventivi in PDF generati in locale, senza dipendenze esterne.

Il layout riprende l'identità delle email: barra sfumata viola/ciano, card
arrotondate, tipografia Helvetica e una tabella leggibile anche stampata in
bianco e nero. Il risultato è un PDF 1.4 valido, apribile con qualsiasi lettore
e allegabile alle email di Gmail.

Il testo viene codificato in WinAnsi (cp1252), quindi accenti italiani e simbolo
dell'euro sono supportati dai font base-14 senza incorporare file di font.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import config

PAGE_W, PAGE_H = 595.28, 841.89  # A4 in punti
MARGIN = 46.0
CONTENT_W = PAGE_W - 2 * MARGIN
MAX_ITEMS = 40
MAX_TOTAL = 1_000_000.0

# Larghezze Helvetica/Helvetica-Bold (unità per 1000 punti), caratteri ASCII.
_REG = {
    32: 278, 33: 278, 34: 355, 35: 556, 36: 556, 37: 889, 38: 667, 39: 191,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    58: 278, 59: 278, 60: 584, 61: 584, 62: 584, 63: 556, 64: 1015,
    65: 667, 66: 667, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722,
    73: 278, 74: 500, 75: 667, 76: 556, 77: 833, 78: 722, 79: 778, 80: 667,
    81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667,
    89: 667, 90: 611, 91: 278, 92: 278, 93: 278, 94: 469, 95: 556, 96: 333,
    97: 556, 98: 556, 99: 500, 100: 556, 101: 556, 102: 278, 103: 556,
    104: 556, 105: 222, 106: 222, 107: 500, 108: 222, 109: 833, 110: 556,
    111: 556, 112: 556, 113: 556, 114: 333, 115: 500, 116: 278, 117: 556,
    118: 500, 119: 722, 120: 500, 121: 500, 122: 500, 123: 334, 124: 260,
    125: 334, 126: 584,
}
for _d in range(48, 58):
    _REG[_d] = 556

_BOLD = {
    32: 278, 33: 333, 34: 474, 35: 556, 36: 556, 37: 889, 38: 722, 39: 238,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    58: 333, 59: 333, 60: 584, 61: 584, 62: 584, 63: 611, 64: 975,
    65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722,
    73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778, 80: 667,
    81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667,
    89: 667, 90: 611, 91: 333, 92: 278, 93: 333, 94: 584, 95: 556, 96: 333,
    97: 556, 98: 611, 99: 556, 100: 611, 101: 556, 102: 333, 103: 611,
    104: 611, 105: 278, 106: 278, 107: 556, 108: 278, 109: 889, 110: 611,
    111: 611, 112: 611, 113: 611, 114: 389, 115: 556, 116: 333, 117: 611,
    118: 556, 119: 778, 120: 556, 121: 556, 122: 500, 123: 389, 124: 280,
    125: 389, 126: 584,
}
for _d in range(48, 58):
    _BOLD[_d] = 556

INK = "#0f172a"
MUTED = "#64748b"
LINE = "#e2e8f0"
SOFT = "#f8fafc"
VIOLET = "#7c3aed"
CYAN = "#0891b2"
WHITE = "#ffffff"


def _rgb(value) -> tuple[float, float, float]:
    if isinstance(value, (tuple, list)):  # colore già normalizzato (sfumatura)
        red, green, blue = (float(part) for part in value[:3])
        return (red, green, blue)
    value = str(value).lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _char_width(ch: str, bold: bool = False) -> int:
    table = _BOLD if bold else _REG
    code = ord(ch)
    if code in table:
        return table[code]
    base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
    if base and ord(base[0]) in table:
        return table[ord(base[0])]
    # €, en/em dash e altri segni tipografici hanno la larghezza di default.
    return 556


def text_width(text: str, font: str = "F1", size: float = 10.0) -> float:
    bold = font.upper().endswith("2") or font.upper() == "F2"
    return sum(_char_width(ch, bold) for ch in str(text)) * size / 1000.0


def _literal(text: str) -> bytes:
    raw = str(text).encode("cp1252", "replace")
    out = bytearray(b"(")
    for byte in raw:
        if byte in (0x28, 0x29, 0x5C):  # ( ) backslash
            out += b"\\" + bytes([byte])
        elif byte < 32:
            out += b" "
        else:
            out.append(byte)
    out += b")"
    return bytes(out)


def _num(value: float) -> bytes:
    return f"{value:.2f}".encode("ascii")


class Canvas:
    """Writer PDF minimale: rettangoli, linee e testo con font base-14."""

    def __init__(self) -> None:
        self.pages: list[list[bytes]] = [[]]
        self.index = 0
        self.width = PAGE_W
        self.height = PAGE_H

    @property
    def ops(self) -> list[bytes]:
        return self.pages[self.index]

    def new_page(self) -> None:
        self.pages.append([])
        self.index = len(self.pages) - 1

    def rect(self, x, y_top, w, h, fill=None, stroke=None, radius=0.0, line_width=0.8):
        y = self.height - y_top - h
        if fill:
            self.ops.append(b" ".join(_num(v) for v in _rgb(fill)) + b" rg")
        if stroke:
            self.ops.append(b" ".join(_num(v) for v in _rgb(stroke)) + b" RG")
        if radius > 0:
            r = min(radius, w / 2, h / 2)
            k = 0.5522847498307936
            p = self.ops
            p.append(b" ".join([_num(x + r), _num(y), b"m"]))
            p.append(b" ".join([_num(x + w - r), _num(y), b"l"]))
            p.append(b" ".join([
                _num(x + w - r + k * r), _num(y),
                _num(x + w), _num(y + r - k * r),
                _num(x + w), _num(y + r), b"c",
            ]))
            p.append(b" ".join([_num(x + w), _num(y + h - r), b"l"]))
            p.append(b" ".join([
                _num(x + w), _num(y + h - r + k * r),
                _num(x + w - r + k * r), _num(y + h),
                _num(x + w - r), _num(y + h), b"c",
            ]))
            p.append(b" ".join([_num(x + r), _num(y + h), b"l"]))
            p.append(b" ".join([
                _num(x + r - k * r), _num(y + h),
                _num(x), _num(y + h - r + k * r),
                _num(x), _num(y + h - r), b"c",
            ]))
            p.append(b" ".join([_num(x), _num(y + r), b"l"]))
            p.append(b" ".join([
                _num(x), _num(y + r - k * r),
                _num(x + r - k * r), _num(y),
                _num(x + r), _num(y), b"c",
            ]))
            p.append(b"h")
        else:
            self.ops.append(b" ".join([_num(x), _num(y), _num(w), _num(h), b"re"]))
        if fill and stroke:
            self.ops.append(b"B")
        elif fill:
            self.ops.append(b"f")
        else:
            self.ops.append(b"S")

    def line(self, x1, y1_top, x2, y2_top, color=LINE, width=0.8):
        self.ops.append(b" ".join(_num(v) for v in _rgb(color)) + b" RG")
        self.ops.append(_num(width) + b" w")
        self.ops.append(b" ".join([
            _num(x1), _num(self.height - y1_top), b"m",
            _num(x2), _num(self.height - y2_top), b"l", b"S",
        ]))

    def text(self, x, y_top, value, font="F1", size=10.0, color=INK, align="left"):
        s = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
        if not s:
            return 0.0
        width = text_width(s, font, size)
        left = x
        if align == "right":
            left = x - width
        elif align == "center":
            left = x - width / 2
        self.ops.append(b" ".join(_num(v) for v in _rgb(color)) + b" rg")
        self.ops.append(
            b"BT /" + font.encode("ascii") + b" " + _num(size) + b" Tf 1 0 0 1 "
            + _num(left) + b" " + _num(self.height - y_top) + b" Tm "
            + _literal(s) + b" Tj ET"
        )
        return width

    def output(self) -> bytes:
        font_regular = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        font_bold = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        objects: list[bytes] = [b"", b"", font_regular, font_bold]
        kids: list[str] = []
        for i, ops in enumerate(self.pages):
            page_no = 5 + i * 2
            content_no = page_no + 1
            kids.append(f"{page_no} 0 R")
            content = b"\n".join(ops)
            objects.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> /ProcSet [/PDF /Text] >> "
                    f"/Contents {content_no} 0 R >>"
                ).encode("ascii")
            )
            objects.append(
                f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
                + content
                + b"\nendstream"
            )
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objects[1] = (
            f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(self.pages)} >>"
        ).encode("ascii")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode("ascii")
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode("ascii")
        return bytes(out)


def _pick(data: dict, *keys, default=None):
    """Primo valore presente e non vuoto; i campi opzionali inviati come null
    (per esempio da un client MCP) non devono sovrascrivere i default."""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _parse_number(value, field: str, minimum: float = 0.0, maximum: float = MAX_TOTAL) -> float:
    text = str(value if value is not None else "").strip()
    text = text.replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} non valido") from exc
    if not number.is_finite() or number < Decimal(str(minimum)) or number > Decimal(str(maximum)):
        raise ValueError(f"{field} fuori intervallo")
    return float(number)


def _money(value: float, currency: str = "EUR") -> str:
    text = f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"
    integer, _, decimals = text.partition(".")
    integer = integer.replace(",", ".")
    body = f"{integer},{decimals}"
    if currency.upper() == "EUR":
        return f"€ {body}"
    return f"{body} {currency.upper()}"


def money_text(value, currency: str = "EUR") -> str:
    """Formatta un importo per email e dashboard (es. “€ 1.234,56”)."""
    try:
        return _money(float(value or 0.0), currency or "EUR")
    except Exception:
        return str(value or "")


def _qty(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def format_date_it(value) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d/%m/%Y")
        except Exception:
            continue
    return text


def _iso_date(value, field: str = "data") -> str:
    if value in (None, ""):
        return date.today().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except Exception:
            continue
    raise ValueError(f"{field} non valida (usa AAAA-MM-GG)")


def _clean(value, limit: int, newlines: bool = False) -> str:
    text = str(value or "").replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not newlines:
        text = text.replace("\n", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()[:limit]


def normalize_quote(data: dict, cfg: dict | None = None) -> dict:
    """Valida e normalizza i dati di un preventivo, calcolando i totali.

    Il modello non può fissare i totali: vengono sempre ricalcolati qui, così
    un prezzo inventato o un arrotondamento sbagliato non arriva al cliente.
    """
    if not isinstance(data, dict):
        raise ValueError("preventivo non valido")
    cfg = cfg if isinstance(cfg, dict) else config.load()
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("serve almeno una voce di preventivo")
    if len(raw_items) > MAX_ITEMS:
        raise ValueError(f"massimo {MAX_ITEMS} voci")

    items: list[dict] = []
    subtotal = Decimal("0")
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"voce {index} non valida")
        description = _clean(
            raw.get("description") or raw.get("desc") or raw.get("name"), 220
        )
        if not description:
            raise ValueError(f"descrizione mancante nella voce {index}")
        qty = _parse_number(
            raw.get("qty", raw.get("quantity", 1)), f"quantità voce {index}", 0.001, 9999
        )
        unit = _clean(raw.get("unit") or raw.get("u") or "", 24)
        price = _parse_number(
            raw.get("unit_price", raw.get("price", raw.get("unitPrice"))),
            f"prezzo voce {index}",
        )
        line = (Decimal(str(qty)) * Decimal(str(price))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        subtotal += line
        items.append({
            "description": description,
            "qty": qty,
            "unit": unit,
            "unit_price": float(price),
            "total": float(line),
        })

    discount_pct = _parse_number(
        _pick(data, "discount_pct", "discount", default=0), "sconto %", 0, 100
    )
    default_vat = _parse_number(_pick(cfg, "vat_rate", default=22), "IVA %", 0, 100)
    vat_pct = _parse_number(_pick(data, "vat_pct", "vat", default=default_vat), "IVA %", 0, 100)
    discount = (subtotal * Decimal(str(discount_pct)) / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    taxable = subtotal - discount
    vat = (taxable * Decimal(str(vat_pct)) / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = taxable + vat
    if total > Decimal(str(MAX_TOTAL)):
        raise ValueError("totale troppo alto")

    valid_days = int(_parse_number(
        _pick(data, "valid_days", default=_pick(cfg, "quote_valid_days", default=30)),
        "validità", 1, 365,
    ))
    issued = _iso_date(_pick(data, "date"), "data")
    valid_until = (
        datetime.strptime(issued, "%Y-%m-%d").date() + timedelta(days=valid_days)
    ).isoformat()

    currency = _clean(_pick(data, "currency", default="EUR"), 3).upper() or "EUR"
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("valuta non valida")

    return {
        "number": _clean(_pick(data, "number"), 40),
        "date": issued,
        "valid_days": valid_days,
        "valid_until": valid_until,
        "currency": currency,
        "client_name": _clean(data.get("client_name") or data.get("client"), 120),
        "client_company": _clean(data.get("client_company") or data.get("company"), 140),
        "client_email": _clean(data.get("client_email") or data.get("email"), 200),
        "client_phone": _clean(data.get("client_phone") or data.get("phone"), 40),
        "client_address": _clean(data.get("client_address") or data.get("address"), 240, newlines=True),
        "items": items,
        "discount_pct": discount_pct,
        "vat_pct": vat_pct,
        "notes": _clean(data.get("notes"), 1600, newlines=True),
        "payment_terms": _clean(
            data.get("payment_terms")
            or cfg.get("payment_terms")
            or "Pagamento: 50% alla conferma, saldo alla consegna.",
            600,
            newlines=True,
        ),
        "totals": {
            "subtotal": float(subtotal),
            "discount": float(discount),
            "taxable": float(taxable),
            "vat": float(vat),
            "total": float(total),
        },
    }


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    for block in str(text).split("\n"):
        words = block.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if text_width(candidate, font, size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # Parola singola più lunga della colonna: spezzala.
            while text_width(word, font, size) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if current:
            lines.append(current)
    return lines or [""]


def render_quote_pdf(quote: dict) -> bytes:
    """Disegna il preventivo normalizzato e restituisce i byte del PDF."""
    q = quote
    cfg = config.load()
    brand = _clean(cfg.get("business_name") or "Attività", 90) or "Attività"
    owner = _clean(cfg.get("owner_name") or "", 90)
    email = _clean(cfg.get("google_account") or "", 120)
    site = _clean(cfg.get("site_url") or "", 160)
    city = _clean(cfg.get("city") or "", 80)
    currency = q.get("currency") or "EUR"
    totals = q.get("totals") or {}
    items = q.get("items") or []
    number = q.get("number") or ""
    issued = q.get("date") or date.today().isoformat()
    valid_until = q.get("valid_until") or issued

    ink, muted, line, soft = _rgb(INK), _rgb(MUTED), _rgb(LINE), _rgb(SOFT)
    violet, cyan, white = _rgb(VIOLET), _rgb(CYAN), _rgb(WHITE)
    c = Canvas()

    def header(minimal: bool = False) -> float:
        top = 0.0
        if not minimal:
            segments = 48
            for i in range(segments):
                t = i / (segments - 1)
                color = tuple(violet[k] + (cyan[k] - violet[k]) * t for k in range(3))
                c.rect(CONTENT_W * i / segments, 0, CONTENT_W / segments + 0.8, 5, fill=color)
            c.rect(MARGIN, 54, 44, 44, fill=VIOLET, radius=12)
            initial = (brand[:1] or "A").upper()
            c.text(MARGIN + 22, 84, initial, "F2", 21, white, "center")
            c.text(MARGIN + 58, 71, brand, "F2", 17, ink)
            c.text(MARGIN + 58, 87, "SEGRETERIA DIGITALE", "F1", 7.6, muted)
            c.text(PAGE_W - MARGIN, 72, "PREVENTIVO", "F2", 20, ink, "right")
            c.text(PAGE_W - MARGIN, 89, f"N. {number}" if number else "", "F1", 9, muted, "right")
            c.line(MARGIN, 112, PAGE_W - MARGIN, 112, line, 0.9)
            return 130.0
        c.rect(MARGIN, 46, 26, 26, fill=VIOLET, radius=8)
        c.text(MARGIN + 13, 64, (brand[:1] or "A").upper(), "F2", 13, white, "center")
        c.text(MARGIN + 36, 63, brand, "F2", 11, ink)
        c.text(PAGE_W - MARGIN, 63, f"Preventivo {number}".strip(), "F1", 9, muted, "right")
        c.line(MARGIN, 86, PAGE_W - MARGIN, 86, line, 0.8)
        return 100.0

    y = header()

    # Riquadro cliente + dettagli
    box_h = 88.0
    c.rect(MARGIN, y, 248, box_h, fill=SOFT, radius=12)
    c.rect(PAGE_W - MARGIN - 243, y, 243, box_h, fill=SOFT, radius=12)
    c.text(MARGIN + 16, y + 20, "PREPARATO PER", "F2", 7.4, muted)
    client_lines = [q.get("client_name") or "Cliente"]
    if q.get("client_company"):
        client_lines.append(q["client_company"])
    for part in (q.get("client_address") or "").split("\n"):
        if part.strip():
            client_lines.append(part.strip())
    if q.get("client_email"):
        client_lines.append(q["client_email"])
    if q.get("client_phone"):
        client_lines.append(q["client_phone"])
    line_y = y + 38
    for index, value in enumerate(client_lines[:5]):
        font = "F2" if index == 0 else "F1"
        size = 10.6 if index == 0 else 8.7
        c.text(MARGIN + 16, line_y, value, font, size, ink if index == 0 else muted)
        line_y += 13

    meta_x = PAGE_W - MARGIN - 243 + 16
    c.text(meta_x, y + 20, "DETTAGLI", "F2", 7.4, muted)
    meta = [
        ("Data", format_date_it(issued)),
        ("Validità", f"{q.get('valid_days', 30)} giorni"),
        ("Scadenza", format_date_it(valid_until)),
        ("Valuta", currency.upper()),
    ]
    row_y = y + 38
    for label, value in meta:
        c.text(meta_x, row_y, label, "F1", 8.4, muted)
        c.text(PAGE_W - MARGIN - 16, row_y, value, "F2", 9.2, ink, "right")
        row_y += 13

    y += box_h + 24

    table_top = y

    def table_header(top: float) -> None:
        c.rect(MARGIN, top, CONTENT_W, 26, fill=INK, radius=7)
        c.text(MARGIN + 11, top + 17, "DESCRIZIONE", "F2", 7.6, white)
        c.text(360, top + 17, "Q.TÀ", "F2", 7.6, white, "right")
        c.text(448, top + 17, "PREZZO", "F2", 7.6, white, "right")
        c.text(PAGE_W - MARGIN - 11, top + 17, "TOTALE", "F2", 7.6, white, "right")

    table_header(table_top)
    y = table_top + 26

    for index, item in enumerate(items):
        desc_lines = _wrap(item["description"], "F1", 9.4, 252)[:4]
        row_h = max(25.0, 13.0 + 11.6 * len(desc_lines))
        if y + row_h > PAGE_H - 170:
            c.new_page()
            y = header(minimal=True)
            table_header(y)
            y += 26
        if index % 2 == 1:
            c.rect(MARGIN, y, CONTENT_W, row_h, fill=SOFT)
        baseline = y + 16
        for offset, text_line in enumerate(desc_lines):
            c.text(MARGIN + 11, baseline + offset * 11.6, text_line, "F1", 9.4, ink)
        c.text(360, baseline, _qty(item["qty"]) + (f" {item['unit']}" if item["unit"] else ""),
               "F1", 9.0, muted, "right")
        c.text(448, baseline, _money(item["unit_price"], currency), "F1", 9.0, ink, "right")
        c.text(PAGE_W - MARGIN - 11, baseline, _money(item["total"], currency), "F2", 9.2, ink, "right")
        c.line(MARGIN, y + row_h, PAGE_W - MARGIN, y + row_h, line, 0.6)
        y += row_h

    # Totali
    totals_rows = [("Subtotale", totals.get("subtotal", 0.0))]
    if float(totals.get("discount") or 0) > 0:
        totals_rows.append((f"Sconto {q.get('discount_pct', 0):g}%", float(totals.get("discount") or 0)))
    totals_rows.append((f"IVA {q.get('vat_pct', 0):g}%", totals.get("vat", 0.0)))
    box_h = 22 * len(totals_rows) + 34
    if y + box_h > PAGE_H - 150:
        c.new_page()
        y = header(minimal=True)
    box_x = PAGE_W - MARGIN - 224
    c.rect(box_x, y + 18, 224, box_h, fill=SOFT, radius=12)
    row_y = y + 40
    for label, value in totals_rows:
        c.text(box_x + 16, row_y, label, "F1", 8.8, muted)
        c.text(box_x + 208, row_y, _money(value, currency), "F1", 9.2, ink, "right")
        row_y += 22
    c.line(box_x + 16, row_y - 12, box_x + 208, row_y - 12, line, 0.8)
    c.text(box_x + 16, row_y + 10, "TOTALE", "F2", 10.4, ink)
    c.text(box_x + 208, row_y + 10, _money(totals.get("total", 0.0), currency), "F2", 12.4, violet, "right")

    # Note e condizioni
    note_y = y + 40
    left_width = 292.0
    sections = []
    if q.get("notes"):
        sections.append(("NOTE", q["notes"]))
    if q.get("payment_terms"):
        sections.append(("CONDIZIONI DI PAGAMENTO", q["payment_terms"]))
    for title, body in sections:
        if note_y > PAGE_H - 150:
            c.new_page()
            note_y = header(minimal=True)
        c.text(MARGIN, note_y, title, "F2", 7.4, muted)
        note_y += 14
        for text_line in _wrap(body, "F1", 8.8, left_width):
            if note_y > PAGE_H - 126:
                c.new_page()
                note_y = header(minimal=True)
            c.text(MARGIN, note_y, text_line, "F1", 8.8, muted)
            note_y += 12
        note_y += 12

    # Footer su ogni pagina
    total_pages = len(c.pages)
    footer_bits = [brand]
    if owner:
        footer_bits.append(owner)
    if email:
        footer_bits.append(email)
    if city:
        footer_bits.append(city)
    if site:
        footer_bits.append(site)
    footer_left = " · ".join(bit for bit in footer_bits if bit)
    valid_text = f"Preventivo valido fino al {format_date_it(valid_until)}"
    for index in range(total_pages):
        c.index = index
        c.line(MARGIN, PAGE_H - 58, PAGE_W - MARGIN, PAGE_H - 58, line, 0.7)
        c.text(MARGIN, PAGE_H - 42, valid_text, "F1", 7.6, muted)
        c.text(PAGE_W - MARGIN, PAGE_H - 42, f"Pagina {index + 1} di {total_pages}",
               "F1", 7.6, muted, "right")
        if footer_left:
            c.text(MARGIN, PAGE_H - 30, footer_left, "F1", 7.0, muted)
    return c.output()


def quote_filename(quote: dict) -> str:
    number = _clean(quote.get("number") or "preventivo", 40)
    client = _clean(quote.get("client_name") or "", 40)
    raw = f"Preventivo-{number}" + (f"-{client}" if client else "")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    return (safe or "Preventivo")[:80] + ".pdf"


def demo_quote(cfg: dict | None = None) -> dict:
    """Preventivo dimostrativo per provare layout e download dalla dashboard."""
    cfg = cfg if isinstance(cfg, dict) else config.load()
    services = [str(x).strip() for x in (cfg.get("services") or []) if str(x).strip()]
    while len(services) < 3:
        services.append(["Sito web vetrina", "Manutenzione mensile", "Consulenza"][len(services)])
    today = date.today()
    return normalize_quote({
        "date": today.isoformat(),
        "client_name": "Cliente Demo",
        "client_company": "Attività di esempio",
        "client_email": str(cfg.get("google_account") or "demo@example.com"),
        "client_phone": "",
        "client_address": str(cfg.get("city") or "Milano"),
        "items": [
            {"description": services[0].capitalize(), "qty": 1, "unit_price": 890, "unit": "forfait"},
            {"description": services[1].capitalize(), "qty": 12, "unit_price": 35, "unit": "mese"},
            {"description": services[2].capitalize(), "qty": 2, "unit_price": 60, "unit": "ora"},
        ],
        "notes": (
            "Documento dimostrativo generato dalla dashboard: serve solo a verificare "
            "il layout del PDF e l'allegato email. Non ha valore contrattuale."
        ),
        "payment_terms": str(cfg.get("payment_terms") or "50% alla conferma, saldo alla consegna."),
    }, cfg)


if __name__ == "__main__":
    import sys

    sample = demo_quote()
    path = sys.argv[1] if len(sys.argv) > 1 else "preventivo-demo.pdf"
    with open(path, "wb") as handle:
        handle.write(render_quote_pdf(sample))
    print(f"{path} ({len(sample['items'])} voci, totale {sample['totals']['total']:.2f})")
