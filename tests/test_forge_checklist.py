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

**Tests:**
- the foo does the thing
- the foo handles the edge case

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
        # a task checklist drops spec: items even when **Spec:** is declared
        self.assertNotIn("spec:Alpha section", ids)
        self.assertNotIn("spec:Beta section", ids)
        self.assertFalse(any(i.startswith("spec:") for i in ids))
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
        self.assertEqual(by_id["t1.a1"].source, "acceptance")

    # --- test items ----------------------------------------------------------

    def test_task_checklist_has_one_test_item_per_test_case(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        test_items = [it for it in items if it.source == "tests"]
        self.assertEqual(len(test_items), 2)

    def test_test_item_ids_are_1_based_in_document_order(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        test_items = [it for it in items if it.source == "tests"]
        self.assertEqual([it.id for it in test_items], ["t1.t1", "t1.t2"])
        self.assertEqual(test_items[0].text, "the foo does the thing")
        self.assertEqual(test_items[1].text, "the foo handles the edge case")

    def test_task_checklist_still_has_globals_and_acceptance_alongside_tests(self):
        items = fc.build_task_checklist(self.plan_path, self.spec_path, 1)
        ids = [it.id for it in items]
        self.assertIn("g1", ids)
        self.assertIn("t1.a1", ids)
        self.assertIn("t1.t1", ids)

    def test_numbering_token_stripped_and_whitespace_collapsed(self):
        # Reuse the plan but point task 1's Spec at the numbered/irregularly
        # spaced "Gamma" heading via a second plan file.
        plan_path = os.path.join(self.tmp, "plan_gamma.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section",
                "**Spec:** Gamma",
            ))
        items = fc.build_final_checklist(plan_path, self.spec_path)
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

    def test_semicolon_inside_inline_code_span_does_not_split(self):
        text = "Run `python3 -c \"import sys; print(1)\"` and confirm output"
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(len(clauses), 1)
        self.assertEqual(clauses[0], text)

    def test_semicolons_outside_inline_code_spans_still_split(self):
        text = "`cmd one` runs clean; `cmd two` also runs clean"
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(clauses, ["`cmd one` runs clean", "`cmd two` also runs clean"])

    def test_clause_solely_inline_code_with_internal_semicolon_still_dropped(self):
        text = "`python3 -c \"a=1; b=2\"`"
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(clauses, [])

    def test_clause_mixing_prose_and_code_with_internal_semicolon_kept(self):
        text = "`python3 -c \"a=1; b=2\"` succeeds"
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(clauses, ['`python3 -c "a=1; b=2"` succeeds'])

    def test_multiple_inline_code_spans_on_one_line(self):
        text = "`cmd; with; semis` passes; and `other; cmd` also passes"
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(
            clauses,
            ["`cmd; with; semis` passes", "and `other; cmd` also passes"],
        )

    def test_task0_style_multiline_acceptance_no_fragments(self):
        # Regression for the f1 bug report: sys.path.insert(0,'scripts')
        # inside a backtick command must not become clause fragments.
        text = (
            "`python3 -c \"import sys; sys.path.insert(0,'scripts')\"` succeeds; "
            "second real clause of prose"
        )
        clauses = fc._split_acceptance_clauses(text)
        self.assertEqual(
            clauses,
            [
                '`python3 -c "import sys; sys.path.insert(0,\'scripts\')"` succeeds',
                "second real clause of prose",
            ],
        )

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

    def test_no_tests_no_globals_command_only_acceptance_raises(self):
        # A **Spec:** declaration no longer keeps a task checklist non-empty
        # — dropping spec: as a task source, this is now reachable via a
        # task with no **Tests:**, no **Global Constraints:**, and an
        # **Acceptance:** of nothing but inline-code commands.
        plan_path = os.path.join(self.tmp, "plan_no_contract.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(
                "# Plan header\n\n"
                "**Goal:** Do nothing much.\n\n"
                "# Task 1\n\n"
                "### Task 1: Command-only task\n"
                "- [ ] Done\n\n"
                "**Files:**\n- Create: `x.py`\n\n"
                "**Spec:** Alpha section\n\n"
                "**Acceptance:** `python3 x.py`\n\n"
                "**Tier:** `standard`\n\n"
                "**Depends on:** nothing.\n"
            )
        with self.assertRaises(RuntimeError) as ctx:
            fc.build_task_checklist(plan_path, self.spec_path, 1)
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


class CitableRefsTests(unittest.TestCase):
    """citable_refs (Task 4): the wider set a per-task review's findings may
    cite — this task's coverage item ids plus the spec:<slug> id of every
    section its **Spec:** line names — and no others (Contract checklist:
    covering and citing are different acts)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-checklist-citable-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_MD)

    def test_citable_refs_is_coverage_ids_plus_declared_spec_ids_and_no_others(self):
        refs = fc.citable_refs(self.plan_path, self.spec_path, 1)
        coverage_ids = {it.id for it in
                        fc.build_task_checklist(self.plan_path, self.spec_path, 1)}
        self.assertEqual(
            refs, coverage_ids | {"spec:Alpha section", "spec:Beta section"},
        )

    def test_citable_refs_is_only_coverage_ids_with_no_spec_declared(self):
        plan_path = os.path.join(self.tmp, "plan_no_spec.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Spec:** Alpha section, Beta section\n\n", "",
            ))
        refs = fc.citable_refs(plan_path, self.spec_path, 1)
        coverage_ids = {it.id for it in
                        fc.build_task_checklist(plan_path, self.spec_path, 1)}
        self.assertEqual(refs, coverage_ids)
        self.assertFalse(any(r.startswith("spec:") for r in refs))

    def test_citable_refs_scoped_to_the_declaring_task_only(self):
        # Task 2 declares only "Beta section" — Task 1's "Alpha section" is
        # not citable from task 2's review.
        refs = fc.citable_refs(self.plan_path, self.spec_path, 2)
        self.assertIn("spec:Beta section", refs)
        self.assertNotIn("spec:Alpha section", refs)


class FinalCitableRefsTests(unittest.TestCase):
    """final_citable_refs: the whole-plan equivalent of citable_refs — the set
    of ids a FINAL review's findings may cite. Wider than
    build_final_checklist, which emits no t<N>.t<M> items (a task-only
    coverage source): a per-task finding dispositioned `seed` is replayed
    into the final packet with its contract_ref intact, and it must stay
    citable there."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-checklist-final-citable-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_MD)

    def test_includes_every_task_test_case_id_the_final_checklist_omits(self):
        final_ids = {it.id for it in
                     fc.build_final_checklist(self.plan_path, self.spec_path)}
        self.assertNotIn("t1.t1", final_ids)   # the seam this exists to close
        refs = fc.final_citable_refs(self.plan_path, self.spec_path)
        self.assertIn("t1.t1", refs)
        self.assertIn("t1.t2", refs)

    def test_is_a_superset_of_the_final_checklist_ids(self):
        final_ids = {it.id for it in
                     fc.build_final_checklist(self.plan_path, self.spec_path)}
        refs = fc.final_citable_refs(self.plan_path, self.spec_path)
        self.assertTrue(final_ids <= refs, final_ids - refs)
        self.assertIn("spec:Alpha section", refs)
        self.assertIn("g1", refs)
        self.assertIn("t1.a1", refs)
        self.assertIn("t2", refs)

    def test_is_the_union_of_every_task_citable_set_and_no_invention(self):
        refs = fc.final_citable_refs(self.plan_path, self.spec_path)
        union = set()
        for n in (1, 2):
            union |= fc.citable_refs(self.plan_path, self.spec_path, n)
        self.assertTrue(union <= refs, union - refs)
        # Membership still means something: an id no plan grammar produces
        # is not citable, which is the whole point of the check.
        self.assertNotIn("t9.t9", refs)
        self.assertNotIn("spec:Nonexistent section", refs)

    def test_plan_with_no_tests_blocks_equals_the_final_checklist_ids(self):
        # The widening adds t<N>.t<M> and nothing else: with no **Tests:**
        # anywhere, the citable set is exactly the final checklist's ids.
        plan_path = os.path.join(self.tmp, "plan_no_tests.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD.replace(
                "**Tests:**\n- the foo does the thing\n"
                "- the foo handles the edge case\n\n", "",
            ))
        self.assertEqual(
            fc.final_citable_refs(plan_path, self.spec_path),
            {it.id for it in
             fc.build_final_checklist(plan_path, self.spec_path)},
        )


class CitableCLITests(unittest.TestCase):
    """--citable composes with the existing scope flags: --task N --citable
    emits that task's citable set, --final --citable the whole plan's, both
    as the JSON array of id strings forge_dispose.py --citable consumes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-checklist-citable-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_MD)

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, SCRIPT] + args,
            capture_output=True, text=True,
        )

    def test_task_citable_emits_the_citable_ref_id_array(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--task", "1",
            "--citable",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        self.assertTrue(all(isinstance(x, str) for x in data), data)
        self.assertEqual(
            set(data), fc.citable_refs(self.plan_path, self.spec_path, 1),
        )

    def test_final_citable_emits_the_whole_plan_ref_id_array(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--final", "--citable",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            set(json.loads(result.stdout)),
            fc.final_citable_refs(self.plan_path, self.spec_path),
        )

    def test_citable_output_is_consumable_by_forge_dispose_citable(self):
        # The producer's output must be exactly what the documented consumer
        # reads: a JSON array of strings loaded straight into a set.
        out = os.path.join(self.tmp, "citable.json")
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--task", "1",
            "--citable", "--out", out,
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as f:
            loaded = set(json.load(f))
        self.assertIn("t1.t1", loaded)
        self.assertIn("spec:Alpha section", loaded)

    def test_citable_with_format_md_is_rejected(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--task", "1",
            "--citable", "--format", "md",
        ])
        self.assertNotEqual(result.returncode, 0)

    def test_citable_still_requires_a_scope_flag(self):
        result = self.run_cli([
            self.plan_path, "--spec", self.spec_path, "--citable",
        ])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
