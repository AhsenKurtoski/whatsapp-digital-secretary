import os
import subprocess
import sys
import unittest
from pathlib import Path

import support  # noqa: F401
import monitor

ROOT = Path(__file__).resolve().parents[1]


class MonitorTests(unittest.TestCase):
    def test_self_test_runs(self):
        env = os.environ.copy()
        env["SECRETARY_BASE"] = str(support.BASE)
        result = subprocess.run(
            [sys.executable, str(ROOT / "monitor.py"), "--self-test"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("self-test", (result.stdout + result.stderr).lower())

    def test_location_uses_configured_city(self):
        # support.py configures Milano; no other real city may be hardcoded.
        self.assertTrue(monitor.LOCATION.search("Sono di Milano"))
        self.assertFalse(monitor.LOCATION.search("Sono di Roma"))


if __name__ == "__main__":
    unittest.main()
