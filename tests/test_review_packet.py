import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "review-packet.py")

PLAN_TASK1 = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Files:**
- Modify: `foo.txt`

**Acceptance:** `true`

**Tier:** trivial

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Files:**
- Modify: `bar.txt`
"""


def run_script(args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
    )


class ReviewPacketGitFixtureTests(unittest.TestCase):
    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix="review-packet-repo-")
        self.addCleanup(shutil.rmtree, self.repo_dir, ignore_errors=True)
        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

        self.plan_path = os.path.join(self.repo_dir, "plan.md")
        with open(self.plan_path, "w") as f:
            f.write(PLAN_TASK1)

        self.src_path = os.path.join(self.repo_dir, "src.txt")
        with open(self.src_path, "w") as f:
            f.write("line one\n")

        self._git("add", ".")
        self._git("commit", "-m", "initial commit")
        self.commit1 = self._git_output("rev-parse", "HEAD").strip()

        with open(self.src_path, "a") as f:
            f.write("line two\n")
        self._git("add", ".")
        self._git("commit", "-m", "second commit")
        self.commit2 = self._git_output("rev-parse", "HEAD").strip()

    def _git(self, *args):
        subprocess.run(
            ["git"] + list(args),
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def _git_output(self, *args):
        result = subprocess.run(
            ["git"] + list(args),
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def test_packet_contains_task_block_and_diff(self):
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [self.plan_path, "1", "--base", self.commit1, "--out", out_dir]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out_path = result.stdout.strip()
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path) as f:
            content = f.read()
        self.assertIn("### Task 1: First task", content)
        self.assertNotIn("### Task 2:", content)
        self.assertIn("line two", content)
        self.assertIn("```diff", content)

    def test_clean_base_head_yields_empty_diff_notice(self):
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script([self.plan_path, "1", "--base", "HEAD", "--out", out_dir])
        self.assertEqual(result.returncode, 0, result.stderr)
        out_path = result.stdout.strip()
        with open(out_path) as f:
            content = f.read()
        self.assertIn("no changes vs HEAD", content)

    def test_bad_git_ref_exits_nonzero_with_stderr_relayed(self):
        result = run_script([self.plan_path, "1", "--base", "not-a-real-ref-xyz"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not-a-real-ref-xyz", result.stderr)

    def test_unknown_task_number_exits_nonzero(self):
        result = run_script([self.plan_path, "99", "--base", self.commit1])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Task 99", result.stderr)

    def test_fence_survives_backtick_lines_in_diff(self):
        doc_path = os.path.join(self.repo_dir, "doc.md")
        with open(doc_path, "w") as f:
            f.write("intro\n```\ncode\n```\nend\n")
        self._git("add", ".")
        self._git("commit", "-m", "add doc with fences")
        base = self._git_output("rev-parse", "HEAD").strip()
        with open(doc_path, "a") as f:
            f.write("changed tail\n")
        self._git("add", ".")
        self._git("commit", "-m", "change near fences")

        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script([self.plan_path, "1", "--base", base, "--out", out_dir])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            lines = f.read().splitlines()

        open_idx, fence = next(
            (i, l[: len(l) - len(l.lstrip("`"))])
            for i, l in enumerate(lines)
            if l.startswith("`") and l.endswith("diff")
        )
        body = lines[open_idx + 1 : len(lines) - 1 - lines[::-1].index(fence)]
        self.assertIn(" ```", body)
        for line in body:
            stripped = line.lstrip(" ")
            run = len(stripped) - len(stripped.lstrip("`"))
            self.assertLess(run, len(fence), "diff body line closes the outer fence: %r" % line)

    def test_fenced_heading_does_not_terminate_task_block(self):
        # issue #12: a fenced markdown example containing '## ...' inside the
        # task must not end the block — that emits a silently thin packet.
        fenced_plan = os.path.join(self.repo_dir, "fenced_plan.md")
        with open(fenced_plan, "w") as f:
            f.write(
                "**Goal:** Ship it.\n\n"
                "### Task 1: Do it\n"
                "```markdown\n"
                "## Example section\n"
                "```\n"
                "tail line after fence\n\n"
                "### Task 2: Other\nbody\n"
            )
        self._git("add", ".")
        self._git("commit", "-m", "add fenced plan")

        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script([fenced_plan, "1", "--base", "HEAD", "--out", out_dir])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        self.assertIn("## Example section", content)
        self.assertIn("tail line after fence", content)
        self.assertNotIn("### Task 2", content)

    # --- h1 terminator, duplicate task numbers, wrong-level diagnosis
    # (issue #13; parity with extract-brief.py, duplicated by design) ---

    def _write_plan(self, name, content):
        path = os.path.join(self.repo_dir, name)
        with open(path, "w") as f:
            f.write(content)
        self._git("add", ".")
        self._git("commit", "-m", "add " + name)
        return path

    def test_h1_heading_terminates_task_block(self):
        plan = self._write_plan(
            "h1_plan.md",
            "**Goal:** Ship it.\n\n### Task 1: Do it\ntask body\n\n"
            "# Appendix: unrelated dump\nappendix line\n",
        )
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script([plan, "1", "--base", "HEAD", "--out", out_dir])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        self.assertIn("task body", content)
        self.assertNotIn("appendix line", content)

    def test_duplicate_task_number_exits_nonzero(self):
        plan = self._write_plan(
            "dup_plan.md",
            "### Task 1: First version\nold body\n\n"
            "### Task 1: Second version\nnew body\n",
        )
        result = run_script([plan, "1", "--base", "HEAD"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Task 1", result.stderr)
        self.assertNotIn("not found", result.stderr)

    def test_wrong_level_task_heading_fails_loud_with_guidance(self):
        # Same contract as extract-brief.py (#10): name the real cause, don't
        # send the reader hunting with a generic "not found".
        plan = self._write_plan(
            "wrong_level_plan.md",
            "**Goal:** Ship it.\n\n## Task 1: Do it\nbody\n",
        )
        result = run_script([plan, "1", "--base", "HEAD"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("### Task 1:", result.stderr)
        self.assertIn("## Task 1:", result.stderr)
        self.assertNotIn("not found", result.stderr)

    def test_out_dir_honored_and_path_printed(self):
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [self.plan_path, "1", "--base", self.commit1, "--out", out_dir]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out_path = result.stdout.strip()
        self.assertEqual(os.path.dirname(out_path), os.path.abspath(out_dir))
        self.assertEqual(os.path.basename(out_path), "task-1-review.md")

    # --- --prior-findings (Phase 7 Task 5) ---

    def test_no_prior_findings_flag_output_unchanged(self):
        # Byte-identical to pre-Task-5 behavior: omitting --prior-findings
        # must not add a section or otherwise perturb the packet.
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [self.plan_path, "1", "--base", self.commit1, "--out", out_dir]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        expected = (
            "### Task 1: First task\n- [ ] Done\n\n**Files:**\n"
            "- Modify: `foo.txt`\n\n**Acceptance:** `true`\n\n"
            "**Tier:** trivial\n\n**Depends on:** nothing\n\n"
            "```diff\ndiff --git a/src.txt b/src.txt\n"
            "index 2d00bd5..e5c5c55 100644\n--- a/src.txt\n+++ b/src.txt\n"
            "@@ -1 +1,2 @@\n line one\n+line two\n```\n"
        )
        self.assertEqual(content, expected)

    def test_prior_findings_flag_appends_labeled_section(self):
        prior_findings = [
            {"id": "f1", "summary": "off-by-one in loop"},
            {"id": "f2", "summary": "unused import"},
        ]
        findings_path = os.path.join(self.repo_dir, "prior-findings.json")
        with open(findings_path, "w") as f:
            json.dump(prior_findings, f)

        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [
                self.plan_path,
                "1",
                "--base",
                self.commit1,
                "--out",
                out_dir,
                "--prior-findings",
                findings_path,
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        self.assertIn("Prior findings", content)
        self.assertIn(
            "label each current finding resolved/carried/new against these",
            content,
        )
        self.assertIn("carried_from", content)
        self.assertIn("f1", content)
        self.assertIn("f2", content)

    # --- --checklist (Phase 13 Task 4) ---

    def test_no_checklist_flag_output_unchanged(self):
        # Byte-identical to pre-Task-4 behavior: omitting --checklist must not
        # add a section or otherwise perturb the packet.
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [self.plan_path, "1", "--base", self.commit1, "--out", out_dir]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        expected = (
            "### Task 1: First task\n- [ ] Done\n\n**Files:**\n"
            "- Modify: `foo.txt`\n\n**Acceptance:** `true`\n\n"
            "**Tier:** trivial\n\n**Depends on:** nothing\n\n"
            "```diff\ndiff --git a/src.txt b/src.txt\n"
            "index 2d00bd5..e5c5c55 100644\n--- a/src.txt\n+++ b/src.txt\n"
            "@@ -1 +1,2 @@\n line one\n+line two\n```\n"
        )
        self.assertEqual(content, expected)

    def test_checklist_flag_appends_contract_checklist_section(self):
        checklist = [
            {"id": "spec:A", "source": "spec", "text": "Section A must hold"},
            {"id": "g1", "source": "global", "text": "No new deps"},
        ]
        checklist_path = os.path.join(self.repo_dir, "checklist.json")
        with open(checklist_path, "w") as f:
            json.dump(checklist, f)

        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [
                self.plan_path, "1", "--base", self.commit1, "--out", out_dir,
                "--checklist", checklist_path,
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        self.assertIn("## Contract checklist", content)
        self.assertIn("- spec:A — Section A must hold", content)
        self.assertIn("- g1 — No new deps", content)

    def test_checklist_and_prior_findings_ordering(self):
        # task block -> diff -> checklist -> prior findings.
        checklist = [{"id": "spec:A", "source": "spec", "text": "Section A"}]
        checklist_path = os.path.join(self.repo_dir, "checklist2.json")
        with open(checklist_path, "w") as f:
            json.dump(checklist, f)
        prior_findings = [{"id": "f1", "summary": "issue"}]
        findings_path = os.path.join(self.repo_dir, "prior-findings2.json")
        with open(findings_path, "w") as f:
            json.dump(prior_findings, f)

        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script(
            [
                self.plan_path, "1", "--base", self.commit1, "--out", out_dir,
                "--checklist", checklist_path,
                "--prior-findings", findings_path,
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        task_idx = content.index("### Task 1")
        diff_idx = content.index("```diff")
        checklist_idx = content.index("## Contract checklist")
        findings_idx = content.index("Prior findings")
        self.assertLess(task_idx, diff_idx)
        self.assertLess(diff_idx, checklist_idx)
        self.assertLess(checklist_idx, findings_idx)

    def test_checklist_bad_json_exits_nonzero(self):
        checklist_path = os.path.join(self.repo_dir, "checklist-bad.json")
        with open(checklist_path, "w") as f:
            f.write("not json")

        result = run_script(
            [
                self.plan_path, "1", "--base", self.commit1,
                "--checklist", checklist_path,
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(checklist_path, result.stderr)

    def test_prior_findings_bad_json_exits_nonzero(self):
        findings_path = os.path.join(self.repo_dir, "prior-findings.json")
        with open(findings_path, "w") as f:
            f.write("not json")

        result = run_script(
            [
                self.plan_path,
                "1",
                "--base",
                self.commit1,
                "--prior-findings",
                findings_path,
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(findings_path, result.stderr)


class ReviewPacketOutsideGitRepoTests(unittest.TestCase):
    def test_plan_outside_git_repo_exits_nonzero(self):
        non_repo_dir = tempfile.mkdtemp(prefix="review-packet-norepo-")
        try:
            plan_path = os.path.join(non_repo_dir, "plan.md")
            with open(plan_path, "w") as f:
                f.write(PLAN_TASK1)
            result = run_script([plan_path, "1", "--base", "HEAD"])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(result.stderr.strip())
        finally:
            shutil.rmtree(non_repo_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class UntrackedFilesInDiffTests(unittest.TestCase):
    """The review diff must include untracked (new, never-staged) files — a
    task whose whole implementation is new files otherwise reviews as an empty
    diff. ``rp.git_diff`` is read-only: no staging, no index mutation."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="review-packet-untracked-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("ignored.txt\n")
        with open(os.path.join(self.d, "tracked.txt"), "w") as f:
            f.write("base\n")
        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", ".")
        self._git("commit", "-m", "base")
        self.base = self._git("rev-parse", "HEAD").stdout.strip()
        import importlib.util
        spec = importlib.util.spec_from_file_location("rp_under_test", SCRIPT)
        self.rp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rp)

    def _git(self, *args):
        return subprocess.run(
            ["git"] + list(args), cwd=self.d, check=True,
            capture_output=True, text=True,
        )

    def _status(self):
        return self._git("status", "--porcelain").stdout

    def test_untracked_file_appears_as_new_file_hunk(self):
        with open(os.path.join(self.d, "brand_new.py"), "w") as f:
            f.write("def added():\n    return 1\n")
        diff = self.rp.git_diff(self.d, self.base)
        self.assertIn("+++ b/brand_new.py", diff)
        self.assertIn("new file mode", diff)
        self.assertIn("+def added():", diff)

    def test_tracked_change_and_untracked_file_both_present(self):
        with open(os.path.join(self.d, "tracked.txt"), "a") as f:
            f.write("changed\n")
        with open(os.path.join(self.d, "brand_new.py"), "w") as f:
            f.write("x = 1\n")
        diff = self.rp.git_diff(self.d, self.base)
        self.assertIn("+changed", diff)
        self.assertIn("+++ b/brand_new.py", diff)
        self.assertLess(diff.index("tracked.txt"), diff.index("brand_new.py"))

    def test_gitignored_file_is_not_included(self):
        with open(os.path.join(self.d, "ignored.txt"), "w") as f:
            f.write("secret\n")
        diff = self.rp.git_diff(self.d, self.base)
        self.assertEqual(diff, "")

    def test_untracked_file_in_subdirectory_uses_repo_relative_path(self):
        os.makedirs(os.path.join(self.d, "src", "pkg"))
        with open(os.path.join(self.d, "src", "pkg", "mod.py"), "w") as f:
            f.write("y = 2\n")
        diff = self.rp.git_diff(self.d, self.base)
        self.assertIn("+++ b/src/pkg/mod.py", diff)

    def test_leaves_index_and_status_untouched(self):
        with open(os.path.join(self.d, "brand_new.py"), "w") as f:
            f.write("x = 1\n")
        before = self._status()
        self.assertEqual(before.strip(), "?? brand_new.py")
        self.rp.git_diff(self.d, self.base)
        self.assertEqual(self._status(), before)

    def test_clean_tree_yields_empty_string(self):
        self.assertEqual(self.rp.git_diff(self.d, self.base), "")

    def test_bad_ref_raises_runtime_error_naming_ref(self):
        with self.assertRaises(RuntimeError) as cm:
            self.rp.git_diff(self.d, "not-a-real-ref-xyz")
        self.assertIn("not-a-real-ref-xyz", str(cm.exception))

    def test_cli_packet_includes_untracked_file(self):
        plan_path = os.path.join(self.d, "plan.md")
        with open(plan_path, "w") as f:
            f.write(PLAN_TASK1)
        self._git("add", "plan.md")
        self._git("commit", "-m", "plan")
        base = self._git("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(self.d, "brand_new.py"), "w") as f:
            f.write("z = 3\n")
        out_dir = tempfile.mkdtemp(prefix="review-packet-out-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        result = run_script([plan_path, "1", "--base", base, "--out", out_dir])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(result.stdout.strip()) as f:
            content = f.read()
        self.assertIn("+++ b/brand_new.py", content)
        self.assertIn("+z = 3", content)
        self.assertNotIn("no changes vs", content)
