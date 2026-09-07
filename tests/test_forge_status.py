"""Tests for scripts/forge_status.py — the run-state reader and renderers shared
by `forge-run.py --status` and the UserPromptSubmit hook.

Fixtures write a real run dir (run.json + per-task receipts) to a temp dir; the
reader/renderers are pure functions over those files.
"""
import datetime
import json
import os
import pathlib
import shlex
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


class IsTerminalTests(unittest.TestCase):
    """``is_terminal`` is the single public definition of the run.json
    status vocabulary's terminal/non-terminal split — implemented directly
    against ``_STATE_MAP`` so a caller outside this module (forge_memory's
    ``defer --run``) never needs its own copy of that vocabulary."""

    def test_running_is_not_terminal(self):
        self.assertFalse(forge_status.is_terminal("running"))

    def test_unrecognized_status_is_not_terminal(self):
        # Matches _STATE_MAP.get(raw, "running")'s existing default: an
        # unrecognized status is treated as still running, never terminal.
        self.assertFalse(forge_status.is_terminal("paused"))
        self.assertFalse(forge_status.is_terminal(None))

    def test_every_known_terminal_status_is_terminal(self):
        for status, external in forge_status._STATE_MAP.items():
            if status == "running":
                continue
            self.assertTrue(
                forge_status.is_terminal(status),
                "{!r} (-> {!r}) should be terminal".format(status, external),
            )


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

    def test_stale_queued_summary_overlaid_by_passed_receipt(self):
        # A resume where every remaining task was already `passed` can leave
        # run.json's tasks stamped `queued` from the seed write until the run's
        # terminal write — the receipt is the fresher truth in that window.
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "running", [_summary(1, "queued", attempts=0)])
            _write_receipt(d, 1, 2, "passed")
            task = forge_status.read_run_state(d)["tasks"][0]
            self.assertEqual(task["status"], "passed")
            self.assertEqual(task["attempts"], 2)

    def test_queued_summary_without_matching_receipt_stays_queued(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "running", [_summary(1, "queued", attempts=0)])
            task = forge_status.read_run_state(d)["tasks"][0]
            self.assertEqual(task["status"], "queued")

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

    def test_render_reports_a_halted_run_as_resumable_with_finding_ids(self):
        # Halt resolution spec: a frozen `scope-decision` halt is resumable —
        # `--status` must say so and name the outstanding finding ids, since
        # those ids are what the human passes back as `--resolve <id>=...`.
        with tempfile.TemporaryDirectory() as d:
            halt = {
                "task": 2,
                "attempt": 1,
                "freeze_commit": "a" * 40,
                "freeze_base": "b" * 40,
                "convergence_state": {"resolved_ids": [], "carried_ids": [],
                                      "prev_acceptance_ok": True},
                "halt_reason": "scope-decision",
                "findings": [{"id": "h1", "summary": "legacy guard is wrong"},
                             {"id": "h2", "summary": "second scope call"}],
                "repair_task": {"title": "Fix the legacy guard"},
                "approved": {},
            }
            _write_run(d, "escalated", [_summary(1, "passed"), _summary(2, "escalated")],
                       halt=halt)
            _write_receipt(d, 2, 1, "escalated", findings=["legacy guard is wrong"],
                           halt_reason="scope-decision")
            state = forge_status.read_run_state(d)
            self.assertEqual(state["halt"], halt)
            out = forge_status.render_status(state)
            self.assertIn("resumable", out)
            self.assertIn("h1", out)
            self.assertIn("h2", out)
            self.assertIn("--resolve", out)

    def test_render_halt_splits_outstanding_on_the_canonical_id(self):
        # A re-issued finding is answered by its CANONICAL id (`carried_from`)
        # — the identity run_plan's `--resolve` validation keys on. Splitting
        # on the raw id would show h1 as still outstanding and print a
        # `--resolve h1b=...` command run_plan rejects as unknown.
        with tempfile.TemporaryDirectory() as d:
            halt = {
                "task": 1,
                "attempt": 2,
                "freeze_commit": "a" * 40,
                "freeze_base": "b" * 40,
                "convergence_state": {},
                "halt_reason": "scope-decision",
                "findings": [
                    {"id": "h1b", "carried_from": "h1", "summary": "re-issued"},
                    {"id": "h9", "summary": "still open"},
                ],
                "approved": {"h1": "repair"},
            }
            _write_run(d, "escalated", [_summary(1, "escalated")], halt=halt)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("outstanding findings: h9", out)
            self.assertIn("--resolve h9=repair|defer", out)
            self.assertNotIn("h1b", out)
            self.assertIn("already resolved: h1=repair", out)

    def test_render_halt_names_the_stage_for_a_stage_freeze(self):
        # A final-review / doc-sync halt freezes its uncommitted edits under a
        # stage-keyed record (no `task`). Rendering it through the per-task
        # wording would print "halted task None" and offer a `--resolve` the
        # stage never accepts.
        with tempfile.TemporaryDirectory() as d:
            halt = {
                "stage": "final-review",
                "freeze_commit": "a" * 40,
                "freeze_base": "b" * 40,
                "halt_reason": "scope-decision",
            }
            _write_run(d, "escalated-final-review", [_summary(1, "passed")],
                       halt=halt)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("final-review", out)
            self.assertIn("a" * 40, out)
            self.assertNotIn("task None", out)
            self.assertNotIn("--resolve", out)

    def test_render_shows_halt_reason_class_for_final_review(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "escalated-final-review", [_summary(1, "passed")])
            _write_final_review(d, findings=["legacy bug"], halt_reason="scope-decision")
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("HALTED", out)
            self.assertIn("scope-decision", out)

    def test_render_shows_deferrals_count_and_summaries_on_running_run(self):
        # Terse one-liner is for a run still in progress — a terminal run
        # gets the full staged-deferrals block instead (see the two tests
        # below), never both.
        with tempfile.TemporaryDirectory() as d:
            deferrals = [{"summary": "unused import"}, {"summary": "dead comment"}]
            _write_run(d, "running", [_summary(1, "passed")], deferrals=deferrals)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("deferrals: 2", out)
            self.assertIn("unused import", out)
            self.assertIn("dead comment", out)

    def test_terminal_run_shows_full_block_not_terse_line(self):
        # A terminal run must show each deferral exactly once — the full
        # staged block, never the terse "deferrals: N — ..." prefix too
        # (that would restate every summary twice, in two truncations).
        with tempfile.TemporaryDirectory() as d:
            deferrals = [
                {"id": "f1", "summary": "unused import"},
                {"id": "f2", "summary": "dead comment"},
            ]
            _write_run(d, "passed", [_summary(1, "passed")], deferrals=deferrals)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertNotIn("deferrals: 2", out)
            self.assertEqual(out.count("unused import"), 1)
            self.assertEqual(out.count("dead comment"), 1)
            self.assertIn("forge_memory.py defer", out)

    def test_running_run_unaffected_by_terminal_change(self):
        with tempfile.TemporaryDirectory() as d:
            deferrals = [{"summary": "unused import"}]
            _write_run(d, "running", [_summary(1, "passed")], deferrals=deferrals)
            out = forge_status.render_status(forge_status.read_run_state(d))
            self.assertIn("deferrals: 1 — unused import", out)
            self.assertNotIn("forge_memory.py defer", out)

    def test_render_no_deferrals_line_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            out = forge_status.render_status(
                self._state(d, "passed", [_summary(1, "passed")]))
            self.assertNotIn("deferrals", out)


class RenderStagedDeferralsTests(unittest.TestCase):
    """``render_staged_deferrals`` — the close-out review surface. It must never
    fabricate a ``--title``/``--why``: a reviewer finding summary runs
    100-200 chars against the deferral schema's 80-char title budget, and this
    module has no way to author prose within budget, so it emits a runnable
    ``forge_memory.py defer`` command template with those two flags left as
    visible placeholders, plus the full untruncated summary above it so a
    human or LLM at the review gate has the material to write from."""

    def _run_json(self, d, deferrals):
        path = os.path.join(d, "run.json")
        with open(path, "w") as f:
            json.dump({"deferrals": deferrals}, f)
        return path

    def test_no_deferrals_renders_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [])
            state = forge_status.read_run_state(d)
            self.assertEqual(forge_status.render_staged_deferrals(state, run_path), [])

    def test_no_deferrals_leaves_status_output_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _write_run(d, "passed", [_summary(1, "passed")])
            state = forge_status.read_run_state(d)
            before = forge_status.render_status(state)
            run_path = os.path.join(d, "run.json")
            forge_status.render_staged_deferrals(state, run_path)
            after = forge_status.render_status(state)
            self.assertEqual(before, after)

    def test_staged_deferral_renders_runnable_defer_command(self):
        with tempfile.TemporaryDirectory() as d:
            summary = "x" * 150
            run_path = self._run_json(d, [{"id": "f1", "summary": summary}])
            state = forge_status.read_run_state(d)
            lines = forge_status.render_staged_deferrals(state, run_path)
            text = "\n".join(lines)
            self.assertIn("f1", text)
            self.assertIn(summary, text)
            self.assertIn("forge_memory.py defer", text)
            # The runner stages agent-found findings, so the emitted
            # template carries the origin the filing will label the issue
            # with. `--follow-up` is retired along with the field.
            self.assertIn("--by agent", text)
            self.assertNotIn("--follow-up", text)
            self.assertIn("--run {}".format(shlex.quote(run_path)), text)
            self.assertIn("--finding-id {}".format(shlex.quote("f1")), text)

    def test_command_uses_title_and_why_placeholders_not_fabrications(self):
        with tempfile.TemporaryDirectory() as d:
            summary = "y" * 120
            run_path = self._run_json(d, [{"id": "f1", "summary": summary}])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            self.assertIn("--title", text)
            self.assertIn("--why", text)
            # The summary itself (fabrication material) must never land
            # inside the --title/--why flag values of the emitted command.
            command_lines = [l for l in text.splitlines() if "forge_memory.py defer" in l]
            self.assertTrue(command_lines)
            for line in command_lines:
                self.assertNotIn(summary, line)

    def test_filed_entry_renders_filed_with_issue_number_and_no_command(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(
                d, [{"id": "f1", "summary": "already filed thing", "issue": "42"}]
            )
            state = forge_status.read_run_state(d)
            lines = forge_status.render_staged_deferrals(state, run_path)
            text = "\n".join(lines)
            self.assertIn("f1", text)
            self.assertIn("42", text)
            self.assertNotIn("forge_memory.py defer", text)

    def test_mixed_run_renders_command_only_for_unfiled(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [
                {"id": "already-done", "summary": "already done", "issue": "7"},
                {"id": "still-pending", "summary": "still pending"},
            ])
            state = forge_status.read_run_state(d)
            lines = forge_status.render_staged_deferrals(state, run_path)
            text = "\n".join(lines)
            defer_commands = [l for l in text.splitlines() if "forge_memory.py defer" in l]
            self.assertEqual(len(defer_commands), 1)
            self.assertIn("still-pending", defer_commands[0])
            self.assertNotIn("already-done", defer_commands[0])

    def test_issue_key_presence_detected_regardless_of_looking_numeric(self):
        # Task 2 pins ``issue`` as a string deliberately (FileStore refs are
        # not numeric) — detection must be presence-of-key, never a type or
        # numeric check.
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(
                d, [{"id": "f1", "summary": "a file-store backed ref", "issue": "deferral-f1-slug"}]
            )
            state = forge_status.read_run_state(d)
            lines = forge_status.render_staged_deferrals(state, run_path)
            text = "\n".join(lines)
            self.assertIn("deferral-f1-slug", text)
            self.assertNotIn("forge_memory.py defer", text)

    def test_emitted_values_with_spaces_quotes_newlines_are_quoted(self):
        with tempfile.TemporaryDirectory() as d:
            tricky_dir = os.path.join(d, "has space's \"quote\"")
            os.makedirs(tricky_dir)
            run_path = self._run_json(
                tricky_dir, [{"id": "weird id\nwith newline", "summary": "z" * 110}]
            )
            state = forge_status.read_run_state(tricky_dir)
            lines = forge_status.render_staged_deferrals(state, run_path)
            text = "\n".join(lines)
            self.assertIn("forge_memory.py defer", text)
            self.assertIn(shlex.quote(run_path), text)
            self.assertIn(shlex.quote("weird id\nwith newline"), text)

    def test_rendering_makes_no_gh_call(self):
        with tempfile.TemporaryDirectory() as d:
            stub_dir = os.path.join(d, "stub-bin")
            os.makedirs(stub_dir)
            marker = os.path.join(d, "gh-was-called")
            gh_stub = os.path.join(stub_dir, "gh")
            with open(gh_stub, "w") as f:
                f.write("#!/bin/sh\ntouch \"{}\"\nexit 1\n".format(marker))
            os.chmod(gh_stub, 0o755)

            run_path = self._run_json(d, [{"id": "f1", "summary": "z" * 110}])
            state = forge_status.read_run_state(d)

            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = stub_dir + os.pathsep + old_path
            try:
                forge_status.render_staged_deferrals(state, run_path)
            finally:
                os.environ["PATH"] = old_path
            self.assertFalse(
                os.path.exists(marker), "render_staged_deferrals must never invoke gh"
            )

    def test_forge_run_clean_run_invokes_gh_zero_times(self):
        with tempfile.TemporaryDirectory() as d:
            stub_dir = os.path.join(d, "stub-bin")
            os.makedirs(stub_dir)
            marker = os.path.join(d, "gh-was-called")
            gh_stub = os.path.join(stub_dir, "gh")
            with open(gh_stub, "w") as f:
                f.write("#!/bin/sh\ntouch \"{}\"\nexit 1\n".format(marker))
            os.chmod(gh_stub, 0o755)

            plan_path = os.path.join(d, "plan.md")
            with open(plan_path, "w") as f:
                f.write(_plan())
            spec_path = os.path.join(d, "spec.md")
            with open(spec_path, "w") as f:
                f.write(MINIMAL_SPEC)
            run_dir = os.path.join(d, "run")
            codex_bin = write_fake_codex(d)

            env = dict(os.environ)
            env["PATH"] = stub_dir + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), plan_path,
                 "--spec", spec_path, "--run-dir", run_dir,
                 "--codex-bin", codex_bin],
                cwd=d, capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertFalse(
                os.path.exists(marker), "forge-run.py must never invoke gh"
            )


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
        # escalated is terminal (halted), so the full staged-deferrals block
        # replaces the terse "deferrals: N — ..." prefix — each summary
        # appears exactly once, not twice in two truncations.
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            deferrals = [{"id": "f1", "summary": "unused import"}]
            _write_run(rd, "escalated", [_summary(1, "passed"), _summary(2, "escalated")],
                       deferrals=deferrals)
            _write_receipt(rd, 2, 2, "escalated", findings=["bad thing"], halt_reason="stuck")
            r = self._status(rd)
            self.assertEqual(r.returncode, 0)
            self.assertIn("stuck", r.stdout)
            self.assertNotIn("deferrals: 1", r.stdout)
            self.assertEqual(r.stdout.count("unused import"), 1)
            self.assertIn("forge_memory.py defer", r.stdout)

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

    def test_terminal_run_status_cli_shows_defer_template_for_unfiled_only(self):
        # End-to-end: the real `--status` CLI path (forge-run.py --status ->
        # forge_status.render_status -> render_staged_deferrals), not a
        # direct call to the renderer, against a TERMINAL run.json.
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            deferrals = [
                {"id": "already-done", "summary": "already done", "issue": "7"},
                {"id": "still-pending", "summary": "z" * 110},
            ]
            _write_run(rd, "passed", [_summary(1, "passed")], deferrals=deferrals)
            r = self._status(rd)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("COMPLETED", r.stdout)
            self.assertIn("forge_memory.py defer", r.stdout)
            self.assertIn("--title", r.stdout)
            self.assertIn("--why", r.stdout)
            self.assertIn("still-pending", r.stdout)
            self.assertIn("z" * 110, r.stdout)
            self.assertIn("already-done", r.stdout)
            self.assertIn("filed as issue #7", r.stdout)
            defer_lines = [l for l in r.stdout.splitlines() if "forge_memory.py defer" in l]
            self.assertEqual(len(defer_lines), 1)
            self.assertIn("still-pending", defer_lines[0])
            self.assertNotIn("already-done", defer_lines[0])
            # No duplicated terse prefix, and each summary appears exactly
            # once — the full block replaces it, not adds to it.
            self.assertNotIn("deferrals: 2", r.stdout)
            self.assertEqual(r.stdout.count("already done"), 1)

    def test_running_run_status_cli_shows_terse_line_and_no_defer_command(self):
        # A running run must never show a pasteable filing command — Task 2's
        # `defer --run` refuses to write into a non-terminal run.json, so
        # showing the command mid-run would hand the user something
        # guaranteed to be rejected.
        with tempfile.TemporaryDirectory() as d:
            rd = os.path.join(d, "run")
            deferrals = [{"id": "still-pending", "summary": "unused import"}]
            _write_run(rd, "running", [_summary(1, "passed")], deferrals=deferrals)
            r = self._status(rd)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("RUNNING", r.stdout)
            self.assertIn("deferrals: 1", r.stdout)
            self.assertIn("unused import", r.stdout)
            self.assertNotIn("forge_memory.py defer", r.stdout)
            self.assertNotIn("--title", r.stdout)


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


class DeferralProvenanceTests(unittest.TestCase):
    """``deferral_provenance`` — the one definition of the ``from`` value a
    runner-staged deferral is filed with. The spec reserves ``from: user``
    for a deferral a human asked for directly (which deliberately skips the
    close-out review gate), so a runner-staged entry must never be
    describable that way."""

    def test_plan_path_alone_when_entry_carries_no_task(self):
        self.assertEqual(
            forge_status.deferral_provenance(
                "docs/forge/plans/p.md", {"id": "F1"}, "/runs/x/run.json"),
            "docs/forge/plans/p.md",
        )

    def test_plan_path_and_task_number_when_the_entry_carries_one(self):
        self.assertEqual(
            forge_status.deferral_provenance(
                "docs/forge/plans/p.md", {"id": "F1", "task_number": 3},
                "/runs/x/run.json"),
            "docs/forge/plans/p.md, Task 3",
        )

    def test_run_json_path_is_the_fallback_when_run_json_records_no_plan(self):
        # Never "user" — that value means something else entirely.
        value = forge_status.deferral_provenance(None, {"id": "F1"}, "/runs/x/run.json")
        self.assertEqual(value, "/runs/x/run.json")
        self.assertNotEqual(value, "user")

    def test_final_review_stage_is_named_and_no_task_number_is_invented(self):
        # A final-review deferral belongs to no single task, so the runner
        # stages the stage that produced it instead of a task number. The one
        # thing this must never do is manufacture a "Task N" for it.
        value = forge_status.deferral_provenance(
            "docs/forge/plans/p.md", {"id": "F1", "stage": "final-review"},
            "/runs/x/run.json",
        )
        self.assertEqual(value, "docs/forge/plans/p.md, final review")
        self.assertNotIn("Task", value)

    def test_run_json_fallback_is_absolute_so_both_callers_agree(self):
        # The two callers spell the same file differently: the `--status`
        # emitter derives it as run_dir + "run.json", while `defer --run` uses
        # whatever path the user typed. A plan-less run.json must still yield
        # ONE provenance string across both, so the fallback is normalized.
        relative = forge_status.deferral_provenance(
            None, {"id": "F1"}, os.path.join("runs", "x", "run.json"))
        absolute = forge_status.deferral_provenance(
            None, {"id": "F1"},
            os.path.join(os.getcwd(), "runs", "x", "run.json"))
        self.assertEqual(relative, absolute)
        self.assertTrue(os.path.isabs(relative))


class StagedDeferralProvenanceTests(unittest.TestCase):
    """The emitted template must record real provenance. Pasting it without
    ``--from`` would file a runner-staged deferral as ``from: user``."""

    def _run_json(self, d, deferrals, plan="docs/forge/plans/p.md"):
        path = os.path.join(d, "run.json")
        with open(path, "w") as f:
            json.dump({"status": "passed", "plan": plan, "deferrals": deferrals}, f)
        return path

    def test_emitted_command_carries_plan_path_as_from(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [{"id": "F1", "summary": "s" * 110}])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            self.assertIn("--from {}".format(shlex.quote("docs/forge/plans/p.md")), text)

    def test_emitted_command_carries_task_number_when_staged_entry_has_one(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(
                d, [{"id": "F1", "summary": "s" * 110, "task_number": 3}])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            self.assertIn(
                "--from {}".format(shlex.quote("docs/forge/plans/p.md, Task 3")), text)

    def test_emitted_command_never_says_user(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [{"id": "F1", "summary": "s" * 110}])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            command = [l for l in text.splitlines() if "forge_memory.py defer" in l][0]
            self.assertNotIn("--from user", command)


class StagedDeferralDuplicateIdTests(unittest.TestCase):
    """Finding ids are reviewer-authored per review and are never namespaced,
    so one run's ``deferrals`` list can hold two entries with the same id.
    The emitted commands must file BOTH, each attributed to its own entry."""

    def _run_json(self, d, deferrals):
        path = os.path.join(d, "run.json")
        with open(path, "w") as f:
            json.dump(
                {"status": "passed", "plan": "docs/forge/plans/p.md",
                 "deferrals": deferrals}, f)
        return path

    def test_colliding_ids_emit_distinct_occurrence_selectors(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [
                {"id": "F1", "summary": "first thing"},
                {"id": "F1", "summary": "second thing"},
            ])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            commands = [l for l in text.splitlines() if "forge_memory.py defer" in l]
            self.assertEqual(len(commands), 2)
            self.assertIn("--occurrence 1", commands[0])
            self.assertIn("--occurrence 2", commands[1])
            self.assertNotEqual(commands[0], commands[1])

    def test_occurrence_counts_only_entries_sharing_the_id(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [
                {"id": "F1", "summary": "one"},
                {"id": "F2", "summary": "two"},
                {"id": "F1", "summary": "three"},
            ])
            state = forge_status.read_run_state(d)
            lines = forge_status.render_staged_deferrals(state, run_path)
            commands = [l for l in lines if "forge_memory.py defer" in l]
            self.assertIn("--occurrence 1", commands[0])
            self.assertNotIn("--occurrence", commands[1])  # F2 is unique
            self.assertIn("--occurrence 2", commands[2])

    def test_unique_id_emits_no_occurrence_flag(self):
        with tempfile.TemporaryDirectory() as d:
            run_path = self._run_json(d, [{"id": "F1", "summary": "only one"}])
            state = forge_status.read_run_state(d)
            text = "\n".join(forge_status.render_staged_deferrals(state, run_path))
            self.assertNotIn("--occurrence", text)
