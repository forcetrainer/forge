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

    def test_real_phase14_plan_lints_clean(self):
        plan = os.path.join(REPO_ROOT, "docs/forge/plans/2026-08-21-phase14-halt-precision.md")
        spec = os.path.join(REPO_ROOT, "docs/forge/archive/specs/2026-08-21-halt-precision-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_phase12b_plan_lints_clean(self):
        plan = os.path.join(REPO_ROOT, "docs/forge/plans/2026-07-17-phase12b-claude-dispatch-parity.md")
        spec = os.path.join(REPO_ROOT, "docs/forge/archive/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md")
        result = subprocess.run(
            [sys.executable, SCRIPT, plan, "--spec", spec],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


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


if __name__ == "__main__":
    unittest.main()
