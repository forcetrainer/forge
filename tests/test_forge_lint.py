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
    task1_tests = overrides.get("task1_tests", "")
    task2_tests = overrides.get("task2_tests", "")

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

{task1_tests}

# Task 2

{task2_heading}
- [ ] Done

**Files:**
- Create: `bar.py`

**Spec:** Beta section

**Acceptance:** `python3 -m pytest -q tests/test_b.py`

{task2_tier}

{task2_depends}

{task2_tests}
""".format(
        goal=goal,
        gc=gc,
        task1_heading=task1_heading,
        task1_spec=task1_spec,
        task1_acceptance=task1_acceptance,
        task1_tier=task1_tier,
        task1_depends=task1_depends,
        task1_tests=task1_tests,
        task2_heading=task2_heading,
        task2_depends=task2_depends,
        task2_tier=task2_tier,
        task2_tests=task2_tests,
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
        return fl.lint_plan(self.plan_path, spec_path, repo_root=self.tmp)

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

    # --- tests grammar ----------------------------------------------------

    def test_tests_bulleted_form_no_defect(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:**\n- case one\n- case two"
        ), spec_path=self.spec_path)
        self.assertEqual(self._errors(defects), [])

    def test_tests_none_form_no_defect(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:** none — covered by acceptance"
        ), spec_path=self.spec_path)
        self.assertEqual(self._errors(defects), [])

    def test_tests_absent_field_no_defect(self):
        defects = self._lint(_base_plan(task1_tests=""), spec_path=self.spec_path)
        self.assertEqual(self._errors(defects), [])

    def test_tests_inline_joined_form_named(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:** case one; case two"
        ), spec_path=self.spec_path)
        errors = self._errors(defects)
        self.assertTrue(any(
            d.where == "task 1" and "Tests" in d.message for d in errors
        ))

    def test_tests_marker_with_neither_bullets_nor_none_named(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:**\nsome prose that is not a bullet"
        ), spec_path=self.spec_path)
        errors = self._errors(defects)
        self.assertTrue(any(
            d.where == "task 1" and "Tests" in d.message for d in errors
        ))

    def test_tests_every_offending_task_reported_in_one_run(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:** case one; case two",
            task2_tests="**Tests:** case three; case four",
        ), spec_path=self.spec_path)
        errors = self._errors(defects)
        self.assertTrue(any(
            d.where == "task 1" and "Tests" in d.message for d in errors
        ))
        self.assertTrue(any(
            d.where == "task 2" and "Tests" in d.message for d in errors
        ))

    def test_tests_defect_severity_is_error(self):
        defects = self._lint(_base_plan(
            task1_tests="**Tests:** case one; case two"
        ), spec_path=self.spec_path)
        matching = [d for d in defects if d.where == "task 1" and "Tests" in d.message]
        self.assertTrue(matching)
        self.assertTrue(all(d.severity == "error" for d in matching))

    def test_lint_task_fields_reports_tests_defect_directly(self):
        # Exercises fl._lint_task_fields in isolation, bypassing lint_plan
        # entirely (so _lint_checklists's indirect parse_test_cases call
        # via build_task_checklist never runs) — this is the only test that
        # would fail if the direct **Tests:** check were removed from
        # _lint_task_fields, since every other Tests test above goes
        # through lint_plan and would still pass via that indirect path.
        block = (
            "### Task 1: First thing\n"
            "- [ ] Done\n\n"
            "**Files:**\n"
            "- Create: `foo.py`\n\n"
            "**Tests:** case one; case two\n\n"
            "**Acceptance:** `python3 -m pytest -q tests/test_a.py`\n\n"
            "**Tier:** `standard`\n\n"
            "**Depends on:** nothing.\n"
        )
        defects, _ = fl._lint_task_fields([("task 1", 1, block)], None)
        errors = [d for d in defects if d.severity == "error"]
        self.assertTrue(any(
            d.where == "task 1" and "Tests" in d.message for d in errors
        ))

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


def _constraint_text(entries):
    """entries: [(id, because), ...] -> canonical-shaped constraints.md text
    (one record per entry; ``because`` is the field under test since it
    carries the widest budget)."""
    blocks = []
    for cid, because in entries:
        blocks.append(
            "## {}\n**Rule:** does a thing\n**Because:** {}\n"
            "**Scope:** repo\n**Source:** user\n".format(
                cid, because,
            )
        )
    return "\n".join(blocks)


def _deferral_text(entries):
    """entries: [(title, why), ...] -> canonical-shaped deferrals.md text."""
    blocks = []
    for title, why in entries:
        blocks.append(
            "## {}\n**Why:** {}\n**From:** user\n**Follow-up:** backlog\n".format(
                title, why,
            )
        )
    return "\n".join(blocks)


class ForgeLintMemoryTests(unittest.TestCase):
    """``check_memory_files`` — the harness-agnostic layer that catches a
    drifted constraints.md/deferrals.md no matter which harness wrote it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="forge-lint-memrepo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.docs_dir = os.path.join(self.repo, "docs", "forge")

    def _write_constraints(self, text):
        os.makedirs(self.docs_dir, exist_ok=True)
        _write(os.path.join(self.docs_dir, "constraints.md"), text)

    def _write_deferrals(self, text):
        os.makedirs(self.docs_dir, exist_ok=True)
        _write(os.path.join(self.docs_dir, "deferrals.md"), text)

    def _write_config(self, config_obj):
        os.makedirs(self.docs_dir, exist_ok=True)
        import json
        _write(os.path.join(self.docs_dir, "config.json"), json.dumps(config_obj))

    def test_drifted_constraints_one_defect_per_record(self):
        drifted = _constraint_text([
            ("bad-one", "x" * 310),
            ("bad-two", "y" * 310),
        ])
        self._write_constraints(drifted)
        defects = fl.check_memory_files(self.repo)
        self.assertEqual(len(defects), 2)
        self.assertTrue(all(d.severity == "error" for d in defects))

    def test_canonical_constraints_no_defects(self):
        self._write_constraints(_constraint_text([("good-one", "a short reason")]))
        defects = fl.check_memory_files(self.repo)
        self.assertEqual(defects, [])

    def test_absent_constraints_no_defects(self):
        # No docs/forge directory at all.
        defects = fl.check_memory_files(self.repo)
        self.assertEqual(defects, [])

    def test_deferrals_checked_when_file_store_configured(self):
        self._write_config({"deferrals": {"store": "file"}})
        self._write_deferrals(_deferral_text([("bad-title", "z" * 310)]))
        defects = fl.check_memory_files(self.repo)
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].severity, "error")

    def test_deferrals_ignored_without_file_store_config(self):
        # No config.json => deferral store defaults to GitHub. A drifted
        # deferrals.md must be ignored entirely — no gh call, no defect.
        self._write_deferrals(_deferral_text([("bad-title", "z" * 310)]))
        defects = fl.check_memory_files(self.repo)
        self.assertEqual(defects, [])

    def test_memory_and_plan_defects_both_reported_in_one_run(self):
        self._write_constraints(_constraint_text([("bad-one", "x" * 310)]))
        defects = fl.lint_plan(
            self._write_plan_with_bad_tier(), repo_root=self.repo,
        )
        errors = [d for d in defects if d.severity == "error"]
        self.assertTrue(any("bogus" in d.message for d in errors))
        self.assertTrue(any(d.where == "memory" for d in errors))

    def _write_plan_with_bad_tier(self):
        plan_path = os.path.join(self.repo, "plan.md")
        _write(plan_path, _base_plan(task1_tier="**Tier:** `bogus`"))
        return plan_path

    def test_existing_lint_behavior_unchanged_with_no_memory_files(self):
        # No docs/forge/ at all under repo_root: the memory check must
        # contribute zero defects, leaving every pre-existing lint defect
        # exactly as it was.
        plan_path = os.path.join(self.repo, "plan.md")
        _write(plan_path, LEGAL_MINIMAL_PLAN)
        other_clean_repo = tempfile.mkdtemp(prefix="forge-lint-memrepo-other-")
        self.addCleanup(shutil.rmtree, other_clean_repo, ignore_errors=True)
        defects_a = fl.lint_plan(plan_path, repo_root=self.repo)
        defects_b = fl.lint_plan(plan_path, repo_root=other_clean_repo)
        self.assertEqual(defects_a, defects_b)

    def test_repo_root_omitted_fails_loud_instead_of_guessing(self):
        # lint_plan must never fall back to os.getcwd() itself — a caller
        # that forgets repo_root gets a loud error, not a silent guess.
        plan_path = os.path.join(self.repo, "plan.md")
        _write(plan_path, LEGAL_MINIMAL_PLAN)
        with self.assertRaises(TypeError):
            fl.lint_plan(plan_path)

    def test_lint_plan_checks_the_given_repo_root_not_process_cwd(self):
        # This is the test that would have caught the original bug: process
        # cwd and repo_root are made to disagree, and lint_plan must follow
        # repo_root, never the process cwd, in either direction.
        dirty_repo = tempfile.mkdtemp(prefix="forge-lint-memrepo-dirty-")
        self.addCleanup(shutil.rmtree, dirty_repo, ignore_errors=True)
        os.makedirs(os.path.join(dirty_repo, "docs", "forge"))
        _write(
            os.path.join(dirty_repo, "docs", "forge", "constraints.md"),
            _constraint_text([("bad-one", "x" * 310)]),
        )
        clean_repo = tempfile.mkdtemp(prefix="forge-lint-memrepo-clean-")
        self.addCleanup(shutil.rmtree, clean_repo, ignore_errors=True)

        plan_path = os.path.join(self.repo, "plan.md")
        _write(plan_path, LEGAL_MINIMAL_PLAN)

        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)

        # process cwd is the dirty repo, but repo_root explicitly names the
        # clean one — no memory defect must appear.
        os.chdir(dirty_repo)
        defects = fl.lint_plan(plan_path, repo_root=clean_repo)
        self.assertFalse(any(d.where == "memory" for d in defects))

        # process cwd is the clean repo, but repo_root explicitly names the
        # dirty one — the memory defect must appear.
        os.chdir(clean_repo)
        defects = fl.lint_plan(plan_path, repo_root=dirty_repo)
        self.assertTrue(any(d.where == "memory" for d in defects))


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

    def _detached_repo_root(self):
        """A repo root the fixtures do not live under, so the spec-coverage
        check has no git history to read here. Without this the fixture tests
        would silently depend on this repo's own branch state — the fixtures
        were themselves added on a branch, so against its merge base every
        fixture section reads as new — which is the very coupling owned
        fixtures exist to remove."""
        root = tempfile.mkdtemp(prefix="forge-lint-fixture-root-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_fixture_phase14_plan_lints_clean(self):
        # Owned fixture, not the live docs/forge/plans document: a plan-grammar
        # change elsewhere must never break this test for a reason unrelated
        # to lint itself.
        plan = os.path.join(REPO_ROOT, "tests/fixtures/plans/legacy-halt-precision.md")
        spec = os.path.join(REPO_ROOT, "tests/fixtures/specs/legacy-halt-precision-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec,
             "--repo-root", self._detached_repo_root()],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixture_phase12b_plan_lints_clean(self):
        plan = os.path.join(REPO_ROOT, "tests/fixtures/plans/legacy-dispatch-parity.md")
        spec = os.path.join(REPO_ROOT, "tests/fixtures/specs/legacy-dispatch-parity-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec,
             "--repo-root", self._detached_repo_root()],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixture_phase14_plan_tier_justification_removed_errors(self):
        # Proves the fixture still exercises real lint rules rather than
        # passing vacuously: strip Task 3's trivial-tier justification and
        # lint must report it as an error.
        plan = os.path.join(REPO_ROOT, "tests/fixtures/plans/legacy-halt-precision.md")
        spec = os.path.join(REPO_ROOT, "tests/fixtures/specs/legacy-halt-precision-design.md")
        with open(plan, encoding="utf-8") as f:
            text = f.read()
        broken_text = text.replace(
            "**Tier:** `trivial` — version strings and one status word; no logic, no design content.",
            "**Tier:** `trivial`",
        )
        self.assertNotEqual(text, broken_text, "fixture's trivial-tier line not found to break")
        fd, broken_path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(broken_text)
            result = subprocess.run(
                [sys.executable, SCRIPT, broken_path, "--spec", spec,
                 "--repo-root", self._detached_repo_root()],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("justification", result.stdout)
        finally:
            os.remove(broken_path)


def _spec_text(system="execution", supersedes=None, changelog=None, extra=""):
    """Canonical-shaped living-spec text; each parameter overridable to
    inject exactly one defect. ``changelog`` is a list of raw lines placed
    verbatim under '## Changelog'; ``None`` means a well-formed single
    entry."""
    fm_lines = ["---"]
    fm_lines.append("system: {}".format(system))
    if supersedes is not None:
        fm_lines.append("supersedes:")
        for path in supersedes:
            fm_lines.append("  - {}".format(path))
    fm_lines.append("---")

    if changelog is None:
        changelog = ["2026-09-05: initial version (#1)"]

    return "\n".join(fm_lines) + "\n\n# Title\n\nBody text.{}\n\n## Changelog\n{}\n".format(
        extra, "\n".join(changelog)
    )


class ForgeLintLivingSpecTests(unittest.TestCase):
    """The five living-spec grammar rules (Phase 14/5): dated filename,
    frontmatter/system-identity, supersedes resolution, Changelog presence
    and entry grammar, and amended-by system existence — plus the
    hand-written frontmatter parser and the corpus mode built on all five."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-lint-spec-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.specs_dir = os.path.join(self.tmp, "docs", "forge", "specs")
        os.makedirs(self.specs_dir)

    def _write_spec(self, name, text):
        path = os.path.join(self.specs_dir, name)
        _write(path, text)
        return path

    # --- rule 2: frontmatter parses, system == filename stem -------------

    def test_clean_spec_lints_with_no_defects(self):
        path = self._write_spec("execution.md", _spec_text(system="execution"))
        self.assertEqual(fl.lint_living_spec(path, repo_root=self.tmp), [])

    def test_system_disagreeing_with_stem_named(self):
        path = self._write_spec("execution.md", _spec_text(system="planning"))
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("planning" in d and "execution" in d for d in defects))

    def test_missing_frontmatter_is_a_defect(self):
        path = self._write_spec("execution.md", "# Title\n\n## Changelog\n2026-09-05: x (#1)\n")
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("system" in d for d in defects))

    # --- rule 3: supersedes resolves relative to docs/forge/ ---------------

    def test_supersedes_path_resolves_no_defect(self):
        archive_dir = os.path.join(self.tmp, "docs", "forge", "archive")
        os.makedirs(archive_dir)
        _write(os.path.join(archive_dir, "2026-01-01-old.md"), "old content\n")
        path = self._write_spec(
            "execution.md",
            _spec_text(system="execution", supersedes=["archive/2026-01-01-old.md"]),
        )
        self.assertEqual(fl.lint_living_spec(path, repo_root=self.tmp), [])

    def test_supersedes_path_that_does_not_resolve_named(self):
        path = self._write_spec(
            "execution.md",
            _spec_text(system="execution", supersedes=["archive/nosuchfile.md"]),
        )
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("archive/nosuchfile.md" in d for d in defects))

    # --- rule 4: Changelog presence and entry grammar ----------------------

    def test_missing_changelog_named(self):
        text = "---\nsystem: execution\n---\n\n# Title\n\nBody, no changelog.\n"
        path = self._write_spec("execution.md", text)
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("Changelog" in d for d in defects))

    def test_malformed_changelog_entry_named_by_line(self):
        path = self._write_spec(
            "execution.md",
            _spec_text(system="execution", changelog=["not a dated entry"]),
        )
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("not a dated entry" in d for d in defects))

    # --- rule 5: amended by [<id>] names a system that exists --------------

    def test_amended_by_unknown_system_named(self):
        self._write_spec("execution.md", _spec_text(system="execution"))
        path = self._write_spec(
            "planning.md",
            _spec_text(
                system="planning",
                changelog=["2026-09-05: amended by [nosuchsystem] — x (#1)"],
            ),
        )
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("nosuchsystem" in d for d in defects))

    def test_amended_by_real_system_no_defect(self):
        self._write_spec("execution.md", _spec_text(system="execution"))
        path = self._write_spec(
            "planning.md",
            _spec_text(
                system="planning",
                changelog=["2026-09-05: amended by [execution] — x (#1)"],
            ),
        )
        self.assertEqual(fl.lint_living_spec(path, repo_root=self.tmp), [])

    # --- rule 1: dated filename ---------------------------------------------

    def test_dated_filename_named(self):
        path = self._write_spec(
            "2026-09-05-execution-design.md",
            _spec_text(system="2026-09-05-execution-design"),
        )
        defects = fl.lint_living_spec(path, repo_root=self.tmp)
        self.assertTrue(any("YYYY-MM-DD" in d for d in defects))

    def test_same_content_renamed_is_clean(self):
        path = self._write_spec("execution.md", _spec_text(system="execution"))
        self.assertEqual(fl.lint_living_spec(path, repo_root=self.tmp), [])

    # --- archive is never linted --------------------------------------------

    def test_dated_frontmatter_less_spec_under_archive_no_defect(self):
        archive_dir = os.path.join(self.tmp, "docs", "forge", "archive")
        os.makedirs(archive_dir)
        path = os.path.join(archive_dir, "2026-01-01-old-design.md")
        _write(path, "# Old dated spec\n\nNo frontmatter, no changelog.\n")
        self.assertEqual(fl.lint_living_spec(path, repo_root=self.tmp), [])

    # --- corpus mode reports every offender, not just the first ------------

    def test_corpus_mode_reports_every_offending_spec(self):
        self._write_spec("execution.md", _spec_text(system="wrong-one"))
        self._write_spec("planning.md", _spec_text(system="also-wrong"))
        defects = fl.lint_spec_corpus(self.tmp)
        self.assertTrue(any("execution.md" in d for d in defects))
        self.assertTrue(any("planning.md" in d for d in defects))

    def test_corpus_mode_clean_when_every_spec_clean(self):
        self._write_spec("execution.md", _spec_text(system="execution"))
        self._write_spec("planning.md", _spec_text(system="planning"))
        self.assertEqual(fl.lint_spec_corpus(self.tmp), [])

    # --- hand-written frontmatter parser: fails loud on malformed grammar ---

    def test_parse_frontmatter_no_dashes_returns_empty_not_raise(self):
        data, body_start = fl.parse_frontmatter(["# Title\n", "\n", "body\n"])
        self.assertEqual(data, {})
        self.assertEqual(body_start, 0)

    def test_parse_frontmatter_unterminated_block_raises(self):
        with self.assertRaises(RuntimeError):
            fl.parse_frontmatter(["---\n", "system: execution\n"])

    def test_parse_frontmatter_non_key_value_line_raises(self):
        with self.assertRaises(RuntimeError):
            fl.parse_frontmatter(["---\n", "not a key value line\n", "---\n"])

    def test_lint_living_spec_malformed_frontmatter_raises_naming_file_and_line(self):
        path = self._write_spec(
            "execution.md",
            "---\nsystem: execution\nnot a key value line\n---\n\n## Changelog\n",
        )
        with self.assertRaises(RuntimeError) as ctx:
            fl.lint_living_spec(path, repo_root=self.tmp)
        message = str(ctx.exception)
        self.assertIn(path, message)
        self.assertIn("line 3", message)

    def test_no_third_party_import_in_forge_lint(self):
        with open(os.path.join(SCRIPTS_DIR, "forge_lint.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("import yaml", src)

    # --- --specs CLI mode ---------------------------------------------------

    def test_cli_specs_mode_exits_nonzero_and_lists_defects(self):
        self._write_spec("execution.md", _spec_text(system="wrong-one"))
        result = subprocess.run(
            [sys.executable, SCRIPT, "--specs", "--repo-root", self.tmp],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("execution.md", result.stdout)

    def test_cli_specs_mode_exits_zero_when_clean(self):
        self._write_spec("execution.md", _spec_text(system="execution"))
        result = subprocess.run(
            [sys.executable, SCRIPT, "--specs", "--repo-root", self.tmp],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ForgeLintRealSpecCorpusTests(unittest.TestCase):
    """docs/forge/specs/ now holds only the four migrated living specs — the
    dated corpus was archived under docs/forge/archive/specs/. This is the
    post-migration acceptance criterion."""

    def test_real_specs_dir_is_compliant_post_migration(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--specs", "--repo-root", REPO_ROOT],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


COVERAGE_SPEC = """# Spec

## Alpha section

Alpha content.

## Beta section

Beta content.

## Risks / constraints

Risk content.

## Changelog

2026-01-01: created.
"""


def _coverage_plan(*spec_values):
    """One task per given **Spec:** value; ``None`` declares no **Spec:**."""
    blocks = []
    for i, value in enumerate(spec_values, 1):
        spec_line = "**Spec:** {}\n\n".format(value) if value else ""
        blocks.append(
            "### Task {n}: Thing {n}\n"
            "- [ ] Done\n\n"
            "**Files:**\n- Create: `f{n}.py`\n\n"
            "{spec}"
            "**Acceptance:** `python3 -m pytest -q`\n\n"
            "**Tier:** `standard`\n\n"
            "**Depends on:** nothing.\n".format(n=i, spec=spec_line)
        )
    return "# Plan header\n\n**Goal:** Ship the thing.\n\n" + "\n".join(
        "# Task {}\n\n{}".format(i, b) for i, b in enumerate(blocks, 1)
    )


class ForgeLintChangedSpecCoverageTests(unittest.TestCase):
    """Every **changed** spec section must be named by some task's
    ``**Spec:**`` line — the coverage gap that no single task's diff can
    reveal, caught before the first line is written rather than mid-run."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="forge-lint-covrepo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.spec_path = os.path.join(self.repo, "spec.md")
        self.plan_path = os.path.join(self.repo, "plan.md")

    # --- fixtures -------------------------------------------------------

    def _git(self, *args):
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        })
        result = subprocess.run(
            ["git", "-C", self.repo] + list(args),
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _init_repo(self, branch="main"):
        self._git("init", "-q", "-b", branch)

    def _commit(self, name, text):
        _write(os.path.join(self.repo, name), text)
        self._git("add", name)
        self._git("commit", "-qm", "add " + name)

    def _commit_spec(self, text=COVERAGE_SPEC):
        self._init_repo()
        self._commit("spec.md", text)

    def _coverage(self, plan_text, spec_text=None, repo_root=None):
        if spec_text is not None:
            _write(self.spec_path, spec_text)
        _write(self.plan_path, plan_text)
        defects = fl.lint_plan(
            self.plan_path, self.spec_path, repo_root=repo_root or self.repo,
        )
        return [d for d in defects if d.where == "spec coverage"]

    # --- the rule -------------------------------------------------------

    def test_changed_section_claimed_yields_no_defect(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace("Alpha content.", "Alpha content, revised.")
        self.assertEqual(self._coverage(_coverage_plan("Alpha section"), changed), [])

    def test_changed_section_claimed_by_no_task_is_an_error_naming_it(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace("Beta content.", "Beta content, revised.")
        defects = self._coverage(_coverage_plan("Alpha section"), changed)
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta section", defects[0].message)
        self.assertEqual(defects[0].severity, "error")

    def test_unchanged_section_claimed_by_no_task_yields_no_defect(self):
        self._commit_spec()
        self.assertEqual(self._coverage(_coverage_plan(None), COVERAGE_SPEC), [])

    def test_changed_changelog_is_exempt(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace(
            "2026-01-01: created.", "2026-01-01: created.\n2026-01-02: amended.",
        )
        self.assertEqual(self._coverage(_coverage_plan(None), changed), [])

    def test_spec_with_no_committed_version_requires_every_section_claimed(self):
        self._init_repo()
        self._commit("README.md", "hello\n")  # HEAD exists; spec.md does not
        defects = self._coverage(_coverage_plan("Alpha section"), COVERAGE_SPEC)
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta section", defects[0].message)

    def test_spec_outside_a_git_repo_yields_no_defect(self):
        outside = tempfile.mkdtemp(prefix="forge-lint-nogit-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.spec_path = os.path.join(outside, "spec.md")
        self.plan_path = os.path.join(outside, "plan.md")
        self.assertEqual(
            self._coverage(_coverage_plan(None), COVERAGE_SPEC, repo_root=outside), [],
        )

    def test_failed_git_read_yields_no_defect(self):
        # A repo with an unborn HEAD: `git show HEAD:spec.md` cannot answer,
        # and an unanswerable git read must never manufacture a defect.
        self._init_repo()
        self.assertEqual(self._coverage(_coverage_plan(None), COVERAGE_SPEC), [])

    def test_every_unclaimed_changed_section_reported_in_one_run(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace(
            "Alpha content.", "Alpha content, revised.",
        ).replace("Beta content.", "Beta content, revised.")
        defects = self._coverage(_coverage_plan(None), changed)
        self.assertEqual(len(defects), 2, defects)
        self.assertTrue(any("Alpha section" in d.message for d in defects))
        self.assertTrue(any("Beta section" in d.message for d in defects))

    # --- the comparison unit --------------------------------------------

    def test_reflow_alone_is_not_a_change(self):
        self._commit_spec()
        reflowed = COVERAGE_SPEC.replace(
            "Beta content.", "Beta\ncontent.",
        ).replace("Alpha content.", "   Alpha content.   ")
        self.assertEqual(self._coverage(_coverage_plan(None), reflowed), [])

    def test_renamed_heading_reads_as_changed(self):
        self._commit_spec()
        renamed = COVERAGE_SPEC.replace("## Beta section", "## Beta area")
        defects = self._coverage(_coverage_plan(None), renamed)
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta area", defects[0].message)

    def test_change_in_a_subsection_does_not_mark_its_parent_changed(self):
        spec = (
            "# Spec\n\n## Alpha section\n\nAlpha content.\n\n"
            "### Alpha detail\n\nDetail content.\n\n"
            "## Beta section\n\nBeta content.\n"
        )
        self._commit_spec(spec)
        changed = spec.replace("Detail content.", "Detail content, revised.")
        defects = self._coverage(_coverage_plan(None), changed)
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Alpha detail", defects[0].message)

    def test_claiming_a_parent_claims_its_changed_subsection(self):
        spec = (
            "# Spec\n\n## Alpha section\n\nAlpha content.\n\n"
            "### Alpha detail\n\nDetail content.\n\n"
            "## Beta section\n\nBeta content.\n"
        )
        self._commit_spec(spec)
        changed = spec.replace("Detail content.", "Detail content, revised.")
        self.assertEqual(self._coverage(_coverage_plan("Alpha section"), changed), [])

    def test_unresolvable_spec_name_does_not_suppress_the_coverage_check(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace("Beta content.", "Beta content, revised.")
        defects = self._coverage(_coverage_plan("Nonexistent section"), changed)
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta section", defects[0].message)

    def test_changed_risks_and_constraints_is_exempt(self):
        self._commit_spec()
        changed = COVERAGE_SPEC.replace("Risk content.", "Risk content, revised.")
        self.assertEqual(self._coverage(_coverage_plan(None), changed), [])

    # --- the baseline is the merge base, not HEAD ------------------------

    def test_section_changed_and_committed_on_the_branch_still_reported(self):
        # The flow amends a spec *and commits it* before the plan is written.
        # Against HEAD the amendment reads as unchanged and the rule is inert
        # in the exact flow it exists for; against the merge base it fires.
        self._commit_spec()
        self._git("checkout", "-q", "-b", "feature")
        _write(self.spec_path, COVERAGE_SPEC.replace("Beta content.", "Beta content, revised."))
        self._git("add", "spec.md")
        self._git("commit", "-qm", "amend spec")
        defects = self._coverage(_coverage_plan(None))
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta section", defects[0].message)
        self.assertEqual(defects[0].severity, "error")

    def test_no_resolvable_merge_base_falls_back_to_head(self):
        # No default branch to resolve and no remote: the baseline degrades to
        # HEAD and the rule goes inert, rather than failing a plan because
        # lint cannot establish what the branch changed.
        self._init_repo(branch="odd-branch")
        self._commit("spec.md", COVERAGE_SPEC)
        self._commit("spec.md", COVERAGE_SPEC.replace("Beta content.", "Beta content, revised."))
        self.assertEqual(self._coverage(_coverage_plan(None)), [])

    def test_dangling_origin_head_falls_through_to_a_resolvable_default(self):
        # A dangling refs/remotes/origin/HEAD is ordinary after a
        # default-branch rename or a partial clone. Taking its answer
        # unverified would make merge-base fail and the rule go permanently,
        # silently inert while a resolvable default branch sits one rung
        # down — lint reporting clean because it cannot see.
        self._commit_spec()
        self._git("symbolic-ref", "refs/remotes/origin/HEAD",
                  "refs/remotes/origin/renamed-away")
        self._git("checkout", "-q", "-b", "feature")
        _write(self.spec_path, COVERAGE_SPEC.replace("Beta content.", "Beta content, revised."))
        self._git("add", "spec.md")
        self._git("commit", "-qm", "amend spec")
        defects = self._coverage(_coverage_plan(None))
        self.assertEqual(len(defects), 1, defects)
        self.assertIn("Beta section", defects[0].message)

    def test_committed_change_against_the_default_branch_itself_is_inert(self):
        # On the default branch, merge-base HEAD <default> is HEAD, so a
        # committed amendment reads as unchanged — the documented degradation.
        self._commit_spec()
        self._commit("spec.md", COVERAGE_SPEC.replace("Beta content.", "Beta content, revised."))
        self.assertEqual(self._coverage(_coverage_plan(None)), [])


class PlanningSkillTemplateTests(unittest.TestCase):
    """The authoring front door: the task-structure template in
    `skills/planning/SKILL.md` is what plan authors copy, so every field form
    it shows must be one `forge_lint.py` accepts. A template that lints as an
    `error` produces plans that never dispatch."""

    SKILL_MD = os.path.join(REPO_ROOT, "skills", "planning", "SKILL.md")

    def _template_field(self, name):
        """The `**<name>:**` block from the ```markdown task-structure
        template — the marker line through the line before the next blank
        line — read out of the real SKILL.md, not a copy."""
        with open(self.SKILL_MD, encoding="utf-8") as f:
            lines = f.read().splitlines()
        start = next(
            i for i, ln in enumerate(lines)
            if ln.startswith("### Task N:")
        )
        marker = "**{}:**".format(name)
        i = next(
            j for j in range(start, len(lines))
            if lines[j].startswith(marker)
        )
        block = [lines[i]]
        for ln in lines[i + 1:]:
            if not ln.strip():
                break
            block.append(ln)
        return "\n".join(block)

    def test_template_tests_field_lints_clean_in_a_plan(self):
        tests_block = self._template_field("Tests")
        plan = LEGAL_MINIMAL_PLAN.replace(
            "**Acceptance:**", tests_block + "\n\n**Acceptance:**",
        )
        tmp = tempfile.mkdtemp(prefix="forge-lint-template-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        plan_path = os.path.join(tmp, "plan.md")
        _write(plan_path, plan)
        defects = fl.lint_plan(plan_path, None, repo_root=tmp)
        self.assertEqual(
            [d for d in defects if d.severity == "error"], [], plan,
        )

    def test_template_tests_field_parses_as_named_cases(self):
        # Not merely "lint doesn't reject it": the template's own block must
        # yield real test-case items, so a plan copied from it produces a
        # non-empty checklist rather than a silently empty one.
        cases = fl.eb.parse_test_cases(
            "### Task 1: T\n\n" + self._template_field("Tests") + "\n",
        )
        self.assertTrue(len(cases) >= 2, cases)


if __name__ == "__main__":
    unittest.main()
