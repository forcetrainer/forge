"""Commit-discipline behavior of the runner (per-task commits, dirty-tree refusal, per-task review base, persisted final-review base)."""
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


class CommitDisciplineTests(unittest.TestCase):
    """Phase 5: the runner commits each passed task, refuses a dirty tree at
    invocation start, uses the prior commit as each task's review base, and
    persists ``base_commit`` for a whole-plan final diff across resume. These
    need a real git repo; harness artifacts are gitignored so the tree is clean
    at run start (mirroring real usage)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-commit-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.d, check=check, capture_output=True, text=True
        )

    def _init_repo(self, tracked=("f1.txt", "f2.txt")):
        # Harness artifacts are committed as ignored so the working tree is clean
        # at run start; the runner's own `.forge/` is also ignored.
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
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

    def _head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _log_subjects(self):
        return self._git("log", "--format=%s").stdout.strip().splitlines()

    def test_dirty_tree_at_start_exits_one_naming_path(self):
        plan = self._plan(PLAN_COMMIT_ONE)
        self._init_repo()
        # Dirty a tracked file before the run.
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("uncommitted\n")
        res = self._run(plan, responses=[{"exit": 0, "msg": ""}])
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("f1.txt", res.stderr)

    def test_dirty_tree_at_start_exits_one_on_resume(self):
        plan = self._plan(PLAN_COMMIT_ONE)
        self._init_repo()
        # First run passes and commits (a commit triggers the final review).
        res1 = self._run(plan, responses=[{"exit": 0, "msg": ""},
                                          {"exit": 0, "msg": _pass_msg()}])
        self.assertEqual(res1.returncode, 0, res1.stderr)
        # Now dirty the tree and resume (same run-dir).
        with open(os.path.join(self.d, "f2.txt"), "a") as f:
            f.write("uncommitted\n")
        res2 = self._run(plan, responses=[{"exit": 0, "msg": ""}])
        self.assertEqual(res2.returncode, 1, res2.stderr)
        self.assertIn("f2.txt", res2.stderr)

    def test_passed_task_creates_one_commit_with_message(self):
        plan = self._plan(PLAN_COMMIT_ONE)
        self._init_repo()
        base = self._head()
        res = self._run(plan, responses=[{"exit": 0, "msg": ""},
                                         {"exit": 0, "msg": _pass_msg()}])  # final review
        self.assertEqual(res.returncode, 0, res.stderr)
        subjects = self._log_subjects()
        self.assertEqual(subjects[0], "forge: task 1 — First task")
        # Exactly one new commit past base.
        self.assertNotEqual(self._head(), base)
        self.assertEqual(len(subjects), 2)  # base + one task commit

    def test_two_passed_tasks_one_commit_each_head_advances(self):
        plan = self._plan(PLAN_COMMIT_TWO)
        self._init_repo()
        res = self._run(plan, responses=[{"exit": 0, "msg": ""},
                                         {"exit": 0, "msg": ""},
                                         {"exit": 0, "msg": _pass_msg()}])  # final review
        self.assertEqual(res.returncode, 0, res.stderr)
        subjects = self._log_subjects()
        self.assertEqual(subjects[0], "forge: task 2 — Second task")
        self.assertEqual(subjects[1], "forge: task 1 — First task")
        self.assertEqual(len(subjects), 3)  # base + two task commits
        # Each commit isolates its own file change.
        t1 = self._git("show", "--stat", "HEAD~1").stdout
        self.assertIn("f1.txt", t1)
        self.assertNotIn("f2.txt", t1)

    def test_escalated_task_creates_no_commit(self):
        # The reviewed task escalates (a persistent in-diff fix finding -> stuck);
        # an escalated task is never committed. f1.txt is tracked, so the finding
        # at f1.txt:2 (the appended line) is verified in-diff.
        plan = self._plan(PLAN_COMMIT_STD)
        self._init_repo()
        base = self._head()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                        # worker a1
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "x")},  # review a1
            {"exit": 0, "msg": ""},                        # worker a2
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "x")},  # review a2 (stuck)
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        # No task commit — HEAD unchanged from base.
        self.assertEqual(self._head(), base)
        self.assertEqual(len(self._log_subjects()), 1)

    def test_base_commit_persisted_in_run_json(self):
        plan = self._plan(PLAN_COMMIT_ONE)
        self._init_repo()
        base = self._head()
        res = self._run(plan, responses=[{"exit": 0, "msg": ""},
                                         {"exit": 0, "msg": _pass_msg()}])  # final review
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["base_commit"], base)
        # The passed task records its commit SHA.
        t1 = next(t for t in summary["tasks"] if t["number"] == 1)
        self.assertEqual(t1["commit"], self._head())

    def test_final_review_base_is_persisted_base_commit_across_resume(self):
        # Task 1 passes and commits on run 1 (HEAD moves). Task 2 escalates, so
        # run 1 halts. On resume (run 2), task 2 passes; the final review must
        # diff the ORIGINAL base_commit (before task 1), not run-2's HEAD — so
        # its packet carries BOTH task markers.
        plan = self._plan(PLAN_COMMIT_ONE_THEN_STD)
        self._init_repo()
        base = self._head()
        res1 = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                             # t1 worker (trivial)
            {"exit": 0, "msg": ""},                             # t2 worker a1
            {"exit": 0, "msg": _fix_findings_msg("f2.txt", "2", "x")},  # t2 review a1
            {"exit": 0, "msg": ""},                             # t2 worker a2
            {"exit": 0, "msg": _fix_findings_msg("f2.txt", "2", "x")},  # t2 review a2 (stuck)
        ])
        self.assertEqual(res1.returncode, 2, res1.stderr)
        self.assertNotEqual(self._head(), base)  # task 1 committed
        # The escalated task's attempt left f2.txt dirty; the human discards it
        # before resume (the precondition requires a clean tree).
        self._git("reset", "--hard")
        # Resume: task 2 passes, then final review.
        res2 = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # t2 worker
            {"exit": 0, "msg": _pass_msg()},  # t2 review
            {"exit": 0, "msg": _pass_msg()},  # final review
        ])
        self.assertEqual(res2.returncode, 0, res2.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        self.assertEqual(summary["base_commit"], base)
        with open(os.path.join(self.run_dir, "final-review.md")) as f:
            packet = f.read()
        self.assertIn("ONEMARK", packet)
        self.assertIn("TWOMARK", packet)

    def test_noop_task_skips_commit_no_empty_commit(self):
        self._init_repo()
        # Plan lives outside the repo, so its ledger annotation doesn't dirty the
        # tree; the task's acceptance (`true`) changes nothing in the repo, so the
        # stage is empty and the commit must be skipped (no empty commit).
        plandir = tempfile.mkdtemp(prefix="forge-run-noop-plan-")
        self.addCleanup(shutil.rmtree, plandir, ignore_errors=True)
        plan = os.path.join(plandir, "plan.md")
        with open(plan, "w") as f:
            f.write(PLAN_COMMIT_NOOP)
        base = self._head()
        res = self._run(plan, responses=[{"exit": 0, "msg": ""}])
        self.assertEqual(res.returncode, 0, res.stderr)
        # No file changed -> no commit; HEAD unchanged, summary commit is null.
        self.assertEqual(self._head(), base)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            summary = json.load(f)
        t1 = next(t for t in summary["tasks"] if t["number"] == 1)
        self.assertIsNone(t1["commit"])

    def test_commit_task_raises_loud_when_git_add_fails(self):
        # `git add -A` failure must fail loud (like every other git call), not
        # silently fall through to an empty-stage skip that drops the task's real
        # changes with no error. Forced deterministically via an unwritable index.
        self._init_repo()
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("change\n")
        task = types.SimpleNamespace(number=1, title="First")
        bad_index = os.path.join(self.d, "nonexistent-dir", "index")
        prev = os.environ.get("GIT_INDEX_FILE")
        os.environ["GIT_INDEX_FILE"] = bad_index

        def _restore():
            if prev is None:
                os.environ.pop("GIT_INDEX_FILE", None)
            else:
                os.environ["GIT_INDEX_FILE"] = prev
        self.addCleanup(_restore)
        with self.assertRaises(RuntimeError) as cm:
            forge_run._git_commit_task(self.d, task)
        self.assertIn("git add", str(cm.exception).lower())

    def test_snapshot_worktree_is_retired(self):
        # The stash-snapshot per-task base is replaced by the prior commit.
        self.assertFalse(hasattr(forge_run, "_snapshot_worktree"))
        src = SCRIPT_PATH.read_text()
        self.assertNotIn("_snapshot_worktree", src)
