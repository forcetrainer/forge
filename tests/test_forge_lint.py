"""forge_lint: each check's error names the offending task/heading, multiple
simultaneous defects all reported in one call, a legal minimal plan lints
clean, an empty checklist is a warning (exit 0), dependency cycles/missing
deps/duplicate numbers/wrong heading levels are named, and the real Phase 14
and Phase 12b plans both lint clean via the CLI."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS_DIR, "forge_lint.py")
sys.path.insert(0, SCRIPTS_DIR)

import forge_lint as fl  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


LEGAL_MINIMAL_PLAN = """# Plan header

**Goal:** Ship the thing.

# Task 1

### Task 1: First thing
- [ ] Done

**Files:**
- Create: `foo.py`

**Acceptance:** `python3 -m pytest -q tests/test_foo.py`

**Tier:** `standard`

**Depends on:** nothing.
"""


SPEC_MD = """# Spec

## Alpha section

Alpha content.

## Beta section

Beta content.
"""


def _base_plan(**overrides):
    """A two-task plan with a **Spec:** and **Global Constraints:**, valid by
    default; each field can be overridden to inject exactly one defect."""
    goal = overrides.get("goal", "**Goal:** Ship the thing.")
    gc = overrides.get("gc", "**Global Constraints:** Keep it simple.")
    task1_spec = overrides.get("task1_spec", "**Spec:** Alpha section")
    task1_tier = overrides.get("task1_tier", "**Tier:** `standard`")
    task1_depends = overrides.get("task1_depends", "**Depends on:** nothing.")
    task1_acceptance = overrides.get(
        "task1_acceptance", "**Acceptance:** `python3 -m pytest -q tests/test_a.py`"
    )
    task1_heading = overrides.get("task1_heading", "### Task 1: First thing")
    task2_heading = overrides.get("task2_heading", "### Task 2: Second thing")
    task2_depends = overrides.get("task2_depends", "**Depends on:** Task 1.")
    task2_tier = overrides.get("task2_tier", "**Tier:** `standard`")

    return """# Plan header

{goal}
{gc}

# Task 1

{task1_heading}
- [ ] Done

**Files:**
- Create: `foo.py`

{task1_spec}

{task1_acceptance}

{task1_tier}

{task1_depends}


# Task 2

{task2_heading}
- [ ] Done

**Files:**
- Create: `bar.py`

**Spec:** Beta section

**Acceptance:** `python3 -m pytest -q tests/test_b.py`

{task2_tier}

{task2_depends}
""".format(
        goal=goal,
        gc=gc,
        task1_heading=task1_heading,
        task1_spec=task1_spec,
        task1_acceptance=task1_acceptance,
        task1_tier=task1_tier,
        task1_depends=task1_depends,
        task2_heading=task2_heading,
        task2_depends=task2_depends,
        task2_tier=task2_tier,
    )


class ForgeLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-lint-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        _write(self.spec_path, SPEC_MD)

    def _lint(self, plan_text, spec_path=None):
        _write(self.plan_path, plan_text)
        return fl.lint_plan(self.plan_path, spec_path)

    def _errors(self, defects):
        return [d for d in defects if d.severity == "error"]

    def _warnings(self, defects):
        return [d for d in defects if d.severity == "warning"]

    # --- clean plans ---------------------------------------------------

    def test_legal_minimal_plan_lints_clean(self):
        defects = self._lint(LEGAL_MINIMAL_PLAN)
        self.assertEqual(self._errors(defects), [])

    def test_valid_two_task_plan_lints_clean(self):
        defects = self._lint(_base_plan(), spec_path=self.spec_path)
        self.assertEqual(defects, [])

    # --- empty checklist is a warning, not an error ---------------------

    def test_empty_checklist_is_warning_not_error(self):
        defects = self._lint(LEGAL_MINIMAL_PLAN)
        warnings = self._warnings(defects)
        self.assertTrue(any("is empty" in d.message for d in warnings))
        self.assertEqual(self._errors(defects), [])

    # --- heading structure -----------------------------------------------

    def test_wrong_heading_level_named(self):
        defects = self._lint(_base_plan(task1_heading="## Task 1: First thing"))
        errors = self._errors(defects)
        self.assertTrue(any("three #" in d.message and "task 1" in d.message for d in errors))

    def test_duplicate_task_number_named(self):
        defects = self._lint(_base_plan(task2_heading="### Task 1: Second thing"))
        errors = self._errors(defects)
        self.assertTrue(any("duplicate task number 1" in d.message for d in errors))

    # --- tier -------------------------------------------------------------

    def test_missing_tier_named(self):
        defects = self._lint(_base_plan(task1_tier=""))
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "Tier" in d.message for d in errors))

    def test_invalid_tier_named(self):
        defects = self._lint(_base_plan(task1_tier="**Tier:** `bogus`"))
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "bogus" in d.message for d in errors))

    def test_missing_tier_justification_named(self):
        defects = self._lint(_base_plan(task1_tier="**Tier:** `complex`"))
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "justification" in d.message for d in errors))

    # --- goal ---------------------------------------------------------------

    def test_missing_goal_named(self):
        defects = self._lint(_base_plan(goal=""))
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "plan header" for d in errors))

    # --- spec ---------------------------------------------------------------

    def test_spec_with_semicolon_named(self):
        defects = self._lint(
            _base_plan(task1_spec="**Spec:** Alpha section; Beta section"),
            spec_path=self.spec_path,
        )
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" for d in errors))

    def test_spec_unresolvable_name_named(self):
        defects = self._lint(
            _base_plan(task1_spec="**Spec:** Nonexistent section"),
            spec_path=self.spec_path,
        )
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "Nonexistent" in d.message for d in errors))

    # --- depends on -----------------------------------------------------

    def test_depends_on_unknown_task_named(self):
        defects = self._lint(_base_plan(task2_depends="**Depends on:** Task 99."))
        errors = self._errors(defects)
        self.assertTrue(any("depends on unknown task 99" in d.message for d in errors))

    def test_dependency_cycle_named(self):
        defects = self._lint(
            _base_plan(task1_depends="**Depends on:** Task 2.", task2_depends="**Depends on:** Task 1.")
        )
        errors = self._errors(defects)
        self.assertTrue(any("cycle" in d.message for d in errors))

    # --- acceptance -----------------------------------------------------

    def test_missing_acceptance_named(self):
        defects = self._lint(_base_plan(task1_acceptance=""))
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "Acceptance" in d.message for d in errors))

    # --- multiple simultaneous defects -----------------------------------

    def test_multiple_simultaneous_defects_all_reported(self):
        defects = self._lint(
            _base_plan(
                task1_tier="**Tier:** `bogus`",
                task2_depends="**Depends on:** Task 99.",
                task1_acceptance="",
            )
        )
        errors = self._errors(defects)
        self.assertTrue(any(d.where == "task 1" and "bogus" in d.message for d in errors))
        self.assertTrue(any(d.where == "task 1" and "Acceptance" in d.message for d in errors))
        self.assertTrue(any("depends on unknown task 99" in d.message for d in errors))
        self.assertGreaterEqual(len(errors), 3)

    def test_structural_and_per_task_defects_all_reported_together(self):
        # Task 1's heading is wrong-level (structural) AND it is separately
        # missing **Acceptance:**; task 2 is structurally fine but has a bad
        # tier. All three must surface in one run — a structural defect in
        # one task must never suppress checks on any other task, or on that
        # task's own otherwise-valid fields.
        defects = self._lint(
            _base_plan(
                task1_heading="## Task 1: First thing",
                task1_acceptance="",
                task2_tier="**Tier:** `bogus`",
                task2_depends="**Depends on:** nothing.",
            ),
            spec_path=self.spec_path,
        )
        errors = self._errors(defects)
        self.assertTrue(any("three #" in d.message for d in errors))
        self.assertTrue(any("Acceptance" in d.message for d in errors))
        self.assertTrue(any(d.where == "task 2" and "bogus" in d.message for d in errors))
        self.assertEqual(len(errors), 3)

    def test_n_distinct_defects_emit_exactly_n_lines(self):
        # A bad tier on task 1 and an unknown dependency on task 2 are two
        # distinct real defects. The bad tier is independently surfaced by
        # both the direct Tier check and the --final checklist reparse —
        # those must collapse into a single line, not two.
        defects = self._lint(
            _base_plan(
                task1_tier="**Tier:** `bogus`",
                task2_depends="**Depends on:** Task 99.",
            ),
            spec_path=self.spec_path,
        )
        self.assertEqual(len(defects), 2)
        messages = sorted(d.message for d in defects)
        self.assertTrue(any("bogus" in m for m in messages))
        self.assertTrue(any("depends on unknown task 99" in m for m in messages))

    def test_duplicate_task_with_dangling_dependency_reports_both(self):
        # The duplicate occurrence itself declares a dependency on a task
        # that doesn't exist. Existence is fully decidable regardless of
        # which duplicate is asking — both the duplicate-number defect and
        # the dangling dependency must be reported.
        defects = self._lint(
            _base_plan(
                task2_heading="### Task 1: Second thing",
                task2_depends="**Depends on:** Task 77.",
            ),
            spec_path=self.spec_path,
        )
        errors = self._errors(defects)
        self.assertTrue(any("duplicate task number 1" in d.message for d in errors))
        self.assertTrue(any("depends on unknown task 77" in d.message for d in errors))

    def test_wrong_level_heading_with_dangling_dependency_reports_both(self):
        defects = self._lint(
            _base_plan(
                task1_heading="## Task 1: First thing",
                task1_depends="**Depends on:** Task 77.",
            ),
            spec_path=self.spec_path,
        )
        errors = self._errors(defects)
        self.assertTrue(any("three #" in d.message for d in errors))
        self.assertTrue(any("depends on unknown task 77" in d.message for d in errors))


class ForgeLintCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-lint-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = os.path.join(self.tmp, "plan.md")
        self.spec_path = os.path.join(self.tmp, "spec.md")
        _write(self.spec_path, SPEC_MD)

    def _run(self, *extra_args):
        return subprocess.run(
            [sys.executable, SCRIPT, self.plan_path] + list(extra_args),
            capture_output=True, text=True,
        )

    def test_cli_exit_0_on_clean_plan(self):
        _write(self.plan_path, _base_plan())
        result = self._run("--spec", self.spec_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_exit_1_on_error(self):
        _write(self.plan_path, _base_plan(task1_tier="**Tier:** `bogus`"))
        result = self._run("--spec", self.spec_path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("bogus", result.stdout)

    def test_cli_exit_0_on_warning_only(self):
        _write(self.plan_path, LEGAL_MINIMAL_PLAN)
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[warning]", result.stdout)

    def test_real_phase14_plan_lints_clean(self):
        plan = os.path.join(REPO_ROOT, "docs/forge/plans/2026-08-21-phase14-halt-precision.md")
        spec = os.path.join(REPO_ROOT, "docs/forge/specs/2026-08-21-halt-precision-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_phase12b_plan_lints_clean(self):
        plan = os.path.join(REPO_ROOT, "docs/forge/plans/2026-07-17-phase12b-claude-dispatch-parity.md")
        spec = os.path.join(REPO_ROOT, "docs/forge/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
