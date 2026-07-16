"""Verdict parsing, reviewer dispatch, the review rework loop, and non-git review handling."""
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


class ParseVerdictTests(unittest.TestCase):
    """parse_verdict: last parseable JSON object matching the two verdict shapes
    (fenced or bare); anything else raises naming the cause."""

    def test_bare_pass(self):
        v = forge_run.parse_verdict('{"verdict": "pass"}')
        self.assertEqual(v.kind, "pass")

    def test_findings_extracted_from_prose_and_fence(self):
        msg = (
            "Here is my review of the diff.\n\n"
            "```json\n"
            '{"verdict": "findings", "findings": ["a.py:3 - missing guard"]}\n'
            "```\n\nThat is all.\n"
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertEqual(v.findings, ["a.py:3 - missing guard"])

    def test_unparseable_prose_raises_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_verdict("Looks good to me, ship it.")
        self.assertIn("verdict", str(ctx.exception).lower())

    def test_malformed_json_raises(self):
        with self.assertRaises(RuntimeError):
            forge_run.parse_verdict('{"verdict": ')

    def test_last_matching_object_wins(self):
        msg = (
            '{"verdict": "pass"}\n'
            "on reflection...\n"
            '{"verdict": "findings", "findings": ["x"]}'
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertEqual(v.findings, ["x"])


class DispatchReviewerUnitTests(unittest.TestCase):
    """dispatch_reviewer routes model/effort by REVIEW_MAP[tier] and returns the
    parsed Verdict — exercised directly against the fake codex (no plan loop)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-rev-unit-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.packet = os.path.join(self.d, "packet.md")
        with open(self.packet, "w") as f:
            f.write("### Task 1: X\n\n```diff\n```\n")
        self.log = os.path.join(self.d, "log")
        self.resp = os.path.join(self.d, "resp.json")
        with open(self.resp, "w") as f:
            json.dump([{"exit": 0, "msg": '{"verdict": "pass"}'}], f)
        self._set_env("FORGE_FAKE_LOG", self.log)
        self._set_env("FORGE_FAKE_RESPONSES", self.resp)

    def _set_env(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(
            lambda: os.environ.__setitem__(key, old)
            if old is not None
            else os.environ.pop(key, None)
        )

    def _argv_for(self, marker):
        with open(self.log) as f:
            for ln in f:
                if not ln.strip():
                    continue
                a = json.loads(ln)
                if "--output-last-message" in a:
                    path = a[a.index("--output-last-message") + 1]
                    if marker in path:
                        return a
        return None

    def test_standard_reviewer_maps_terra_high(self):
        run_dir = os.path.join(self.d, "run-s")
        os.makedirs(run_dir)
        task = forge_run.Task(number=1, title="t", tier="standard")
        verdict = forge_run.dispatch_reviewer(task, self.packet, self.fake, run_dir)
        self.assertEqual(verdict.kind, "pass")
        argv = self._argv_for("task-1-review-last")
        self.assertIsNotNone(argv)
        self.assertIn("gpt-5.6-terra", argv)
        self.assertIn("model_reasoning_effort=high", argv)
        self.assertNotIn("ultra", " ".join(argv))

    def test_complex_reviewer_maps_sol_high(self):
        run_dir = os.path.join(self.d, "run-c")
        os.makedirs(run_dir)
        task = forge_run.Task(number=2, title="t", tier="complex")
        verdict = forge_run.dispatch_reviewer(task, self.packet, self.fake, run_dir)
        self.assertEqual(verdict.kind, "pass")
        argv = self._argv_for("task-2-review-last")
        self.assertIsNotNone(argv)
        self.assertIn("gpt-5.6-sol", argv)
        self.assertIn("model_reasoning_effort=high", argv)
        self.assertNotIn("ultra", " ".join(argv))


class ReviewLoopTests(unittest.TestCase):
    """Standard/complex review + rework + halt + final review. These need a git
    repo because the review packet is a ``git diff`` against the run baseline."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-review-")
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

    def _run(self, plan_path, responses=None):
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
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def test_standard_dispatches_reviewer_with_mapped_model_and_passes(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # reviewer (clamps for final review)
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        rev = _find_dispatch(argvs, "task-1-review-last")
        self.assertIsNotNone(rev, argvs)
        self.assertIn("gpt-5.6-terra", rev)
        self.assertIn("model_reasoning_effort=high", rev)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["review_verdict"], {"verdict": "pass"})

    def test_findings_then_rework_carries_findings_text_in_worker_prompt(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                                  # worker a1
            {"exit": 0, "msg": _findings_msg("GUARDXYZ needed at a.py:3")},  # review a1
            {"exit": 0, "msg": ""},                                  # worker a2 (rework)
            {"exit": 0, "msg": _pass_msg()},                         # review a2
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        # The rework worker's brief carries the finding text; the fake logs the
        # full argv (prompt is the last arg), so the marker must appear there.
        with open(self.log) as f:
            self.assertIn("GUARDXYZ", f.read())

    def test_second_findings_verdict_halts_escalated_and_stops_next_task(self):
        plan = self._plan(PLAN_STD_THEN_TRIVIAL)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                              # t1 worker a1
            {"exit": 0, "msg": _findings_msg("a.py:1 - issue")}, # t1 review a1
            {"exit": 0, "msg": ""},                              # t1 worker a2
            {"exit": 0, "msg": _findings_msg("a.py:1 - still")}, # t1 review a2
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertTrue(receipt["outstanding_findings"])
        # Task 2 is never dispatched.
        self.assertFalse(
            os.path.exists(os.path.join(self.run_dir, "task-2-worker-last.txt"))
        )
        # Ledger annotated escalated on task 1.
        with open(plan) as f:
            content = f.read()
        self.assertIn("escalated:", content)

    def test_unparseable_reviewer_verdict_exits_one_naming_cause(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                       # worker
            {"exit": 0, "msg": "looks good, no JSON"},    # reviewer: unparseable
        ])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("verdict", res.stderr.lower())

    def test_final_review_dispatched_sol_high_after_all_pass(self):
        plan = self._plan(PLAN_PASS)  # trivial task: no per-task reviewer
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # trivial worker
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        fr = _find_dispatch(argvs, "final-review-last")
        self.assertIsNotNone(fr, argvs)
        self.assertIn("gpt-5.6-sol", fr)
        self.assertIn("model_reasoning_effort=high", fr)
        # A trivial task never dispatches a per-task reviewer.
        self.assertIsNone(_find_dispatch(argvs, "task-1-review-last"))

    def test_final_review_findings_exit_two_status_escalated_final_review(self):
        plan = self._plan(PLAN_PASS)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                                   # worker
            {"exit": 0, "msg": _findings_msg("spec drift at x")},     # final review
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["status"], "escalated-final-review")

    def test_second_reviewed_task_packet_isolated_to_its_own_diff(self):
        # Two sequential standard tasks, each mutating its OWN tracked file. Task 1
        # commits when it passes, so task 2's per-task base is task 1's commit and
        # its packet carries only task 2's change — never task 1's.
        plan = self._plan(PLAN_TWO_STD)
        for name in ("f1.txt", "f2.txt"):
            with open(os.path.join(self.d, name), "w") as f:
                f.write("base\n")
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # t1 worker
            {"exit": 0, "msg": _pass_msg()},  # t1 review
            {"exit": 0, "msg": ""},           # t2 worker
            {"exit": 0, "msg": _pass_msg()},  # t2 review
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-review.md")) as f:
            p1 = f.read()
        with open(os.path.join(self.run_dir, "task-2-review.md")) as f:
            p2 = f.read()
        self.assertIn("TASK1MARK", p1)
        self.assertIn("TASK2MARK", p2)
        # The task-2 packet must not carry task 1's change (now committed).
        self.assertNotIn("TASK1MARK", p2)

    def test_reviewer_process_crash_exits_one_naming_cause(self):
        # The reviewer subprocess exits non-zero but still writes a parseable
        # verdict. A runner that discards the reviewer's exit code would trust the
        # message and pass; the runner must instead fail loud on a crashed
        # reviewer rather than silently trust (or reuse) its output.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},            # worker
            {"exit": 3, "msg": _pass_msg()},   # reviewer crashes (exit 3)
        ])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("reviewer", res.stderr.lower())

    def test_reviewer_crash_message_preserves_stderr_tail(self):
        # Teeing must not lose the reviewer's stderr tail used in the crash
        # message — it is now sourced from the tee'd stream, not proc.stderr.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},
            {"exit": 3, "msg": "", "stderr": "REVIEWER_BOOM_DETAIL"},
        ])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("REVIEWER_BOOM_DETAIL", res.stderr)

    def test_passed_task_writes_live_log_with_phase_headers(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-live.log")) as f:
            log = f.read()
        self.assertIn("── worker · codex exec", log)
        self.assertIn("── acceptance ──", log)
        self.assertIn("── review · codex exec", log)

    def test_run_records_per_task_timestamps_and_run_metadata(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertIn("started_at", data)
        self.assertIn("pid", data)
        t1 = data["tasks"][0]
        self.assertTrue(t1.get("started_at"))
        self.assertTrue(t1.get("ended_at"))

    def test_run_start_announces_monitor_command(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("monitor:", res.stdout)
        self.assertIn("forge-monitor.py", res.stdout)


class ReviewNonGitTests(unittest.TestCase):
    """Review-path behaviors that need no git repo: trivial tier skips the
    reviewer entirely, and a worker crash consumes rework iterations without ever
    reaching the reviewer."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-review-nogit-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _plan(self, content, name="plan.md"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _run(self, plan_path, responses=None):
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
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def test_trivial_tier_skips_reviewer_dispatch_entirely(self):
        # Non-git cwd: no final review either, so the log must show no reviewer.
        plan = self._plan(PLAN_PASS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        self.assertIsNone(_find_dispatch(argvs, "review-last"), argvs)

    def test_worker_crash_counts_as_failed_iteration_within_cap(self):
        # Standard tier, but the worker crashes every attempt so the reviewer is
        # never reached; two crashes hit the rework cap -> escalated, exit 2.
        plan = self._plan(PLAN_STD)
        res = self._run(plan, responses=[{"exit": 1, "msg": ""}])
        self.assertEqual(res.returncode, 2, res.stderr)
        argvs = _log_argvs(self.log)
        self.assertIsNone(_find_dispatch(argvs, "task-1-review-last"), argvs)
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["worker_exit_code"], 1)
