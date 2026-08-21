"""forge_checklist: id forms per source, numbering-token stripping and
whitespace collapse in spec: ids, acceptance clause split on ';' (dropping
solely-inline-code clauses), --final union/dedup + t<N> integration items,
--task N scoping, fail-loud on unresolvable/ambiguous **Spec:** names and on
an empty checklist, reduce_checklist, render_section, and the CLI's
--format json/md."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS_DIR, "forge_checklist.py")
sys.path.insert(0, SCRIPTS_DIR)

import forge_checklist as fc  # noqa: E402


PLAN_MD = """# Plan header

**Goal:** Build something.
**Global Constraints:** First constraint sentence. Second constraint sentence with `inline.code` in it. Third one.

# Task 1

### Task 1: First thing
- [ ] Done

**Files:**
- Create: `foo.py`

**Spec:** Alpha section, Beta section

**Acceptance:** `python3 -m pytest -q tests/test_foo.py` all pass; `python3 foo.py`

**Tier:** `standard`

**Depends on:** nothing.


# Task 2

### Task 2: Second thing
- [ ] Done

**Files:**
- Create: `bar.py`

**Spec:** Beta section

**Acceptance:** `python3 -m pytest -q tests/test_bar.py`

**Tier:** `standard`

**Depends on:** Task 1.
"""

SPEC_MD = """# Spec

## Alpha section

Alpha content line one.
More alpha content.

## Beta section

Beta content here.

## 2. Gamma  Section

Gamma content.
"""


class ForgeChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-checklist-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_MD)

    # --- id forms / sources ------------------------------------------------

    def test_task_checklist_ids_and_sources(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        ids = [it.id for it in items]
        self.assertIn("spec:Alpha section", ids)
        self.assertIn("spec:Beta section", ids)
        self.assertIn("g1", ids)
        self.assertIn("g2", ids)
        self.assertIn("g3", ids)
        self.assertIn("t1.a1", ids)
        # solely-inline-code clause ("`python3 foo.py`") is excluded
        self.assertNotIn("t1.a2", ids)
        # integration items only appear in the final checklist
        self.assertNotIn("t1", ids)

        by_id = {it.id: it for it in items}
        self.assertEqual(by_id["g1"].source, "global")
        self.assertEqual(by_id["spec:Alpha section"].source, "spec")
        self.assertEqual(by_id["t1.a1"].source, "acceptance")

    def test_numbering_token_stripped_and_whitespace_collapsed(self):
        # Reuse the plan but point task 1's Spec at the numbered/irregularly
        # spaced "Gamma" heading via a second plan file.
        plan_path = os.path.join(self.tmp, "plan_gamma.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section",
                "**Spec:** Gamma",
            ))
        items = fc.build_task_checklist(plan_path, self.spec_path, 1)
        ids = [it.id for it in items]
        self.assertIn("spec:Gamma Section", ids)

    # --- acceptance clause splitting ----------------------------------------

    def test_acceptance_clause_split_on_semicolon(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        acceptance_items = [it for it in items if it.source == "acceptance"]
        self.assertEqual(len(acceptance_items), 1)
        self.assertEqual(acceptance_items[0].id, "t1.a1")

    def test_clause_solely_inline_code_excluded(self):
        # Task 2's only acceptance clause is solely an inline-code command.
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 2)
        acceptance_items = [it for it in items if it.source == "acceptance"]
        self.assertEqual(acceptance_items, [])

    def test_clause_mixing_prose_and_code_included(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        by_id = {it.id: it for it in items}
        self.assertIn("`python3 -m pytest -q tests/test_foo.py`", by_id["t1.a1"].text)
        self.assertIn("all pass", by_id["t1.a1"].text)

    # --- --final union / dedup / integration items --------------------------

    def test_final_checklist_unions_and_dedups_spec_sections(self):
        items = fc.build_final_checklist(self.plan_path, self.spec_path)
        spec_ids = [it.id for it in items if it.source == "spec"]
        self.assertEqual(spec_ids.count("spec:Beta section"), 1)
        self.assertIn("spec:Alpha section", spec_ids)

    def test_final_checklist_adds_one_integration_item_per_task(self):
        items = fc.build_final_checklist(self.plan_path, self.spec_path)
        integration = {it.id: it for it in items if it.source == "integration"}
        self.assertEqual(set(integration), {"t1", "t2"})
        self.assertIn("First thing", integration["t1"].text)
        self.assertIn("Second thing", integration["t2"].text)

    def test_task_scope_excludes_other_tasks_acceptance(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        ids = [it.id for it in items]
        self.assertFalse(any(i.startswith("t2.") for i in ids))

    # --- fail-loud -----------------------------------------------------------

    def test_unresolvable_spec_name_raises(self):
        plan_path = os.path.join(self.tmp, "plan_bad_spec.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section",
                "**Spec:** Nonexistent heading",
            ))
        with self.assertRaises(RuntimeError) as ctx:
            fc.build_task_checklist(plan_path, self.spec_path, 1)
        self.assertIn("Nonexistent heading", str(ctx.exception))

    def test_ambiguous_spec_name_raises(self):
        spec_path = os.path.join(self.tmp, "spec_ambiguous.md")
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write("# Spec\n\n## Gamma\n\ncontent\n\n## Gamma Extra\n\nmore\n")
        plan_path = os.path.join(self.tmp, "plan_ambiguous.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section",
                "**Spec:** Gamma",
            ))
        with self.assertRaises(RuntimeError) as ctx:
            fc.build_task_checklist(plan_path, spec_path, 1)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_empty_checklist_raises_naming_absent_source(self):
        plan_path = os.path.join(self.tmp, "plan_empty.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(
                "# Plan header\n\n"
                "**Goal:** Do nothing much.\n\n"
                "# Task 1\n\n"
                "### Task 1: Empty task\n"
                "- [ ] Done\n\n"
                "**Files:**\n- Create: `x.py`\n\n"
                "**Acceptance:** `python3 x.py`\n\n"
                "**Tier:** `standard`\n\n"
                "**Depends on:** nothing.\n"
            )
        with self.assertRaises(RuntimeError) as ctx:
            fc.build_task_checklist(plan_path, None, 1)
        msg = str(ctx.exception)
        self.assertIn("task 1", msg)
        self.assertIn("empty", msg)

    def test_spec_declared_without_spec_path_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            fc.build_task_checklist(self.plan_path, None, 1)
        self.assertIn("--spec", str(ctx.exception))

    # --- reduce_checklist ------------------------------------------------------

    def test_reduce_checklist_keeps_only_referenced_ids(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        findings = [
            {"id": "f1", "contract_ref": "g1"},
            {"id": "f2", "contract_ref": None},
        ]
        reduced = fc.reduce_checklist(items, findings)
        self.assertEqual([it.id for it in reduced], ["g1"])

    def test_reduce_checklist_empty_when_no_findings_match(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        findings = [{"id": "f1", "contract_ref": "not-an-id"}]
        self.assertEqual(fc.reduce_checklist(items, findings), [])

    # --- render_section --------------------------------------------------------

    def test_render_section_stable_and_parseable(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        rendered = fc.render_section(items)
        self.assertTrue(rendered.startswith("## Contract checklist\n"))
        body_lines = [
            ln for ln in rendered.splitlines()[1:] if ln.strip()
        ]
        self.assertEqual(len(body_lines), len(items))
        for line, item in zip(body_lines, items):
            self.assertEqual(line, "- {} — {}".format(item.id, item.text))

    # --- CLI ---------------------------------------------------------------------

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, SCRIPT] + args,
            capture_output=True, text=True,
        )

    def test_cli_format_json(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--task", "1",
            "--format", "json",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        ids = [d["id"] for d in data]
        self.assertIn("t1.a1", ids)
        for d in data:
            self.assertEqual(set(d), {"id", "source", "text"})

    def test_cli_format_md(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--task", "1",
            "--format", "md",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## Contract checklist", result.stdout)
        self.assertIn("t1.a1", result.stdout)

    def test_cli_final(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--final",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        ids = {d["id"] for d in data}
        self.assertIn("t1", ids)
        self.assertIn("t2", ids)

    def test_cli_unresolvable_spec_exits_nonzero(self):
        plan_path = os.path.join(self.tmp, "plan_cli_bad.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section",
                "**Spec:** Nope",
            ))
        result = self.run_cli([
            plan_path, "--spec", self.spec_path, "--task", "1",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nope", result.stderr)


if __name__ == "__main__":
    unittest.main()
