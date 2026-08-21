"""Worker dispatch argv/model/effort, acceptance-command execution, and worker/reviewer subprocess timeouts."""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest

from _forge_support import *  # noqa: F401,F403


class DispatchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-dispatch-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("# Task brief\n")

    def test_tier_resolution_emits_exact_model_effort_argv(self):
        for tier, (model, effort) in forge_run.TIER_MAP.items():
            run_dir = os.path.join(self.d, "run-" + tier)
            os.makedirs(run_dir, exist_ok=True)
            task = forge_run.Task(number=1, title="t", tier=tier)
            res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
            argv = res.argv
            self.assertIn("exec", argv)
            self.assertIn("-m", argv)
            self.assertIn(model, argv)
            self.assertIn("-c", argv)
            self.assertIn("model_reasoning_effort=" + effort, argv)
            self.assertIn("--output-last-message", argv)

    def test_ultra_never_appears_in_emitted_argv(self):
        for tier in forge_run.TIER_MAP:
            run_dir = os.path.join(self.d, "runu-" + tier)
            os.makedirs(run_dir, exist_ok=True)
            task = forge_run.Task(number=1, title="t", tier=tier)
            res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
            self.assertNotIn("ultra", " ".join(res.argv))

    def test_prompt_carries_contract_preamble_and_brief(self):
        run_dir = os.path.join(self.d, "run-prompt")
        os.makedirs(run_dir, exist_ok=True)
        task = forge_run.Task(number=1, title="t", tier="trivial")
        res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
        prompt = res.argv[-1]
        self.assertIn("# Task brief", prompt)
        self.assertIn("forge execution worker", prompt)

    def test_missing_contract_source_raises(self):
        empty = tempfile.mkdtemp(prefix="forge-run-noagents-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        old = os.environ.get("FORGE_AGENTS_DIR")
        os.environ["FORGE_AGENTS_DIR"] = empty
        self.addCleanup(
            lambda: os.environ.__setitem__("FORGE_AGENTS_DIR", old)
            if old is not None
            else os.environ.pop("FORGE_AGENTS_DIR", None)
        )
        run_dir = os.path.join(self.d, "run-noagents")
        os.makedirs(run_dir, exist_ok=True)
        task = forge_run.Task(number=1, title="t", tier="trivial")
        with self.assertRaises(RuntimeError):
            forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)


class RunAcceptanceTests(unittest.TestCase):
    def test_success_and_failure_recorded_per_command(self):
        d = tempfile.mkdtemp(prefix="forge-run-acc-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        task = forge_run.Task(
            number=1, title="t", tier="trivial",
            acceptance_commands=["true", "false"],
        )
        results = forge_run.run_acceptance(task, d)
        self.assertEqual([r.command for r in results], ["true", "false"])
        self.assertEqual(results[0].exit_code, 0)
        self.assertNotEqual(results[1].exit_code, 0)


class TimeoutTests(unittest.TestCase):
    """--timeout SECONDS bounds worker and reviewer codex subprocess calls. A
    worker timeout is a failed iteration (rework/escalation path); a reviewer
    timeout is a contract error (loud exit 1, no receipt)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-timeout-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self):
        # Ignore harness artifacts so the working tree is clean at run start
        # (the commit-discipline precondition halts on a dirty tree).
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog\nresponses.json\nrun/\n.forge/\n")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _plan(self, content, name="plan.md"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _run(self, plan_path, extra_args=(), responses=None):
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        if responses is not None:
            resp_path = os.path.join(self.d, "responses.json")
            with open(resp_path, "w") as f:
                json.dump(responses, f)
            env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def test_worker_timeout_counts_as_failed_iteration_then_escalates(self):
        # A worker timeout is an execution failure -> implicit fix-retry finding:
        # it reworks every attempt (never scope-halts) until the backstop
        # (MAX_ATTEMPTS_BACKSTOP == 5), then escalates with the timeout as the
        # outstanding finding.
        plan = self._plan(PLAN_PASS)  # trivial, no reviewer, no git repo needed
        res = self._run(
            plan,
            extra_args=["--timeout", "0.2"],
            responses=[{"exit": 0, "msg": "", "sleep": 2}],
        )
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-5.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "backstop")
        self.assertTrue(
            any("timed out" in f_ for f_ in receipt["outstanding_findings"])
        )

    def test_reviewer_timeout_exits_one_naming_cause(self):
        plan = self._plan(PLAN_STD)  # standard tier -> reviewer dispatched
        self._init_repo()
        res = self._run(
            plan,
            extra_args=["--timeout", "0.2"],
            responses=[
                {"exit": 0, "msg": ""},              # worker: fast
                {"exit": 0, "msg": _pass_msg(), "sleep": 2},  # reviewer: sleeps past timeout
            ],
        )
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("reviewer", res.stderr.lower())
        self.assertIn("timed out", res.stderr.lower())


class AutofixAndDeferralTests(unittest.TestCase):
    """--autofix flag threading (default `auto`, `gate` short-circuits, an
    invalid value rejected by argparse) and task-level deferral aggregation into
    run.json (Phase 7 Task 7: Autonomy flag + Deferral handling). Standard-tier
    plans need a git repo (the reviewer packet is a `git diff`)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-autofix-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self, tracked=()):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog\nresponses.json\nrun/\n.forge/\n")
        for name in tracked:
            with open(os.path.join(self.d, name), "w") as f:
                f.write("base\n")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _plan(self, content, name="plan.md"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _run(self, plan_path, extra_args=(), responses=None):
        if os.path.exists(self.log):
            os.remove(self.log)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        if responses is not None:
            resp_path = os.path.join(self.d, "responses.json")
            with open(resp_path, "w") as f:
                json.dump(responses, f)
            env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _worker_dispatch_count(self, marker):
        return sum(
            1 for a in _log_argvs(self.log) if _find_dispatch([a], marker) is not None
        )

    def test_invalid_autofix_value_rejected_by_argparse(self):
        # An out-of-`choices` value is an argparse error (exit 2) naming the flag
        # and the bad choice -- not an "unrecognized argument", which is what a
        # runner with no --autofix flag would emit.
        plan = self._plan(PLAN_STD)
        res = self._run(plan, extra_args=["--autofix", "bogus"])
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("invalid choice", res.stderr.lower())
        self.assertIn("autofix", res.stderr.lower())

    def test_default_autofix_mode_is_auto(self):
        # No --autofix flag -> mode defaults to `auto`, recorded in run.json.
        # Acceptance `true` changes nothing, so the whole-plan diff is empty and
        # no final review / doc-sync runs -- the terminal write still records it.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # reviewer
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["autofix_mode"], "auto")

    def test_gate_mode_halts_task_on_any_finding_without_fix_dispatch(self):
        # --autofix gate reaches execute_task: any reviewer finding (even an
        # improvement) halts at attempt 1 with halt_reason "gate", and no rework
        # worker is dispatched (the worker runs exactly once).
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, extra_args=["--autofix", "gate"], responses=[
            {"exit": 0, "msg": ""},                    # worker
            {"exit": 0, "msg": _findings_msg("nit")},  # reviewer: any finding
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "gate")
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["autofix_mode"], "gate")
        self.assertEqual(self._worker_dispatch_count("task-1-worker-last"), 1)

    def test_gate_mode_threads_to_final_review(self):
        # --autofix gate reaches the final-review loop through run_plan (not a
        # hardcoded "auto"): the per-task review passes, but any final-review
        # finding halts the run with an escalated-final-review status.
        plan = self._plan(PLAN_STD_TRACKED)
        self._init_repo(tracked=["f1.txt"])
        res = self._run(plan, extra_args=["--autofix", "gate"], responses=[
            {"exit": 0, "msg": ""},                          # worker
            {"exit": 0, "msg": _pass_msg()},                 # task 1 review
            {"exit": 0, "msg": _findings_msg("final nit")},  # final review: gate halt
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["status"], "escalated-final-review")
        self.assertEqual(data["autofix_mode"], "gate")

    def test_task_deferrals_aggregate_into_run_json(self):
        # An improvement-only per-task finding defers (auto mode); the task passes
        # and its deferral is aggregated into run.json under `deferrals`, carrying
        # the summary/impact for the orchestrator's DEFERRALS.md write-back.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        # The passed task's ledger annotation commits a plan.md change, so the
        # whole-plan diff is non-empty and the final review runs -> pass it
        # explicitly (and let doc-sync clamp to that pass) so the only deferral
        # aggregated is the task's own.
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                             # worker
            {"exit": 0, "msg": _findings_msg("harmless nit")},  # reviewer: improvement
            {"exit": 0, "msg": _pass_msg()},                    # final review: pass
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(len(data["deferrals"]), 1)
        self.assertEqual(data["deferrals"][0]["summary"], "harmless nit")
        self.assertEqual(data["deferrals"][0]["impact"], "improvement")


if __name__ == "__main__":
    unittest.main()
