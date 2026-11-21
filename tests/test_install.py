import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _as_posix(value: Path | str) -> str:
    return str(value).replace("\\", "/")


def _write_stub(directory: Path, name: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)


def _base_environment(tmp: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp / "home"
    (home / ".config" / "digital-secretary").mkdir(parents=True)
    (home / ".openclaw").mkdir()
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".config" / "digital-secretary" / "config.json").write_text(
        '{"business_name":"Test","owner_name":"Test","bot_name":"Bot",'
        '"owner_number":"+390000000000","google_account":"test@example.com",'
        '"language":"italiano","timezone":"Europe/Rome","city":"Roma",'
        '"site_url":"https://example.com","services":["test"],"hours":"9-18",'
        '"pricing_note":"test","dashboard_port":8799}',
        encoding="utf-8",
    )
    (home / ".openclaw" / "openclaw.json").write_text("{}\n", encoding="utf-8")
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "systemctl")
    _write_stub(bin_dir, "curl")
    _write_stub(bin_dir, "loginctl")
    # Evita di interrogare un'eventuale installazione OpenClaw reale durante
    # il test: il probe deve fallire subito e l'installer deve proseguire.
    openclaw = bin_dir / "openclaw"
    openclaw.write_text("#!/bin/sh\nexit 127\n", encoding="ascii")
    openclaw.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": _as_posix(home),
            "SECRETARY_BASE": _as_posix(home / ".openclaw" / "secretary"),
            "SECRETARY_CONFIG": _as_posix(
                home / ".config" / "digital-secretary" / "config.json"
            ),
            "OPENCLAW_CONFIG": _as_posix(home / ".openclaw" / "openclaw.json"),
            "PYTHON": _as_posix(sys.executable),
            "PATH": _as_posix(bin_dir) + os.pathsep + env.get("PATH", ""),
        }
    )
    return env, home, bin_dir


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        if not self.bash:
            self.skipTest("bash non disponibile")
        if not shutil.which("install"):
            self.skipTest("install non disponibile")

    def test_installer_copies_remote_access_with_private_mode(self):
        with tempfile.TemporaryDirectory(prefix="secretary-install-") as tmp:
            tmp_path = Path(tmp)
            env, home, _ = _base_environment(tmp_path)
            result = subprocess.run(
                [self.bash, str(ROOT / "scripts" / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("dashboard: http://127.0.0.1:8799", result.stdout)
            installed = home / ".openclaw" / "secretary" / "remote_access.py"
            self.assertTrue(installed.is_file(), result.stdout + result.stderr)
            self.assertEqual(
                installed.read_bytes(), (ROOT / "remote_access.py").read_bytes()
            )
            wrapper = home / ".local" / "bin" / "gog-secretary"
            self.assertTrue(wrapper.is_file(), result.stdout + result.stderr)
            self.assertEqual(wrapper.read_bytes(), (ROOT / "gog-secretary").read_bytes())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o700)
            self_check = subprocess.run(
                [sys.executable, str(installed), "--self-test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(self_check.returncode, 0, self_check.stderr)
            self.assertIn("SELF-TEST OK", self_check.stdout)

    def test_installer_stops_when_cryptography_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="secretary-install-deps-") as tmp:
            tmp_path = Path(tmp)
            env, home, _ = _base_environment(tmp_path)
            # Un pacchetto che solleva ImportError simula la dipendenza assente
            # senza dover installare un secondo interprete.
            blocked = tmp_path / "blocked"
            (blocked / "cryptography").mkdir(parents=True)
            (blocked / "cryptography" / "__init__.py").write_text(
                "raise ImportError('cryptography assente')\n", encoding="utf-8"
            )
            env["PYTHONPATH"] = _as_posix(blocked)
            result = subprocess.run(
                [self.bash, str(ROOT / "scripts" / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cryptography", result.stderr)
            self.assertIn("requirements.txt", result.stderr)
            self.assertFalse((home / ".openclaw" / "secretary").exists())

    def test_installer_reports_the_tools_the_gateway_exposes(self):
        with tempfile.TemporaryDirectory(prefix="secretary-install-tools-") as tmp:
            tmp_path = Path(tmp)
            env, home, bin_dir = _base_environment(tmp_path)
            probe = bin_dir / "openclaw"
            payload = json.dumps({
                "servers": {"secretary": {}},
                "tools": ["secretary__request_quote", "mcp__secretary__request_email"],
            })
            probe.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *'mcp probe'*) printf '%s\\n' '" + payload + "'; exit 0;;\n"
                "esac\n"
                "exit 127\n",
                encoding="ascii",
            )
            probe.chmod(0o755)
            result = subprocess.run(
                [self.bash, str(ROOT / "scripts" / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("bot tools: request_email,request_quote", result.stdout)

    def test_installer_fails_when_gateway_restart_fails(self):
        with tempfile.TemporaryDirectory(prefix="secretary-install-restart-") as tmp:
            tmp_path = Path(tmp)
            env, home, bin_dir = _base_environment(tmp_path)
            # systemctl fallisce solo sul riavvio del gateway: l'installazione
            # non deve dichiararsi riuscita con il bot senza tool.
            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\ncase \"$*\" in *openclaw-gateway.service*) exit 1;; esac\nexit 0\n",
                encoding="ascii",
            )
            systemctl.chmod(0o755)
            result = subprocess.run(
                [self.bash, str(ROOT / "scripts" / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("openclaw-gateway.service", result.stderr)
            self.assertIn("systemctl --user status", result.stderr)

    def test_installer_fails_when_remote_access_source_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="secretary-install-missing-") as tmp:
            tmp_path = Path(tmp)
            env, home, _ = _base_environment(tmp_path)
            source = tmp_path / "source"
            (source / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts" / "install.sh", source / "scripts" / "install.sh")
            # gog-secretary è presente: il test deve fallire solo per l'helper mancante.
            shutil.copy2(ROOT / "gog-secretary", source / "gog-secretary")
            result = subprocess.run(
                [self.bash, str(source / "scripts" / "install.sh")],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("File richiesto mancante", result.stderr)
            self.assertIn("remote_access.py", result.stderr)
            self.assertFalse((home / ".openclaw" / "secretary").exists())


if __name__ == "__main__":
    unittest.main()
