import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import support  # noqa: F401  (sets the isolated environment)
import dashboard

ROOT = Path(__file__).resolve().parents[1]


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self._run = dashboard.run
        dashboard.run = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CLI must not run in the HTTP path"))
        self._path = dashboard.WATCHDOG_STATE
        self.tmp = tempfile.TemporaryDirectory(prefix="secretary-runtime-")
        dashboard.WATCHDOG_STATE = os.path.join(self.tmp.name, "watchdog_state.json")

    def tearDown(self):
        dashboard.run = self._run
        dashboard.WATCHDOG_STATE = self._path
        dashboard.CHANNEL_CACHE["data"] = None
        dashboard.CHANNEL_CACHE["ts"] = 0.0
        self.tmp.cleanup()

    def write_state(self, state="healthy", age=10):
        last = (datetime.now(dashboard.ROME) - timedelta(seconds=age)).isoformat(timespec="seconds")
        Path(dashboard.WATCHDOG_STATE).write_text(
            json.dumps({"last_check": last, "last_result": {"whatsapp": state}}),
            encoding="utf-8",
        )

    def test_channel_status_reads_watchdog_without_cli(self):
        self.write_state("healthy", 10)
        started = time.monotonic()
        data = dashboard.channel_status()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(data["state"], "healthy")
        self.assertEqual(data["source"], "watchdog")

    def test_stale_watchdog_uses_last_cache(self):
        self.write_state("healthy", 10)
        dashboard.channel_status()
        self.write_state("unreachable", 3600)
        data = dashboard.channel_status()
        self.assertTrue(data.get("stale"))
        self.assertEqual(data["state"], "healthy")


class FallbackConfigTests(unittest.TestCase):
    def run_sync(self, secrets):
        home = tempfile.TemporaryDirectory(prefix="secretary-sync-")
        self.addCleanup(home.cleanup)
        base = Path(home.name) / ".openclaw" / "secretary"
        base.mkdir(parents=True)
        (base / "secrets.env").write_text("".join(f"{k}={v}\n" for k, v in secrets.items()), encoding="utf-8")
        config = Path(home.name) / ".openclaw" / "openclaw.json"
        config.write_text(
            json.dumps(
                {
                    "agents": {"defaults": {"model": {"primary": "groq/openai/gpt-oss-120b", "fallbacks": []}}},
                    "models": {"providers": {"groq": {"apiKey": "${GROQ_API_KEY}"}}},
                }
            ),
            encoding="utf-8",
        )
        env = dict(
            os.environ,
            HOME=home.name,
            SECRETARY_BASE=str(base),
            OPENCLAW_CONFIG=str(config),
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "sync_fallbacks.py")],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(config.read_text(encoding="utf-8"))

    def test_gemini_becomes_primary_when_key_exists(self):
        cfg = self.run_sync({"GEMINI_API_KEY": "test-gemini-key-000000000000000000"})
        model = cfg["agents"]["defaults"]["model"]
        self.assertEqual(model["primary"], "google/gemini-3.5-flash")
        self.assertEqual(model["fallbacks"][0], "google/gemini-3.5-flash-lite")
        self.assertIn("groq/openai/gpt-oss-120b", model["fallbacks"])

    def test_groq_stays_primary_without_gemini(self):
        cfg = self.run_sync({"GROQ_API_KEY_2": "gsk-test-key-000000000000000002"})
        model = cfg["agents"]["defaults"]["model"]
        self.assertEqual(model["primary"], "groq/openai/gpt-oss-120b")
        self.assertIn("groq2/openai/gpt-oss-120b", model["fallbacks"])


if __name__ == "__main__":
    unittest.main()
