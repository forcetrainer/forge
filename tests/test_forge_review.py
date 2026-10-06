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
import forge_common


# Local, module-scoped copies with a Tier justification: _forge_support.py's
# shared PLAN_PASS / PLAN_STD_THEN_TRIVIAL fixtures use bare `**Tier:** trivial`
# with no justification, which forge_plan.py now requires for any off-floor
# tier (docs/forge/DEFERRALS.md, Task 2 deferral) — kept local rather than
# editing the shared fixture module, out of this task's scope.
PLAN_PASS_JUSTIFIED = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Files:**
- Modify: `foo.txt`

**Acceptance:** `true` passes

**Tier:** trivial — mechanical, single call site

**Depends on:** nothing
"""

PLAN_STD_THEN_TRIVIAL_JUSTIFIED = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** standard

**Depends on:** nothing

### Task 2: Trivial follow-up
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — mechanical, single call site

**Depends on:** Task 1
"""

# All-standard plan for the final-review max-tier test — PLAN_STD already
# covers this (standard is the floor tier and needs no justification).

# A single complex-tier task, used to prove the final review of a plan
# containing a complex task routes to complex-tier (sol·high), not a pinned
# ceiling.
PLAN_COMPLEX = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Complex task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** complex — cross-cutting invariant: shared dispatch contract

**Depends on:** nothing
"""


# parse_verdict coverage moved to tests/test_forge_classify.py::ParseVerdictTests
# with the Phase 7 per-finding schema (Finding objects, loud contract error on an
# unlocated contract-breaking finding); the old list[str] cases here are retired
# rather than left as stale duplicates asserting the removed contract.


class DispatchReviewerUnitTests(unittest.TestCase):
    """dispatch_reviewer routes model/effort by TIER_MAP[task.tier] — reviewer
    tier = task tier, fresh context, no separate reviewer table — and returns
    the parsed Verdict, exercised directly against the fake codex (no plan
    loop)."""

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
        self.plog = self.log + ".prompts"
        self._set_env("FORGE_FAKE_PROMPT_LOG", self.plog)
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

    def test_standard_reviewer_maps_sol_medium(self):
        run_dir = os.path.join(self.d, "run-s")
        os.makedirs(run_dir)
        task = forge_run.Task(number=1, title="t", tier="standard")
        verdict = forge_run.dispatch_reviewer(
            task, self.packet, self.fake, run_dir, cwd=scratch_repo(self))
        self.assertEqual(verdict.kind, "pass")
        argv = self._argv_for("task-1-review-last")
        self.assertIsNotNone(argv)
        self.assertIn("gpt-6.1-sol", argv)
        self.assertIn("model_reasoning_effort=medium", argv)
        self.assertNotIn("ultra", " ".join(argv))

    def test_complex_reviewer_maps_sol_high(self):
        run_dir = os.path.join(self.d, "run-c")
        os.makedirs(run_dir)
        task = forge_run.Task(number=2, title="t", tier="complex")
        verdict = forge_run.dispatch_reviewer(
            task, self.packet, self.fake, run_dir, cwd=scratch_repo(self))
        self.assertEqual(verdict.kind, "pass")
        argv = self._argv_for("task-2-review-last")
        self.assertIsNotNone(argv)
        self.assertIn("gpt-6.1-sol", argv)
        self.assertIn("model_reasoning_effort=high", argv)
        self.assertNotIn("ultra", " ".join(argv))


class ReviewMapRetiredTests(unittest.TestCase):
    """The once-separate reviewer-routing table is retired: reviewer routing
    reads TIER_MAP exclusively, and its name is absent from both the forge_run
    and forge_common namespaces — resolves the codex-execution.md hazard of
    two tier tables silently going stale against each other on a model-churn
    edit. (Name built at runtime so this assertion doesn't itself keep the
    retired symbol alive as a grep hit.)"""

    def test_review_map_absent_from_module_namespaces(self):
        retired_name = "REVIEW" + "_MAP"
        self.assertFalse(hasattr(forge_run, retired_name))
        self.assertFalse(hasattr(forge_run.forge_common, retired_name))


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
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
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
        self.plog = self.log + ".prompts"
        if os.path.exists(self.plog):
            os.remove(self.plog)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.plog
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
        self.assertIn("gpt-6.1-sol", rev)
        self.assertIn("model_reasoning_effort=medium", rev)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["review_verdict"], {"verdict": "pass"})

    def test_fix_finding_then_rework_carries_findings_text_in_worker_prompt(self):
        # An in-diff contract-breaking finding (disposition "fix") reworks; its
        # summary is carried into the rework worker's brief. f1.txt is tracked and
        # the acceptance appends to it, so the finding at f1.txt:2 is verified
        # in-diff.
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                                  # worker a1
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "GUARDXYZ needed here")},             # review a1 (fix)
            {"exit": 0, "msg": ""},                                  # worker a2 (rework)
            {"exit": 0, "msg": _pass_msg()},                         # review a2
            {"exit": 0, "msg": _pass_msg()},                         # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        # The rework worker's brief carries the finding text. The prompt travels
        # on stdin now, not in argv, so assert against the fake's prompt log.
        self.assertTrue(
            any("GUARDXYZ" in pr for pr in _log_prompts(self.plog))
        )

    def test_persistent_fix_finding_stuck_escalated_and_stops_next_task(self):
        # The same in-diff fix finding coming back across two consecutive attempts
        # with nothing resolved is "stuck" -> escalate at attempt 2 (the worker
        # cannot make progress), and the dependent task 2 is never dispatched.
        plan = self._plan(PLAN_STD_TRACKED_THEN_TRIVIAL)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                                    # t1 worker a1
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},  # t1 review a1
            {"exit": 0, "msg": ""},                                    # t1 worker a2
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "still")},  # t1 review a2
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "stuck")
        self.assertTrue(receipt["outstanding_findings"])
        # The attempt-2 re-review packet carries the prior attempt's finding set so
        # the reviewer can label convergence against it (task-N-review.md is
        # overwritten each attempt, so this is attempt 2's packet). Assert on
        # the prior finding's own summary text — true regardless of which
        # packet builder produced this lap's packet or what its section
        # headers are called.
        with open(os.path.join(self.run_dir, "task-1-review.md")) as f:
            self.assertIn("issue", f.read())
        # Task 2 is never dispatched.
        self.assertFalse(
            os.path.exists(os.path.join(self.run_dir, "task-2-worker-last.txt"))
        )
        # Ledger annotated escalated on task 1 — but a `stuck` halt freezes
        # just like every other class now (Halt resolution: "Every halt
        # class freezes"), so the annotation rides inside the freeze commit
        # and the working tree itself is reset to the checkpoint rather than
        # left showing it directly.
        with open(os.path.join(self.run_dir, "run.json")) as f:
            run = json.load(f)
        freeze_commit = run["halt"]["freeze_commit"]
        self.assertTrue(freeze_commit)
        frozen_plan = subprocess.run(
            ["git", "show", "{}:plan.md".format(freeze_commit)],
            cwd=self.d, capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("escalated:", frozen_plan)
        with open(plan) as f:
            content = f.read()
        self.assertNotIn("escalated:", content)

    def test_unparseable_reviewer_verdict_exits_one_naming_cause(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                       # worker
            {"exit": 0, "msg": "looks good, no JSON"},    # reviewer: unparseable
        ])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("verdict", res.stderr.lower())

    def test_final_review_of_all_trivial_plan_routes_to_trivial_tier(self):
        # A plan whose only task is trivial: no per-task reviewer, but the final
        # review still runs, at the plan's max tier (trivial -> luna/low) — not
        # a pinned ceiling.
        plan = self._plan(PLAN_PASS_JUSTIFIED)  # trivial task: no per-task reviewer
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # trivial worker
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        fr = _find_dispatch(argvs, "final-review-last")
        self.assertIsNotNone(fr, argvs)
        self.assertIn("gpt-6-luna", fr)
        self.assertIn("model_reasoning_effort=low", fr)
        # A trivial task never dispatches a per-task reviewer.
        self.assertIsNone(_find_dispatch(argvs, "task-1-review-last"))

    def test_final_review_of_all_standard_plan_routes_to_standard_tier(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # task 1 review
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        fr = _find_dispatch(argvs, "final-review-last")
        self.assertIsNotNone(fr, argvs)
        self.assertIn("gpt-6.1-sol", fr)
        self.assertIn("model_reasoning_effort=medium", fr)

    def test_final_review_of_plan_with_complex_task_routes_to_complex_tier(self):
        plan = self._plan(PLAN_COMPLEX)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # task 1 review (complex tier)
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        rev = _find_dispatch(argvs, "task-1-review-last")
        self.assertIsNotNone(rev, argvs)
        self.assertIn("gpt-6.1-sol", rev)
        self.assertIn("model_reasoning_effort=high", rev)
        fr = _find_dispatch(argvs, "final-review-last")
        self.assertIsNotNone(fr, argvs)
        self.assertIn("gpt-6.1-sol", fr)
        self.assertIn("model_reasoning_effort=high", fr)

    def test_final_review_improvement_finding_defers_run_completes(self):
        # An improvement-only final-review finding (no contract_ref) defers
        # rather than halting -- findings are no longer a single-shot human gate
        # (Deferral handling: "improvement finding -> defer, run continues").
        plan = self._plan(PLAN_PASS_JUSTIFIED)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                                   # worker
            {"exit": 0, "msg": _findings_msg("spec drift at x")},     # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["status"], "passed")

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

    def test_run_start_announces_short_monitor_command_and_writes_launcher(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        # short one-token command (no long plugin path that would line-wrap)
        self.assertIn("sh .forge/watch", res.stdout)
        self.assertNotIn("forge-monitor.py", res.stdout)
        # the launcher itself carries the real path + --follow
        watch = os.path.join(self.d, ".forge", "watch")
        self.assertTrue(os.path.exists(watch))
        with open(watch) as f:
            content = f.read()
        self.assertIn("forge-monitor.py", content)
        self.assertIn("--follow", content)


class FinalReviewLoopTests(unittest.TestCase):
    """run_final_review_loop: the whole-plan final review now runs the same
    convergence loop as a per-task review (Final review spec: "now runs the
    same loop"). Exercised directly (not through the full CLI, since the
    --gate case has no CLI flag until Task 7) against the fake codex over a
    real git repo -- the diff base is always ``run_base``, so a fix dispatch's
    edits stay uncommitted and simply accumulate into the next re-review's
    diff."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-finalreview-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.d, "fakelog")
        self._set_env("FORGE_FAKE_LOG", self.log)
        self.plog = self.log + ".prompts"
        self._set_env("FORGE_FAKE_PROMPT_LOG", self.plog)

    def _set_env(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(
            lambda: os.environ.__setitem__(key, old)
            if old is not None
            else os.environ.pop(key, None)
        )

    def _responses(self, responses):
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        self._set_env("FORGE_FAKE_RESPONSES", resp_path)

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo_with_task_work(self):
        # The run dir and fake-codex logs live in the repository directory and
        # are written during a review: ignore them so only a reviewer's own
        # write changes the unchanged-repository fingerprint.
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        # A base commit, then a second commit simulating the plan's own task
        # work (an append to a tracked file) -- run_base is the commit BEFORE
        # the task work, so the whole-plan diff has something to point a
        # finding at.
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._git("add", "-A")
        self._git("commit", "-m", "base")
        run_base = forge_run._git_head(self.d)
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("NEEDFIX\n")
        self._git("add", "-A")
        self._git("commit", "-m", "task work")
        return run_base

    def _log_lines(self):
        return subprocess.run(
            ["git", "log", "--oneline"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout

    def test_findings_fix_then_repass_commits_once(self):
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},  # a1
            {"exit": 0, "msg": "",                                          # fix dispatch
             "append_file": os.path.join(self.d, "f1.txt"),
             "append_text": "FIXED\n"},
            {"exit": 0, "msg": _pass_msg()},                                # a2
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto",
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.attempts, 2)
        log = self._log_lines()
        self.assertEqual(log.count("fix: final-review"), 1)

    def test_pre_existing_contract_breaking_halts_with_repair_task(self):
        run_base = self._init_repo_with_task_work()
        repair = {"title": "Fix legacy bug", "tier": "standard"}
        self._responses([
            # line 99 is well outside the two-line diff -> verified pre-existing.
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "99", "legacy bug", repair_task=repair)},
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto",
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "scope-decision")
        self.assertEqual(outcome.repair_task, repair)
        self.assertNotIn("fix: final-review", self._log_lines())
        # The halt-reason class must land on the persisted final-review receipt
        # too (not just the in-memory TaskOutcome) — `--status` reads run.json +
        # receipts only, never the live return value.
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["halt_reason"], "scope-decision")

    def test_improvement_finding_defers_run_completes(self):
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": _findings_msg("style nit")},  # improvement -> defer
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto",
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(len(outcome.deferrals), 1)
        self.assertNotIn("fix: final-review", self._log_lines())

    def test_gate_mode_halts_on_any_finding(self):
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": _findings_msg("even a harmless nit")},
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "gate",
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "gate")
        self.assertNotIn("fix: final-review", self._log_lines())

    def test_rereview_packet_carries_prior_findings(self):
        # A re-review's packet must carry the prior attempt's outstanding fix
        # findings so a fresh-context final reviewer can label resolved/carried/
        # new — same as the per-task path. Without threading, the final reviewer
        # is told nothing and the convergence stop silently degrades to
        # backstop-only (Final review spec: "the same loop").
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},   # a1 -> fix
            {"exit": 0, "msg": "",                                           # fix dispatch
             "append_file": os.path.join(self.d, "f1.txt"), "append_text": "FIXED\n"},
            {"exit": 0, "msg": _pass_msg()},                                 # a2 re-review -> pass
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto",
        )
        self.assertEqual(outcome.status, "passed")
        packet = open(os.path.join(self.run_dir, "final-review.md")).read()
        # The a2 re-review is a verification lap (Session continuity /
        # Delta-scoped verification packets specs) — its packet is the
        # resumed reviewer's delta-scoped packet ("## Outstanding findings"),
        # not the cold-packet "Prior findings" labeling section, but the
        # prior attempt's outstanding finding is still carried into it.
        self.assertIn("Outstanding findings", packet)
        self.assertIn("issue", packet)

    def test_transient_fix_dispatch_crash_reworks_not_regression(self):
        # The final loop has no acceptance command that can regress; a fix-
        # dispatch crash is an implicit fix-retry (rework to backstop), exactly
        # like execute_task's worker crash — never a spurious green->red
        # regression halt.
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},   # a1 -> fix
            {"exit": 1, "msg": ""},                                          # a2 fix dispatch crashes
            {"exit": 0, "msg": "",                                           # a3 fix dispatch succeeds
             "append_file": os.path.join(self.d, "f1.txt"), "append_text": "FIXED\n"},
            {"exit": 0, "msg": _pass_msg()},                                 # a3 re-review -> pass
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto",
        )
        self.assertEqual(outcome.status, "passed")
        self.assertNotEqual(outcome.halt_reason, "regression")
        self.assertEqual(outcome.attempts, 3)


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
        self.plog = self.log + ".prompts"
        if os.path.exists(self.plog):
            os.remove(self.plog)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.plog
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
        plan = self._plan(PLAN_PASS_JUSTIFIED)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        self.assertIsNone(_find_dispatch(argvs, "review-last"), argvs)

    def test_worker_crash_counts_as_failed_iteration_within_backstop(self):
        # Standard tier, but the worker crashes every attempt so the reviewer is
        # never reached; five crashes hit MAX_ATTEMPTS_BACKSTOP -> escalated,
        # exit 2 (the old 2-iteration cap is retired).
        plan = self._plan(PLAN_STD)
        res = self._run(plan, responses=[{"exit": 1, "msg": ""}])
        self.assertEqual(res.returncode, 2, res.stderr)
        argvs = _log_argvs(self.log)
        self.assertIsNone(_find_dispatch(argvs, "task-1-review-last"), argvs)
        with open(os.path.join(self.run_dir, "task-1-attempt-5.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["worker_exit_code"], 1)


class FindingModelTests(unittest.TestCase):
    """Finding dataclass + finding_to_dict (Phase 7 Task 1 verdict model)."""

    def test_finding_to_dict_roundtrips_all_fields(self):
        f = forge_common.Finding(
            id="f1",
            summary="missing null check",
            file="scripts/foo.py",
            lines="12-20",
            provenance="in-diff",
            impact="contract-breaking",
            contract_ref="Acceptance: `pytest -q`",
            convergence="carried",
            carried_from="f0",
            repair_task={"title": "fix it"},
            disposition="fix",
        )
        d = forge_common.finding_to_dict(f)
        self.assertEqual(d["id"], "f1")
        self.assertEqual(d["summary"], "missing null check")
        self.assertEqual(d["location"], {"file": "scripts/foo.py", "lines": "12-20"})
        self.assertEqual(d["provenance"], "in-diff")
        self.assertEqual(d["impact"], "contract-breaking")
        self.assertEqual(d["contract_ref"], "Acceptance: `pytest -q`")
        self.assertEqual(d["convergence"], "carried")
        self.assertEqual(d["carried_from"], "f0")
        self.assertEqual(d["repair_task"], {"title": "fix it"})
        self.assertEqual(d["disposition"], "fix")

    def test_finding_to_dict_optional_fields_default_none(self):
        f = forge_common.Finding(
            id="f2", summary="nit", file="a.py", lines="3",
            provenance="pre-existing", impact="improvement",
        )
        d = forge_common.finding_to_dict(f)
        self.assertIsNone(d["contract_ref"])
        self.assertIsNone(d["convergence"])
        self.assertIsNone(d["carried_from"])
        self.assertIsNone(d["repair_task"])
        self.assertIsNone(d["disposition"])


class VerdictSerializationTests(unittest.TestCase):
    """verdict_to_dict over the new Finding-based Verdict.findings."""

    def test_pass_verdict(self):
        v = forge_common.Verdict(kind="pass")
        self.assertEqual(forge_common.verdict_to_dict(v), {"verdict": "pass"})

    def test_findings_verdict_serializes_two_findings(self):
        f1 = forge_common.Finding(
            id="f1", summary="one", file="a.py", lines="1",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="Acceptance: `true`",
        )
        f2 = forge_common.Finding(
            id="f2", summary="two", file="b.py", lines="5-6",
            provenance="pre-existing", impact="improvement",
        )
        v = forge_common.Verdict(kind="findings", findings=[f1, f2])
        d = forge_common.verdict_to_dict(v)
        self.assertEqual(d["verdict"], "findings")
        self.assertEqual(
            d["findings"],
            [forge_common.finding_to_dict(f1), forge_common.finding_to_dict(f2)],
        )


class TaskOutcomeFieldsTests(unittest.TestCase):
    """TaskOutcome gains halt_reason/deferrals/repair_task (default-empty)."""

    def test_defaults(self):
        o = forge_common.TaskOutcome(status="passed", attempts=1, summary="")
        self.assertIsNone(o.halt_reason)
        self.assertEqual(o.deferrals, [])
        self.assertIsNone(o.repair_task)

    def test_settable(self):
        o = forge_common.TaskOutcome(
            status="escalated", attempts=5, summary="halted",
            halt_reason="scope-decision", deferrals=[{"id": "f2"}],
            repair_task={"title": "fix it"},
        )
        self.assertEqual(o.halt_reason, "scope-decision")
        self.assertEqual(o.deferrals, [{"id": "f2"}])
        self.assertEqual(o.repair_task, {"title": "fix it"})


class ConstantsTests(unittest.TestCase):
    """Rework backstop + autonomy/halt-reason constants (Phase 7 Task 1)."""

    def test_max_attempts_backstop_is_five(self):
        self.assertEqual(forge_common.MAX_ATTEMPTS_BACKSTOP, 5)

    def test_old_max_attempts_name_is_gone(self):
        self.assertFalse(hasattr(forge_common, "MAX_ATTEMPTS"))

    def test_autofix_modes(self):
        self.assertEqual(forge_common.AUTOFIX_MODES, ("auto", "gate"))

    def test_halt_reasons(self):
        self.assertEqual(
            forge_common.HALT_REASONS,
            ("scope-decision", "regression", "stuck", "backstop", "gate"),
        )


class ReviewVerdictInstructionTests(unittest.TestCase):
    """REVIEW_VERDICT_INSTRUCTION names every per-finding schema field."""

    def test_names_each_schema_field(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        for field_name in (
            "id", "summary", "location", "file", "lines", "provenance",
            "impact", "contract_ref", "convergence", "carried_from",
            "repair_task",
        ):
            self.assertIn(field_name, instr, field_name)

    def test_names_repair_task_subfields(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        for field_name in ("title", "files", "spec", "tests", "acceptance", "tier"):
            self.assertIn(field_name, instr, field_name)

    def test_names_classification_rules(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn("in-diff", instr)
        self.assertIn("pre-existing", instr)
        self.assertIn("contract-breaking", instr)
        self.assertIn("improvement", instr)

    def test_unverifiable_is_a_coverage_status(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn(
            '"satisfied" | "violated" | "n/a" | "unverifiable"', instr
        )

    def test_unverifiable_is_an_impact_value(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn(
            '"contract-breaking" | "improvement" | "unverifiable"', instr
        )

    def test_contract_ref_requires_a_citable_ref_from_this_review(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn("copied verbatim from the '## Citable refs' list", instr)
        self.assertNotIn("slug", instr)

    def test_unverifiable_requires_reason_but_no_backing_finding(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn(
            '"unverifiable" requires a reason in evidence and obliges no '
            "backing finding",
            instr,
        )

    def test_contract_ref_no_longer_described_as_acceptance_criterion_or_spec_section(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertNotIn("acceptance criterion or spec section", instr)

    def test_findings_reported_regardless_of_provenance(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        # Case-insensitive: the rule is what must be stated, not its
        # position in a sentence. Asserting the literal lowercase form made a
        # capitalisation fix a test failure.
        self.assertIn(
            "every finding you see is reported regardless of provenance",
            instr.lower(),
        )

    def test_unverifiable_finding_requires_reason_in_summary(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn(
            'a finding with impact "unverifiable" requires a reason in its '
            "summary",
            instr,
        )

    def test_unverifiable_finding_carries_no_repair_task(self):
        instr = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn(
            'a finding with impact "unverifiable" '
            "carries no repair_task",
            instr,
        )


# --- validation retry resumes its reviewer (Session continuity) -------------


def _thread_stream(thread_id):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


# A contract-breaking finding whose location has no line range: a location
# defect, so the verdict is invalid and drives the one validation retry.
_INVALID_LOCATION_MSG = json.dumps({
    "verdict": "findings",
    "findings": [{
        "id": "f1", "summary": "BADLOC", "location": {"file": "f1.txt"},
        "provenance": "in-diff", "impact": "contract-breaking",
        "contract_ref": "Acceptance: `true`", "convergence": None,
        "carried_from": None, "repair_task": None,
    }],
})


class TaskReviewRetryResumeTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-review-retry-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.d, "fakelog")
        self.plog = self.log + ".prompts"
        self._old_env = {
            k: os.environ.get(k) for k in (
                "FORGE_FAKE_LOG", "FORGE_FAKE_RESPONSES", "FORGE_FAKE_PROMPT_LOG")
        }
        self.addCleanup(self._restore_env)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        for args in (["init"], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "Test"], ["add", "-A"],
                     ["commit", "-m", "base"]):
            subprocess.run(["git", *args], cwd=self.d, check=True,
                           capture_output=True, text=True)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _execute(self, responses):
        plan = os.path.join(self.d, "plan.md")
        with open(plan, "w") as f:
            f.write(PLAN_STD_TRACKED)
        path = os.path.join(self.d, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        os.environ["FORGE_FAKE_RESPONSES"] = path
        os.environ["FORGE_FAKE_LOG"] = self.log
        os.environ["FORGE_FAKE_PROMPT_LOG"] = self.plog
        task = forge_run.order_tasks(forge_run.parse_plan_tasks(plan))[0]
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        return outcome, threads

    def _review_calls(self):
        pairs = []
        for a, pr in zip(_log_argvs(self.log), _log_prompts(self.plog)):
            if ("--output-last-message" in a
                    and "task-1-review-last" in a[a.index("--output-last-message") + 1]):
                pairs.append((a, pr))
        return pairs

    def test_invalid_discovery_verdict_retries_by_resuming_recorded_thread(self):
        outcome, threads = self._execute([
            {"exit": 0, "msg": ""},                                   # worker
            {"exit": 0, "msg": _INVALID_LOCATION_MSG,
             "stdout": _thread_stream("th-rev1")},                    # review (invalid)
            {"exit": 0, "msg": _pass_msg()},                          # retry
        ])
        self.assertEqual(outcome.status, "passed")
        calls = self._review_calls()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("resume", calls[0][0])
        self.assertIn("resume", calls[1][0])
        self.assertIn("th-rev1", calls[1][0])

    def test_resumed_retry_prompt_has_defects_and_instruction_but_no_diff(self):
        self._execute([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _INVALID_LOCATION_MSG,
             "stdout": _thread_stream("th-rev1")},
            {"exit": 0, "msg": _pass_msg()},
        ])
        calls = self._review_calls()
        first, retry = calls[0][1], calls[1][1]
        self.assertIn("NEEDFIX", first)  # the discovery packet did carry the diff
        self.assertIn("f1", retry)
        self.assertIn("resubmit", retry.lower())
        self.assertNotIn("NEEDFIX", retry)
        self.assertNotIn("diff --git", retry)

    def test_failed_retry_resume_dispatches_cold_with_packet_and_defects(self):
        outcome, threads = self._execute([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _INVALID_LOCATION_MSG,
             "stdout": _thread_stream("th-rev1")},
            {"exit": 1, "msg": ""},                                   # retry resume fails
            {"exit": 0, "msg": _pass_msg()},                          # cold fallback
        ])
        self.assertEqual(outcome.status, "passed")
        calls = self._review_calls()
        self.assertEqual(len(calls), 3)
        self.assertIn("resume", calls[1][0])
        self.assertNotIn("resume", calls[2][0])
        self.assertIn("NEEDFIX", calls[2][1])           # the full packet
        self.assertIn("f1", calls[2][1])                 # plus the defect list
        self.assertIn("no location.lines", calls[2][1])
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertTrue(receipt["resume_fallback"])

    def test_missing_recorded_thread_retries_cold_with_packet_and_flags_fallback(self):
        outcome, _ = self._execute([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _INVALID_LOCATION_MSG},   # no thread.started event
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertEqual(outcome.status, "passed")
        calls = self._review_calls()
        self.assertNotIn("resume", calls[1][0])
        self.assertIn("NEEDFIX", calls[1][1])
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            self.assertTrue(json.load(f)["resume_fallback"])

    # Deliberate no-change guards: these hold before and after the retry resumes.
    def test_retry_does_not_advance_attempt_counter(self):
        self._execute([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _INVALID_LOCATION_MSG,
             "stdout": _thread_stream("th-rev1")},
            {"exit": 0, "msg": _pass_msg()},
        ])
        self.assertTrue(os.path.exists(
            os.path.join(self.run_dir, "task-1-attempt-1.json")))
        self.assertFalse(os.path.exists(
            os.path.join(self.run_dir, "task-1-attempt-2.json")))

    def test_second_invalid_verdict_is_still_a_contract_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._execute([
                {"exit": 0, "msg": ""},
                {"exit": 0, "msg": _INVALID_LOCATION_MSG,
                 "stdout": _thread_stream("th-rev1")},
                {"exit": 0, "msg": _INVALID_LOCATION_MSG},
            ])
        self.assertIn("still invalid after one retry", str(ctx.exception))

    def test_valid_first_verdict_dispatches_exactly_once(self):
        self._execute([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg(), "stdout": _thread_stream("th-rev1")},
        ])
        self.assertEqual(len(self._review_calls()), 1)
        self.assertFalse(os.path.exists(
            os.path.join(self.run_dir, "task-1-retry-defects.md")))


# --- reviewer write discipline: task reviews --------------------------------

_WORKER = {"exit": 0, "msg": ""}


class TaskReviewerWroteTests(ReviewerWroteCase):
    """A task reviewer that changes the repository halts the run as
    `reviewer-wrote` (exit 2), freezing the pre-review capture."""

    def _assert_frozen_halt(self, res, stray="stray.txt"):
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "reviewer-wrote")
        self.assertIn(stray, " ".join(receipt["outstanding_findings"]))
        halt = self.halt_record()
        self.assertEqual(halt["task"], 1)
        self.assertEqual(halt["halt_reason"], "reviewer-wrote")
        self.assertEqual(halt["freeze_base"], self.base_sha)
        self.assertIn(stray, " ".join(halt["changes"]))
        sha = halt["freeze_commit"]
        self.assertTrue(sha)
        # The freeze is the worker's work alone: the acceptance edit is in it,
        # the reviewer's stray file is not.
        self.assertIn("NEEDFIX", self.git("show", sha + ":f1.txt"))
        self.assertFalse(self.in_commit(sha, stray))
        self.assertEqual(self.git("rev-parse", sha + "^").strip(), self.base_sha)
        self.assertEqual(self.porcelain(), "")
        self.assertFalse(os.path.exists(self.stray))
        return halt

    def test_cold_reviewer_write_halts_and_freezes_pre_review_capture(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": _pass_msg(), "file_ops": [self.stray_op()]},
        ])
        self._assert_frozen_halt(res)
        self.assertEqual(len(self.dispatches("task-1-review-last")), 1)
        self.assertEqual(self.commit_count(), 1)  # no task commit
        self.assertNotIn("passed", self.run_json()["status"])

    def test_reviewer_that_writes_nothing_passes_through(self):
        res = self.run_cli([_WORKER, {"exit": 0, "msg": _pass_msg()}])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIsNone(self.halt_record())

    def test_reviewer_commit_halts_with_branch_and_head_restored(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": _pass_msg(), "file_ops": [
                self.stray_op(),
                {"op": "git", "args": ["add", "-A"]},
                {"op": "git", "args": ["commit", "-m", "reviewer commit"]},
            ]},
        ])
        halt = self._assert_frozen_halt(res)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base_sha)
        self.assertEqual(
            self.git("rev-parse", "refs/heads/main").strip(), self.base_sha)
        stub_sha = halt["reviewer_head"]
        self.assertEqual(self.git("log", "-1", "--format=%s", stub_sha).strip(),
                         "reviewer commit")
        self.assertIn(stub_sha, " ".join(halt["changes"]))
        # Left unreachable, never deleted.
        self.assertEqual(
            subprocess.run(["git", "merge-base", "--is-ancestor", stub_sha, "HEAD"],
                           cwd=self.repo).returncode, 1)
        self.assertEqual(self.commit_count(), 1)

    def test_reviewer_branch_switch_halts_with_head_reattached(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": _pass_msg(), "file_ops": [
                {"op": "git", "args": ["checkout", "-b", "other"]},
            ]},
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        halt = self.halt_record()
        self.assertEqual(halt["halt_reason"], "reviewer-wrote")
        self.assertIn("branch", " ".join(halt["changes"]))
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base_sha)
        self.assertEqual(self.porcelain(), "")
        self.assertIn("NEEDFIX", self.git("show", halt["freeze_commit"] + ":f1.txt"))

    def test_resumed_reviewer_write_then_success_halts(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-rev1")},
            {"exit": 0, "msg": _pass_msg(), "file_ops": [self.stray_op()]},
        ])
        self._assert_frozen_halt(res)
        self.assertEqual(len(self.dispatches("task-1-review-last")), 2)

    def test_resumed_reviewer_write_then_crash_is_not_a_resume_fallback(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-rev1")},
            {"exit": 1, "msg": "", "file_ops": [self.stray_op()]},
            {"exit": 0, "msg": _pass_msg()},  # a cold fallback would land here
        ])
        self._assert_frozen_halt(res)
        self.assertEqual(len(self.dispatches("task-1-review-last")), 2)
        self.assertEqual(self.commit_count(), 1)

    def test_resumed_reviewer_write_then_timeout_halts(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-rev1")},
            {"exit": 0, "msg": "", "sleep": 30, "ops_first": True,
             "file_ops": [self.stray_op()]},
            {"exit": 0, "msg": _pass_msg()},
        ], extra_args=("--timeout", "3"))
        self._assert_frozen_halt(res)
        self.assertEqual(len(self.dispatches("task-1-review-last")), 2)

    def test_halt_resumes_and_brief_names_the_reviewer_write(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": _pass_msg(), "file_ops": [self.stray_op()]},
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        res = self.run_cli([_WORKER, {"exit": 0, "msg": _pass_msg()}])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIsNone(self.halt_record())
        with open(os.path.join(self.run_dir, "task-1-attempt-2-brief.md")) as f:
            brief = f.read()
        self.assertIn("reviewer-wrote", brief)
        self.assertIn("reviewer changed the repository", brief)
        self.assertNotIn("stray.txt", self.git("show", "HEAD", "--stat"))


class TaskFreezeFailureTests(ReviewerWroteCase):
    """A git failure while freezing a reviewer-wrote halt must leave a halt
    record and an error naming both the reviewer write and the command."""

    def test_commit_tree_failure_records_halt_and_names_both(self):
        rc, err = self.main_failing_git([
            _WORKER,
            {"exit": 0, "msg": _pass_msg(), "file_ops": [self.stray_op()]},
        ], "commit-tree")
        self.assertEqual(rc, 1, err)
        self.assertIn("reviewer", err)
        self.assertIn("stray.txt", err)
        self.assertIn("commit-tree", err)
        run = self.run_json()
        self.assertEqual(run["status"], "contract-error")
        halt = run["halt"]
        self.assertEqual(halt["task"], 1)
        self.assertEqual(halt["halt_reason"], "reviewer-wrote")
        self.assertIn("stray.txt", " ".join(halt["changes"]))
        self.assertIsNone(halt["freeze_commit"])
        self.assertIn("commit-tree", halt["freeze_error"])


class RunDirInsideRepoTests(ReviewerWroteCase):
    """A --run-dir inside the repository that git does not ignore would make
    the runner's own files trip the fingerprint; refuse it up front."""

    def _run(self, run_dir):
        self.run_dir = run_dir
        return self.run_cli([_WORKER, {"exit": 0, "msg": _pass_msg()}])

    def test_unignored_in_repo_run_dir_is_a_contract_error_before_dispatch(self):
        res = self._run(os.path.join(self.repo, "myrun"))
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("myrun", res.stderr)
        self.assertIn("not git-ignored", res.stderr)
        self.assertIn(".forge", res.stderr)
        self.assertEqual(self.dispatches("task-1"), [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "myrun")))

    def test_relative_unignored_run_dir_is_refused_too(self):
        res = self._run("myrun")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("not git-ignored", res.stderr)

    def test_ignored_in_repo_run_dir_is_accepted(self):
        with open(os.path.join(self.repo, ".gitignore"), "a") as f:
            f.write("myrun/\n")
        self.git("add", "-A")
        self.git("commit", "-m", "ignore myrun")
        res = self._run(os.path.join(self.repo, "myrun"))
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_default_forge_run_dir_is_accepted(self):
        res = self._run(os.path.join(self.repo, ".forge", "runs", "x"))
        self.assertEqual(res.returncode, 0, res.stderr)


class FingerprintErrorTaskTests(ReviewerWroteCase):
    """A fingerprint that cannot be taken is a contract error (exit 1) naming
    the git command: no fallback dispatch, no coverage retry, no commit."""

    def _assert_contract_error(self, rc, err, reviews, expect_commit_count=1):
        self.assertEqual(rc, 1, err)
        self.assertIn("git rev-parse HEAD", err)
        self.assertEqual(self.run_json()["status"], "contract-error")
        self.assertEqual(len(self.dispatches("task-1-review-last")), reviews)
        self.assertEqual(self.commit_count(), expect_commit_count)

    def test_failure_before_cold_task_reviewer(self):
        rc, err = self.main_failing_fingerprint([_WORKER, {"exit": 0, "msg": _pass_msg()}], 1)
        self._assert_contract_error(rc, err, reviews=0)

    def test_failure_on_exit_of_cold_task_reviewer(self):
        rc, err = self.main_failing_fingerprint([_WORKER, {"exit": 0, "msg": _pass_msg()}], 2)
        self._assert_contract_error(rc, err, reviews=1)

    def test_failure_before_resumed_task_reviewer(self):
        rc, err = self.main_failing_fingerprint([
            _WORKER,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-rev1")},
            {"exit": 0, "msg": _pass_msg()},
            {"exit": 0, "msg": _pass_msg()},
        ], 3)
        self._assert_contract_error(rc, err, reviews=1)

    def test_failure_on_exit_of_resumed_task_reviewer(self):
        rc, err = self.main_failing_fingerprint([
            _WORKER,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-rev1")},
            {"exit": 1, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},  # a cold fallback would land here
        ], 4)
        self._assert_contract_error(rc, err, reviews=2)


class AcceptanceInPacketTests(ReviewerWroteCase):
    def test_discovery_packet_has_acceptance_results_and_verification_does_not(self):
        res = self.run_cli([
            _WORKER,
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs a guard")},          # review a1: fix
            {"exit": 0, "msg": ""},                         # worker a2
            {"exit": 0, "msg": _pass_msg()},                # review a2 (verification)
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        prompts = _log_prompts(self.log + ".prompts")
        argvs = _log_argvs(self.log)
        review_prompts = [
            p for a, p in zip(argvs, prompts)
            if "--output-last-message" in a
            and "task-1-review-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(review_prompts), 2)
        discovery, verification = review_prompts
        self.assertIn("## Acceptance results", discovery)
        self.assertIn("echo NEEDFIX >> f1.txt", discovery)
        self.assertNotIn("## Acceptance results", verification)
