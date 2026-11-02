# SPDX-License-Identifier: Apache-2.0
"""The load test (wave 14, Q6) keeps working: a small installation from `bench/load_setup.py`, and a few seconds of
`bench/load.py` against `pinax serve` started from it, with every role signing in and opening pages without an error."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from conftest import sqlite_only

BENCH = Path(__file__).parents[1] / "bench"


def _load_module():
    spec = importlib.util.spec_from_file_location("load", BENCH / "load.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["load"] = module
    spec.loader.exec_module(module)
    return module


@sqlite_only  # the installation is SQLite in its own folder; on PostgreSQL it would need a database of its own
def test_a_short_load_test(tmp_path):
    folder = tmp_path / "load"
    done = subprocess.run([sys.executable, str(BENCH / "load_setup.py"), str(folder), "--files", "5000", "--labs", "5"],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    spec = json.loads((folder / "people.json").read_text())
    assert {p["role"] for p in spec["people"]} == {"pi", "member", "admin", "auditor", "finance", "division"}
    assert all(lab["projects"] for lab in spec["labs"])

    load = _load_module()
    mix = {"pi": 2, "member": 1, "division": 1, "admin": 1, "auditor": 1, "finance": 1}
    report, path = load.run(folder, people=7, seconds=4, ramp=1, think=(0.05, 0.2), mix=mix, name="short",
                            searchers=2, settings={"PINAX_WIDE_QUERIES": "1"})
    assert path.exists() and (folder / "reports" / "short.json").exists()
    assert report["settings"]["roles"] == mix and report["settings"]["server_settings"] == {"PINAX_WIDE_QUERIES": "1"}
    assert report["totals"]["pages"] > 20 and report["totals"]["errors"] == 0, report["errors"]
    assert {"Age map", "Chargeback", "All data"} <= set(report["pages"])
    assert report["memory_mb"]["peak"] > 0
    assert report["budgets"]["no errors"] is True
    assert report["searches"]["finished"]["n"] >= 2, report["searches"]
    assert report["budgets"]["whole-filesystem searches finish, or stop with Pinax's explanation"] is True
    assert "administrators searching whole filesystems" in path.read_text()
    ok, text = load.compare(report, report)
    assert ok and "They agree" in text
