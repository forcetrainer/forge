"""forge-monitor.py — the read-only live TUI.

Render helpers are pure over a run-state dict + log lines; tests snapshot them
with rich's recording Console (styles stripped, so assertions are on text). The
Live loop itself is not exercised — only the render surface and the CLI's
resolve/error paths, which return before any Live is entered.

Requires `rich` (the monitor's one dependency); run under the project venv.
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

import pytest

pytest.importorskip("rich")  # the monitor's one dependency; its UI can't be tested without it

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MON_PATH = REPO_ROOT / "scripts" / "forge-monitor.py"
_spec = importlib.util.spec_from_file_location("forge_monitor", MON_PATH)
forge_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forge_monitor)

import forge_status  # scripts/ is on sys.path after loading the monitor
from rich.console import Console


def _base_tasks():
    return [
        {"number": 1, "title": "Parse plan", "tier": "standard", "status": "passed",
         "attempts": 1, "commit": "a", "started_at": "2026-07-15T09:00:00Z",
         "ended_at": "2026-07-15T09:00:41Z"},
        {"number": 2, "title": "Tee to disk", "tier": "complex", "status": "running",
         "attempts": 0, "commit": None, "started_at": "2026-07-15T09:00:41Z",
         "ended_at": None},
        {"number": 3, "title": "Write the docs", "tier": "trivial", "status": "queued",
         "attempts": 0, "commit": None, "started_at": None, "ended_at": None},
    ]


def _write_run(d, **over):
    data = {
        "plan": "/proj/2026-07-15-forge-run-monitor.md", "spec": "/proj/spec.md",
        "status": "running", "base_commit": "abc",
        "started_at": "2026-07-15T09:00:00Z", "pid": os.getpid(),
        "updated_at": "2026-07-15T09:00:50Z",
        "current_task": 2, "current_phase": "worker", "tasks": _base_tasks(),
    }
    data.update(over)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run.json"), "w") as f:
        json.dump(data, f)


def _render(state, log_lines=None, now=None, width=100):
    console = Console(record=True, width=width, force_terminal=False)
    console.print(forge_monitor._render(state, log_lines or [], now=now))
    return console.export_text(styles=False)


class HelperTests(unittest.TestCase):
    def test_latest_run_dir_picks_newest(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "runs")
            # Set explicit mtimes so "newest" is unambiguous (same-instant creates tie).
            for name, mtime in (("run-a", 100), ("run-c", 300), ("run-b", 200)):
                p = os.path.join(root, name)
                os.makedirs(p)
                os.utime(p, (mtime, mtime))
            self.assertEqual(
                os.path.basename(forge_monitor._latest_run_dir(root)), "run-c")

    def test_latest_run_dir_none_when_absent(self):
        self.assertIsNone(forge_monitor._latest_run_dir("/no/such/runs"))

    def test_tail_returns_last_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.log")
            with open(p, "w") as f:
                f.write("\n".join("line{}".format(i) for i in range(10)) + "\n")
            tail = forge_monitor._tail(p, 3)
            self.assertEqual([t.strip() for t in tail], ["line7", "line8", "line9"])

    def test_tail_missing_file_is_empty(self):
        self.assertEqual(forge_monitor._tail("/no/such.log", 5), [])


class RenderTests(unittest.TestCase):
    def test_running_marks_current_task_and_lists_roster(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d)
            out = _render(forge_status.read_run_state(d))
            # all planned tasks appear, including the queued one
            self.assertIn("Parse plan", out)
            self.assertIn("Tee to disk", out)
            self.assertIn("Write the docs", out)
            # the live panel names the in-flight task + phase
            self.assertIn("task 2", out)
            self.assertIn("worker", out)

    def test_live_panel_shows_tailed_output(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d)
            out = _render(forge_status.read_run_state(d),
                          log_lines=["some codex output", "SENTINEL_LINE_XYZ"])
            self.assertIn("SENTINEL_LINE_XYZ", out)

    def test_missing_live_log_shows_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d)
            out = _render(forge_status.read_run_state(d), log_lines=[])
            self.assertIn("waiting for output", out.lower())

    def test_completed_shows_complete_banner(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _base_tasks()
            for t in tasks:
                t["status"] = "passed"
            _write_run(d, status="passed", current_task=None, current_phase=None,
                       tasks=tasks)
            out = _render(forge_status.read_run_state(d))
            self.assertIn("RUN COMPLETE", out)
            self.assertIn("3/3", out)

    def test_halted_shows_halt_banner_with_finding(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _base_tasks()
            tasks[1]["status"] = "escalated"
            _write_run(d, status="escalated", current_task=None, current_phase=None,
                       tasks=tasks)
            # the escalated task's outstanding finding lives on its receipt
            with open(os.path.join(d, "task-2-attempt-2.json"), "w") as f:
                json.dump({"task_number": 2, "status": "escalated",
                           "outstanding_findings": ["tee drops reviewer stderr"]}, f)
            out = _render(forge_status.read_run_state(d))
            self.assertIn("HALTED", out)
            self.assertIn("task 2", out)
            self.assertIn("tee drops reviewer stderr", out)

    def test_contract_error_shows_banner(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, status="contract-error", current_task=None,
                       current_phase=None, contract_error="malformed plan")
            out = _render(forge_status.read_run_state(d))
            self.assertIn("CONTRACT ERROR", out)
            self.assertIn("malformed plan", out)

    def test_stalled_running_shows_stalled_and_no_banner(self):
        with tempfile.TemporaryDirectory() as d:
            # No live pid + a quiet heartbeat (now far ahead) = a dead runner.
            _write_run(d, pid=None)
            state = forge_status.read_run_state(d, now=__import__("time").time() + 10000)
            out = _render(state)
            self.assertIn("STALLED?", out)
            self.assertNotIn("RUN COMPLETE", out)
            self.assertNotIn("HALTED", out)

    def test_partial_run_json_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run.json"), "w") as f:
                f.write('{"status": "running"')  # truncated / invalid
            # read_run_state tolerates it (receipts fallback); render must not crash
            state = forge_status.read_run_state(d) or {
                "run_dir": d, "plan": None, "state": "running", "reason": None,
                "current_task": None, "current_phase": None, "started_at": None,
                "updated_at": None, "stale": False, "tasks": [],
            }
            _render(state)  # no exception


class TerminalAndBannerTests(unittest.TestCase):
    def test_stale_running_is_not_terminal(self):
        # A stale running run must NOT end the watch — the cutoff can trip on a
        # long quiet-but-healthy phase; exiting would abandon a live run.
        self.assertFalse(forge_monitor._is_terminal({"state": "running", "stale": True}))
        for s in ("completed", "halted", "contract-error"):
            self.assertTrue(forge_monitor._is_terminal({"state": s, "stale": False}))

    def test_completed_without_review_omits_review_clean(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _base_tasks()
            for t in tasks:
                t["status"] = "passed"
            _write_run(d, status="passed", current_task=None, current_phase=None, tasks=tasks)
            out = _render(forge_status.read_run_state(d))
            self.assertIn("RUN COMPLETE", out)
            self.assertNotIn("review clean", out)

    def test_completed_with_passing_review_shows_review_clean(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _base_tasks()
            for t in tasks:
                t["status"] = "passed"
            _write_run(d, status="passed", current_task=None, current_phase=None, tasks=tasks)
            with open(os.path.join(d, "final-review.json"), "w") as f:
                json.dump({"verdict": "pass"}, f)
            out = _render(forge_status.read_run_state(d))
            self.assertIn("review clean", out)

    def test_final_review_halt_surfaces_verdict_finding(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = _base_tasks()
            for t in tasks:
                t["status"] = "passed"
            _write_run(d, status="escalated-final-review", current_task=None,
                       current_phase=None, tasks=tasks)
            with open(os.path.join(d, "final-review.json"), "w") as f:
                json.dump({"verdict": "findings",
                           "findings": ["integration: elapsed drops on resume"]}, f)
            out = _render(forge_status.read_run_state(d))
            self.assertIn("HALTED", out)
            self.assertIn("final review", out.lower())
            self.assertIn("elapsed drops on resume", out)

    def test_interrupted_task_not_spun_under_terminal_state(self):
        with tempfile.TemporaryDirectory() as d:
            # task 2 is mid-flight ("running") when a contract error strikes
            _write_run(d, status="contract-error", current_task=None,
                       current_phase=None, contract_error="reviewer crashed")
            out = _render(forge_status.read_run_state(d))
            self.assertIn("CONTRACT ERROR", out)
            self.assertIn("interrupted", out)


class CliTests(unittest.TestCase):
    def test_missing_run_dir_prints_and_exits_nonzero(self):
        code = forge_monitor.main(["--run-dir", "/no/such/run"])
        self.assertEqual(code, 1)

    def test_run_dir_and_latest_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            forge_monitor.main(["--run-dir", "x", "--latest"])

    def test_requires_a_target(self):
        with self.assertRaises(SystemExit):
            forge_monitor.main([])


if __name__ == "__main__":
    unittest.main()
