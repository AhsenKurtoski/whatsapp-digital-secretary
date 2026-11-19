#!/usr/bin/env python3
"""Build the public website with SEO, analytics and security headers.

The dashboard is private and local-only; this site is the public presence.
`site/config.json` holds the real domain, verification tokens and analytics
IDs. Nothing is invented: an empty ID simply produces no tag, and the build
stops on an invalid value instead of publishing a broken verification meta.

Usage:
    python3 site_build.py --config site/config.json --out site/dist
    python3 site_build.py --self-test
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATES = ROOT / "site"
DEFAULT_CONFIG = DEFAULT_TEMPLATES / "config.json"

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.googletagmanager.com https://static.cloudflareinsights.com; "
    "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com "
    "https://cloudflareinsights.com; "
    "img-src 'self' data: https://www.google-analytics.com; "
    "style-src 'self'; font-src 'self'; object-src 'none'; media-src 'none'; "
    "frame-src 'none'; worker-src 'none'; manifest-src 'self'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'; upgrade-insecure-requests"
)
CSP_META = CSP.replace("frame-ancestors 'none'; ", "")
SECURITY_HEADERS = [
    ("Content-Security-Policy", CSP),
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    (
        "Permissions-Policy",
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
        "microphone=(), payment=(), usb=(), display-capture=(), browsing-topics=()",
    ),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("X-Permitted-Cross-Domain-Policies", "none"),
]
PAGES = ["index.html", "privacy.html", "termini.html", "404.html"]
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".txt", ".xml", ".webmanifest"}
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{6,160}$")
GA_RE = re.compile(r"^G-[A-Z0-9]{4,20}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _fail(message: str) -> None:
    raise SystemExit(f"SITO: {message}")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"config non trovata: {path}")
    except json.JSONDecodeError as exc:
        _fail(f"config JSON non valida: {exc}")
    if not isinstance(data, dict):
        _fail("la config deve essere un oggetto JSON")
    return data


def _url(value: str, name: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        _fail(f"{name} mancante: impostala in site/config.json (es. https://tuodominio.it)")
    if not URL_RE.match(value):
        _fail(f"{name} non valida: serve un URL https assoluto, senza spazi")
    return value


def _text(value: str, name: str, limit: int) -> str:
    value = str(value or "").strip()
    if not value:
        _fail(f"{name} mancante")
    value = " ".join(value.split())
    if len(value) > limit:
        _fail(f"{name} troppo lungo ({len(value)} > {limit})")
    if CONTROL_RE.search(value):
        _fail(f"{name} contiene caratteri di controllo")
    return value


def _token(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if not TOKEN_RE.match(value):
        _fail(f"{name} non valido: usa solo lettere, numeri, punto, underscore o trattino")
    return value


def _json_text(value: str) -> str:
    """Escape a string for safe embedding inside a JSON-LD <script> block."""
    return (
        json.dumps(value, ensure_ascii=False)[1:-1]
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _verification_metas(config: dict) -> str:
    tokens = config.get("verification") or {}
    if not isinstance(tokens, dict):
        _fail("verification deve essere un oggetto")
    rows = [
        ("google", "google-site-verification"),
        ("bing", "msvalidate.01"),
        ("yandex", "yandex-verification"),
    ]
    tags = []
    for key, meta_name in rows:
        token = _token(tokens.get(key, ""), f"verification.{key}")
        if token:
            tags.append(f'<meta name="{meta_name}" content="{html.escape(token, quote=True)}">')
    return "\n".join(tags)


def _analytics_js(config: dict) -> tuple[str, dict]:
    analytics = config.get("analytics") or {}
    if not isinstance(analytics, dict):
        _fail("analytics deve essere un oggetto")
    ga = str(analytics.get("ga4_measurement_id") or "").strip()
    if ga and not GA_RE.match(ga):
        _fail("analytics.ga4_measurement_id non valido: deve essere G-XXXXXXXXXX")
    cf = _token(analytics.get("cloudflare_token", ""), "analytics.cloudflare_token")
    payload = {"ga4": ga, "cloudflare": cf, "privacyUrl": "/privacy.html"}
    return "window.GINA_ANALYTICS = " + json.dumps(payload, ensure_ascii=False) + ";\n", payload


def _replacements(config: dict, base_url: str) -> dict[str, str]:
    owner_name = _text(str(config.get("owner_name") or ""), "owner_name", 120)
    site_name = _text(str(config.get("site_name") or ""), "site_name", 120)
    description = _text(str(config.get("description") or ""), "description", 300)
    owner_url = _url(
        str(config.get("owner_url") or "https://github.com/AhsenKurtoski"), "owner_url"
    )
    repo_url = _url(str(config.get("repo_url") or ""), "repo_url")
    security = _url(
        str(config.get("security_txt_contact") or f"{repo_url}/security/advisories/new"),
        "security_txt_contact",
    )
    today = datetime.now(UTC)
    return {
        "{{BASE_URL}}": base_url,
        "{{OWNER_NAME}}": html.escape(owner_name, quote=True),
        "{{OWNER_NAME_JSON}}": _json_text(owner_name),
        "{{SITE_NAME}}": html.escape(site_name, quote=True),
        "{{SITE_NAME_JSON}}": _json_text(site_name),
        "{{DESCRIPTION}}": html.escape(description, quote=True),
        "{{DESCRIPTION_JSON}}": _json_text(description),
        "{{OWNER_URL}}": html.escape(owner_url, quote=True),
        "{{REPO_URL}}": html.escape(repo_url, quote=True),
        "{{SECURITY_CONTACT}}": html.escape(security, quote=True),
        "{{LOCALE}}": html.escape(str(config.get("locale") or "it_IT"), quote=True),
        "{{YEAR}}": today.strftime("%Y"),
        "{{DATE_IT}}": today.strftime("%d/%m/%Y"),
        "{{VERIFICATION}}": _verification_metas(config),
        "{{CSP_META}}": html.escape(CSP_META, quote=True),
    }


def _render(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(key, value)
    leftovers = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if leftovers:
        _fail("placeholder non risolti: " + ", ".join(sorted(set(leftovers))))
    return template


def _headers_file() -> str:
    return "/*\n" + "".join(f"  {name}: {value}\n" for name, value in SECURITY_HEADERS)


def _vercel_config() -> str:
    payload = {
        "headers": [{
            "source": "/(.*)",
            "headers": [{"key": name, "value": value} for name, value in SECURITY_HEADERS],
        }],
        "git": {"deploymentEnabled": {"main": True}},
        "trailingSlash": False,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _sitemap(base_url: str, today: str) -> str:
    rows = []
    for page, priority, freq in (
        ("", "1.0", "monthly"),
        ("/privacy.html", "0.5", "yearly"),
        ("/termini.html", "0.3", "yearly"),
    ):
        rows.append(
            "  <url>\n"
            f"    <loc>{html.escape(base_url + '/' + page.lstrip('/'))}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows) + "\n</urlset>\n"
    )


def _robots(base_url: str) -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /404.html\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )


def _security_txt(base_url: str, contact: str, today: datetime, repo_url: str) -> str:
    expires = today.replace(year=today.year + 1).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return (
        f"Contact: {contact}\n"
        f"Expires: {expires}\n"
        "Preferred-Languages: it, en\n"
        f"Canonical: {base_url}/.well-known/security.txt\n"
        f"Policy: {repo_url}/blob/main/SECURITY.md\n"
        "Acknowledgments: https://github.com/AhsenKurtoski\n"
    )


def build(config_path: Path, out_dir: Path, templates: Path | None = None,
          base_url_override: str = "") -> dict:
    templates = templates or DEFAULT_TEMPLATES
    config = _read_json(config_path)
    base_url = _url(base_url_override or config.get("base_url", ""), "base_url")
    values = _replacements(config, base_url)
    analytics_js, analytics = _analytics_js(config)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    (out_dir / ".well-known").mkdir(parents=True)

    for page in PAGES:
        source = templates / page
        if not source.is_file():
            _fail(f"template mancante: {source}")
        rendered = _render(source.read_text(encoding="utf-8"), values)
        (out_dir / page).write_text(rendered, encoding="utf-8")

    for asset in (
        "style.css", "app.js", "analytics.js", "favicon.svg", "og.jpg",
        "dashboard-480.webp", "dashboard-720.webp", "dashboard-1080.webp", "dashboard-1440.webp",
    ):
        source = templates / "assets" / asset
        if not source.is_file():
            _fail(f"asset mancante: {source}")
        shutil.copy2(source, out_dir / "assets" / asset)
    shutil.copy2(templates / "manifest.json", out_dir / "manifest.json")
    (out_dir / "assets" / "analytics.config.js").write_text(analytics_js, encoding="utf-8")

    today = datetime.now(UTC)
    (out_dir / "sitemap.xml").write_text(_sitemap(base_url, today.date().isoformat()), encoding="utf-8")
    (out_dir / "robots.txt").write_text(_robots(base_url), encoding="utf-8")
    (out_dir / "_headers").write_text(_headers_file(), encoding="utf-8")
    (out_dir / "vercel.json").write_text(_vercel_config(), encoding="utf-8")
    security = _security_txt(
        base_url,
        html.unescape(values["{{SECURITY_CONTACT}}"]),
        today,
        html.unescape(values["{{REPO_URL}}"]),
    )
    (out_dir / "security.txt").write_text(security, encoding="utf-8")
    (out_dir / ".well-known" / "security.txt").write_text(security, encoding="utf-8")

    for path in out_dir.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            if "{{" in text and "}}" in text:
                _fail(f"placeholder non risolto in {path.relative_to(out_dir)}")

    return {
        "ok": True,
        "out": str(out_dir),
        "base_url": base_url,
        "pages": len(PAGES),
        "analytics": analytics,
        "headers": len(SECURITY_HEADERS),
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="site-build-") as tmp:
        root = Path(tmp)
        config = {
            "base_url": "https://example.com",
            "site_name": "Gina Test",
            "owner_name": "Ahsen Kurtoski",
            "owner_url": "https://github.com/AhsenKurtoski",
            "repo_url": "https://github.com/AhsenKurtoski/whatsapp-digital-secretary",
            "description": "Segreteria digitale WhatsApp self-hosted con approvazione umana e dashboard privata.",
            "analytics": {"ga4_measurement_id": "G-TEST12345", "cloudflare_token": ""},
            "verification": {"google": "google-token-123", "bing": "BING123456", "yandex": ""},
            "security_txt_contact": "https://github.com/AhsenKurtoski/whatsapp-digital-secretary/security/advisories/new",
        }
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        result = build(path, root / "dist")
        index = (root / "dist" / "index.html").read_text(encoding="utf-8")
        assert "G-TEST12345" in (root / "dist" / "assets" / "analytics.config.js").read_text(encoding="utf-8")
        assert 'name="google-site-verification"' in index
        assert 'name="msvalidate.01"' in index
        assert "https://example.com/" in index
        assert "{{" not in index
        assert (root / "dist" / "sitemap.xml").is_file()
        assert "Sitemap: https://example.com/sitemap.xml" in (root / "dist" / "robots.txt").read_text(encoding="utf-8")
        assert "Content-Security-Policy" in (root / "dist" / "_headers").read_text(encoding="utf-8")
        assert (root / "dist" / "termini.html").is_file()
        assert (root / "dist" / "assets" / "og.jpg").is_file()
        assert result["pages"] == 4
        print("SITE BUILD SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public SEO website")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out", default=str(DEFAULT_TEMPLATES / "dist"))
    parser.add_argument("--base-url", default="", help="override base_url (solo per test locali)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    result = build(Path(args.config), Path(args.out), base_url_override=args.base_url)
    print(
        f"SITO OK: {result['pages']} pagine in {result['out']} · "
        f"base {result['base_url']} · analytics "
        f"{'GA4' if result['analytics'].get('ga4') else 'nessuna'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
