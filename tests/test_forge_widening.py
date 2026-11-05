"""forge_dispose.scope_widenings, the ``widened`` subcommand, and the worker
record (Scope widening, The shared decision helper). Every test runs against a
real throwaway git repository built here."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _forge_support import *  # noqa: F401,F403 — sets up sys.path
import forge_common  # noqa: E402
import forge_dispose  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "forge_dispose.py")

PLAN = """# Plan

**Goal:** Build.
**Global Constraints:**
- Keep it small.

### Task 1: First
- [ ] Done

**Files:**
- Modify: `a.py`
- Test: `tests/test_a.py`

**Tests:**
- first behavior

**Acceptance:**
- `true` passes

**Tier:** standard

**Depends on:** nothing

### Task 2: Second
- [ ] Done

**Files:**
- Modify: `b.py`

**Tests:**
- second behavior

**Acceptance:**
- `true` passes

**Tier:** standard

**Depends on:** Task 1
"""

DECLARED_PLAN = PLAN.replace(
    "**Global Constraints:**",
    "**Spec files:**\n- docs/spec.md\n**Global Constraints:**", 1)

SPEC = "---\nsystem: spec\n---\n\n## Widening\n\ntext\n"


class WideningTestBase(unittest.TestCase):
    plan_text = PLAN

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="forge-widening-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.work = tempfile.mkdtemp(prefix="forge-widening-work-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.git("init")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        for name in ("a.py", "b.py", "c.py", "d.py", "tests/test_a.py"):
            self.write(name, "x = 1\ny = 2\n")
        self.write("docs/plan.md", self.plan_text)
        self.write("docs/spec.md", SPEC)
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.plan = os.path.join(self.repo, "docs", "plan.md")
        self.spec = os.path.join(self.repo, "docs", "spec.md")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            capture_output=True, text=True).stdout

    def write(self, rel, text, root=None):
        path = os.path.join(root or self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def edit(self, rel):
        self.write(rel, "x = 1\ny = CHANGED\nz = 3\n")

    def record(self, obj, raw=None):
        path = os.path.join(self.work, "record.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(obj))
        return path

    def commit(self, message="work"):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def detect(self, scope="t1", base="HEAD", state=None, record=None,
               spec=None, checkpoint="HEAD"):
        return forge_dispose.scope_widenings(
            self.repo, self.plan, spec, scope, base, checkpoint, state,
            record or os.path.join(self.work, "absent.json"))

    def paths(self, result):
        return [w.path for w in result.widened]


class DetectionTests(WideningTestBase):
    def test_an_existing_file_outside_the_tasks_files_modified_in_the_working_tree_is_a_widening(self):
        self.edit("c.py")
        result = self.detect()
        self.assertEqual(self.paths(result), ["c.py"])
        self.assertEqual((result.widened[0].added, result.widened[0].removed), (2, 1))
        self.assertEqual(result.deleted, [])

    def test_an_edit_inside_a_declared_file_is_not_a_widening(self):
        self.edit("a.py")
        self.edit("tests/test_a.py")
        self.assertEqual(self.detect().widened, [])
        # b.py is task 2's, not task 1's
        self.edit("b.py")
        self.assertEqual(self.paths(self.detect()), ["b.py"])

    def test_a_created_file_is_not_a_widening(self):
        self.write("new_untracked.py", "1\n")
        self.write("new_staged.py", "1\n")
        self.git("add", "new_staged.py")
        result = self.detect()
        self.assertEqual((result.widened, result.deleted), ([], []))

    def test_the_plan_file_and_every_declared_spec_file_are_never_widenings(self):
        self.write("docs/plan.md", self.plan_text + "\namended\n")
        self.write("docs/spec.md", SPEC + "\namended\n")
        self.edit("c.py")
        self.assertEqual(self.paths(self.detect(spec=self.spec)), ["c.py"])

    def test_a_legacy_plans_spec_file_is_never_a_widening(self):
        self.write("docs/spec.md", SPEC + "\namended\n")
        self.assertEqual(self.detect(spec=self.spec).widened, [])
        # without --spec the same edit is a widening: the exemption is the argument
        self.assertEqual(self.paths(self.detect()), ["docs/spec.md"])

    def test_an_out_of_scope_deleted_file_is_listed_in_deleted_not_widened(self):
        os.remove(os.path.join(self.repo, "d.py"))
        os.remove(os.path.join(self.repo, "a.py"))  # in scope: neither
        result = self.detect()
        self.assertEqual(result.deleted, ["d.py"])
        self.assertEqual(result.widened, [])

    def test_a_rename_of_an_out_of_scope_file_is_a_delete_plus_an_add_and_lists_the_old_path_as_deleted(self):
        self.git("mv", "c.py", "renamed.py")
        result = self.detect()
        self.assertEqual(result.deleted, ["c.py"])
        self.assertEqual(result.widened, [])

    def test_final_scope_includes_every_tasks_files_and_every_path_changed_between_run_base_and_head(self):
        self.edit("c.py")
        self.git("add", "c.py")
        self.git("commit", "-m", "run work")
        self.edit("a.py")
        self.edit("b.py")  # task 2's file: in final scope
        self.write("c.py", "again\n")  # changed in the run: in final scope
        self.assertEqual(self.detect(scope="final", base=self.base).widened, [])
        # the task scope for the same tree widens on b.py and c.py
        self.assertEqual(self.paths(self.detect(scope="t1")), ["b.py", "c.py"])

    def test_a_file_no_task_declared_and_the_run_never_touched_edited_during_the_final_review_is_a_widening(self):
        self.edit("c.py")
        self.git("add", "c.py")
        self.git("commit", "-m", "run work")
        self.edit("d.py")
        result = self.detect(scope="final", base=self.base)
        self.assertEqual(self.paths(result), ["d.py"])

    def test_a_git_failure_raises_naming_the_cause_and_the_cli_exits_1(self):
        with self.assertRaises(RuntimeError) as cm:
            self.detect(scope="final", base="no-such-ref")
        self.assertIn("no-such-ref", str(cm.exception))
        proc = self.run_cli("--base", "no-such-ref", "--checkpoint", "HEAD",
                            "--scope", "final")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-such-ref", proc.stderr)

    def test_a_task_scope_with_an_unknown_base_raises_naming_the_cause_and_the_cli_exits_1(self):
        with self.assertRaises(RuntimeError) as cm:
            self.detect(scope="t1", base="no-such-ref")
        self.assertIn("no-such-ref", str(cm.exception))
        proc = self.run_cli("--base", "no-such-ref", "--checkpoint", "HEAD",
                            "--scope", "t1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-such-ref", proc.stderr)

    def run_cli(self, *args, plan=None):
        return subprocess.run(
            [sys.executable, SCRIPT, "widened", "--plan", plan or self.plan,
             *args],
            cwd=self.repo, capture_output=True, text=True)


AMENDED_PLAN = PLAN.replace(
    "- Modify: `a.py`\n", "- Modify: `a.py`\n- Modify: `c.py`\n", 1)


class CheckpointTests(WideningTestBase):
    """Detection compares from the recorded checkpoint, and reads the plan as
    committed there (Scope widening, The checkpoint)."""

    def test_a_checkpoint_older_than_a_commit_holding_an_out_of_scope_edit_still_lists_the_path_as_a_widening(self):
        checkpoint = self.base
        self.edit("c.py")
        self.commit("worker commit")
        result = self.detect(checkpoint=checkpoint)
        self.assertEqual(self.paths(result), ["c.py"])
        self.assertEqual(self.paths(self.detect(checkpoint="HEAD")), [])

    def test_a_checkpoint_older_than_a_commit_holding_an_out_of_scope_deletion_still_lists_the_path_as_deleted(self):
        checkpoint = self.base
        os.remove(os.path.join(self.repo, "d.py"))
        self.commit("worker deleted")
        result = self.detect(checkpoint=checkpoint)
        self.assertEqual(result.deleted, ["d.py"])
        self.assertEqual(self.detect(checkpoint="HEAD").deleted, [])

    def test_a_worker_that_adds_a_path_to_its_tasks_files_in_the_plans_working_copy_does_not_put_that_path_in_scope(self):
        self.write("docs/plan.md", AMENDED_PLAN)
        self.edit("c.py")
        self.assertEqual(self.paths(self.detect(checkpoint=self.base)), ["c.py"])

    def test_scope_is_read_from_the_plan_as_committed_at_the_checkpoint(self):
        self.write("docs/plan.md", AMENDED_PLAN)
        amended = self.commit("amend plan")
        self.edit("c.py")
        # the checkpoint before the amendment: c.py is outside task 1
        self.assertEqual(self.paths(self.detect(checkpoint=self.base)), ["c.py"])
        # the checkpoint at the amendment: c.py is inside it
        self.assertEqual(self.detect(checkpoint=amended).widened, [])

    def test_a_plan_amendment_committed_before_the_turn_does_change_scope(self):
        self.write("docs/plan.md", AMENDED_PLAN)
        checkpoint = self.commit("amend plan")
        self.edit("c.py")
        self.assertEqual(self.detect(checkpoint=checkpoint).widened, [])

    def test_final_scope_adds_paths_changed_between_run_base_and_the_checkpoint_not_a_later_head(self):
        self.edit("c.py")
        checkpoint = self.commit("run work")
        self.edit("d.py")
        self.commit("later commit")  # a worker's commit after the checkpoint
        result = self.detect(scope="final", base=self.base, checkpoint=checkpoint)
        self.assertEqual(self.paths(result), ["d.py"])

    def test_an_accepted_widening_is_pruned_when_its_path_no_longer_differs_from_the_checkpoint(self):
        checkpoint = self.base
        self.edit("c.py")
        self.commit("worker commit")
        self.write("c.py", "x = 1\ny = 2\n")  # back to the checkpoint's content
        state = forge_dispose.ConvergenceState()
        state.accepted_widenings = {"c.py": {
            "blob": "x", "reason": "r", "serves": None, "ruling_reason": "ok"}}
        result = self.detect(checkpoint=checkpoint, state=state)
        self.assertEqual(result.widened, [])
        forge_dispose.prune_accepted_widenings(state, result)
        self.assertEqual(state.accepted_widenings, {})

    def test_line_counts_are_measured_from_the_checkpoint(self):
        self.edit("c.py")
        self.commit("worker commit")
        self.write("c.py", "x = 1\ny = CHANGED\nz = 3\nw = 4\n")
        result = self.detect(checkpoint=self.base)
        self.assertEqual((result.widened[0].added, result.widened[0].removed), (3, 1))

    def test_decision_json_line_counts_are_measured_from_the_checkpoint(self):
        self.edit("c.py")
        self.commit("worker commit")
        self.write("c.py", "x = 1\ny = CHANGED\nz = 3\nw = 4\n")
        vpath = os.path.join(self.work, "v.json")
        with open(vpath, "w") as f:
            json.dump({"verdict": "pass", "widenings": [_ruling("c.py")]}, f)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--verdict", vpath, "--base", "HEAD",
             "--checkpoint", self.base, "--scope", "t1", "--attempt", "1",
             "--acceptance-ok", "true", "--autofix", "auto", "--plan", self.plan],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (entry,) = json.loads(proc.stdout)["widenings"]["accepted"]
        self.assertEqual((entry["added"], entry["removed"]), (3, 1))

    def test_a_checkpoint_that_is_not_a_commit_raises_naming_the_cause_and_the_cli_exits_1(self):
        with self.assertRaises(RuntimeError) as cm:
            self.detect(checkpoint="not-a-commit")
        self.assertIn("not-a-commit", str(cm.exception))
        self.assertIn("checkpoint", str(cm.exception))
        tree = self.git("rev-parse", "HEAD^{tree}").strip()
        with self.assertRaises(RuntimeError) as cm:
            self.detect(checkpoint=tree)  # an object, but not a commit
        self.assertIn("checkpoint", str(cm.exception))
        proc = subprocess.run(
            [sys.executable, SCRIPT, "widened", "--plan", self.plan,
             "--base", "HEAD", "--checkpoint", "not-a-commit", "--scope", "t1"],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not-a-commit", proc.stderr)

    def test_a_plan_that_does_not_exist_at_the_checkpoint_raises_naming_the_plan_and_the_checkpoint(self):
        empty = self.git("hash-object", "-t", "tree", "/dev/null").strip()
        bare = self.git("commit-tree", empty, "-m", "no plan").strip()
        with self.assertRaises(RuntimeError) as cm:
            self.detect(checkpoint=bare)
        self.assertIn("plan.md", str(cm.exception))
        self.assertIn(bare, str(cm.exception))

    def test_a_plan_outside_the_repository_root_is_read_as_given(self):
        outside = self.write("plan.md", self.plan_text, root=self.work)
        result = forge_dispose.scope_widenings(
            self.repo, outside, None, "t1", "HEAD", "HEAD", None,
            os.path.join(self.work, "absent.json"))
        self.edit("c.py")
        result = forge_dispose.scope_widenings(
            self.repo, outside, None, "t1", "HEAD", "HEAD", None,
            os.path.join(self.work, "absent.json"))
        self.assertEqual(self.paths(result), ["c.py"])

    def test_an_in_repo_plan_that_is_git_ignored_untracked_or_deleted_raises_naming_the_plan_and_the_checkpoint(self):
        head = self.git("rev-parse", "HEAD").strip()
        # ignored (and untracked at a checkpoint that lacks it)
        self.write("ignored-plan.md", self.plan_text)
        self.write(".gitignore", "ignored-plan.md\n")
        self.write("loose-plan.md", self.plan_text)
        self.git("rm", "-q", "docs/plan.md")
        self.git("commit", "-m", "remove plan")
        removed = self.git("rev-parse", "HEAD").strip()
        cases = [
            (os.path.join(self.repo, "ignored-plan.md"), head),
            (os.path.join(self.repo, "loose-plan.md"), head),
            (self.plan, removed),  # deleted
        ]
        for plan, checkpoint in cases:
            with self.subTest(plan=os.path.basename(plan)):
                with self.assertRaises(RuntimeError) as cm:
                    forge_dispose.scope_widenings(
                        self.repo, plan, None, "t1", "HEAD", checkpoint, None,
                        os.path.join(self.work, "absent.json"))
                self.assertIn(os.path.basename(plan), str(cm.exception))
                self.assertIn(checkpoint, str(cm.exception))

    def test_an_amendment_committed_alone_moves_the_checkpoint_so_the_amended_path_is_in_scope(self):
        self.edit("a.py")
        self.commit("task commit")
        self.write("docs/plan.md", AMENDED_PLAN)
        amendment = self.commit("amend plan: task 1 also edits c.py")
        # uncommitted task work on top: the amended path and another one
        self.edit("c.py")
        self.edit("d.py")
        result = self.detect(base=amendment, checkpoint=amendment)
        self.assertEqual(self.paths(result), ["d.py"])
        # diffed from the amendment commit: the earlier a.py edit is not in play
        self.assertEqual((result.widened[0].added, result.widened[0].removed), (2, 1))

    def test_a_working_copy_spec_files_addition_does_not_exempt_the_spec(self):
        declared = PLAN.replace(
            "**Global Constraints:**",
            "**Spec files:**\n- docs/spec.md\n**Global Constraints:**", 1)
        self.write("docs/plan.md", declared)  # working copy only: not committed
        self.write("docs/spec.md", SPEC + "\namended\n")
        result = self.detect(checkpoint=self.base)
        self.assertEqual(self.paths(result), ["docs/spec.md"])

    def test_a_working_copy_only_test_case_is_not_a_citable_serves(self):
        extra = PLAN.replace("- first behavior\n", "- first behavior\n- extra behavior\n", 1)
        self.write("docs/plan.md", extra)  # working copy only: not committed
        self.edit("c.py")
        rec = self.record([{"path": "c.py", "reason": "r", "serves": "t1.t2"}])
        result = self.detect(checkpoint=self.base, record=rec)
        self.assertEqual(len(result.record_defects), 1)
        self.assertIn("t1.t2", result.record_defects[0])
        ok = self.record([{"path": "c.py", "reason": "r", "serves": "t1.t1"}])
        self.assertEqual(self.detect(checkpoint=self.base, record=ok).record_defects, [])
        # committed, it is citable
        checkpoint = self.commit("amend plan: add a test case")
        self.edit("d.py")
        self.write("docs/plan.md", extra)
        rec = self.record([{"path": "d.py", "reason": "r", "serves": "t1.t2"}])
        self.assertEqual(self.detect(checkpoint=checkpoint, record=rec).record_defects, [])

    def test_omitting_checkpoint_is_a_usage_error_naming_the_flag_on_both_clis(self):
        widened = subprocess.run(
            [sys.executable, SCRIPT, "widened", "--plan", self.plan,
             "--base", "HEAD", "--scope", "t1"],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(widened.returncode, 2)
        self.assertIn("--checkpoint", widened.stderr)
        decision = subprocess.run(
            [sys.executable, SCRIPT, "--verdict", "v.json", "--base", "HEAD",
             "--scope", "t1", "--attempt", "1", "--acceptance-ok", "true",
             "--autofix", "auto", "--plan", self.plan],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(decision.returncode, 2)
        self.assertIn("--checkpoint", decision.stderr)


class RecordTests(WideningTestBase):
    def setUp(self):
        super().setUp()
        self.edit("c.py")

    def entry(self, **kw):
        e = {"path": "c.py", "reason": "needed", "serves": None}
        e.update(kw)
        return e

    def test_the_records_reason_and_serves_attach_to_their_path(self):
        rec = self.record([self.entry(serves="t1.t1")])
        result = self.detect(record=rec)
        self.assertEqual(result.record_defects, [])
        w = result.widened[0]
        self.assertEqual((w.reason, w.serves), ("needed", "t1.t1"))
        self.assertEqual(result.due, result.widened)

    def test_an_absent_record_leaves_every_widening_unjustified(self):
        result = self.detect()
        self.assertEqual(result.record_defects, [])
        self.assertEqual((result.widened[0].reason, result.widened[0].serves),
                         (None, None))

    def test_a_record_that_does_not_parse_is_a_record_defect(self):
        result = self.detect(record=self.record(None, raw="{not json"))
        self.assertEqual(len(result.record_defects), 1)
        self.assertIn("record", result.record_defects[0])
        wrong_shape = self.detect(record=self.record({"path": "c.py"}))
        self.assertEqual(len(wrong_shape.record_defects), 1)

    def test_a_record_entry_for_a_path_that_is_not_a_widening_is_a_record_defect(self):
        result = self.detect(record=self.record([self.entry(path="a.py")]))
        self.assertEqual(len(result.record_defects), 1)
        self.assertIn("a.py", result.record_defects[0])

    def test_a_duplicate_record_path_is_a_record_defect(self):
        result = self.detect(record=self.record([self.entry(), self.entry()]))
        self.assertEqual(len(result.record_defects), 1)
        self.assertIn("duplicate", result.record_defects[0])
        self.assertIn("c.py", result.record_defects[0])

    def test_an_empty_reason_is_a_record_defect(self):
        for reason in ("", "   ", None):
            result = self.detect(record=self.record([self.entry(reason=reason)]))
            self.assertEqual(len(result.record_defects), 1, reason)
            self.assertIn("reason", result.record_defects[0])

    def test_a_serves_outside_the_citable_set_and_the_outstanding_identities_is_a_record_defect(self):
        result = self.detect(record=self.record([self.entry(serves="t9:f9")]))
        self.assertEqual(len(result.record_defects), 1)
        self.assertIn("t9:f9", result.record_defects[0])
        # a task-2 ref is not citable from task 1's review
        result = self.detect(record=self.record([self.entry(serves="t2.t1")]))
        self.assertEqual(len(result.record_defects), 1)

    def test_an_entry_missing_path_reason_or_serves_is_a_record_defect_naming_the_entry(self):
        for key in ("path", "reason", "serves"):
            entry = self.entry()
            del entry[key]
            result = self.detect(record=self.record([entry]))
            self.assertEqual(len(result.record_defects), 1, key)
            self.assertIn("entry 1", result.record_defects[0])
            self.assertIn(key, result.record_defects[0])

    def test_a_null_serves_is_accepted(self):
        result = self.detect(record=self.record([self.entry(serves=None)]))
        self.assertEqual(result.record_defects, [])

    def test_a_serves_naming_an_outstanding_finding_identity_from_the_state_is_accepted(self):
        state = forge_dispose.ConvergenceState(carried_ids={"t1:f3"})
        result = self.detect(
            state=state, record=self.record([self.entry(serves="t1:f3")]))
        self.assertEqual(result.record_defects, [])
        self.assertEqual(result.widened[0].serves, "t1:f3")


class AcceptedTests(WideningTestBase):
    def blob(self, rel):
        return self.git("hash-object", rel).strip()

    def state(self, blob):
        state = forge_dispose.ConvergenceState()
        state.accepted_widenings = {"c.py": {
            "blob": blob, "reason": "r", "serves": None, "ruling_reason": "ok"}}
        return state

    def test_a_widening_accepted_at_its_current_blob_is_not_due_a_ruling(self):
        self.edit("c.py")
        result = self.detect(state=self.state(self.blob("c.py")))
        self.assertEqual(self.paths(result), ["c.py"])
        self.assertEqual(result.due, [])

    def test_a_widening_whose_content_changed_since_acceptance_is_due_a_ruling_again(self):
        self.edit("c.py")
        accepted = self.blob("c.py")
        self.write("c.py", "different\n")
        result = self.detect(state=self.state(accepted))
        self.assertEqual([w.path for w in result.due], ["c.py"])


class OutputTests(WideningTestBase):
    def setUp(self):
        super().setUp()
        self.edit("c.py")
        self.edit("d.py")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, "widened", "--plan", self.plan,
             "--base", "HEAD", "--checkpoint", "HEAD", "--scope", "t1", *args],
            cwd=self.repo, capture_output=True, text=True)

    def test_the_md_format_renders_each_due_path_as_a_json_string_with_its_justification_or_no_justification_given(self):
        rec = self.record([{"path": "c.py", "reason": "needed it", "serves": "t1.t1"}])
        proc = self.cli("--widenings", rec, "--format", "md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertTrue(out.startswith("## Widened files"))
        self.assertIn('"c.py"', out)
        self.assertIn("reason: needed it", out)
        self.assertIn("serves: t1.t1", out)
        self.assertIn('"d.py"', out)
        self.assertIn("no justification given", out)
        rec = self.record([{"path": "c.py", "reason": "r", "serves": None}])
        self.assertIn("serves: none", self.cli(
            "--widenings", rec, "--format", "md").stdout)

    def test_the_json_format_prints_widened_deleted_and_record_defects_with_each_widened_entrys_path_reason_and_serves(self):
        os.remove(os.path.join(self.repo, "b.py"))
        rec = self.record([{"path": "c.py", "reason": "needed", "serves": "t1.t1"}])
        proc = self.cli("--widenings", rec)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {
            "widened": [
                {"path": "c.py", "reason": "needed", "serves": "t1.t1"},
                {"path": "d.py", "reason": None, "serves": None},
            ],
            "deleted": ["b.py"],
            "record_defects": [],
        })

    def test_an_absent_or_missing_state_file_reads_as_a_fresh_state(self):
        for args in ([], ["--state", os.path.join(self.work, "nope.json")]):
            proc = self.cli(*args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                [w["path"] for w in json.loads(proc.stdout)["widened"]],
                ["c.py", "d.py"])

    def test_the_state_files_accepted_widenings_suppress_a_due_path(self):
        blob = self.git("hash-object", "c.py").strip()
        state = os.path.join(self.work, "state.json")
        with open(state, "w") as f:
            json.dump({"accepted_widenings": {"c.py": {
                "blob": blob, "reason": "r", "serves": None,
                "ruling_reason": "ok"}}}, f)
        proc = self.cli("--state", state)
        self.assertEqual(
            [w["path"] for w in json.loads(proc.stdout)["widened"]], ["d.py"])


class DeclaredSpecTests(WideningTestBase):
    plan_text = DECLARED_PLAN

    def test_declared_spec_files_are_exempt_without_a_spec_argument(self):
        self.write("docs/spec.md", SPEC + "\namended\n")
        self.edit("c.py")
        self.assertEqual(self.paths(self.detect()), ["c.py"])


class BriefTests(unittest.TestCase):
    def test_the_brief_names_the_record_path_and_carries_widening_instruction_verbatim_when_widenings_record_is_given(self):
        tmp = tempfile.mkdtemp(prefix="forge-widening-brief-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        plan = os.path.join(tmp, "plan.md")
        with open(plan, "w") as f:
            f.write(PLAN)
        out = os.path.join(tmp, "out")
        record = os.path.join(tmp, "scratch", "widenings-t1.json")
        script = os.path.join(REPO_ROOT, "scripts", "extract-brief.py")
        proc = subprocess.run(
            [sys.executable, script, plan, "1", "--out", out,
             "--widenings-record", record],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(proc.stdout.strip()) as f:
            brief = f.read()
        self.assertIn(record, brief)
        self.assertIn(forge_common.WIDENING_INSTRUCTION, brief)
        for key in ("path", "reason", "serves"):
            self.assertIn(key, forge_common.WIDENING_INSTRUCTION)
        self.assertIn("Every entry", forge_common.WIDENING_INSTRUCTION)
        self.assertIn("null", forge_common.WIDENING_INSTRUCTION)
        proc = subprocess.run(
            [sys.executable, script, plan, "1", "--out", out],
            capture_output=True, text=True)
        with open(proc.stdout.strip()) as f:
            self.assertNotIn(forge_common.WIDENING_INSTRUCTION, f.read())


def _ruling(path, ruling="accepted", reason="needed and minimal"):
    return {"path": path, "ruling": ruling, "reason": reason}


def _verdict(widenings=None, findings=None):
    obj = {"verdict": "findings" if findings else "pass"}
    if findings:
        obj["findings"] = findings
    if widenings is not None:
        obj["widenings"] = widenings
    return forge_dispose._verdict_from_obj(obj)


class VerdictValidationTests(unittest.TestCase):
    def test_a_verdict_missing_a_ruling_for_a_due_path_is_a_validation_defect(self):
        defects = forge_dispose.validate_widenings(_verdict([]), ["c.py"])
        self.assertEqual(len(defects), 1)
        self.assertIn("c.py", defects[0])
        self.assertEqual(forge_dispose.validate_widenings(
            _verdict([_ruling("c.py")]), ["c.py"]), [])

    def test_a_verdict_ruling_on_a_path_not_due_is_a_validation_defect(self):
        defects = forge_dispose.validate_widenings(
            _verdict([_ruling("c.py"), _ruling("z.py")]), ["c.py"])
        self.assertEqual(len(defects), 1)
        self.assertIn("z.py", defects[0])
        self.assertEqual(len(forge_dispose.validate_widenings(
            _verdict([_ruling("z.py")]), [])), 1)

    def test_a_duplicate_ruling_an_unknown_ruling_value_or_an_empty_reason_is_a_validation_defect(self):
        due = ["c.py"]
        dup = forge_dispose.validate_widenings(
            _verdict([_ruling("c.py"), _ruling("c.py")]), due)
        self.assertTrue(any("duplicate" in d and "c.py" in d for d in dup))
        unknown = forge_dispose.validate_widenings(
            _verdict([_ruling("c.py", ruling="maybe")]), due)
        self.assertTrue(any("maybe" in d for d in unknown))
        for bad in ("", "   ", None):
            empty = forge_dispose.validate_widenings(
                _verdict([_ruling("c.py", reason=bad)]), due)
            self.assertTrue(any("reason" in d for d in empty), bad)

    def test_a_malformed_widenings_entry_is_a_defect_and_a_non_list_fails_loud(self):
        defects = forge_dispose.validate_widenings(_verdict(["c.py"]), ["c.py"])
        self.assertTrue(defects)
        with self.assertRaises(RuntimeError):
            forge_dispose._verdict_from_obj(
                {"verdict": "pass", "widenings": "c.py"})

    def test_a_reviewer_finding_id_beginning_widen_is_a_validation_defect(self):
        for fid in ("widen:c.py", "t1:widen:c.py", "final:widen:c.py"):
            verdict = _verdict(findings=[
                {"id": fid, "summary": "s", "impact": "improvement"}])
            defects = forge_dispose.validate_finding_ids(verdict)
            self.assertTrue(any("widen:" in d for d in defects), fid)
        verdict = _verdict(findings=[
            {"id": "f1", "summary": "s", "carried_from": "t1:widen:c.py",
             "impact": "improvement"}])
        self.assertTrue(any(
            "widen:" in d for d in forge_dispose.validate_finding_ids(verdict)))
        ok = _verdict(findings=[
            {"id": "f1", "summary": "s", "impact": "improvement"}])
        self.assertEqual(forge_dispose.validate_finding_ids(ok), [])


class RulingTests(WideningTestBase):
    def due(self, record=None):
        self.edit("c.py")
        return self.detect(record=record).due

    def test_an_accepted_ruling_records_the_paths_blob_and_justification_in_accepted_widenings(self):
        rec = self.record([{"path": "c.py", "reason": "needed it", "serves": "t1.t1"}])
        due = self.due(rec)
        state = forge_dispose.ConvergenceState()
        verdict = _verdict([_ruling("c.py", reason="minimal")])
        rejected = forge_dispose.apply_widening_rulings(
            verdict, due, "t1", state, self.repo)
        self.assertEqual(rejected, [])
        self.assertEqual(state.accepted_widenings, {"c.py": {
            "blob": self.git("hash-object", "c.py").strip(),
            "reason": "needed it", "serves": "t1.t1",
            "ruling_reason": "minimal"}})

    def test_a_rejected_ruling_raises_a_fix_finding_with_identity_scope_widen_path_and_the_stated_provenance_impact_and_contract_ref(self):
        due = self.due()
        state = forge_dispose.ConvergenceState()
        verdict = _verdict([_ruling("c.py", "rejected", "not needed")])
        (finding,) = forge_dispose.apply_widening_rulings(
            verdict, due, "t1", state, self.repo)
        self.assertEqual(finding.identity, "t1:widen:c.py")
        self.assertEqual(finding.provenance, "in-diff")
        self.assertEqual(finding.impact, "improvement")
        self.assertIsNone(finding.contract_ref)
        self.assertEqual(finding.disposition, "fix")
        self.assertEqual(finding.summary, "not needed")
        self.assertEqual(finding.file, "c.py")
        self.assertEqual(state.accepted_widenings, {})
        direct = forge_dispose.rejected_widening_finding("c.py", "r", "final")
        self.assertEqual(direct.identity, "final:widen:c.py")

    def test_rulings_never_change_the_verdict_value(self):
        due = self.due()
        verdict = _verdict([_ruling("c.py", "rejected", "no")])
        forge_dispose.apply_widening_rulings(
            verdict, due, "t1", forge_dispose.ConvergenceState(), self.repo)
        self.assertEqual(verdict.kind, "pass")
        self.assertEqual(verdict.findings, [])


class DecisionCLITests(WideningTestBase):
    def dispose(self, verdict, attempt=1, state=None, autofix="auto",
                widenings=None, scope="t1", extra=()):
        vpath = os.path.join(self.work, "verdict-%d.json" % attempt)
        with open(vpath, "w") as f:
            json.dump(verdict, f)
        args = [sys.executable, SCRIPT, "--verdict", vpath, "--base", self.base,
                "--checkpoint", self.base, "--scope", scope, "--attempt", str(attempt),
                "--acceptance-ok", "true", "--autofix", autofix,
                "--plan", self.plan]
        if state is not None:
            args += ["--state", state]
        if widenings is not None:
            args += ["--widenings", widenings]
        proc = subprocess.run(args + list(extra), cwd=self.repo,
                              capture_output=True, text=True)
        return proc

    def decide(self, verdict, **kw):
        proc = self.dispose(verdict, **kw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        decision = json.loads(proc.stdout)
        path = os.path.join(self.work, "state-out.json")
        with open(path, "w") as f:
            json.dump(decision["state"], f)
        return decision, path

    def test_omitting_plan_is_a_usage_error_naming_the_flag(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--verdict", "v.json", "--base", "HEAD",
             "--checkpoint", "HEAD", "--scope", "t1", "--attempt", "1", "--acceptance-ok", "true",
             "--autofix", "auto"],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--plan", proc.stderr)

    def test_passing_repair_snapshot_is_a_usage_error_naming_the_flag(self):
        self.edit("a.py")
        proc = self.dispose({"verdict": "pass"},
                            extra=["--repair-snapshot", "0" * 40])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--repair-snapshot", proc.stderr)

    def test_a_verdict_with_widenings_is_checked_against_the_helpers_own_recomputation_not_a_caller_list(self):
        self.edit("c.py")
        # ruling on a path that is not a widening, and none for c.py
        proc = self.dispose({"verdict": "pass", "widenings": [_ruling("d.py")]})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("d.py", proc.stderr)
        self.assertIn("c.py", proc.stderr)

    def test_omitting_widenings_leaves_every_widening_unjustified(self):
        self.edit("c.py")
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py")]})
        (entry,) = decision["widenings"]["accepted"]
        self.assertIsNone(entry["reason"])
        self.assertIsNone(entry["serves"])
        self.assertEqual(entry["ruling_reason"], "needed and minimal")

    def test_the_widenings_record_reaches_decision_json_as_the_accepted_entrys_reason_and_serves(self):
        self.edit("c.py")
        rec = self.record([{"path": "c.py", "reason": "needed it", "serves": "t1.t1"}])
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py")]}, widenings=rec)
        entry = decision["widenings"]["accepted"][0]
        self.assertEqual(entry["reason"], "needed it")
        self.assertEqual(entry["serves"], "t1.t1")
        self.assertEqual(
            decision["state"]["accepted_widenings"]["c.py"]["reason"], "needed it")

    def test_a_pass_verdict_with_a_rejected_ruling_reworks_and_lists_the_rejected_identity(self):
        self.edit("c.py")
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]})
        self.assertEqual(decision["action"], "rework")
        self.assertEqual(decision["widenings"]["rejected"],
                         [{"path": "c.py", "identity": "t1:widen:c.py"}])
        self.assertEqual([f["identity"] for f in decision["findings"]["fix"]],
                         ["t1:widen:c.py"])
        self.assertEqual(decision["state"]["carried_ids"], ["t1:widen:c.py"])

    def test_a_reverted_rejected_widening_resolves_without_a_ruling(self):
        self.edit("c.py")
        _, state = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]})
        self.git("checkout", "c.py")
        decision, _ = self.decide({"verdict": "pass"}, attempt=2, state=state)
        self.assertEqual(decision["action"], "pass")
        self.assertIn("t1:widen:c.py", decision["state"]["resolved_ids"])

    def test_a_rejected_widening_rejected_again_with_nothing_resolved_halts_stuck(self):
        self.edit("c.py")
        rej = {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]}
        _, state = self.decide(rej)
        decision, _ = self.decide(rej, attempt=2, state=state)
        self.assertEqual((decision["action"], decision["halt_reason"]),
                         ("halt", "stuck"))

    def test_a_rejected_widening_accepted_on_the_next_ruling_resolves(self):
        self.edit("c.py")
        _, state = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]})
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py")]},
            attempt=2, state=state)
        self.assertEqual(decision["action"], "pass")
        self.assertIn("t1:widen:c.py", decision["state"]["resolved_ids"])
        self.assertIn("c.py", decision["state"]["accepted_widenings"])

    def test_a_widening_resolved_then_rejected_again_later_halts_regression(self):
        self.edit("c.py")
        _, state = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]})
        self.git("checkout", "c.py")
        _, state = self.decide({"verdict": "pass"}, attempt=2, state=state)
        self.edit("c.py")
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]},
            attempt=3, state=state)
        self.assertEqual((decision["action"], decision["halt_reason"]),
                         ("halt", "regression"))

    def test_gate_mode_halts_on_a_rejected_widening(self):
        self.edit("c.py")
        decision, _ = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]},
            autofix="gate")
        self.assertEqual((decision["action"], decision["halt_reason"]),
                         ("halt", "gate"))

    def test_an_out_of_scope_deletion_halts_scope_delete_whatever_the_findings_and_rulings(self):
        self.edit("c.py")
        os.remove(os.path.join(self.repo, "d.py"))
        finding = {"id": "f1", "summary": "bad", "impact": "improvement",
                   "location": {"file": "a.py", "lines": "1"}}
        for verdict in (
            {"verdict": "pass", "widenings": [_ruling("c.py")]},
            {"verdict": "pass", "widenings": [_ruling("c.py", "rejected", "no")]},
            {"verdict": "findings", "findings": [finding],
             "widenings": [_ruling("c.py")]},
        ):
            decision, _ = self.decide(verdict)
            self.assertEqual((decision["action"], decision["halt_reason"]),
                             ("halt", "scope-delete"))
            self.assertEqual(decision["widenings"]["deleted"], ["d.py"])

    def test_a_scope_delete_halt_applies_to_an_execution_failure_attempt_too(self):
        os.remove(os.path.join(self.repo, "d.py"))
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--execution-failure", "--base", self.base,
             "--checkpoint", self.base, "--scope", "t1", "--attempt", "1", "--acceptance-ok", "false",
             "--autofix", "auto", "--plan", self.plan],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["halt_reason"], "scope-delete")

    def test_decision_json_widenings_lists_every_accepted_widening_so_far_with_line_counts_and_drops_one_no_longer_widened(self):
        self.edit("c.py")
        decision, state = self.decide(
            {"verdict": "pass", "widenings": [_ruling("c.py")]})
        self.assertEqual(decision["widenings"], {
            "accepted": [{"path": "c.py", "reason": None, "serves": None,
                          "ruling_reason": "needed and minimal",
                          "added": 2, "removed": 1}],
            "rejected": [], "deleted": []})
        self.edit("d.py")
        decision, state = self.decide(
            {"verdict": "pass", "widenings": [_ruling("d.py")]},
            attempt=2, state=state)
        self.assertEqual([e["path"] for e in decision["widenings"]["accepted"]],
                         ["c.py", "d.py"])
        self.git("checkout", "c.py")
        decision, _ = self.decide({"verdict": "pass"}, attempt=3, state=state)
        self.assertEqual([e["path"] for e in decision["widenings"]["accepted"]],
                         ["d.py"])
        self.assertEqual(list(decision["state"]["accepted_widenings"]), ["d.py"])


class StateAndConstantsTests(unittest.TestCase):
    def test_a_state_file_without_accepted_widenings_reads_as_empty(self):
        self.assertEqual(
            forge_dispose.ConvergenceState.from_dict({}).accepted_widenings, {})
        state = forge_dispose.ConvergenceState.from_dict(
            {"resolved_ids": ["a"], "carried_ids": []})
        self.assertEqual(state.accepted_widenings, {})
        entry = {"blob": "b", "reason": "r", "serves": None, "ruling_reason": "x"}
        round_trip = forge_dispose.ConvergenceState.from_dict(
            forge_dispose.ConvergenceState(
                accepted_widenings={"c.py": entry}).to_dict())
        self.assertEqual(round_trip.accepted_widenings, {"c.py": entry})

    def test_halt_reasons_no_longer_contains_repair_out_of_scope_and_legacy_halt_reasons_does(self):
        self.assertEqual(forge_common.HALT_REASONS, (
            "scope-decision", "regression", "stuck", "backstop", "gate",
            "scope-delete"))
        self.assertNotIn("repair-out-of-scope", forge_common.HALT_REASONS)
        self.assertEqual(forge_common.LEGACY_HALT_REASONS,
                         ("repair-out-of-scope",))

    def test_review_verdict_instruction_states_the_widenings_array_one_ruling_per_listed_path_and_that_rulings_never_change_the_verdict(self):
        text = forge_common.REVIEW_VERDICT_INSTRUCTION
        self.assertIn('"widenings"', text)
        self.assertIn("one ruling per path", text)
        self.assertIn("## Widened files", text)
        self.assertIn('"accepted" | "rejected"', text)
        self.assertIn("never change the verdict", text)

    def test_the_verdict_dataclass_carries_widenings_as_emitted(self):
        self.assertEqual(forge_common.Verdict(kind="pass").widenings, [])
        verdict = _verdict([_ruling("c.py")])
        self.assertEqual(verdict.widenings, [_ruling("c.py")])


if __name__ == "__main__":
    unittest.main()
