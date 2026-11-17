import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from scripts import openclaw_compat


def fake_openclaw(tmp: Path) -> Path:
    script = tmp / "fake_openclaw.py"
    script.write_text(
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if '--version' in args:\n"
        "    print(os.environ.get('FAKE_VERSION', 'OpenClaw 2026.10.7'))\n"
        "elif args[:2] == ['plugins', 'install'] and '--help' in args:\n"
        "    print('Usage: openclaw plugins install --force')\n"
        "    if os.environ.get('FAKE_ACCEPT', '1') == '1':\n"
        "        print('  --accept-capabilities')\n"
        "elif args[:2] == ['config', 'schema']:\n"
        "    print('{\"allowConversationAccess\": true}' if "
        "os.environ.get('FAKE_HOOK', '1') == '1' else "
        "'{\"allowPromptInjection\": true}')\n"
        "else:\n"
        "    print('{}')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp / "openclaw.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = tmp / "openclaw"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return wrapper


def run_check_compat(fake: Path, environment: dict[str, str]) -> subprocess.CompletedProcess:
    bash = shutil.which("bash")
    if not bash:
        raise unittest.SkipTest("bash non disponibile")
    env = os.environ.copy()
    env.update(environment)
    env["PATH"] = str(fake.parent) + os.pathsep + env.get("PATH", "")
    env["PYTHON"] = sys.executable
    return subprocess.run(
        [bash, str(Path(__file__).resolve().parent.parent / "scripts" / "install.sh"),
         "--check-compat"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


class OpenClawCompatTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(
            openclaw_compat.parse_version("OpenClaw 2026.10.7 (157d8c2)"),
            (2026, 10, 7),
        )
        self.assertEqual(openclaw_compat.parse_version("not a version"), ())

    def test_minimum_version_comparison(self):
        self.assertTrue(
            openclaw_compat.version_at_least((2026, 10, 7), (2026, 4, 22))
        )
        self.assertTrue(
            openclaw_compat.version_at_least((2026, 4, 22), (2026, 4, 22))
        )
        self.assertFalse(
            openclaw_compat.version_at_least((2026, 4, 21), (2026, 4, 22))
        )

    def test_feature_flags_are_detected_without_writing(self):
        self.assertTrue(openclaw_compat.supports_accept_capabilities(
            "Usage: openclaw plugins install --accept-capabilities"
        ))
        self.assertFalse(openclaw_compat.supports_accept_capabilities(
            "Usage: openclaw plugins install --force"
        ))
        self.assertTrue(openclaw_compat.supports_conversation_access(
            '{"allowConversationAccess": true}'
        ))
        self.assertFalse(openclaw_compat.supports_conversation_access(
            '{"allowPromptInjection": true}'
        ))

    def test_fake_openclaw_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_openclaw(Path(tmp))
            cases = [
                ({"FAKE_VERSION": "OpenClaw 2026.4.21", "FAKE_ACCEPT": "1", "FAKE_HOOK": "1"},
                 {"refuse": True, "allowed": False, "accept": False, "burst_only": False}),
                ({"FAKE_VERSION": "OpenClaw 2026.10.7", "FAKE_ACCEPT": "0", "FAKE_HOOK": "1"},
                 {"refuse": False, "allowed": True, "accept": False, "burst_only": False}),
                ({"FAKE_VERSION": "OpenClaw 2026.10.7", "FAKE_ACCEPT": "1", "FAKE_HOOK": "0"},
                 {"refuse": False, "allowed": True, "accept": True, "burst_only": True}),
            ]
            for environment, expected in cases:
                with self.subTest(environment=environment), mock.patch.dict(
                    os.environ, environment, clear=False
                ):
                    result = openclaw_compat.inspect(str(fake))
                    plan = openclaw_compat.make_install_plan(result)
                    self.assertEqual(plan.refuse, expected["refuse"])
                    self.assertEqual(plan.allowed, expected["allowed"])
                    self.assertEqual(plan.accept_capabilities, expected["accept"])
                    self.assertEqual(plan.burst_only, expected["burst_only"])

    def test_installer_check_compat_uses_fake_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = fake_openclaw(Path(tmp))
            old = run_check_compat(fake, {
                "FAKE_VERSION": "OpenClaw 2026.4.21",
                "FAKE_ACCEPT": "1", "FAKE_HOOK": "1",
            })
            self.assertEqual(old.returncode, 1, old.stderr)
            old_plan = json.loads(old.stdout)
            self.assertTrue(old_plan["refuse"])
            self.assertFalse(old_plan["allowed"])

            legacy = run_check_compat(fake, {
                "FAKE_VERSION": "OpenClaw 2026.10.7",
                "FAKE_ACCEPT": "0", "FAKE_HOOK": "1",
            })
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            legacy_plan = json.loads(legacy.stdout)
            self.assertTrue(legacy_plan["allowed"])
            self.assertFalse(legacy_plan["accept_capabilities"])
            self.assertFalse(legacy_plan["burst_only"])
            self.assertTrue(legacy_plan["warnings"])
            self.assertIn("legacy", legacy_plan["warnings"][0])

            burst_only = run_check_compat(fake, {
                "FAKE_VERSION": "OpenClaw 2026.10.7",
                "FAKE_ACCEPT": "1", "FAKE_HOOK": "0",
            })
            self.assertEqual(burst_only.returncode, 0, burst_only.stderr)
            burst_plan = json.loads(burst_only.stdout)
            self.assertTrue(burst_plan["allowed"])
            self.assertTrue(burst_plan["burst_only"])
            self.assertTrue(any("retry disattivato" in warning for warning in burst_plan["warnings"]))

    def test_inspect_combines_version_and_features(self):
        responses = [
            (0, "OpenClaw 2026.10.7 (157d8c2)\n"),
            (0, "Usage: openclaw plugins install --accept-capabilities\n"),
            (0, '{"plugins": {"allowConversationAccess": true}}\n'),
        ]
        with mock.patch.object(
            openclaw_compat, "resolve_openclaw", return_value=["openclaw"]
        ), mock.patch.object(openclaw_compat, "_run", side_effect=responses):
            result = openclaw_compat.inspect()
        self.assertTrue(result.present)
        self.assertEqual(result.version, "2026.10.7")
        self.assertTrue(result.version_ok)
        self.assertTrue(result.accept_capabilities)
        self.assertTrue(result.conversation_access)


if __name__ == "__main__":
    unittest.main()
