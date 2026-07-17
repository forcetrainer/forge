"""Tests for scripts/forge_status.py — the run-state reader and renderers shared
by `forge-run.py --status` and the UserPromptSubmit hook.

Fixtures write a real run dir (run.json + per-task receipts) to a temp dir; the
reader/renderers are pure functions over those files.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = str(pathlib.Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import forge_status  # noqa: E402
from _forge_support import (  # noqa: E402
    MINIMAL_SPEC,
    PLAN_STD,
    SCRIPT_PATH,
    write_fake_codex,
)


def _plan(run_dir_for_capture=None):
    """A one-trivial-task plan. When run_dir_for_capture is given, task 1's
    acceptance copies the live run.json aside so a test can observe mid-run state."""
    acc = "true"
    if run_dir_for_capture:
        acc = "cp {}/run.json {}/captured.json".format(
            run_dir_for_capture, run_dir_for_capture
        )
    return (
        "# Fixture Plan\n\n**Goal:** Do the thing.\n\n"
        "### Task 1: First task\n- [ ] Done\n\n"
        "**Acceptance:** `{}`\n\n**Tier:** trivial — test fixture, mechanical\n\n**Depends on:** nothing\n".format(acc)
    )


def _run_cli(argv, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)] + argv,
        cwd=cwd, capture_output=True, text=True, env=env,
    )


def _write_run(run_dir, status, tasks, base_commit="abc123", contract_error=None, **extra):
    """Write a run.json with the given top-level status and task summaries.
    ``**extra`` merges additional top-level fields (e.g. deferrals, autofix_mode,
    doc_sync) so callers opt in without a separate writer."""
    os.makedirs(run_dir, exist_ok=True)
    data = {
        "plan": "/p/plan.md",
        "spec": "/p/spec.md",
        "status": status,
        "base_commit": base_commit,
        "tasks": tasks,
    }
    if contract_error is not None:
        data["contract_error"] = contract_error
    data.update(extra)
    with open(os.path.join(run_dir, "run.json"), "w") as f:
        json.dump(data, f)


def _write_receipt(run_dir, number, attempt, status, findings=None, halt_reason=None):
    os.makedirs(run_dir, exist_ok=True)
    receipt = {
        "task_number": number,
        "title": "Task {}".format(number),
        "tier": "standard",
        "attempt": attempt,
        "status": status,
        "outstanding_findings": findings or [],
    }
    if halt_reason is not None:
        receipt["halt_reason"] = halt_reason
    path = os.path.join(run_dir, "task-{}-attempt-{}.json".format(number, attempt))
    with open(path, "w") as f:
        json.dump(receipt, f)
    return path


def _write_final_review(run_dir, verdict="findings", findings=None, halt_reason=None):
    os.makedirs(run_dir, exist_ok=True)
    data = {"verdict": verdict, "findings": findings or []}
    if halt_reason is not None:
        data["halt_reason"] = halt_reason
    with open(os.path.join(run_dir, "final-review.json"), "w") as f:
        json.dump(data, f)


def _summary(number, status, attempts=1):
    return {
        "number": number,
        "title": "Task {}".format(number),
        "tier": "standard",
        "status": status,
        "attempts": attempts,
        "commit": None,
    }


class ReadRunStateTests(unittest.TestCase):
    def test_missing_dir_returns_none(self):
        self.assertIsNone(forge_status.read_run_state("/no/such/dir"))

    def test_empty_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(forge_status.read_run_state(d))

    def test_running_status_maps_to_running(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "running", [_summary(1, "passed")])
            self.assertEqual(forge_status.read_run_state(d)["state"], "running")

    def test_passed_status_maps_to_completed(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "passed", [_summary(1, "passed")])
            self.assertEqual(forge_status.read_run_state(d)["state"], "completed")

    def test_escalated_status_maps_to_halted(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated", [_summary(1, "passed"), _summary(2, "escalated")])
            self.assertEqual(forge_status.read_run_state(d)["state"], "halted")

    def test_final_review_escalation_maps_to_halted_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-final-review", [_summary(1, "passed")])
            state = forge_status.read_run_state(d)
            self.assertEqual(state["state"], "halted")
            self.assertIn("final review", state["reason"].lower())
            # No final-review.json in this fixture (backcompat / no-receipt case):
            # halt_class must tolerate absence, not crash.
            self.assertIsNone(state["halt_class"])

    def test_final_review_escalation_surfaces_halt_reason_class(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-final-review", [_summary(1, "passed")])
            _write_final_review(d, findings=["legacy bug"], halt_reason="scope-decision")
            state = forge_status.read_run_state(d)
            self.assertEqual(state["state"], "halted")
            self.assertEqual(state["halt_class"], "scope-decision")

    def test_contract_error_status_maps_with_message(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "contract-error", [], contract_error="malformed plan")
            state = forge_status.read_run_state(d)
            self.assertEqual(state["state"], "contract-error")
            self.assertIn("malformed plan", state["reason"])

    def test_receipts_only_infers_running(self):
        with tempfile.TemporaryDirectory() as d:
            _write_receipt(d, 1, 1, "passed")
            self.assertEqual(forge_status.read_run_state(d)["state"], "running")

    def test_escalated_task_reason_and_truncated_finding(self):
        with tempfile.TemporaryDirectory() as d:
            long_finding = "x" * 300
            _write_run(d, "escalated", [_summary(1, "passed"), _summary(2, "escalated")])
            _write_receipt(d, 2, 2, "escalated", findings=[long_finding])
            state = forge_status.read_run_state(d)
            self.assertIn("task 2", state["reason"].lower())
            t2 = next(t for t in state["tasks"] if t["number"] == 2)
            self.assertIsNotNone(t2["finding"])
            self.assertLessEqual(len(t2["finding"]), 110)

    def test_escalated_task_surfaces_halt_reason_class(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated", [_summary(1, "passed"), _summary(2, "escalated")])
            _write_receipt(d, 2, 2, "escalated", findings=["bad thing"], halt_reason="stuck")
            state = forge_status.read_run_state(d)
            self.assertEqual(state["halt_class"], "stuck")
            t2 = next(t for t in state["tasks"] if t["number"] == 2)
            self.assertEqual(t2["halt_reason"], "stuck")

    def test_surfaces_deferrals_from_run_json(self):
        with tempfile.TemporaryDirectory() as d:
            deferrals = [
                {"summary": "unused import", "location": {"file": "a.py", "lines": "1"}},
            ]
            _write_run(d, "passed", [_summary(1, "passed")], deferrals=deferrals)
            state = forge_status.read_run_state(d)
            self.assertEqual(state["deferrals"], deferrals)

    def test_backcompat_missing_scope_autonomy_fields(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "passed", [_summary(1, "passed")])
            state = forge_status.read_run_state(d)
            self.assertEqual(state["deferrals"], [])
            self.assertIsNone(state["autofix_mode"])
            self.assertIsNone(state["doc_sync"])
            self.assertIsNone(state["halt_class"])

    def test_doc_sync_halt_maps_to_halted_with_contradiction_reason(self):
        # The terminal doc-sync stage's own halt (escalated-doc-sync) is a third
        # terminal state --status must report; its cause is doc_sync.contradiction.
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-doc-sync", [_summary(1, "passed")],
                       doc_sync={"status": "halt",
                                 "contradiction": "README claims X, code does Y"})
            state = forge_status.read_run_state(d)
            self.assertEqual(state["state"], "halted")
            self.assertIn("README claims X", state["reason"])


class RenderStatusTests(unittest.TestCase):
    def _state(self, run_dir, status, tasks, **kw):
        _write_run(run_dir, status, tasks, **kw)
        return forge_status.read_run_state(run_dir)

    def test_headers_distinguish_states(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("RUNNING", forge_status.render_status(
                self._state(d, "running", [_summary(1, "passed")])))
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("COMPLETED", forge_status.render_status(
                self._state(d, "passed", [_summary(1, "passed")])))
        with tempfile.TemporaryDirectory() as d:
            out = forge_status.render_status(
                self._state(d, "escalated", [_summary(1, "escalated")]))
            self.assertIn("HALTED", out)
        with tempfile.TemporaryDirectory() as d:
            out = forge_status.render_status(
                self._state(d, "contract-error", [], contract_error="boom"))
            self.assertIn("CONTRACT-ERROR", out)
            self.assertIn("boom", out)

    def test_escalated_task_line_shows_finding(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated", [_summary(1, "escalated")])
            _write_receipt(d, 1, 2, "escalated", findings=["bad thing at foo.py:10"])
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("task 1", out)
            self.assertIn("bad thing at foo.py:10", out)

    def test_render_shows_doc_sync_contradiction(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-doc-sync", [_summary(1, "passed")],
                       doc_sync={"status": "halt",
                                 "contradiction": "README claims X, code does Y"})
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("HALTED", out)
            self.assertIn("README claims X", out)

    def test_render_shows_halt_reason_class(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated", [_summary(1, "escalated")])
            _write_receipt(d, 1, 1, "escalated", findings=["bad thing"],
                           halt_reason="scope-decision")
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("scope-decision", out)

    def test_render_shows_halt_reason_class_for_final_review(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-final-review", [_summary(1, "passed")])
            _write_final_review(d, findings=["legacy bug"], halt_reason="scope-decision")
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("HALTED", out)
            self.assertIn("scope-decision", out)

    def test_render_shows_deferrals_count_and_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            deferrals = [{"summary": "unused import"}, {"summary": "dead comment"}]
            _write_run(d, "passed", [_summary(1, "passed")], deferrals=deferrals)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("deferrals: 2", out)
            self.assertIn("unused import", out)
            self.assertIn("dead comment", out)

    def test_render_no_deferrals_line_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            out = forge_status.render_status(
                self._state(d, "passed", [_summary(1, "passed")]))
            self.assertNotIn("deferrals", out)


class StatusCliTests(unittest.TestCase):
    def _status(self, run_dir, codex_bin=None, env=None):
        argv = ["--status", "--run-dir", run_dir]
        if codex_bin:
            argv += ["--codex-bin", codex_bin]
        return _run_cli(argv, env=env)

    def test_completed_run_prints_completed_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            _write_run(rd, "passed", [_summary(1, "passed")])
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("COMPLETED", r.stdout)

    def test_halted_run_prints_halted_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            _write_run(rd, "escalated", [_summary(1, "passed"), _summary(2, "escalated")])
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("HALTED", r.stdout)
            self.assertIn("task 2", r.stdout.lower())

    def test_contract_error_run_prints_message(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            _write_run(rd, "contract-error", [], contract_error="malformed plan")
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("CONTRACT-ERROR", r.stdout)
            self.assertIn("malformed plan", r.stdout)

    def test_halted_run_with_deferrals_prints_halt_reason_and_deferrals(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            deferrals = [{"summary": "unused import"}]
            _write_run(rd, "escalated", [_summary(1, "passed"), _summary(2, "escalated")],
                       deferrals=deferrals)
            _write_receipt(rd, 2, 2, "escalated", findings=["bad thing"], halt_reason="stuck")
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("stuck", r.stdout)
            self.assertIn("deferrals: 1", r.stdout)
            self.assertIn("unused import", r.stdout)

    def test_status_without_scope_autonomy_fields_renders_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            _write_run(rd, "passed", [_summary(1, "passed")])
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("COMPLETED", r.stdout)
            self.assertNotIn("deferrals", r.stdout)

    def test_nonexistent_dir_prints_no_run_exit_0(self):
        r = self._status("/no/such/run/dir")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no run at", r.stdout.lower())

    def test_status_dispatches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            _write_run(rd, "passed", [_summary(1, "passed")])
            fake = write_fake_codex(d)
            log = os.path.join(d, "codex.log")
            env = os.environ.copy()
            env["FORGE_FAKE_LOG"] = log
            self._status(rd, codex_bin=fake, env=env)
            self.assertFalse(os.path.exists(log))


class IncrementalRunJsonTests(unittest.TestCase):
    """A non-git cwd skips the clean-tree precondition, so a trivial plan runs
    end-to-end via the fake codex without a repo."""

    def _run_plan(self, cwd, plan_text, run_dir, extra=None):
        plan_path = os.path.join(cwd, "plan.md")
        spec_path = os.path.join(cwd, "spec.md")
        with open(plan_path, "w") as f:
            f.write(plan_text)
        with open(spec_path, "w") as f:
            f.write(MINIMAL_SPEC)
        fake = write_fake_codex(cwd)
        argv = [plan_path, "--spec", spec_path, "--run-dir", run_dir,
                "--codex-bin", fake] + (extra or [])
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = os.path.join(cwd, "codex.log")
        return _run_cli(argv, cwd=cwd, env=env)

    def test_running_written_before_task_terminal_at_end(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            r = self._run_plan(d, _plan(run_dir_for_capture=rd), rd)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(rd, "captured.json")) as f:
                mid = json.load(f)
            self.assertEqual(mid["status"], "running")
            with open(os.path.join(rd, "run.json")) as f:
                final = json.load(f)
            self.assertEqual(final["status"], "passed")

    def test_progress_pointer_live_mid_run_then_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            r = self._run_plan(d, _plan(run_dir_for_capture=rd), rd)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(rd, "captured.json")) as f:
                mid = json.load(f)
            self.assertEqual(mid["current_task"], 1)
            self.assertEqual(mid["current_phase"], "acceptance")
            with open(os.path.join(rd, "run.json")) as f:
                final = json.load(f)
            self.assertNotIn("current_task", final)
            self.assertNotIn("current_phase", final)

    def test_contract_error_before_run_dir_writes_no_run_json(self):
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            # --effort references a task number the plan lacks -> contract error
            # raised before the run dir is created; spec says no run.json.
            r = self._run_plan(d, _plan(), rd, extra=["--effort", "99=high"])
            self.assertEqual(r.returncode, 1)
            self.assertFalse(os.path.exists(os.path.join(rd, "run.json")))

    def test_contract_error_after_run_dir_persists_marker(self):
        # A standard task reaches the reviewer; an unparseable verdict is a
        # contract error raised mid-loop, after the run dir exists -> marker.
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
            plan_path = os.path.join(d, "plan.md")
            spec_path = os.path.join(d, "spec.md")
            with open(plan_path, "w") as f:
                f.write(PLAN_STD)
            with open(spec_path, "w") as f:
                f.write(MINIMAL_SPEC)
            resp = os.path.join(d, "responses.json")
            with open(resp, "w") as f:
                # worker call (ok), then reviewer call returning non-JSON.
                json.dump([{"exit": 0, "msg": ""},
                           {"exit": 0, "msg": "totally not a verdict"}], f)
            fake = write_fake_codex(d)
            # Commit everything so the working tree is clean at run start (else the
            # clean-tree precondition trips before the run dir is created).
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
            rd = os.path.join(d, "run")
            env = os.environ.copy()
            env["FORGE_FAKE_LOG"] = os.path.join(d, "codex.log")
            env["FORGE_FAKE_RESPONSES"] = resp
            r = _run_cli([plan_path, "--spec", spec_path, "--run-dir", rd,
                          "--codex-bin", fake], cwd=d, env=env)
            self.assertEqual(r.returncode, 1, r.stderr)
            with open(os.path.join(rd, "run.json")) as f:
                data = json.load(f)
            self.assertEqual(data["status"], "contract-error")
            self.assertTrue(data.get("contract_error"))


class DirtyTreeNoRunJsonTests(unittest.TestCase):
    def test_dirty_tree_writes_no_run_json(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
            with open(os.path.join(d, "tracked.txt"), "w") as f:
                f.write("v1\n")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
            # make the tree dirty
            with open(os.path.join(d, "tracked.txt"), "w") as f:
                f.write("v2\n")
            plan_path = os.path.join(d, "plan.md")
            spec_path = os.path.join(d, "spec.md")
            with open(plan_path, "w") as f:
                f.write(_plan())
            with open(spec_path, "w") as f:
                f.write(MINIMAL_SPEC)
            rd = os.path.join(d, "run")
            r = _run_cli([plan_path, "--spec", spec_path, "--run-dir", rd,
                          "--codex-bin", write_fake_codex(d)], cwd=d)
            self.assertEqual(r.returncode, 1)
            self.assertFalse(os.path.exists(os.path.join(rd, "run.json")))


def _iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dead_pid():
    """A pid that has certainly exited (our own reaped child)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class ProgressFieldsTests(unittest.TestCase):
    def _write_ex(self, d, status, tasks, **extra):
        os.makedirs(d, exist_ok=True)
        data = {"plan": "/p/plan.md", "spec": "/p/spec.md", "status": status,
                "base_commit": "abc", "tasks": tasks}
        data.update(extra)
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump(data, f)

    def test_surfaces_current_task_phase_and_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "running", [_summary(1, "passed")],
                           current_task=2, current_phase="worker",
                           started_at="2026-07-15T09:00:00Z",
                           updated_at="2026-07-15T09:05:00Z")
            st = forge_status.read_run_state(d)
            self.assertEqual(st["current_task"], 2)
            self.assertEqual(st["current_phase"], "worker")
            self.assertEqual(st["started_at"], "2026-07-15T09:00:00Z")
            self.assertEqual(st["updated_at"], "2026-07-15T09:05:00Z")

    def test_backcompat_missing_fields_default(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "running", [_summary(1, "passed")])
            st = forge_status.read_run_state(d)
            self.assertIsNone(st["current_task"])
            self.assertIsNone(st["current_phase"])
            self.assertIsNone(st["started_at"])
            self.assertIsNone(st["updated_at"])
            self.assertFalse(st["stale"])

    def test_fresh_running_not_stale(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "running", [_summary(1, "passed")], updated_at=_iso_now())
            st = forge_status.read_run_state(d)
            self.assertFalse(st["stale"])

    def test_running_past_cutoff_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "running", [_summary(1, "passed")])
            st = forge_status.read_run_state(d, now=time.time() + 10000)
            self.assertTrue(st["stale"])

    def test_dead_pid_with_quiet_heartbeat_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "running", [_summary(1, "passed")], pid=_dead_pid())
            # heartbeat quiet (now far ahead) AND pid dead -> confirmed dead
            st = forge_status.read_run_state(d, now=time.time() + 10000)
            self.assertTrue(st["stale"])

    def test_live_pid_rescues_quiet_run(self):
        with tempfile.TemporaryDirectory() as d:
            # heartbeat quiet (now far ahead) but the runner pid is alive (ours) —
            # a long silent codex-exec phase must NOT read as stalled.
            self._write_ex(d, "running", [_summary(1, "passed")], pid=os.getpid())
            st = forge_status.read_run_state(d, now=time.time() + 10000)
            self.assertFalse(st["stale"])

    def test_terminal_states_never_stale(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "passed", [_summary(1, "passed")])
            st = forge_status.read_run_state(d, now=time.time() + 10000)
            self.assertFalse(st["stale"])

    def test_render_shows_stalled_for_stale_running(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_ex(d, "running", [_summary(1, "passed")])
            st = forge_status.read_run_state(d, now=time.time() + 10000)
            self.assertIn("STALLED?", forge_status.render_status(st))


if __name__ == "__main__":
    unittest.main()
