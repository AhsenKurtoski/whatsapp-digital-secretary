#!/usr/bin/env python3
"""Verifica sul campo del profilo low_memory, su una macchina vera.

I test automatici girano in una sandbox: non possono dire se *questa* macchina
supporta i limiti di memoria di systemd, se un job e' gia' stato ucciso dall'OOM
killer o se lo swap basta. Questo script risponde a quelle domande leggendo lo
stato reale del sistema, senza modificare nulla.

Controlla:
  - profilo attivo (`low_memory`) e RAM totale;
  - cgroup v1/v2 e controller `memory` disponibile per la sessione utente;
  - limiti e consumo reale (`MemoryCurrent`, `MemoryPeak`) di ogni unit;
  - OOM kill e kill per memoria nei log degli ultimi giorni;
  - footprint reale rispetto al budget;
  - swap totale/usato e zram;
  - `vm.swappiness` (su 2 GB un valore alto fa thrash).

Uso:
    python3 scripts/lowmem_check.py
    python3 scripts/lowmem_check.py --json
    python3 scripts/lowmem_check.py --since "48 hours ago"
    python3 scripts/lowmem_check.py --budget-mb 1200
    python3 scripts/lowmem_check.py --self-test

Esce con 1 se almeno un controllo e' `fail`; `warn` non fa fallire.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import footprint  # noqa: E402

import config  # noqa: E402

CGROUP_ROOT = Path("/sys/fs/cgroup")
MEMINFO = "/proc/meminfo"
SWAPS = "/proc/swaps"
SWAPPINESS = "/proc/sys/vm/swappiness"
UNITS = (
    "openclaw-dashboard.service",
    "openclaw-gateway.service",
    "openclaw-watchdog.service",
    "openclaw-secretary-health.service",
    "openclaw-secretary-reminders.service",
    "openclaw-secretary-monitor.service",
    "openclaw-secretary-digest.service",
)
OOM_RE = re.compile(
    r"out of memory|oom[-_ ]?kill|killed process|memory cgroup out of memory|status=9/kill",
    re.I,
)
UNLIMITED = 2**62


def read_text(path: str | Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def cgroup_version(root: str | Path = CGROUP_ROOT) -> str:
    """`v2`, `v1` o `none` (systemd senza cgroup, es. alcuni container)."""
    root = Path(root)
    if (root / "cgroup.controllers").exists():
        return "v2"
    if (root / "memory").exists() or (root / "memory.kmem.limit_in_bytes").exists():
        return "v1"
    return "none"


def has_memory_controller(root: str | Path = CGROUP_ROOT, version: str | None = None) -> bool:
    root = Path(root)
    version = version or cgroup_version(root)
    if version == "v2":
        controllers = read_text(root / "cgroup.controllers").split()
        return "memory" in controllers
    if version == "v1":
        return (root / "memory").exists()
    return False


def parse_props(text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


def to_mb(value: object) -> float | None:
    """Byte systemd -> MB; None per '[not set]', 'infinity' e valori assurdi."""
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    if number <= 0 or number >= UNLIMITED:
        return None
    return round(number / (1024 * 1024), 1)


def unit_state(unit: str, run) -> dict:
    code, out = run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "MemoryMax",
            "-p",
            "MemoryHigh",
            "-p",
            "MemoryCurrent",
            "-p",
            "MemoryPeak",
            "-p",
            "NRestarts",
        ]
    )
    props = parse_props(out)
    return {
        "unit": unit,
        "installed": props.get("LoadState") == "loaded",
        "active": props.get("ActiveState") == "active",
        "memory_max_mb": to_mb(props.get("MemoryMax")),
        "memory_high_mb": to_mb(props.get("MemoryHigh")),
        "memory_current_mb": to_mb(props.get("MemoryCurrent")),
        "memory_peak_mb": to_mb(props.get("MemoryPeak")),
        "restarts": props.get("NRestarts", "0"),
        "error": None if code == 0 else out[:200],
    }


def oom_hits(unit: str, since: str, run) -> list[str]:
    code, out = run(
        [
            "journalctl",
            "--user",
            "-u",
            unit,
            "--since",
            since,
            "--no-pager",
            "-o",
            "cat",
        ],
        timeout=25,
    )
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if OOM_RE.search(line)][:5]


def swap_info(meminfo_path: str | Path = MEMINFO, swaps_path: str | Path = SWAPS) -> dict:
    memory = footprint.parse_meminfo(read_text(meminfo_path))
    swaps = [line for line in read_text(swaps_path).splitlines()[1:] if line.strip()]
    return {
        "total_mb": memory.get("swap_total_mb", 0),
        "used_mb": memory.get("swap_used_mb", 0),
        "devices": len(swaps),
        "zram_mb": zram_size_mb(),
    }


def zram_size_mb(root: str | Path = CGROUP_ROOT.parent / "block") -> float:
    total = 0
    try:
        devices = list(Path(root).glob("zram*"))
    except Exception:
        return 0.0
    for device in devices:
        try:
            size = int(read_text(device / "disksize", "0").strip() or 0)
        except ValueError:
            continue
        total += size
    return round(total / (1024 * 1024), 1)


def swappiness(path: str | Path = SWAPPINESS) -> int | None:
    try:
        return int(read_text(path).strip())
    except (TypeError, ValueError):
        return None


def _check(check_id: str, status: str, detail: str, hint: str = "") -> dict:
    return {"id": check_id, "status": status, "detail": detail, "hint": hint}


def unit_check(state: dict) -> dict:
    unit = state["unit"]
    if not state["installed"]:
        return _check(f"unit:{unit}", "skip", "unit non installata", "installa con bash scripts/install.sh")
    if not state["memory_max_mb"]:
        return _check(
            f"unit:{unit}",
            "fail",
            "nessun MemoryMax: il cgroup non e' limitato",
            "reinstalla le unit con bash scripts/install.sh",
        )
    used = state.get("memory_current_mb") or 0.0
    peak = state.get("memory_peak_mb") or used
    ratio = (peak / state["memory_max_mb"]) if state["memory_max_mb"] else 0.0
    if ratio >= 0.9:
        return _check(
            f"unit:{unit}",
            "fail",
            f"picco {peak:.0f} MB su {state['memory_max_mb']:.0f} MB ({ratio:.0%}): rischio OOM kill",
            "alza MemoryMax o riduci il carico; controlla i log con journalctl --user",
        )
    if ratio >= 0.7:
        return _check(
            f"unit:{unit}",
            "warn",
            f"picco {peak:.0f} MB su {state['memory_max_mb']:.0f} MB ({ratio:.0%})",
            "monitora: sei vicino al limite",
        )
    return _check(
        f"unit:{unit}",
        "ok",
        f"max {state['memory_max_mb']:.0f} MB, ora {used:.0f} MB, picco {peak:.0f} MB",
    )


def assess(
    *,
    ram_mb: int,
    profile: bool,
    cgroup: str,
    controller: bool,
    units: list[dict],
    ooms: dict[str, list[str]],
    footprint_report: dict,
    swap: dict,
    swappiness_value: int | None,
) -> list[dict]:
    checks: list[dict] = []
    checks.append(
        _check(
            "profile",
            "ok" if profile else ("warn" if ram_mb and ram_mb < 4096 else "ok"),
            f"low_memory={'on' if profile else 'off'}, RAM {ram_mb or '?'} MB",
            "su 2 GB conviene \"low_memory\": true in ~/.config/digital-secretary/config.json",
        )
    )
    if cgroup == "none":
        checks.append(
            _check(
                "cgroup",
                "fail",
                "cgroup non disponibile: i limiti systemd non possono funzionare",
                "verifica che systemd gestisca la sessione (WSL2: systemd=true in /etc/wsl.conf)",
            )
        )
    elif cgroup == "v1":
        checks.append(
            _check(
                "cgroup",
                "warn",
                "cgroup v1: MemoryHigh/Max possono essere ignorati",
                "meglio cgroup v2 (Ubuntu 22.04+); i limiti restano un extra, non una garanzia",
            )
        )
    elif not controller:
        checks.append(
            _check(
                "cgroup",
                "fail",
                "cgroup v2 senza controller 'memory' nella sessione utente",
                "abilita il controller memory (systemd.unified_cgroup_hierarchy=1)",
            )
        )
    else:
        checks.append(_check("cgroup", "ok", "cgroup v2 con controller memory"))

    checks.extend(unit_check(state) for state in units)

    for unit, hits in ooms.items():
        if hits:
            checks.append(
                _check(
                    f"oom:{unit}",
                    "fail",
                    f"{len(hits)} righe di OOM/kill nei log: {hits[0][:160]}",
                    "alza MemoryMax o indaga il processo; vedi docs/LOW_SPEC.md",
                )
            )
    if not any(ooms.values()):
        checks.append(_check("oom", "ok", "nessun OOM kill nei log controllati"))

    verdict = footprint_report.get("verdict", {})
    used = footprint_report.get("process_total_mb", 0)
    budget = footprint_report.get("budget_mb", 0)
    status = {"over": "fail", "near": "warn"}.get(verdict.get("status"), "ok")
    checks.append(
        _check(
            "footprint",
            status,
            f"{used:.0f} MB su budget {budget:.0f} MB: {verdict.get('detail', '')}",
            "python3 scripts/footprint.py per il dettaglio per processo",
        )
    )

    total_swap = swap.get("total_mb", 0)
    devices = swap.get("devices", 0)
    zram = swap.get("zram_mb", 0)
    if ram_mb and ram_mb < 4096 and total_swap < 512:
        checks.append(
            _check(
                "swap",
                "fail",
                f"RAM {ram_mb} MB ma solo {total_swap} MB di swap",
                "aggiungi zram o uno swapfile da 1-2 GB (docs/LOW_SPEC.md)",
            )
        )
    elif total_swap < 1024:
        checks.append(
            _check(
                "swap",
                "warn",
                f"swap {total_swap} MB ({devices} dispositivi, zram {zram} MB)",
                "con 2 GB conviene zram + swapfile",
            )
        )
    else:
        checks.append(
            _check(
                "swap",
                "ok",
                f"swap {total_swap} MB usato {swap.get('used_mb', 0)} MB, zram {zram} MB",
            )
        )

    if swappiness_value is None:
        checks.append(_check("swappiness", "skip", "vm.swappiness non leggibile"))
    elif swappiness_value <= 20:
        checks.append(_check("swappiness", "ok", f"vm.swappiness={swappiness_value}"))
    else:
        checks.append(
            _check(
                "swappiness",
                "warn",
                f"vm.swappiness={swappiness_value}",
                "su 2 GB imposta 10: sysctl -w vm.swappiness=10",
            )
        )
    return checks


def summarize(checks: list[dict]) -> dict:
    counts = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for item in checks:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def run_command(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def gather(since: str = "7 days ago", budget_mb: float = footprint.DEFAULT_BUDGET_MB) -> dict:
    ram = config.total_ram_mb()
    units = [unit_state(unit, run_command) for unit in UNITS]
    ooms = {
        state["unit"]: oom_hits(state["unit"], since, run_command)
        for state in units
        if state["installed"]
    }
    return assess(
        ram_mb=ram,
        profile=config.low_memory(),
        cgroup=cgroup_version(),
        controller=has_memory_controller(),
        units=units,
        ooms=ooms,
        footprint_report=footprint.report(budget_mb),
        swap=swap_info(),
        swappiness_value=swappiness(),
    )


def report(checks: list[dict], as_json: bool = False) -> int:
    counts = summarize(checks)
    if as_json:
        print(json.dumps({"checks": checks, "summary": counts}, ensure_ascii=False, indent=2))
    else:
        icons = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
        print("Verifica low_memory sulla macchina reale\n")
        for item in checks:
            print(f"[{icons.get(item['status'], '?')}] {item['id']}: {item['detail']}")
            if item.get("hint") and item["status"] in {"warn", "fail"}:
                print(f"       -> {item['hint']}")
        print(
            f"\n{counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail, "
            f"{counts['skip']} skip"
        )
    return 1 if counts["fail"] else 0


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert cgroup_version(root) == "none"
        (root / "cgroup.controllers").write_text("cpu io memory\n", encoding="utf-8")
        assert cgroup_version(root) == "v2"
        assert has_memory_controller(root)
        (root / "cgroup.controllers").write_text("cpu io\n", encoding="utf-8")
        assert not has_memory_controller(root)
        v1 = root / "v1"
        (v1 / "memory").mkdir(parents=True)
        assert cgroup_version(v1) == "v1"
        assert has_memory_controller(v1)

        meminfo = root / "meminfo"
        meminfo.write_text(
            "MemTotal:        2048000 kB\nMemAvailable:     800000 kB\n"
            "SwapTotal:       1048576 kB\nSwapFree:         524288 kB\n",
            encoding="utf-8",
        )
        assert footprint.parse_meminfo(meminfo.read_text())["swap_total_mb"] == 1024
        swaps = root / "swaps"
        swaps.write_text("Filename Type Size Used Priority\n/swapfile file 1048572 0 -2\n", encoding="utf-8")

    assert parse_props("MemoryMax=536870912\nActiveState=active\n") == {
        "MemoryMax": "536870912",
        "ActiveState": "active",
    }
    assert to_mb("536870912") == 512.0
    assert to_mb("infinity") is None
    assert to_mb(2**64 - 1) is None
    assert to_mb("0") is None

    hits = [line for line in ["Started dashboard", "Out of memory: Killed process 42"] if OOM_RE.search(line)]
    assert len(hits) == 1

    healthy = {
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
    assert unit_check(healthy)["status"] == "ok"
    tight = dict(healthy, memory_peak_mb=490.0)
    assert unit_check(tight)["status"] == "fail"
    missing = dict(healthy, memory_max_mb=None)
    assert unit_check(missing)["status"] == "fail"
    absent = dict(healthy, installed=False)
    assert unit_check(absent)["status"] == "skip"

    base = dict(
        ram_mb=2048,
        profile=True,
        cgroup="v2",
        controller=True,
        units=[healthy],
        ooms={"openclaw-dashboard.service": []},
        footprint_report={"process_total_mb": 300, "budget_mb": 1500, "verdict": {"status": "ok", "detail": "ok"}},
        swap={"total_mb": 2048, "used_mb": 100, "devices": 2, "zram_mb": 1024},
        swappiness_value=10,
    )
    checks = assess(**base)
    assert summarize(checks)["fail"] == 0
    assert summarize(checks)["warn"] == 0

    bad = dict(base, cgroup="none", controller=False, ooms={"openclaw-dashboard.service": ["oom-kill"]})
    bad_checks = assess(**bad)
    assert summarize(bad_checks)["fail"] >= 2
    assert any(item["id"].startswith("oom:") for item in bad_checks)

    no_swap = dict(base, swap={"total_mb": 0, "used_mb": 0, "devices": 0, "zram_mb": 0})
    assert any(item["status"] == "fail" for item in assess(**no_swap) if item["id"] == "swap")
    high_swap = dict(base, swappiness_value=60)
    assert any(item["status"] == "warn" for item in assess(**high_swap) if item["id"] == "swappiness")
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica il profilo low_memory sulla macchina reale")
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--since", default="7 days ago", help="finestra dei log (default: '7 days ago')")
    parser.add_argument(
        "--budget-mb", type=float, default=footprint.DEFAULT_BUDGET_MB, help="budget RAM dei processi"
    )
    parser.add_argument("--self-test", action="store_true", help="test interni senza toccare il sistema")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return report(gather(since=args.since, budget_mb=args.budget_mb), as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
