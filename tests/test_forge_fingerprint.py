"""Repository fingerprint, tree diff, freeze_tree, restore_refs and the
forge_fingerprint CLI (Reviewer write discipline spec)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _forge_support import *  # noqa: F401,F403

import forge_git

CLI = str(REPO_ROOT / "scripts" / "forge_fingerprint.py")


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-fp-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.write(".gitignore", "ignored/\n.forge/\n")
        self.write("f1.txt", "base\n")
        self.write("f2.txt", "two\n")
        _git(self.d, "init", "-b", "main")
        _git(self.d, "config", "user.email", "t@example.com")
        _git(self.d, "config", "user.name", "Test")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-m", "base")

    def write(self, rel, text, mode="w"):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode) as f:
            f.write(text)

    def status(self):
        return _git(self.d, "status", "--porcelain")

    def head(self):
        return _git(self.d, "rev-parse", "HEAD").strip()


class FingerprintTests(RepoCase):
    def test_stable_across_two_calls(self):
        a = forge_git.repo_fingerprint(self.d)
        b = forge_git.repo_fingerprint(self.d)
        self.assertEqual(a, b)
        self.assertEqual(set(a), {"tree", "index", "head", "branch"})
        self.assertEqual(a["head"], self.head())
        self.assertEqual(a["branch"], "refs/heads/main")

    def _changes(self, mutate):
        before = forge_git.repo_fingerprint(self.d)
        mutate()
        after = forge_git.repo_fingerprint(self.d)
        self.assertNotEqual(before, after)

    def test_changes_on_tracked_edit(self):
        self._changes(lambda: self.write("f1.txt", "edited\n"))

    def test_changes_on_new_untracked_file(self):
        self._changes(lambda: self.write("new.txt", "n\n"))

    def test_changes_on_tracked_deletion(self):
        self._changes(lambda: os.remove(os.path.join(self.d, "f2.txt")))

    def test_changes_on_git_add(self):
        self.write("new.txt", "n\n")
        before = forge_git.repo_fingerprint(self.d)
        _git(self.d, "add", "new.txt")
        after = forge_git.repo_fingerprint(self.d)
        self.assertNotEqual(before["index"], after["index"])
        self.assertEqual(before["tree"], after["tree"])

    def test_changes_on_commit(self):
        def commit():
            self.write("new.txt", "n\n")
            _git(self.d, "add", "-A")
            _git(self.d, "commit", "-m", "more")
        self._changes(commit)

    def test_changes_on_branch_switch(self):
        before = forge_git.repo_fingerprint(self.d)
        _git(self.d, "checkout", "-b", "other")
        after = forge_git.repo_fingerprint(self.d)
        self.assertNotEqual(before, after)
        self.assertEqual(before["head"], after["head"])
        self.assertEqual(after["branch"], "refs/heads/other")

    def test_detached_head_branch_is_null(self):
        _git(self.d, "checkout", "--detach")
        self.assertIsNone(forge_git.repo_fingerprint(self.d)["branch"])

    def test_stable_across_gitignored_write(self):
        before = forge_git.repo_fingerprint(self.d)
        self.write("ignored/x.txt", "x\n")
        self.write(".forge/y.txt", "y\n")
        self.assertEqual(before, forge_git.repo_fingerprint(self.d))

    def test_status_unchanged_by_fingerprint(self):
        self.write("f1.txt", "edited\n")
        self.write("new.txt", "n\n")
        _git(self.d, "add", "f2.txt")
        before = self.status()
        forge_git.repo_fingerprint(self.d)
        forge_git.capture_tree(self.d)
        self.assertEqual(before, self.status())

    def test_git_failure_raises_fingerprint_error_not_runtime_error(self):
        empty = tempfile.mkdtemp(prefix="forge-fp-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with self.assertRaises(forge_git.FingerprintError) as cm:
            forge_git.repo_fingerprint(empty)
        self.assertNotIsInstance(cm.exception, RuntimeError)
        self.assertIn("git", str(cm.exception))

    def test_error_classes_are_not_runtime_errors(self):
        self.assertFalse(issubclass(forge_git.RepositoryChangedError, RuntimeError))
        self.assertFalse(issubclass(forge_git.FingerprintError, RuntimeError))
        e = forge_git.RepositoryChangedError(["x"])
        self.assertEqual(e.changes, ["x"])


class FingerprintDiffTests(RepoCase):
    def test_equal_is_empty(self):
        a = forge_git.repo_fingerprint(self.d)
        self.assertEqual(forge_git.fingerprint_diff(self.d, a, dict(a)), [])

    def test_names_path_on_tracked_edit(self):
        a = forge_git.repo_fingerprint(self.d)
        self.write("f1.txt", "edited\n")
        b = forge_git.repo_fingerprint(self.d)
        lines = forge_git.fingerprint_diff(self.d, a, b)
        self.assertTrue(any("f1.txt" in ln for ln in lines), lines)

    def test_names_component_on_commit(self):
        a = forge_git.repo_fingerprint(self.d)
        self.write("f1.txt", "edited\n")
        _git(self.d, "commit", "-am", "c")
        b = forge_git.repo_fingerprint(self.d)
        lines = forge_git.fingerprint_diff(self.d, a, b)
        self.assertTrue(any("head" in ln.lower() for ln in lines), lines)
        self.assertTrue(any(a["head"] in ln and b["head"] in ln for ln in lines), lines)


class CliTests(RepoCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, CLI, *args], cwd=self.d, capture_output=True, text=True
        )

    def test_snapshot_prints_json(self):
        p = self.run_cli("snapshot")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('"tree"', p.stdout)
        self.assertEqual(json.loads(p.stdout), forge_git.repo_fingerprint(self.d))

    def test_verify_exit_codes(self):
        fp = self.run_cli("snapshot").stdout.strip()
        ok = self.run_cli("verify", fp)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.write("f1.txt", "edited\n")
        bad = self.run_cli("verify", fp)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("f1.txt", bad.stdout)

    def test_verify_accepts_a_path(self):
        fp = self.run_cli("snapshot").stdout
        path = os.path.join(self.d, ".forge", "fp.json")
        self.write(".forge/fp.json", fp)
        self.assertEqual(self.run_cli("verify", path).returncode, 0)

    def test_verify_exits_1_on_git_failure(self):
        fp = self.run_cli("snapshot").stdout.strip()
        empty = tempfile.mkdtemp(prefix="forge-fp-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        p = subprocess.run([sys.executable, CLI, "verify", fp], cwd=empty,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)

    def test_verify_malformed_input_exits_1(self):
        p = self.run_cli("verify", "{not json")
        self.assertEqual(p.returncode, 1)


class TreeDiffTests(RepoCase):
    def test_untracked_edit_deletion_and_unchanged(self):
        self.write("u_edit.txt", "one\n")
        self.write("u_del.txt", "gone\n")
        self.write("u_same.txt", "same\n")
        snap = forge_git.snapshot_tree(self.d)
        self.write("u_edit.txt", "one\ntwo\n")
        os.remove(os.path.join(self.d, "u_del.txt"))
        self.write("u_add.txt", "added\n")
        delta = forge_git.repair_delta(self.d, snap)
        self.assertEqual(delta.count("diff --git"), 3, delta)
        self.assertIn("+two", delta)
        self.assertIn("deleted file mode", delta)
        self.assertIn("new file mode", delta)
        self.assertNotIn("u_same.txt", delta)

    def test_tree_diff_is_git_diff_text(self):
        a = forge_git.capture_tree(self.d)
        self.write("f1.txt", "edited\n")
        b = forge_git.capture_tree(self.d)
        self.assertEqual(forge_git.tree_diff(self.d, a, b), _git(self.d, "diff", a, b))

    def test_snapshot_tree_none_outside_repo(self):
        e = tempfile.mkdtemp(prefix="forge-fp-norepo-")
        self.addCleanup(shutil.rmtree, e, ignore_errors=True)
        self.assertIsNone(forge_git.snapshot_tree(e))


class FreezeTests(RepoCase):
    def test_freeze_attempt_new_files_only_freezes_non_empty(self):
        self.write("brand_new.txt", "hi\n")
        sha = forge_git.freeze_attempt(self.d, "refs/forge/freeze/r/task-1")
        self.assertTrue(sha)
        self.assertIn("brand_new.txt",
                      _git(self.d, "show", "--name-only", "--format=", sha))
        self.assertEqual(self.status(), "")

    def test_freeze_attempt_clean_tree_is_none(self):
        self.assertIsNone(forge_git.freeze_attempt(self.d, "refs/forge/freeze/r/t"))

    def test_freeze_tree_parks_captured_content_only(self):
        self.write("worker.txt", "work\n")
        tree = forge_git.capture_tree(self.d)
        self.write("reviewer.txt", "late\n")  # created after the capture
        ref = "refs/forge/freeze/r/task-1"
        sha = forge_git.freeze_tree(self.d, tree, ref, self.head())
        self.assertEqual(_git(self.d, "rev-parse", ref).strip(), sha)
        self.assertEqual(_git(self.d, "rev-parse", sha + "^{tree}").strip(), tree)
        self.assertEqual(_git(self.d, "rev-parse", sha + "^").strip(), self.head())
        files = _git(self.d, "ls-tree", "-r", "--name-only", sha)
        self.assertIn("worker.txt", files)
        self.assertNotIn("reviewer.txt", files)
        self.assertEqual(self.status(), "")


class RestoreRefsTests(RepoCase):
    def test_after_commit_branch_moves_back_commit_stays_present(self):
        fp = forge_git.repo_fingerprint(self.d)
        self.write("f1.txt", "edited\n")
        _git(self.d, "commit", "-am", "reviewer commit")
        rogue = self.head()
        self.assertNotEqual(rogue, fp["head"])
        forge_git.restore_refs(self.d, fp)
        self.assertEqual(self.head(), fp["head"])
        self.assertEqual(_git(self.d, "rev-parse", "refs/heads/main").strip(), fp["head"])
        self.assertEqual(_git(self.d, "cat-file", "-t", rogue).strip(), "commit")
        branches = _git(self.d, "branch", "--contains", rogue)
        self.assertEqual(branches.strip(), "")

    def test_after_branch_switch_reattaches(self):
        fp = forge_git.repo_fingerprint(self.d)
        _git(self.d, "checkout", "-b", "other")
        forge_git.restore_refs(self.d, fp)
        self.assertEqual(
            _git(self.d, "symbolic-ref", "HEAD").strip(), "refs/heads/main")

    def test_detached_fingerprint_detaches(self):
        _git(self.d, "checkout", "--detach")
        fp = forge_git.repo_fingerprint(self.d)
        _git(self.d, "checkout", "main")
        forge_git.restore_refs(self.d, fp)
        p = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=self.d,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.head(), fp["head"])


class FreezeCliTests(RepoCase):
    REF = "refs/forge/freeze/r/task-1"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, CLI, *args], cwd=self.d, capture_output=True, text=True
        )

    def snapshot(self):
        return self.run_cli("snapshot").stdout.strip()

    def test_after_reviewer_commit_branch_returns_and_ref_holds_recorded_tree(self):
        self.write("worker.txt", "work\n")
        fp = self.snapshot()
        rec = json.loads(fp)
        self.write("reviewer.txt", "late\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-m", "reviewer commit")
        rogue = self.head()
        p = self.run_cli("freeze", fp, "--ref", self.REF)
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = p.stdout.strip()
        self.assertEqual(_git(self.d, "rev-parse", self.REF).strip(), sha)
        self.assertEqual(_git(self.d, "rev-parse", sha + "^{tree}").strip(), rec["tree"])
        self.assertEqual(_git(self.d, "rev-parse", sha + "^").strip(), rec["head"])
        self.assertEqual(self.head(), rec["head"])
        files = _git(self.d, "ls-tree", "-r", "--name-only", sha)
        self.assertIn("worker.txt", files)
        self.assertNotIn("reviewer.txt", files)
        self.assertEqual(_git(self.d, "branch", "--contains", rogue).strip(), "")
        self.assertEqual(self.status(), "")

    def test_after_branch_switch_head_is_reattached(self):
        self.write("worker.txt", "work\n")
        fp = self.snapshot()
        _git(self.d, "checkout", "-b", "other")
        p = self.run_cli("freeze", fp, "--ref", self.REF)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(_git(self.d, "symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.assertEqual(self.status(), "")

    def test_prints_none_when_recorded_tree_equals_head_tree(self):
        fp = self.snapshot()
        self.write("reviewer.txt", "late\n")
        p = self.run_cli("freeze", fp, "--ref", self.REF)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "none")
        self.assertNotEqual(
            subprocess.run(["git", "rev-parse", "-q", "--verify", self.REF],
                           cwd=self.d, capture_output=True).returncode, 0)
        self.assertEqual(self.status(), "")

    def test_git_failure_exits_1_naming_the_command(self):
        fp = self.snapshot()
        bad = json.loads(fp)
        bad["head"] = "0" * 40
        p = self.run_cli("freeze", json.dumps(bad), "--ref", self.REF)
        self.assertEqual(p.returncode, 1)
        self.assertIn("git", p.stderr)


if __name__ == "__main__":
    unittest.main()
