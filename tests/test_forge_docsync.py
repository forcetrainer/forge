"""Terminal doc-sync stage: reconcile-only doc updates committed as `docs: sync`,
the contradiction halt, and the all-green gating in run_plan (Phase 7 Task 7)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _forge_support import *  # noqa: F401,F403


class DispatchDocSyncTests(unittest.TestCase):
    """dispatch_doc_sync exercised directly against the fake codex over a real
    git repo: a stale doc is reconciled + committed `docs: sync`, no drift makes
    no commit, and an unreconcilable contradiction halts with the conflict named
    (Terminal doc-sync stage spec)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-docsync-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.d, "fakelog")
        self._set_env("FORGE_FAKE_LOG", self.log)

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
        # A base commit (docs + a tracked file), then a task-work commit --
        # run_base is the pre-task-work commit so the whole-plan diff is non-empty.
        # Harness artifacts are gitignored so an unrelated file never lands in the
        # doc-sync commit.
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        with open(os.path.join(self.d, "README.md"), "w") as f:
            f.write("# Docs\n\nold reference\n")
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._git("add", "-A")
        self._git("commit", "-m", "base")
        run_base = forge_run._git_head(self.d)
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("NEWLINE\n")
        self._git("add", "-A")
        self._git("commit", "-m", "task work")
        return run_base

    def _log_subjects(self):
        return subprocess.run(
            ["git", "log", "--format=%s"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()

    def test_stale_doc_reconciled_and_committed(self):
        run_base = self._init_repo_with_task_work()
        self._responses([
            {"exit": 0, "msg": '{"doc_sync": "reconciled"}',
             "append_file": os.path.join(self.d, "README.md"),
             "append_text": "new reference\n"},
        ])
        diff = forge_run._git_diff(self.d, run_base)
        result = forge_run.dispatch_doc_sync(
            self.spec, run_base, diff, self.run_dir, "standard", self.fake, self.d,
        )
        self.assertEqual(result.status, "reconciled")
        self.assertIsNotNone(result.commit)
        self.assertIn("README.md", result.reconciled)
        self.assertEqual(self._log_subjects()[0], "docs: sync")

    def test_no_doc_drift_makes_no_commit(self):
        run_base = self._init_repo_with_task_work()
        before = self._log_subjects()
        self._responses([
            {"exit": 0, "msg": '{"doc_sync": "clean"}'},  # no edit
        ])
        diff = forge_run._git_diff(self.d, run_base)
        result = forge_run.dispatch_doc_sync(
            self.spec, run_base, diff, self.run_dir, "standard", self.fake, self.d,
        )
        self.assertEqual(result.status, "clean")
        self.assertIsNone(result.commit)
        self.assertEqual(self._log_subjects(), before)
        self.assertNotIn("docs: sync", " ".join(self._log_subjects()))

    def test_contradiction_halts_with_name(self):
        run_base = self._init_repo_with_task_work()
        before = self._log_subjects()
        self._responses([
            {"exit": 0, "msg": json.dumps({
                "doc_sync": "contradiction",
                "contradiction":
                    "README claims f1 is JSON but the diff makes it plain text",
            })},
        ])
        diff = forge_run._git_diff(self.d, run_base)
        result = forge_run.dispatch_doc_sync(
            self.spec, run_base, diff, self.run_dir, "standard", self.fake, self.d,
        )
        self.assertEqual(result.status, "halt")
        self.assertIn("README claims f1 is JSON", result.contradiction)
        self.assertIsNone(result.commit)
        self.assertEqual(self._log_subjects(), before)  # no docs: sync commit


class DocSyncRunPlanGatingTests(unittest.TestCase):
    """Doc-sync runs once, only after an all-green final review (Terminal
    doc-sync stage), exercised through the full CLI over a git repo."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-docsync-cli-")
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
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        with open(os.path.join(self.d, "README.md"), "w") as f:
            f.write("# Docs\n\nold\n")
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

    def _log_subjects(self):
        return subprocess.run(
            ["git", "log", "--format=%s"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()

    def test_doc_sync_runs_after_all_green_final_review(self):
        # PLAN_STD_TRACKED's acceptance appends to committed f1.txt -> non-empty
        # whole-plan diff -> final review runs and passes -> doc-sync dispatched.
        plan = self._plan(PLAN_STD_TRACKED)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # task 1 review
            {"exit": 0, "msg": _pass_msg()},  # final review
            {"exit": 0, "msg": '{"doc_sync": "reconciled"}',   # doc-sync
             "append_file": os.path.join(self.d, "README.md"),
             "append_text": "synced\n"},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("docs: sync", self._log_subjects())
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["doc_sync"]["status"], "reconciled")
        self.assertIn("README.md", data["doc_sync"]["reconciled"])

    def test_doc_sync_skipped_when_final_review_escalates(self):
        # Final review halts on a pre-existing contract-breaking finding (its
        # location falls outside the whole-plan diff) -> doc-sync never runs, and
        # no `docs: sync` commit is created.
        plan = self._plan(PLAN_STD_TRACKED)
        self._init_repo()
        repair = {"title": "fix legacy", "tier": "standard"}
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # task 1 review
            {"exit": 0, "msg": _fix_findings_msg(   # final review: pre-existing halt
                "untouched.py", "5", "legacy bug",
                contract_ref="Spec §X", repair_task=repair)},
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertNotIn("docs: sync", self._log_subjects())
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["status"], "escalated-final-review")
        self.assertNotIn("doc_sync", data)


if __name__ == "__main__":
    unittest.main()
