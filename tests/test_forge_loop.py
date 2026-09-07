"""End-to-end plan-loop subprocess runs, resume, and the --effort CLI path."""
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


class LoopSubprocessTests(unittest.TestCase):
    """End-to-end: invoke forge-run.py as a subprocess with a fake codex on the
    --codex-bin seam and the plan's dir as cwd (so acceptance commands run there)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-loop-")
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

    def test_help_exits_zero(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_passing_task_writes_receipt_with_all_fields_and_brief_sha(self):
        plan = self._plan(PLAN_PASS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        receipt_path = os.path.join(self.run_dir, "task-1-attempt-1.json")
        with open(receipt_path) as f:
            receipt = json.load(f)
        for key in ("task_number", "title", "tier", "model", "effort",
                    "brief_path", "brief_sha256", "worker_exit_code",
                    "acceptance_results", "review_verdict", "attempt", "status"):
            self.assertIn(key, receipt)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["tier"], "trivial")
        self.assertEqual(receipt["model"], "gpt-5.6-luna")
        self.assertEqual(receipt["effort"], "low")
        import hashlib
        with open(receipt["brief_path"], "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(receipt["brief_sha256"], actual)

    def test_run_json_summarizes_task_statuses(self):
        plan = self._plan(PLAN_PASS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["status"], "passed")
        statuses = {t["number"]: t["status"] for t in summary["tasks"]}
        self.assertEqual(statuses[1], "passed")

    def test_ledger_annotated_passed_with_attempts(self):
        plan = self._plan(PLAN_PASS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(plan) as f:
            content = f.read()
        self.assertIn("[x] Done", content)
        self.assertIn("passed, 1 attempt(s)", content)

    def test_depends_on_order_dependency_dispatched_first(self):
        plan = self._plan(PLAN_DEPS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(self.log) as f:
            log_lines = f.read().splitlines()
        # Each line is the argv of one worker dispatch; the --output-last-message
        # path names the task. Task 1 must be dispatched before Task 2.
        joined = "\n".join(log_lines)
        pos1 = joined.find("task-1-worker-last")
        pos2 = joined.find("task-2-worker-last")
        self.assertNotEqual(pos1, -1)
        self.assertNotEqual(pos2, -1)
        self.assertLess(pos1, pos2)

    def test_dependency_failure_halts_before_dependent_dispatched(self):
        # Task 1 (the dependency) fails; Task 2 depends on it and must never be
        # dispatched. Guards run_plan's break-on-escalation: a refactor that kept
        # looping would dispatch the dependent, and this test would catch it.
        plan = self._plan(PLAN_DEPS)
        res = self._run(plan, responses=[{"exit": 1, "msg": ""}])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(self.log) as f:
            log_lines = [ln for ln in f.read().splitlines() if ln.strip()]
        # Every worker dispatch is the failed dependency (task 1), never the
        # dependent (task 2). A crashing worker consumes the rework cap, so task 1
        # is dispatched more than once (initial + one rework) — the invariant under
        # test is that task 2 is never reached, not the exact attempt count.
        self.assertTrue(log_lines)
        self.assertTrue(
            all("task-1-worker-last" in ln for ln in log_lines), log_lines
        )
        self.assertNotIn("task-2-worker-last", "\n".join(log_lines))
        # Task 2's worker last-message file is never created.
        self.assertFalse(
            os.path.exists(os.path.join(self.run_dir, "task-2-worker-last.txt"))
        )

    def test_worker_nonzero_exit_marks_attempt_failed_and_halts(self):
        plan = self._plan(PLAN_PASS)
        res = self._run(plan, responses=[{"exit": 1, "msg": ""}])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["worker_exit_code"], 1)
        self.assertNotEqual(receipt["status"], "passed")

    def test_acceptance_failure_marks_attempt_failed_and_halts(self):
        plan = self._plan(PLAN_ACC_FAIL)
        res = self._run(plan)
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertNotEqual(receipt["status"], "passed")
        self.assertTrue(
            any(r["exit_code"] != 0 for r in receipt["acceptance_results"])
        )

    def test_malformed_plan_bad_heading_exits_one_naming_cause(self):
        plan = self._plan(PLAN_BAD_HEADING)
        res = self._run(plan)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("### Task 1:", res.stderr)

    def test_malformed_plan_duplicate_number_exits_one_naming_cause(self):
        plan = self._plan(PLAN_DUP)
        res = self._run(plan)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("duplicate", res.stderr.lower())

    def test_run_writes_forge_gitignore(self):
        # Receipts spec (2026-07-13 amendment): on run-dir creation the runner
        # writes a self-ignoring `.forge/.gitignore` containing `*` — no
        # target-repo setup required.
        plan = self._plan(PLAN_PASS)
        res = self._run(plan)
        self.assertEqual(res.returncode, 0, res.stderr)
        gitignore_path = os.path.join(self.d, ".forge", ".gitignore")
        self.assertTrue(os.path.exists(gitignore_path))
        with open(gitignore_path) as f:
            content = f.read()
        self.assertEqual(content.strip(), "*")

    def test_missing_contract_source_cli_exits_one_naming_cause(self):
        # Spec Tests bullet: "missing agents/*.md contract source exits 1" —
        # driven through the CLI (not just the unit-level dispatch_worker raise).
        plan = self._plan(PLAN_PASS)
        empty = os.path.join(self.d, "no-agents")
        os.makedirs(empty, exist_ok=True)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_AGENTS_DIR"] = empty
        res = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("contract source", res.stderr.lower())


class ResumeTests(unittest.TestCase):
    """Re-invocation with an existing --run-dir skips tasks whose latest receipt
    status is ``passed`` and resumes at the incomplete/escalated one. Trivial
    tasks + worker-crash escalation keep this off the git path."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-resume-")
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

    def _run(self, plan_path, responses):
        # Fresh log every invocation so the fake's response index starts at 0 and
        # the log reflects only this invocation's dispatches.
        if os.path.exists(self.log):
            os.remove(self.log)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
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

    def test_resume_skips_passed_tasks_and_resumes_at_escalated(self):
        plan = self._plan(PLAN_TWO_TRIVIAL)  # task 2 depends on task 1
        # Run 1: task 1 passes, task 2 crashes both attempts -> escalated, exit 2.
        res1 = self._run(plan, responses=[
            {"exit": 0, "msg": ""},  # task 1 worker
            {"exit": 1, "msg": ""},  # task 2 worker attempt 1
            {"exit": 1, "msg": ""},  # task 2 worker attempt 2
        ])
        self.assertEqual(res1.returncode, 2, res1.stderr)
        # Run 2 (same run-dir): task 1 is skipped (passed receipt); task 2 resumes
        # and now passes.
        res2 = self._run(plan, responses=[{"exit": 0, "msg": ""}])
        self.assertEqual(res2.returncode, 0, res2.stderr)
        joined = "\n".join(ln for ln in open(self.log).read().splitlines())
        self.assertNotIn("task-1-worker-last", joined)  # task 1 not re-dispatched
        self.assertIn("task-2-worker-last", joined)     # task 2 resumed
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["status"], "passed")
        with open(plan) as f:
            content = f.read()
        self.assertIn("[x] Done", content)

    def test_resume_forwards_to_run_plan_with_declared_signature(self):
        # resume(plan_path, spec_path, run_dir) is the documented re-invocation
        # entry: it forwards to run_plan with the production defaults (codex on
        # PATH, cwd = getcwd()). Guards against signature drift and dead code.
        calls = []
        orig = forge_run.run_plan

        def _record(*a, **k):
            calls.append(a)
            return 0

        forge_run.run_plan = _record
        try:
            rc = forge_run.resume("plan.md", "spec.md", "/run/dir")
        finally:
            forge_run.run_plan = orig
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertEqual(args[0], "plan.md")
        self.assertEqual(args[1], "spec.md")
        self.assertEqual(args[2], "/run/dir")
        self.assertEqual(args[3], "codex")
        self.assertEqual(args[4], os.getcwd())


class EffortOverrideCliTests(unittest.TestCase):
    """CLI --effort N=LEVEL applies only to task N's worker dispatch."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-effort-")
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

    def test_override_changes_effort_for_only_that_task(self):
        plan = self._plan(PLAN_DEPS)  # task 1 (trivial) then task 2 depends on it
        res = self._run(plan, extra_args=["--effort", "1=max"])
        self.assertEqual(res.returncode, 0, res.stderr)
        argvs = _log_argvs(self.log)
        t1 = _find_dispatch(argvs, "task-1-worker-last")
        t2 = _find_dispatch(argvs, "task-2-worker-last")
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        self.assertIn("model_reasoning_effort=max", t1)
        self.assertIn("model_reasoning_effort=low", t2)  # trivial default, unaffected by the override

    def test_ultra_effort_rejected_cli_exits_one_naming_cause(self):
        plan = self._plan(PLAN_PASS)
        res = self._run(plan, extra_args=["--effort", "1=ultra"])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("ultra", res.stderr.lower())

    def test_unknown_task_number_rejected_cli_exits_one_naming_cause(self):
        plan = self._plan(PLAN_PASS)  # only task 1 exists
        res = self._run(plan, extra_args=["--effort", "99=max"])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("99", res.stderr)


# --- halt freeze / resume (Halt resolution spec) -----------------------------

# A standard (reviewed) task whose acceptance passes only when FROZENWORK is
# present in the tracked f1.txt. The scripted worker writes that line on the
# first invocation only, so a resumed invocation passes acceptance if — and
# only if — the frozen attempt was really restored to the working tree.
# Acceptance is command-only (no prose clause), so the task has no contract
# checklist and the fixture verdicts need no coverage array.
PLAN_FREEZE = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `grep -q FROZENWORK f1.txt`

**Tier:** standard

**Depends on:** nothing
"""

# The same task with an acceptance command that is satisfied by the checkpoint
# itself, so a halted attempt leaves nothing to freeze.
PLAN_FREEZE_NOOP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true`

**Tier:** standard

**Depends on:** nothing
"""

REPAIR_TASK = {
    "title": "Fix the legacy guard",
    "files": ["f1.txt"],
    "spec": "Halt resolution",
    "tests": ["the guard holds"],
    "acceptance": "`true`",
    "tier": "standard",
}


class HaltFreezeResumeTests(unittest.TestCase):
    """A `scope-decision` halt freezes the paused attempt, leaves the tree
    clean, and records a halt record the next invocation resumes from
    (Halt resolution spec). End-to-end through the CLI so `--resolve`, the
    clean-tree precondition and the exit codes are exercised as shipped."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-halt-resume-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")
        self.f1 = os.path.join(self.d, "f1.txt")
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        with open(self.f1, "w") as f:
            f.write("base\n")
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            # The fake binary, its logs and the run dir must be ignored: the
            # freeze's `git clean -fd` removes untracked, non-ignored paths.
            f.write("fake_codex.py\nfakelog*\nresponses.json\nrun/\n.forge/\n")
        self.plan = self._plan(PLAN_FREEZE)
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        ).stdout

    def _plan(self, content, name="plan.md", where=None):
        p = os.path.join(where or self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _run(self, responses, extra_args=(), plan=None):
        # Fresh log every invocation so the fake's response index restarts.
        if os.path.exists(self.log):
            os.remove(self.log)
        if os.path.exists(self.log + ".prompts"):
            os.remove(self.log + ".prompts")
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.log + ".prompts"
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan or self.plan,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _halt_msg(self, id="h1"):
        # Line 99 is far outside the reviewed diff -> verified pre-existing;
        # contract-breaking + pre-existing is the scope-decision cell.
        return _fix_findings_msg(
            "f1.txt", "99", "the legacy guard is wrong", id=id,
            repair_task=REPAIR_TASK,
        )

    def _worker_writes_frozen(self):
        return {"exit": 0, "msg": "", "append_file": self.f1,
                "append_text": "FROZENWORK\n"}

    def _halt_run(self, plan=None):
        res = self._run([self._worker_writes_frozen(),
                         {"exit": 0, "msg": self._halt_msg()}], plan=plan)
        self.assertEqual(res.returncode, 2, res.stderr)
        return res

    def _halt_record(self):
        with open(os.path.join(self.run_dir, "run.json")) as f:
            return json.load(f).get("halt")

    def _porcelain(self):
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _briefs(self):
        """Every worker brief this invocation wrote, newest last."""
        out = []
        for name in sorted(os.listdir(self.run_dir)):
            if name.startswith("task-1-attempt-") and name.endswith("-brief.md"):
                with open(os.path.join(self.run_dir, name)) as f:
                    out.append(f.read())
        return out

    def test_scope_decision_halt_freezes_and_leaves_tree_clean(self):
        self._halt_run()
        record = self._halt_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["task"], 1)
        self.assertEqual(record["attempt"], 1)
        self.assertEqual(record["halt_reason"], "scope-decision")
        head = self._git("rev-parse", "HEAD").strip()
        self.assertEqual(record["freeze_base"], head)
        self.assertTrue(record["freeze_commit"])
        # The freeze holds the paused attempt and is parked off the branch.
        show = self._git("show", record["freeze_commit"])
        self.assertIn("FROZENWORK", show)
        self.assertEqual(self._porcelain(), "")

    def test_reinvocation_after_halt_is_not_refused_by_clean_tree_check(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertNotIn("working tree not clean", res.stderr)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_unrelated_dirty_tree_at_resume_is_still_refused(self):
        self._halt_run()
        with open(os.path.join(self.d, "unrelated.txt"), "w") as f:
            f.write("a human's uncommitted work\n")
        res = self._run([{"exit": 0, "msg": ""}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("working tree not clean", res.stderr)
        self.assertIn("unrelated.txt", res.stderr)

    def test_resume_restores_frozen_work_and_dispatches_reconcile_brief(self):
        self._halt_run()
        # The resumed worker writes nothing: acceptance (`grep -q FROZENWORK`)
        # can only pass if the freeze was replayed into the working tree.
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 0, res.stderr)
        brief = self._briefs()[-1]
        self.assertIn("restored", brief.lower())
        # Not the plain brief: the plain brief has no reconciliation section.
        self.assertIn("Resumed after a halt", brief)
        prompts = _log_prompts(self.log + ".prompts")
        self.assertTrue(any("Resumed after a halt" in p for p in prompts))

    def test_conflicting_replay_leaves_checkpoint_and_supplies_frozen_diff(self):
        self._halt_run()
        # The human's fix rewrites exactly the line the frozen attempt added
        # after, so the replay cannot apply.
        with open(self.f1, "w") as f:
            f.write("base\nHUMANFIX\n")
        self._git("add", "-A")
        self._git("commit", "-m", "human fix")
        head = self._git("rev-parse", "HEAD").strip()
        res = self._run([self._worker_writes_frozen(),
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 0, res.stderr)
        brief = self._briefs()[-1]
        self.assertIn("reference", brief.lower())
        self.assertIn("FROZENWORK", brief)  # the frozen diff, as reference text
        # The task restarted from the checkpoint: the human's commit is the
        # parent of the task's own commit, nothing was replayed onto it.
        parents = self._git(
            "rev-list", "--parents", "-n", "1",
            self._git("log", "--format=%H", "--grep", "forge: task 1",
                      "-n", "1").strip(),
        ).split()
        self.assertEqual(parents[1], head)

    def test_resume_without_resolve_halts_again_on_the_same_finding(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": self._halt_msg()}])
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertEqual(record["halt_reason"], "scope-decision")
        self.assertEqual(record["approved"], {})
        # The brief must not tell the worker a decision was made: nothing was
        # resolved, so the scope finding is still an open question.
        brief = self._briefs()[-1]
        self.assertNotIn("### Resolved finding(s)", brief)
        self.assertIn("NO finding resolved", brief)

    def test_resolve_repair_lets_the_same_finding_pass(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": self._halt_msg()},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            run = json.load(f)
        self.assertEqual(run["status"], "passed")
        self.assertIsNone(run.get("halt"))
        # Only what the human actually answered is named as resolved, with
        # the resolution they gave.
        brief = self._briefs()[-1]
        self.assertIn("### Resolved finding(s)", brief)
        self.assertIn("h1 (repair)", brief)

    def test_resolve_defer_stages_a_deferral_and_does_not_halt(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": self._halt_msg()},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=defer"])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            run = json.load(f)
        staged = run.get("deferrals") or []
        self.assertTrue(any(d.get("id") == "h1" for d in staged), staged)
        self.assertEqual(
            [d.get("task_number") for d in staged if d.get("id") == "h1"], [1]
        )

    def test_resolve_of_an_id_absent_from_the_halt_record_raises_naming_it(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""}],
                        extra_args=["--resolve", "nope9=repair"])
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("nope9", res.stderr)
        # The halt record survives the failed invocation — the run is still
        # resumable once the human names a real finding id.
        self.assertIsNotNone(self._halt_record())

    def test_missing_freeze_commit_object_at_resume_raises_naming_the_sha(self):
        self._halt_run()
        bogus = "0" * 40
        path = os.path.join(self.run_dir, "run.json")
        with open(path) as f:
            run = json.load(f)
        run["halt"]["freeze_commit"] = bogus
        with open(path, "w") as f:
            json.dump(run, f)
        res = self._run([{"exit": 0, "msg": ""}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn(bogus, res.stderr)

    def test_halt_with_nothing_to_freeze_records_null_and_resumes(self):
        # The plan lives outside the repo, so not even the ledger annotation
        # touches the tree: the halted attempt has nothing to freeze at all.
        outside = tempfile.mkdtemp(prefix="forge-halt-plan-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        plan = self._plan(PLAN_FREEZE_NOOP, where=outside)
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": self._halt_msg()}], plan=plan)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertIsNone(record["freeze_commit"])
        self.assertEqual(self._porcelain(), "")
        res2 = self._run([{"exit": 0, "msg": ""},
                          {"exit": 0, "msg": _pass_msg()}],
                         extra_args=["--resolve", "h1=repair"], plan=plan)
        self.assertEqual(res2.returncode, 0, res2.stderr)
        brief = self._briefs()[-1]
        self.assertIn("checkpoint", brief.lower())

    def test_reconcile_brief_carries_the_real_resolution_delta(self):
        # End-to-end wiring of freeze_base -> resolution delta: the delta is
        # `git diff <the checkpoint the freeze was taken against>`, so the
        # human's fix — committed on top of that checkpoint while the task was
        # paused — must appear in the resumed worker's brief. Recomputing the
        # base from the current HEAD, or dropping the diff, empties exactly
        # this assertion.
        self._halt_run()
        with open(os.path.join(self.d, "fix.txt"), "w") as f:
            f.write("HUMANFIXMARKER\n")
        self._git("add", "-A")
        self._git("commit", "-m", "human fix")
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 0, res.stderr)
        brief = self._briefs()[-1]
        self.assertIn("Resolution delta", brief)
        self.assertIn("HUMANFIXMARKER", brief)
        # And the worker was actually sent it, not merely a file on disk.
        prompts = _log_prompts(self.log + ".prompts")
        self.assertTrue(any("HUMANFIXMARKER" in p for p in prompts), prompts)

    def test_passed_task_after_a_resumed_halt_commits_the_frozen_work(self):
        self._halt_run()
        res = self._run([{"exit": 0, "msg": ""},
                         {"exit": 0, "msg": _pass_msg()}],
                        extra_args=["--resolve", "h1=repair"])
        self.assertEqual(res.returncode, 0, res.stderr)
        sha = self._git("log", "--format=%H", "--grep", "forge: task 1",
                        "-n", "1").strip()
        self.assertTrue(sha)
        self.assertIn("FROZENWORK", self._git("show", "{}:f1.txt".format(sha)))
        self.assertEqual(self._porcelain(), "")


# --- freeze on every halt class (Task 6: Halt resolution — every class) -----

# A standard task whose acceptance flips green -> red depending on whether
# `fail_flag` exists, so a second attempt can manufacture the regression
# rule's acceptance-based trigger without any harness change.
PLAN_REGRESSION = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `test ! -f fail_flag`

**Tier:** standard

**Depends on:** nothing
"""

# A standard task whose acceptance is satisfied by the very first marker
# written and stays green forever after (nothing ever removes it) — so a
# scope-decision-then-regression sequence can be scripted without the
# acceptance command itself flipping red mid-sequence.
PLAN_REGRESSION_SEQUENCE = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `grep -q PARTIALFIX f1.txt`

**Tier:** standard

**Depends on:** nothing
"""


class FreezeEveryHaltClassTests(unittest.TestCase):
    """Every halt class — not only `scope-decision` — freezes the paused
    attempt, writes the halt record, and leaves the tree clean (Halt
    resolution: "Every halt class freezes"). `--resolve` and the
    approved-finding exemption stay scope-decision-only."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-halt-freeze-classes-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")
        self.f1 = os.path.join(self.d, "f1.txt")
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        with open(self.f1, "w") as f:
            f.write("base\n")
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fake_codex.py\nfakelog*\nresponses.json\nrun/\n.forge/\n")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        ).stdout

    def _plan(self, content, name="plan.md"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _set_plan(self, content):
        """Write plan.md and commit it: every fixture plan here lives inside
        the repo, so it must be part of the clean-tree checkpoint the same
        way HaltFreezeResumeTests' setUp commits PLAN_FREEZE before `git
        init` — otherwise plan.md itself is the dirty path the clean-tree
        precondition (correctly) refuses."""
        self.plan = self._plan(content)
        self._git("add", "-A")
        self._git("commit", "-m", "plan")

    def _run(self, responses, extra_args=(), plan=None):
        if os.path.exists(self.log):
            os.remove(self.log)
        if os.path.exists(self.log + ".prompts"):
            os.remove(self.log + ".prompts")
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.log + ".prompts"
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan or self.plan,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _halt_record(self):
        with open(os.path.join(self.run_dir, "run.json")) as f:
            return json.load(f).get("halt")

    def _porcelain(self):
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_regression_halt_freezes_and_leaves_tree_clean(self):
        self._set_plan(PLAN_REGRESSION)
        head = self._git("rev-parse", "HEAD").strip()
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "OK\n"},                                   # a1 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs work")},                         # a1 review: fix -> rework
            {"exit": 0, "msg": "", "append_file":
                os.path.join(self.d, "fail_flag"), "append_text": "x\n"},
        ]
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["halt_reason"], "regression")
        self.assertEqual(record["task"], 1)
        self.assertEqual(record["freeze_base"], head)
        self.assertTrue(record["freeze_commit"])
        self.assertIsNone(record["repair_task"])
        show = self._git("show", record["freeze_commit"])
        self.assertIn("OK", show)
        self.assertEqual(self._porcelain(), "")

    def test_stuck_halt_freezes_and_leaves_tree_clean(self):
        self._set_plan(PLAN_STD)
        head = self._git("rev-parse", "HEAD").strip()
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "OK\n"},                                   # a1 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs work")},                         # a1 review -> rework
            {"exit": 0, "msg": ""},                                    # a2 worker: no progress
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs work")},                         # a2 review: same, unresolved
        ]
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["halt_reason"], "stuck")
        self.assertEqual(record["freeze_base"], head)
        self.assertTrue(record["freeze_commit"])
        self.assertIsNone(record["repair_task"])
        self.assertEqual(self._porcelain(), "")

    def test_backstop_halt_freezes_and_leaves_tree_clean(self):
        self._set_plan(PLAN_STD)
        head = self._git("rev-parse", "HEAD").strip()
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "OK\n"},                                   # a1 worker
        ]
        for i, fid in enumerate(["a1", "a2", "a3", "a4", "a5"]):
            if i > 0:
                responses.append({"exit": 0, "msg": ""})
            responses.append({"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue {}".format(fid), id=fid)})
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["halt_reason"], "backstop")
        self.assertEqual(record["attempt"], 5)
        self.assertEqual(record["freeze_base"], head)
        self.assertTrue(record["freeze_commit"])
        self.assertIsNone(record["repair_task"])
        self.assertEqual(self._porcelain(), "")

    def test_gate_mode_halt_freezes_and_leaves_tree_clean(self):
        self._set_plan(PLAN_STD_TRACKED)
        head = self._git("rev-parse", "HEAD").strip()
        responses = [
            {"exit": 0, "msg": ""},                                    # a1 worker
            {"exit": 0, "msg": _findings_msg("a stylistic nit")},       # a1 review: any finding -> gate halt
        ]
        res = self._run(responses, extra_args=["--autofix", "gate"])
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertIsNotNone(record)
        self.assertEqual(record["halt_reason"], "gate")
        self.assertEqual(record["freeze_base"], head)
        self.assertTrue(record["freeze_commit"])
        self.assertIsNone(record["repair_task"])
        self.assertEqual(self._porcelain(), "")

    def test_non_scope_halt_status_offers_no_resolve_command(self):
        self._set_plan(PLAN_REGRESSION)
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "OK\n"},
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "needs work")},
            {"exit": 0, "msg": "", "append_file":
                os.path.join(self.d, "fail_flag"), "append_text": "x\n"},
        ]
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertEqual(record["halt_reason"], "regression")
        self.assertIsNone(record["repair_task"])
        status = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--status", "--run-dir", self.run_dir],
            cwd=self.d, capture_output=True, text=True,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn("--resolve", status.stdout)

    def test_resolve_naming_a_finding_from_a_non_scope_halt_raises(self):
        self._set_plan(PLAN_REGRESSION)
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "OK\n"},
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs work", id="regfind")},
            {"exit": 0, "msg": "", "append_file":
                os.path.join(self.d, "fail_flag"), "append_text": "x\n"},
        ]
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        record = self._halt_record()
        self.assertEqual(record["halt_reason"], "regression")
        # The finding named in the record's `findings` list still cannot be
        # resolved with --resolve: the record it came from is not a
        # scope-decision halt, so the id is rejected exactly like an unknown
        # one (Halt resolution: --resolve stays scope-decision-only).
        finding_id = record["findings"][0]["id"]
        res2 = self._run([{"exit": 0, "msg": ""}],
                         extra_args=["--resolve", "{}=repair".format(finding_id)])
        self.assertEqual(res2.returncode, 1, res2.stdout)
        self.assertIn(finding_id, res2.stderr)
        self.assertIsNotNone(self._halt_record())

    def test_scope_decision_then_regression_on_resume_reflects_the_second_freeze(self):
        # The exact reported sequence: a scope-decision halt, a resume with
        # --resolve, then a regression halt on the resumed attempt. The run
        # is still resumable, the tree is clean, and the record names the
        # second freeze rather than the first.
        #
        # Acceptance checks only the marker round A writes (never removed),
        # so it stays green through every attempt — unlike PLAN_FREEZE's
        # FROZENWORK-gated acceptance, which round A's PARTIALFIX-only
        # attempt would fail, misaligning the scripted responses against an
        # execution-failure attempt neither review response was meant for.
        self._set_plan(PLAN_REGRESSION_SEQUENCE)
        # Round A: a real fix finding ("regfind") that will later reappear.
        # Round B: the scope-decision halt (h1), during which "regfind"
        # disappears from the reviewer's findings and is recorded resolved.
        responses = [
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "PARTIALFIX\n"},                           # a1 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "needs more work", id="regfind")},      # a1 review -> rework
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "MOREWORK\n"},                             # a2 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "99", "the legacy guard is wrong", id="h1",
                repair_task=REPAIR_TASK)},                              # a2 review -> scope-decision halt
        ]
        res = self._run(responses)
        self.assertEqual(res.returncode, 2, res.stderr)
        first_record = self._halt_record()
        self.assertEqual(first_record["halt_reason"], "scope-decision")
        first_freeze = first_record["freeze_commit"]

        # Resume: --resolve h1=repair. The worker adds nothing further, and
        # the reviewer re-flags "regfind" at the same (still in-diff)
        # location it originally occupied — a runner-recorded resolved id
        # reappearing, which is the regression rule.
        res2 = self._run(
            [{"exit": 0, "msg": ""},
             {"exit": 0, "msg": _fix_findings_msg(
                 "f1.txt", "2", "regression: reappeared", id="regfind")}],
            extra_args=["--resolve", "h1=repair"],
        )
        self.assertEqual(res2.returncode, 2, res2.stderr)
        second_record = self._halt_record()
        self.assertIsNotNone(second_record)
        self.assertEqual(second_record["task"], 1)
        self.assertEqual(second_record["halt_reason"], "regression")
        self.assertEqual(second_record["attempt"], 3)  # count continues, not reset
        self.assertTrue(second_record["freeze_commit"])
        # The record names the SECOND freeze: its findings are the
        # regression's, not the scope-decision halt's original h1 finding.
        self.assertEqual(
            [f["id"] for f in second_record["findings"]], ["regfind"]
        )
        self.assertEqual(self._porcelain(), "")

    def test_resumed_run_halted_twice_in_a_row_refreezes_each_time_no_orphan(self):
        # A resumed run that halts again re-freezes and rewrites the record —
        # never left dirty-and-unrecorded — and the freeze ref is reused
        # (same ref name), never orphaned.
        self._set_plan(PLAN_FREEZE)
        res = self._run([
            {"exit": 0, "msg": "", "append_file": self.f1,
             "append_text": "FROZENWORK\n"},
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "99", "the legacy guard is wrong", id="h1",
                repair_task=REPAIR_TASK)},
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        first_record = self._halt_record()
        first_freeze = first_record["freeze_commit"]
        run_id = os.path.basename(os.path.normpath(self.run_dir))
        ref = "refs/forge/freeze/{}/task-1".format(run_id)
        refs_before = self._git(
            "for-each-ref", "--format=%(refname)", "refs/forge/freeze/"
        ).split()
        self.assertEqual(refs_before, [ref])

        # Resume with no --resolve: the same scope-decision finding is still
        # outstanding, so the task halts again on the same class.
        res2 = self._run(
            [{"exit": 0, "msg": ""},
             {"exit": 0, "msg": _fix_findings_msg(
                 "f1.txt", "99", "the legacy guard is wrong", id="h1",
                 repair_task=REPAIR_TASK)}],
        )
        self.assertEqual(res2.returncode, 2, res2.stderr)
        second_record = self._halt_record()
        self.assertIsNotNone(second_record)
        self.assertTrue(second_record["freeze_commit"])
        refs_after = self._git(
            "for-each-ref", "--format=%(refname)", "refs/forge/freeze/"
        ).split()
        self.assertEqual(refs_after, [ref])  # still exactly one ref: reused, not orphaned
        self.assertEqual(self._git("cat-file", "-e", second_record["freeze_commit"]
                                    ), "")  # resolvable
        self.assertEqual(self._porcelain(), "")
