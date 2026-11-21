"""Profilo a basso consumo: rilevamento RAM, limiti, footprint e healthcheck."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

import config
import healthcheck
from scripts import footprint

ROOT = Path(__file__).resolve().parents[1]


def write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class LowMemoryDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = self.dir / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_total_ram_is_read_from_meminfo(self):
        meminfo = self.dir / "meminfo"
        meminfo.write_text("MemTotal:        2048000 kB\nMemAvailable: 900000 kB\n", encoding="utf-8")
        self.assertEqual(config.total_ram_mb(str(meminfo)), 2000)
        self.assertEqual(config.total_ram_mb(str(self.dir / "missing")), 0)

    def test_env_overrides_everything(self):
        write_config(self.cfg, {"low_memory": False})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.dict(
            os.environ, {"SECRETARY_LOW_MEMORY": "1"}
        ):
            self.assertTrue(config.low_memory())
        with mock.patch.dict(os.environ, {"SECRETARY_LOW_MEMORY": "0"}):
            self.assertFalse(config.low_memory())

    def test_config_file_flag_wins_over_autodetect(self):
        write_config(self.cfg, {"low_memory": True})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("SECRETARY_LOW_MEMORY", None)
            self.assertTrue(config.low_memory())

    def test_autodetect_only_below_4gb(self):
        write_config(self.cfg, {})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.object(
            config, "total_ram_mb", return_value=2048
        ), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRETARY_LOW_MEMORY", None)
            self.assertTrue(config.low_memory())
        with mock.patch.object(config, "total_ram_mb", return_value=8192):
            self.assertFalse(config.low_memory())
        with mock.patch.object(config, "total_ram_mb", return_value=0):
            self.assertFalse(config.low_memory())

    def test_load_clamps_parallel_calls_in_low_memory(self):
        write_config(self.cfg, {"max_concurrent": 5})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.dict(
            os.environ, {"SECRETARY_LOW_MEMORY": "1"}
        ):
            self.assertEqual(config.load()["max_concurrent"], 2)
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.dict(
            os.environ, {"SECRETARY_LOW_MEMORY": "0"}
        ):
            self.assertEqual(config.load()["max_concurrent"], 5)

    def test_explicit_auto_and_false_are_respected(self):
        write_config(self.cfg, {"low_memory": "auto"})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.object(
            config, "total_ram_mb", return_value=2048
        ), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRETARY_LOW_MEMORY", None)
            self.assertTrue(config.low_memory())
        write_config(self.cfg, {"low_memory": False})
        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.object(
            config, "total_ram_mb", return_value=2048
        ):
            self.assertFalse(config.low_memory())

    def test_load_reads_the_config_file_once(self):
        write_config(self.cfg, {"max_concurrent": 4})
        calls = []
        real = config._read_file

        def counting():
            calls.append(1)
            return real()

        with mock.patch.object(config, "DEFAULT_CONFIG_PATH", self.cfg), mock.patch.object(
            config, "_read_file", counting
        ):
            config.load()
        self.assertEqual(len(calls), 1, "load() deve leggere config.json una sola volta")

    def test_module_caps_follow_the_profile(self):
        def caps(flag: str) -> str:
            env = dict(os.environ, SECRETARY_LOW_MEMORY=flag)
            out = subprocess.run(
                [sys.executable, "-c", "import memory, conversations; print(memory.MAX_MESSAGES, conversations.MAX_MESSAGES)"],
                capture_output=True, text=True, check=True, env=env, cwd=str(ROOT),
            )
            return out.stdout.strip()

        self.assertEqual(caps("0"), "300 200")
        self.assertEqual(caps("1"), "120 80")


class FootprintTests(unittest.TestCase):
    def test_self_test_runs(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(footprint.self_test(), 0)
        self.assertIn("SELF-TEST OK", out.getvalue())

    def test_verdict_thresholds(self):
        self.assertEqual(footprint.verdict(400, 1500)["status"], "ok")
        self.assertEqual(footprint.verdict(1300, 1500)["status"], "near")
        self.assertEqual(footprint.verdict(1600, 1500)["status"], "over")

    def test_parse_and_report_shape(self):
        parsed = footprint.parse_meminfo("MemTotal: 8000000 kB\nMemAvailable: 2000000 kB\n")
        self.assertEqual(parsed["total_mb"], 7812)
        self.assertEqual(parsed["available_mb"], 1953)
        data = footprint.report(1200)
        self.assertEqual(data["budget_mb"], 1200)
        self.assertIn(data["verdict"]["status"], {"ok", "near", "over"})
        self.assertIn("process_total_mb", data)

    def test_cli_json_outputs_valid_json(self):
        out = subprocess.run(
            [sys.executable, "scripts/footprint.py", "--json"],
            capture_output=True, text=True, check=True, cwd=str(ROOT),
        )
        data = json.loads(out.stdout)
        self.assertIn("system", data)
        self.assertIn("processes", data)


class UnitAndHealthTests(unittest.TestCase):
    def test_services_cap_memory(self):
        units = sorted((ROOT / "systemd").glob("*.service"))
        self.assertTrue(units)
        for unit in units:
            text = unit.read_text(encoding="utf-8")
            if unit.name == "openclaw-dashboard.service":
                self.assertIn("MemoryMax=512M", text)
                self.assertIn("MemoryHigh=384M", text)
                # Il cgroup include i figli Node: un TasksMax basso li strozzerebbe.
                self.assertNotIn("TasksMax", text)
            else:
                self.assertIn("MemoryMax=512M", text, unit.name)

    def test_installer_ships_the_gateway_dropin(self):
        text = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("30-low-memory.conf", text)
        self.assertIn("MemoryHigh=1200M", text)
        self.assertIn("config.low_memory()", text)

    def test_verify_runs_the_footprint_self_test(self):
        text = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/footprint.py --self-test", text)

    def test_check_ram_reports_ok_warn_and_critical(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "meminfo"

        path.write_text("MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n", encoding="utf-8")
        self.assertEqual(healthcheck.check_ram(str(path))["status"], "ok")

        path.write_text("MemTotal: 2000000 kB\nMemAvailable: 350000 kB\n", encoding="utf-8")
        warn = healthcheck.check_ram(str(path))
        self.assertEqual(warn["status"], "warn")
        self.assertIn("low_memory", warn["action"])

        path.write_text("MemTotal: 2000000 kB\nMemAvailable: 100000 kB\n", encoding="utf-8")
        self.assertEqual(healthcheck.check_ram(str(path))["status"], "critical")

        missing = healthcheck.check_ram(str(Path(tmp.name) / "nope"))
        self.assertEqual(missing["status"], "info")

    def test_low_spec_doc_is_linked(self):
        for name in ("README.md", "README.it.md"):
            self.assertIn("docs/LOW_SPEC.md", (ROOT / name).read_text(encoding="utf-8"), name)


class AgentPromptTests(unittest.TestCase):
    def setUp(self):
        self.prompt = (ROOT / "AI_AGENT_PROMPT.md").read_text(encoding="utf-8")

    def test_prompt_contains_the_real_commands(self):
        for command in (
            "bash scripts/install.sh",
            "bash scripts/verify.sh",
            "bash scripts/coverage.sh",
            "scripts/footprint.py",
            "python3 setup.py",
            "python3 doctor.py --short",
        ):
            self.assertIn(command, self.prompt, command)

    def test_prompt_forbids_leaking_secrets_and_removing_guards(self):
        self.assertIn("mai stampare", self.prompt.lower())
        self.assertIn("non disattivare", self.prompt.lower())
        self.assertIn("nessun docker", self.prompt.lower())

    def test_prompt_is_linked_from_the_readmes_and_agents(self):
        for name in ("README.md", "README.it.md", "AGENTS.md"):
            self.assertIn("AI_AGENT_PROMPT.md", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
