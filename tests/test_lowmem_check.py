"""Verifica sul campo del profilo low_memory (scripts/lowmem_check.py)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from scripts import lowmem_check

ROOT = Path(__file__).resolve().parents[1]


def healthy_unit(**overrides):
    state = {
        "unit": "openclaw-dashboard.service",
        "installed": True,
        "active": True,
        "memory_max_mb": 512.0,
        "memory_high_mb": 384.0,
        "memory_current_mb": 32.0,
        "memory_peak_mb": 60.0,
        "restarts": "0",
        "error": None,
    }
    state.update(overrides)
    return state


def base_assess(**overrides):
    args = dict(
        ram_mb=2048,
        profile=True,
        cgroup="v2",
        controller=True,
        units=[healthy_unit()],
        ooms={"openclaw-dashboard.service": []},
        footprint_report={
            "process_total_mb": 300,
            "budget_mb": 1500,
            "verdict": {"status": "ok", "detail": "300 MB su un budget di 1500 MB"},
        },
        swap={"total_mb": 2048, "used_mb": 100, "devices": 2, "zram_mb": 1024},
        swappiness_value=10,
    )
    args.update(overrides)
    return args


class CgroupTests(unittest.TestCase):
    def test_detects_none_v1_and_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(lowmem_check.cgroup_version(root), "none")
            v1 = root / "v1"
            (v1 / "memory").mkdir(parents=True)
            self.assertEqual(lowmem_check.cgroup_version(v1), "v1")
            self.assertTrue(lowmem_check.has_memory_controller(v1))
            (root / "cgroup.controllers").write_text("cpu io memory\n", encoding="utf-8")
            self.assertEqual(lowmem_check.cgroup_version(root), "v2")
            self.assertTrue(lowmem_check.has_memory_controller(root))
            (root / "cgroup.controllers").write_text("cpu io\n", encoding="utf-8")
            self.assertFalse(lowmem_check.has_memory_controller(root))

    def test_missing_files_are_not_crashes(self):
        self.assertEqual(lowmem_check.cgroup_version(Path("/nonexistent/cgroup")), "none")
        self.assertFalse(lowmem_check.has_memory_controller(Path("/nonexistent/cgroup")))


class ParsingTests(unittest.TestCase):
    def test_props_and_bytes_to_mb(self):
        props = lowmem_check.parse_props("MemoryMax=536870912\nActiveState=active\n")
        self.assertEqual(props["MemoryMax"], "536870912")
        self.assertEqual(lowmem_check.to_mb("536870912"), 512.0)
        for unlimited in ("infinity", "18446744073709551615", "0", "n/a", None):
            self.assertIsNone(lowmem_check.to_mb(unlimited), unlimited)

    def test_swap_and_zram_are_read_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meminfo = root / "meminfo"
            meminfo.write_text(
                "MemTotal: 2048000 kB\nMemAvailable: 800000 kB\n"
                "SwapTotal: 1048576 kB\nSwapFree: 524288 kB\n",
                encoding="utf-8",
            )
            swaps = root / "swaps"
            swaps.write_text("Filename Type Size Used Priority\n/swapfile file 1 0 -2\n", encoding="utf-8")
            block = root / "block"
            zram = block / "zram0"
            zram.mkdir(parents=True)
            (zram / "disksize").write_text(str(512 * 1024 * 1024), encoding="utf-8")
            info = lowmem_check.swap_info(meminfo, swaps)
            self.assertEqual(info["total_mb"], 1024)
            self.assertEqual(info["used_mb"], 512)
            self.assertEqual(info["devices"], 1)
            with mock.patch.object(lowmem_check, "zram_size_mb", return_value=512.0):
                info = lowmem_check.swap_info(meminfo, swaps)
            self.assertEqual(info["zram_mb"], 512.0)
            self.assertEqual(lowmem_check.zram_size_mb(block), 512.0)

    def test_swappiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "swappiness"
            path.write_text("10\n", encoding="utf-8")
            self.assertEqual(lowmem_check.swappiness(path), 10)
        self.assertIsNone(lowmem_check.swappiness("/nonexistent/swappiness"))


class UnitTests(unittest.TestCase):
    def test_healthy_unit_is_ok(self):
        self.assertEqual(lowmem_check.unit_check(healthy_unit())["status"], "ok")

    def test_near_limit_warns_and_above_ninety_fails(self):
        self.assertEqual(lowmem_check.unit_check(healthy_unit(memory_peak_mb=400.0))["status"], "warn")
        self.assertEqual(lowmem_check.unit_check(healthy_unit(memory_peak_mb=500.0))["status"], "fail")

    def test_missing_cap_is_a_failure_but_absent_unit_is_skipped(self):
        self.assertEqual(lowmem_check.unit_check(healthy_unit(memory_max_mb=None))["status"], "fail")
        self.assertEqual(lowmem_check.unit_check(healthy_unit(installed=False))["status"], "skip")

    def test_oom_lines_are_matched(self):
        text = "Started dashboard\nOut of memory: Killed process 42 (node)\nstatus=9/KILL"
        hits = [line for line in text.splitlines() if lowmem_check.OOM_RE.search(line)]
        self.assertEqual(len(hits), 2)

    def test_unit_state_parses_systemctl_output(self):
        out = (
            "LoadState=loaded\nActiveState=active\nMemoryMax=536870912\n"
            "MemoryHigh=402653184\nMemoryCurrent=33554432\nMemoryPeak=62914560\nNRestarts=0\n"
        )
        state = lowmem_check.unit_state("x.service", lambda cmd: (0, out))
        self.assertTrue(state["installed"])
        self.assertEqual(state["memory_max_mb"], 512.0)
        self.assertEqual(state["memory_current_mb"], 32.0)

    def test_missing_systemctl_is_reported_as_not_installed(self):
        state = lowmem_check.unit_state("x.service", lambda cmd: (127, "systemctl: not found"))
        self.assertFalse(state["installed"])
        self.assertIn("not found", state["error"])


class AssessTests(unittest.TestCase):
    def test_healthy_machine_has_no_failures(self):
        checks = lowmem_check.assess(**base_assess())
        self.assertEqual(lowmem_check.summarize(checks)["fail"], 0)
        self.assertEqual(lowmem_check.summarize(checks)["warn"], 0)

    def test_low_ram_without_profile_and_swap_fails(self):
        checks = lowmem_check.assess(
            **base_assess(
                profile=False,
                swap={"total_mb": 0, "used_mb": 0, "devices": 0, "zram_mb": 0},
            )
        )
        statuses = {item["id"]: item["status"] for item in checks}
        self.assertEqual(statuses["swap"], "fail")
        self.assertEqual(statuses["profile"], "warn")
        self.assertEqual(lowmem_check.summarize(checks)["fail"], 1)

    def test_cgroup_and_oom_problems_fail(self):
        checks = lowmem_check.assess(
            **base_assess(
                cgroup="none",
                controller=False,
                ooms={"openclaw-secretary-health.service": ["oom-kill"]},
            )
        )
        statuses = {item["id"]: item["status"] for item in checks}
        self.assertEqual(statuses["cgroup"], "fail")
        self.assertEqual(statuses["oom:openclaw-secretary-health.service"], "fail")

    def test_cgroup_v1_without_controller_is_a_warning(self):
        checks = lowmem_check.assess(**base_assess(cgroup="v1", controller=False))
        self.assertEqual({item["id"]: item["status"] for item in checks}["cgroup"], "warn")

    def test_footprint_over_budget_fails_and_near_warns(self):
        over = base_assess(
            footprint_report={
                "process_total_mb": 1700,
                "budget_mb": 1500,
                "verdict": {"status": "over", "detail": "oltre"},
            }
        )
        self.assertEqual(
            {item["id"]: item["status"] for item in lowmem_check.assess(**over)}["footprint"], "fail"
        )
        near = base_assess(
            footprint_report={
                "process_total_mb": 1300,
                "budget_mb": 1500,
                "verdict": {"status": "near", "detail": "vicino"},
            }
        )
        self.assertEqual(
            {item["id"]: item["status"] for item in lowmem_check.assess(**near)}["footprint"], "warn"
        )

    def test_high_swappiness_warns(self):
        checks = lowmem_check.assess(**base_assess(swappiness_value=60))
        self.assertEqual(
            {item["id"]: item["status"] for item in checks}["swappiness"], "warn"
        )


class ReportTests(unittest.TestCase):
    def test_fail_exits_one_and_json_is_valid(self):
        checks = lowmem_check.assess(
            **base_assess(cgroup="none", controller=False)
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = lowmem_check.report(checks, as_json=True)
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertGreaterEqual(payload["summary"]["fail"], 1)
        self.assertTrue(payload["checks"])

    def test_healthy_exits_zero_text(self):
        checks = lowmem_check.assess(**base_assess())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(lowmem_check.report(checks), 0)
        self.assertIn("Verifica low_memory", out.getvalue())

    def test_self_test(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            lowmem_check.self_test()
        self.assertIn("SELF-TEST OK", out.getvalue())


class DocumentationTests(unittest.TestCase):
    def test_script_runs_in_verify_and_is_documented(self):
        verify = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn("lowmem_check.py --self-test", verify)
        spec = (ROOT / "docs" / "LOW_SPEC.md").read_text(encoding="utf-8")
        self.assertIn("scripts/lowmem_check.py", spec)
        for name in ("README.md", "README.it.md"):
            self.assertIn("lowmem_check.py", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
