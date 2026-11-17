"""Il teardown dello smoke test non deve mai bloccare la verifica.

Regressione per il blocco di `scripts/verify.sh`: il gateway veniva terminato
solo come processo padre, i figli restavano vivi e il secondo `wait()` rilanciava
`TimeoutExpired` dentro il `finally`, lasciando la matrice appesa.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import smoke_guard  # noqa: E402


class StopProcessTests(unittest.TestCase):
    def tearDown(self):
        process = getattr(self, "process", None)
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "SIGTERM non è ignorabile allo stesso modo su Windows")
    def test_stubborn_process_is_killed_and_teardown_returns(self):
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(300)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.3)
        started = time.monotonic()
        smoke_guard.stop_process(
            self.process, Path(tempfile.gettempdir()) / "smoke-teardown-test.log"
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 20, "il teardown ha atteso troppo")
        self.assertIsNotNone(self.process.poll(), "il processo è ancora vivo")

    def test_already_finished_process_is_a_noop(self):
        self.process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.process.wait(timeout=10)
        started = time.monotonic()
        smoke_guard.stop_process(
            self.process, Path(tempfile.gettempdir()) / "smoke-teardown-test.log"
        )
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
