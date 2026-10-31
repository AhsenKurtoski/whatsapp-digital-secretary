import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="secretary-tests-"))
BASE = TMP / "base"
WORKSPACE = TMP / "workspace"
CONFIG = TMP / "config.json"

BASE.mkdir(parents=True, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)

CONFIG.write_text(
    json.dumps(
        {
            "business_name": "Attività Demo",
            "owner_name": "Mario Rossi",
            "bot_name": "Sofia",
            "owner_number": "+393331112222",
            "google_account": "demo@example.com",
            "language": "italiano",
            "timezone": "Europe/Rome",
            "city": "Milano",
            "site_url": "https://example.com",
            "services": ["riparazioni", "preventivi"],
            "hours": "Lun–Ven 9:00–18:00",
            "pricing_note": "I prezzi li conferma il titolare.",
            "dashboard_port": 8787,
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

os.environ["SECRETARY_CONFIG"] = str(CONFIG)
os.environ["SECRETARY_BASE"] = str(BASE)
os.environ["SECRETARY_WORKSPACE"] = str(WORKSPACE)
os.environ["OPENCLAW_CONFIG"] = str(TMP / "openclaw.json")
os.environ["OPENCLAW_DB"] = str(TMP / "openclaw.sqlite")

sys.path.insert(0, str(ROOT))


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)
