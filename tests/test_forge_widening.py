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

    def detect(self, scope="t1", base="HEAD", state=None, record=None,
               spec=None):
        return forge_dispose.scope_widenings(
            self.repo, self.plan, spec, scope, base, state,
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
        proc = self.run_cli("--base", "no-such-ref", "--scope", "final")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-such-ref", proc.stderr)

    def test_a_task_scope_with_an_unknown_base_raises_naming_the_cause_and_the_cli_exits_1(self):
        with self.assertRaises(RuntimeError) as cm:
            self.detect(scope="t1", base="no-such-ref")
        self.assertIn("no-such-ref", str(cm.exception))
        proc = self.run_cli("--base", "no-such-ref", "--scope", "t1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-such-ref", proc.stderr)

    def run_cli(self, *args, plan=None):
        return subprocess.run(
            [sys.executable, SCRIPT, "widened", "--plan", plan or self.plan,
             *args],
            cwd=self.repo, capture_output=True, text=True)


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
             "--base", "HEAD", "--scope", "t1", *args],
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


if __name__ == "__main__":
    unittest.main()
