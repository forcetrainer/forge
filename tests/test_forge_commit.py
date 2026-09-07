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

import forge_git


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


PLAN_NEW_FILE_ONLY = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: New file only
- [ ] Done

**Acceptance:** `printf 'def added():\\n    return 1\\n' > brand_new.py`

**Tier:** standard

**Depends on:** nothing
"""


class UntrackedFilesReviewedTests(CommitDisciplineTests):
    """A task whose whole implementation is new files (nothing tracked
    touched) must still put that implementation in front of the reviewer —
    the per-task packet's diff includes untracked files, and the task's
    commit sweeps them in as before."""

    def test_new_file_only_task_review_packet_contains_the_file(self):
        plan = self._plan(PLAN_NEW_FILE_ONLY)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},             # worker
            {"exit": 0, "msg": _pass_msg()},    # per-task reviewer
            {"exit": 0, "msg": _pass_msg()},    # final review
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-review.md")) as f:
            packet = f.read()
        self.assertIn("+++ b/brand_new.py", packet)
        self.assertIn("+def added():", packet)
        self.assertNotIn("no changes vs", packet)
        # Commit discipline unchanged: the new file rides in the task commit.
        self.assertIn("brand_new.py", self._git("show", "--stat", "HEAD").stdout)


class FreezeAttemptTests(unittest.TestCase):
    """Task 1 (halt-resume): the freeze/restore primitives. A halted task's
    in-progress attempt — tracked AND untracked non-ignored changes — is
    captured as a commit parked off the mainline under a forge-owned ref, the
    working tree returns to HEAD with nothing staged, and the frozen change is
    replayed (or reported as conflicting) on resume."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-freeze-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.d, check=check, capture_output=True, text=True
        )

    def _init_repo(self):
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("ignored.txt\n.forge/\n")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _write(self, name, text):
        path = os.path.join(self.d, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def _head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _status(self):
        return self._git("status", "--porcelain").stdout

    def _ref(self):
        return forge_git.freeze_ref_name("20260907T101954", 3)

    def test_freeze_ref_name_shape(self):
        self.assertEqual(
            forge_git.freeze_ref_name("20260907T101954", 3),
            "refs/forge/freeze/20260907T101954/task-3",
        )

    def test_freezes_tracked_modification_tree_clean_and_ref_resolves(self):
        self._init_repo()
        head = self._head()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        self.assertIsNotNone(sha)
        # Working tree back at the checkpoint.
        self.assertEqual(self._status(), "")
        with open(os.path.join(self.d, "f1.txt")) as f:
            self.assertEqual(f.read(), "base\n")
        # The ref resolves to the captured commit, whose parent is HEAD and
        # whose content is the frozen work.
        self.assertEqual(
            self._git("rev-parse", self._ref()).stdout.strip(), sha)
        self.assertEqual(
            self._git("rev-parse", sha + "^").stdout.strip(), head)
        self.assertIn(
            "in progress", self._git("show", sha + ":f1.txt").stdout)

    def test_freezes_and_restores_an_untracked_new_file(self):
        # `git stash create` never sees untracked files; a task built entirely
        # from new files would freeze as empty. This is the case that exists.
        self._init_repo()
        self._write("brand_new.py", "def added():\n    return 1\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        self.assertIsNotNone(sha)
        self.assertFalse(os.path.exists(os.path.join(self.d, "brand_new.py")))
        self.assertEqual(self._status(), "")
        self.assertTrue(forge_git.restore_freeze(self.d, sha))
        with open(os.path.join(self.d, "brand_new.py")) as f:
            self.assertIn("def added():", f.read())

    def test_gitignored_path_is_left_out_of_the_freeze(self):
        self._init_repo()
        self._write("f1.txt", "base\nchange\n")
        self._write("ignored.txt", "secret\n")
        os.makedirs(os.path.join(self.d, ".forge"))
        self._write(os.path.join(".forge", "state.json"), "{}\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        names = self._git("show", "--name-only", "--format=", sha).stdout
        self.assertIn("f1.txt", names)
        self.assertNotIn("ignored.txt", names)
        self.assertNotIn(".forge", names)
        # The freeze never deletes an ignored path from the working tree.
        self.assertTrue(os.path.exists(os.path.join(self.d, "ignored.txt")))
        self.assertTrue(
            os.path.exists(os.path.join(self.d, ".forge", "state.json")))

    def test_index_left_unstaged_so_a_later_add_all_sweeps_nothing(self):
        # The recorded prior failure: a `git add -A` capture left the attempt
        # staged, and the next task's `git add -A && git commit` swept it in.
        self._init_repo()
        self._write("f1.txt", "base\nin progress\n")
        self._write("brand_new.py", "x = 1\n")
        forge_git.freeze_attempt(self.d, self._ref())
        self.assertEqual(self._git("diff", "--cached", "--stat").stdout, "")
        self._git("add", "-A")
        commit = self._git("commit", "-m", "later", check=False)
        self.assertNotEqual(commit.returncode, 0, commit.stdout)
        self.assertIn("nothing to commit", commit.stdout + commit.stderr)

    def test_returns_none_and_writes_no_ref_when_tree_equals_head(self):
        self._init_repo()
        self.assertIsNone(forge_git.freeze_attempt(self.d, self._ref()))
        self.assertNotEqual(
            self._git("rev-parse", "--verify", self._ref(), check=False).returncode, 0)

    def test_does_not_move_head_or_the_branch_ref(self):
        self._init_repo()
        head = self._head()
        branch = self._git("symbolic-ref", "HEAD").stdout.strip()
        branch_sha = self._git("rev-parse", branch).stdout.strip()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        self.assertEqual(self._head(), head)
        self.assertEqual(self._git("symbolic-ref", "HEAD").stdout.strip(), branch)
        self.assertEqual(self._git("rev-parse", branch).stdout.strip(), branch_sha)
        # The freeze is reachable only through its own ref.
        self.assertNotIn(
            sha, self._git("rev-list", branch).stdout.split())

    def test_restores_onto_a_head_that_advanced_by_an_unrelated_commit(self):
        self._init_repo()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        # The human fixes something unrelated and commits.
        self._write("other.txt", "fix\n")
        self._git("add", "-A")
        self._git("commit", "-m", "human fix")
        head = self._head()
        self.assertTrue(forge_git.restore_freeze(self.d, sha))
        with open(os.path.join(self.d, "f1.txt")) as f:
            self.assertIn("in progress", f.read())
        with open(os.path.join(self.d, "other.txt")) as f:
            self.assertEqual(f.read(), "fix\n")
        self.assertEqual(self._head(), head)

    def test_conflicting_replay_returns_false_and_leaves_tree_clean(self):
        self._init_repo()
        self._write("f1.txt", "base\nfrozen version\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        # The human's fix rewrites the same lines.
        self._write("f1.txt", "base\nhuman version\n")
        self._git("add", "-A")
        self._git("commit", "-m", "human fix on the same lines")
        head = self._head()
        self.assertFalse(forge_git.restore_freeze(self.d, sha))
        self.assertEqual(self._status(), "")
        self.assertEqual(self._head(), head)
        with open(os.path.join(self.d, "f1.txt")) as f:
            body = f.read()
        self.assertIn("human version", body)
        self.assertNotIn("<<<<<<<", body)

    def test_freeze_diff_returns_patch_text_for_a_tracked_change(self):
        self._init_repo()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        patch = forge_git.freeze_diff(self.d, sha)
        self.assertIn("f1.txt", patch)
        self.assertIn("+in progress", patch)

    def test_freeze_diff_returns_patch_text_for_an_untracked_freeze(self):
        self._init_repo()
        self._write("brand_new.py", "def added():\n    return 1\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        patch = forge_git.freeze_diff(self.d, sha)
        self.assertIn("+++ b/brand_new.py", patch)
        self.assertIn("+def added():", patch)

    def test_restore_freeze_raises_naming_the_sha_when_object_is_missing(self):
        self._init_repo()
        missing = "0" * 40
        with self.assertRaises(RuntimeError) as cm:
            forge_git.restore_freeze(self.d, missing)
        self.assertIn(missing, str(cm.exception))

    def test_freeze_diff_raises_naming_the_sha_when_object_is_missing(self):
        self._init_repo()
        missing = "0" * 40
        with self.assertRaises(RuntimeError) as cm:
            forge_git.freeze_diff(self.d, missing)
        self.assertIn(missing, str(cm.exception))

    def test_freeze_attempt_returns_none_outside_a_git_repo(self):
        self._write("f1.txt", "loose\n")
        self.assertIsNone(forge_git.freeze_attempt(self.d, self._ref()))

    # --- Rework lap: diff-driver immunity and untracked nested repos --------

    def _install_textconv_driver(self):
        """A repo-local textconv driver on ``*.txt`` — the shape a real repo
        gets from an LFS/binary-doc setup. Committed, so it is part of the
        checkpoint rather than part of the frozen change."""
        self._git("config", "diff.forgetest.textconv", "sed s/.*/CONVERTED/")
        self._write(".gitattributes", "*.txt diff=forgetest\n")
        self._git("add", "-A")
        self._git("commit", "-m", "textconv driver")

    def test_freeze_diff_is_immune_to_a_textconv_driver(self):
        self._init_repo()
        self._install_textconv_driver()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        patch = forge_git.freeze_diff(self.d, sha)
        self.assertIn("+in progress", patch)
        self.assertNotIn("CONVERTED", patch)

    def test_freeze_diff_is_immune_to_an_external_diff_driver(self):
        self._init_repo()
        self._git("config", "diff.external", "sh -c 'echo EXTERNAL' --")
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        patch = forge_git.freeze_diff(self.d, sha)
        self.assertIn("+in progress", patch)
        self.assertNotIn("EXTERNAL", patch)

    def test_restore_freeze_is_immune_to_a_textconv_driver(self):
        # A converted patch does not apply, so a driver-configured repo would
        # report a conflict that does not exist and discard the frozen work.
        self._init_repo()
        self._install_textconv_driver()
        self._write("f1.txt", "base\nin progress\n")
        sha = forge_git.freeze_attempt(self.d, self._ref())
        self.assertTrue(forge_git.restore_freeze(self.d, sha))
        with open(os.path.join(self.d, "f1.txt")) as f:
            self.assertEqual(f.read(), "base\nin progress\n")

    def test_untracked_nested_repo_raises_naming_the_path(self):
        # `git add -A` records an untracked nested repo as a gitlink whose
        # commit the outer store does not have, and `git clean -fd` will not
        # remove it — the freeze would be unrestorable and the tree would not
        # be back at HEAD. Both a nested repo with a commit and an unborn one.
        for label, commit_inner in (("committed", True), ("unborn", False)):
            with self.subTest(label):
                self.setUp()
                self._init_repo()
                self._write("f1.txt", "base\nin progress\n")
                nested = os.path.join(self.d, "nested")
                os.makedirs(nested)
                with open(os.path.join(nested, "a.txt"), "w") as f:
                    f.write("inner\n")
                for args in (("init",), ("config", "user.email", "t@example.com"),
                             ("config", "user.name", "Test")):
                    subprocess.run(["git", *args], cwd=nested, check=True,
                                   capture_output=True, text=True)
                if commit_inner:
                    for args in (("add", "-A"), ("commit", "-m", "inner")):
                        subprocess.run(["git", *args], cwd=nested, check=True,
                                       capture_output=True, text=True)
                with self.assertRaises(RuntimeError) as cm:
                    forge_git.freeze_attempt(self.d, self._ref())
                self.assertIn("nested", str(cm.exception))
                # Nothing captured, nothing destroyed: no ref, the attempt is
                # still in the tree, and the nested repo is untouched.
                self.assertNotEqual(
                    self._git("rev-parse", "--verify", self._ref(),
                              check=False).returncode, 0)
                with open(os.path.join(self.d, "f1.txt")) as f:
                    self.assertIn("in progress", f.read())
                self.assertTrue(os.path.exists(os.path.join(nested, "a.txt")))
