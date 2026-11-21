#!/usr/bin/env python3
"""Audit the built public site: SEO, structured data, analytics and headers.

This is a real gate, not a checklist: errors exit non-zero and are printed with
the page they belong to. Run it after `site_build.py` and in CI.

Usage:
    python3 seo_audit.py --dir site/dist
    python3 seo_audit.py --dir site/dist --json
    python3 seo_audit.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

PAGES = ("index.html", "privacy.html", "termini.html", "autore.html", "404.html")
REQUIRED_HEADERS = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
)
GA_RE = re.compile(r"^G-[A-Z0-9]{4,20}$")


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.h1 = 0
        self.json_ld: list[str] = []
        self.images: list[dict] = []
        self.links: list[dict] = []
        self.scripts: list[str] = []
        self._in_title = False
        self._in_json = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "html":
            self.lang = values.get("lang", "")
        elif tag == "title":
            self._in_title = True
            self._buffer = []
        elif tag == "meta":
            name = values.get("name") or values.get("property") or ""
            if name and values.get("content"):
                self.meta[name.lower()] = values["content"]
        elif tag == "link" and "canonical" in values.get("rel", "").lower():
            self.canonical = values.get("href", "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "script":
            if values.get("type") == "application/ld+json":
                self._in_json = True
                self._buffer = []
            elif values.get("src"):
                self.scripts.append(values["src"])
        elif tag == "img":
            self.images.append({"src": values.get("src", ""), "alt": values.get("alt", "")})
        elif tag == "a":
            self.links.append({"href": values.get("href", ""), "target": values.get("target", ""),
                               "rel": values.get("rel", "")})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self.title = " ".join("".join(self._buffer).split())
            self._in_title = False
        elif tag == "script" and self._in_json:
            self.json_ld.append("".join(self._buffer))
            self._in_json = False

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_json:
            self._buffer.append(data)


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.pages: dict[str, dict] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _parse(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def _types(blocks: list[str]) -> set[str]:
    found: set[str] = set()
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                kind = item.get("@type")
                if isinstance(kind, str):
                    found.add(kind)
                elif isinstance(kind, list):
                    found.update(str(value) for value in kind)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def audit(directory: Path) -> dict:
    report = Audit()
    if not directory.is_dir():
        raise SystemExit(f"AUDIT: cartella non trovata: {directory}")

    base_url = ""
    for page in PAGES:
        path = directory / page
        if not path.is_file():
            report.error(f"{page}: file mancante")
            continue
        parsed = _parse(path)
        info = {
            "title": parsed.title,
            "description": parsed.meta.get("description", ""),
            "canonical": parsed.canonical,
            "h1": parsed.h1,
            "json_ld": len(parsed.json_ld),
            "schema_types": sorted(_types(parsed.json_ld)),
            "images": len(parsed.images),
        }
        report.pages[page] = info

        if page == "404.html":
            if "noindex" not in parsed.meta.get("robots", "").lower():
                report.error("404.html: deve avere meta robots noindex")
        else:
            title = parsed.title
            if not 15 <= len(title) <= 65:
                report.error(f"{page}: title lungo {len(title)} caratteri (15-65)")
            description = parsed.meta.get("description", "")
            if not 50 <= len(description) <= 165:
                report.error(f"{page}: meta description lunga {len(description)} caratteri (50-165)")
            if parsed.meta.get("robots", "").lower().startswith("noindex"):
                report.error(f"{page}: la pagina pubblica non deve essere noindex")
            if not parsed.canonical.startswith("https://"):
                report.error(f"{page}: canonical mancante o non assoluto")
            elif not base_url:
                base_url = parsed.canonical.split("//", 1)[1].split("/", 1)[0]
            if parsed.meta.get("og:title", "") == "":
                report.error(f"{page}: og:title mancante")
            if parsed.meta.get("og:description", "") == "":
                report.error(f"{page}: og:description mancante")
            if parsed.meta.get("og:url", "") == "":
                report.error(f"{page}: og:url mancante")
            if parsed.meta.get("og:image", "") == "":
                report.error(f"{page}: og:image mancante")
            if parsed.meta.get("twitter:card", "") == "":
                report.error(f"{page}: twitter:card mancante")

        if parsed.h1 != 1:
            report.error(f"{page}: {parsed.h1} tag h1 (atteso 1)")
        if not parsed.lang:
            report.error(f"{page}: attributo lang mancante")
        if not parsed.meta.get("viewport"):
            report.error(f"{page}: viewport mancante")
        for block in parsed.json_ld:
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                report.error(f"{page}: JSON-LD non valido ({exc.msg})")
        for image in parsed.images:
            if not image["alt"].strip():
                report.error(f"{page}: immagine senza alt ({image['src']})")
        for link in parsed.links:
            if link["target"] == "_blank" and "noopener" not in link["rel"]:
                report.error(f"{page}: link target=_blank senza rel=noopener ({link['href']})")
        if "{{" in path.read_text(encoding="utf-8"):
            report.error(f"{page}: placeholder non risolto")

    index = report.pages.get("index.html", {})
    types = set(index.get("schema_types") or [])
    for required in ("Person", "SoftwareApplication", "WebSite", "FAQPage"):
        if required not in types:
            report.error(f"index.html: structured data {required} mancante")

    sitemap = directory / "sitemap.xml"
    if not sitemap.is_file():
        report.error("sitemap.xml mancante")
    else:
        sitemap_text = sitemap.read_text(encoding="utf-8")
        if base_url and base_url not in sitemap_text:
            report.error("sitemap.xml non contiene il dominio del canonical")
        for page in ("privacy.html", "termini.html", "autore.html"):
            if page not in sitemap_text:
                report.error(f"sitemap.xml non contiene {page}")

    robots = directory / "robots.txt"
    if not robots.is_file():
        report.error("robots.txt mancante")
    elif "Sitemap:" not in robots.read_text(encoding="utf-8"):
        report.error("robots.txt senza direttiva Sitemap")

    headers = directory / "_headers"
    if not headers.is_file():
        report.error("_headers mancante (security headers per Cloudflare Pages/Netlify)")
    else:
        text = headers.read_text(encoding="utf-8")
        for name in REQUIRED_HEADERS:
            if name not in text:
                report.error(f"_headers: manca {name}")

    vercel = directory / "vercel.json"
    if not vercel.is_file():
        report.error("vercel.json mancante")
    else:
        try:
            data = json.loads(vercel.read_text(encoding="utf-8"))
            names = {row["key"] for row in data["headers"][0]["headers"]}
        except Exception:
            names = set()
        missing = [name for name in REQUIRED_HEADERS if name not in names]
        if missing:
            report.error("vercel.json: header mancanti " + ", ".join(missing))

    security_txt = directory / ".well-known" / "security.txt"
    if not security_txt.is_file():
        report.error(".well-known/security.txt mancante")
    else:
        content = security_txt.read_text(encoding="utf-8")
        for field in ("Contact:", "Expires:", "Canonical:"):
            if field not in content:
                report.error(f"security.txt: campo {field} mancante")

    config = directory / "assets" / "analytics.config.js"
    if not config.is_file():
        report.error("assets/analytics.config.js mancante")
    else:
        text = config.read_text(encoding="utf-8")
        match = re.search(r'"ga4"\s*:\s*"([^"]*)"', text)
        ga = match.group(1) if match else ""
        if ga and not GA_RE.match(ga):
            report.error(f"analytics.config.js: GA4 non valido ({ga})")
        if not ga:
            report.warn("GA4 non configurato: nessun dato analytics finché non imposti ga4_measurement_id")

    if "Ahsen Kurtoski" not in (directory / "index.html").read_text(encoding="utf-8"):
        report.warn("index.html: il nome dell'autore non compare nella pagina")

    return {
        "ok": not report.errors,
        "pages": report.pages,
        "errors": report.errors,
        "warnings": report.warnings,
    }


def self_test() -> int:
    import site_build

    with tempfile.TemporaryDirectory(prefix="seo-audit-") as tmp:
        root = Path(tmp)
        config = {
            "base_url": "https://example.com",
            "site_name": "Gina Test",
            "owner_name": "Ahsen Kurtoski",
            "owner_url": "https://github.com/AhsenKurtoski",
            "repo_url": "https://github.com/AhsenKurtoski/whatsapp-digital-secretary",
            "description": "Segreteria digitale WhatsApp self-hosted con approvazione umana e dashboard privata.",
            "analytics": {"ga4_measurement_id": "", "cloudflare_token": ""},
            "verification": {"google": "", "bing": "", "yandex": ""},
        }
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        site_build.build(path, root / "dist")
        report = audit(root / "dist")
        assert report["ok"], report["errors"]
        assert report["warnings"], "senza GA4 deve esserci un avviso esplicito"

        broken = root / "broken"
        site_build.build(path, broken)
        index = broken / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace("<h1>", "<h1></h1><h1>", 1),
            encoding="utf-8",
        )
        report = audit(broken)
        assert not report["ok"]
        assert any("h1" in error for error in report["errors"])
    print("SEO AUDIT SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the public site")
    parser.add_argument("--dir", default="site/dist")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    report = audit(Path(args.dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for page, info in report["pages"].items():
            print(f"· {page}: {info['title']!r} · h1={info['h1']} · schema={','.join(info['schema_types'])}")
        for warning in report["warnings"]:
            print(f"  WARN {warning}")
        for error in report["errors"]:
            print(f"  FAIL {error}")
        print("SEO AUDIT OK" if report["ok"] else f"SEO AUDIT FALLITO: {len(report['errors'])} errori")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
