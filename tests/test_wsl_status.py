import subprocess
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from scripts import wsl_status


def _completed(stdout=b"", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, b"")


HEALTHY = {
    "wsl": True,
    "repo": "/home/u/whatsapp-digital-secretary",
    "installed": True,
    "config": True,
    "crypto": True,
    "openclaw": True,
    "node": True,
    "python": True,
    "gateway": "active",
    "tools": list(wsl_status.REQUIRED_TOOLS),
}


class ParseTests(unittest.TestCase):
    def test_reads_flags_paths_and_tools(self):
        state = wsl_status.parse_probe(
            "wsl=1\n"
            "repo=/home/u/whatsapp-digital-secretary\n"
            "installed=1\nconfig=0\ncrypto=1\nopenclaw=0\nnode=1\npython=1\n"
            "gateway=active\n"
            "tools=request_quote,request_email\n"
        )
        self.assertTrue(state["wsl"])
        self.assertEqual(state["repo"], "/home/u/whatsapp-digital-secretary")
        self.assertTrue(state["installed"])
        self.assertFalse(state["config"])
        self.assertFalse(state["openclaw"])
        self.assertEqual(state["gateway"], "active")
        self.assertEqual(state["tools"], ["request_quote", "request_email"])

    def test_ignores_noise_and_keeps_defaults(self):
        state = wsl_status.parse_probe("warning: niente bash\ngateway=\ntools=\n")
        self.assertEqual(state["gateway"], "unknown")
        self.assertEqual(state["tools"], [])
        self.assertFalse(state["installed"])

    def test_missing_tools_lists_only_what_the_bot_needs(self):
        state = {"tools": ["request_quote", "request_email"]}
        self.assertEqual(
            wsl_status.missing_tools(state), ["request_quote_resend", "request_calendar"]
        )


class DistroTests(unittest.TestCase):
    def test_returns_empty_off_windows(self):
        with mock.patch.object(wsl_status.os, "name", "posix"):
            self.assertEqual(wsl_status.windows_distros(), [])

    def test_parses_utf16_listing(self):
        payload = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")
        with mock.patch.object(wsl_status.os, "name", "nt"):
            names = wsl_status.windows_distros(runner=lambda *a, **k: _completed(payload))
        self.assertEqual(names, ["Ubuntu", "Debian"])

    def test_skips_header_lines(self):
        payload = "Windows Subsystem for Linux Distributions:\r\nUbuntu\r\n".encode("utf-16-le")
        with mock.patch.object(wsl_status.os, "name", "nt"):
            names = wsl_status.windows_distros(runner=lambda *a, **k: _completed(payload))
        self.assertEqual(names, ["Ubuntu"])

    def test_probe_distro_decodes_utf16_output(self):
        payload = "python=1\nrepo=\ninstalled=0\n".encode("utf-16-le")
        calls = []

        def fake_runner(command, **_kwargs):
            calls.append(command)
            return _completed(payload)

        with mock.patch.object(wsl_status.os, "name", "nt"):
            state = wsl_status.probe_distro("Ubuntu", runner=fake_runner)
        self.assertTrue(calls[0][:3] == ["wsl.exe", "-d", "Ubuntu"])
        self.assertTrue(state["python"])
        self.assertFalse(state["installed"])


class NextStepTests(unittest.TestCase):
    def test_nothing_to_do_when_everything_is_healthy(self):
        step = wsl_status.next_step(HEALTHY)
        self.assertEqual(step["where"], "wsl")
        self.assertTrue(step["command"].startswith("openclaw mcp probe"))
        self.assertIn("messaggio di prova", step["reason"])

    def test_missing_cryptography_points_at_the_package(self):
        step = wsl_status.next_step({**HEALTHY, "crypto": False})
        self.assertIn("python3-cryptography", step["command"])

    def test_missing_repo_points_at_clone(self):
        step = wsl_status.next_step({**HEALTHY, "repo": ""})
        self.assertTrue(step["command"].startswith("git clone"))

    def test_missing_setup_points_at_setup(self):
        step = wsl_status.next_step({**HEALTHY, "config": False, "installed": False})
        self.assertTrue(step["command"].endswith("python3 setup.py"))
        self.assertIn("cd /home/u/whatsapp-digital-secretary", step["command"])

    def test_missing_installation_points_at_installer(self):
        step = wsl_status.next_step({**HEALTHY, "installed": False, "gateway": "unknown"})
        self.assertTrue(step["command"].endswith("bash scripts/install.sh"))

    def test_inactive_gateway_points_at_restart(self):
        step = wsl_status.next_step({**HEALTHY, "gateway": "inactive"})
        self.assertIn("systemctl --user restart openclaw-gateway.service", step["command"])
        self.assertIn("inactive", step["reason"])

    def test_missing_request_quote_points_at_approval_setup(self):
        step = wsl_status.next_step({**HEALTHY, "tools": ["request_email", "request_calendar"]})
        self.assertIn("approval_setup.py", step["command"])
        self.assertIn("request_quote", step["reason"])

    def test_no_wsl_on_windows_points_at_install(self):
        with mock.patch.object(wsl_status.os, "name", "nt"):
            step = wsl_status.next_step({})
        self.assertEqual(step["command"], "wsl --install -d Ubuntu")
        self.assertEqual(step["where"], "powershell")


class RenderTests(unittest.TestCase):
    def test_report_ends_with_command_and_reason(self):
        step = wsl_status.next_step(HEALTHY)
        text = wsl_status.render(HEALTHY, "Ubuntu", step, ["Ubuntu"])
        self.assertIn("Gateway: active", text)
        self.assertIn("Tool del bot: request_quote", text)
        self.assertIn("Prossimo comando", text)
        self.assertIn(step["command"], text)

    def test_report_marks_missing_tools(self):
        state = {**HEALTHY, "tools": ["request_email"]}
        step = wsl_status.next_step(state)
        text = wsl_status.render(state, "", step, [])
        self.assertIn("mancano request_quote", text)


class LocalRepoTests(unittest.TestCase):
    def test_finds_repo_from_current_directory(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(wsl_status.local_repo(root / "tests"), str(root))

    def test_returns_empty_when_not_a_repo(self):
        self.assertEqual(wsl_status.local_repo(Path("/")), "")


class SelfTestTests(unittest.TestCase):
    def test_self_test_runs(self):
        wsl_status.self_test()


if __name__ == "__main__":
    unittest.main()
