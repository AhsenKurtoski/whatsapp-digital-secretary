#!/usr/bin/env python3
"""Controlla i link relativi di documentazione e pagine HTML.

Un link relativo rotto nella documentazione è un errore vero: chi installa
segue `docs/...` e trova un 404 nel repository. Questo controllo percorre i
file `.md` e `.html` del progetto, ignora i blocchi di codice (esempi
dimostrativi) e gli URL assoluti, e fallisce elencando i bersagli mancanti.

Uso:
    python3 link_check.py
    python3 link_check.py --json
    python3 link_check.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {
    ".git",
    ".lighthouseci",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "artifacts",
    "_audit",
    "dist",
}
SUFFIXES = {".md", ".html"}
# I blocchi di codice contengono esempi (anche path futuri): non sono link.
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
MD_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_ATTR_RE = re.compile(r'(?:href|src)="([^"]+)"')
SKIP_PREFIXES = (
    "http://", "https://", "mailto:", "tel:", "data:", "javascript:",
    "//", "#", "/", "{{", "$",
)


def _files(root: Path) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        parts = set(path.relative_to(root).parts[:-1])
        if parts & SKIP_DIRS:
            continue
        found.append(path)
    return found


def check(root: Path | None = None) -> dict:
    """Ritorna ``{ok, checked, broken}`` sui link relativi del progetto."""
    base = Path(root or ROOT)
    broken: list[str] = []
    checked = 0
    for path in _files(base):
        text = FENCE_RE.sub("", path.read_text(encoding="utf-8", errors="replace"))
        for raw in MD_LINK_RE.findall(text) + HTML_ATTR_RE.findall(text):
            target = raw.split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(SKIP_PREFIXES) or "${" in target:
                continue
            checked += 1
            if not (path.parent / target).exists():
                broken.append(f"{path.relative_to(base)} -> {raw}")
    return {"ok": not broken, "checked": checked, "broken": broken}


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="link-check-") as tmp:
        root = Path(tmp)
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("[indietro](../README.md)\n", encoding="utf-8")
        (root / "README.md").write_text(
            "[guida](docs/guide.md) e [rotto](missing.md)\n\n"
            "```markdown\n[esempio](missing-in-fence.md)\n```\n\n"
            "[web](https://example.com) [ancora](#sezione) [assoluto](/privacy.html)\n",
            encoding="utf-8",
        )
        (root / "page.html").write_text(
            '<a href="docs/guide.md">ok</a><img src="missing.png" alt="x">',
            encoding="utf-8",
        )
        report = check(root)
        assert report["ok"] is False, report
        assert report["checked"] == 5, report
        assert len(report["broken"]) == 2, report
        assert any("missing.md" in item for item in report["broken"]), report
        assert any("missing.png" in item for item in report["broken"]), report
        assert not any("missing-in-fence" in item for item in report["broken"]), report

        (root / "missing.md").write_text("ok\n", encoding="utf-8")
        (root / "missing.png").write_bytes(b"\x89PNG")
        assert check(root)["ok"] is True, check(root)
    print("LINK CHECK SELF-TEST OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica i link relativi del progetto")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    report = check(Path(args.root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["broken"]:
            print(f"  FAIL {item}")
        print(
            f"LINK CHECK {'OK' if report['ok'] else 'FALLITO'}: "
            f"{report['checked']} link relativi controllati, {len(report['broken'])} rotti"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
