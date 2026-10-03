"""Plan parsing, task ordering, and --effort override parsing."""
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest

from _forge_support import *  # noqa: F401,F403


# Local variants of the shared trivial-tier fixtures, carrying the
# justification the new contract requires for an off-floor (non-standard)
# tier. Kept local to this file (rather than editing the shared
# _forge_support.py fixtures used by other test modules) to stay within this
# task's file scope.
PLAN_DEPS_JUSTIFIED = PLAN_DEPS.replace(
    "**Tier:** trivial", "**Tier:** trivial — mechanical, single call site"
)
PLAN_PASS_JUSTIFIED = PLAN_PASS.replace(
    "**Tier:** trivial", "**Tier:** trivial — mechanical, single call site"
)


class ParsePlanTasksTests(unittest.TestCase):
    def _write(self, content):
        d = tempfile.mkdtemp(prefix="forge-run-parse-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w") as f:
            f.write(content)
        return p

    def test_parses_number_title_tier_depends_acceptance(self):
        tasks = forge_run.parse_plan_tasks(self._write(PLAN_DEPS_JUSTIFIED))
        by_num = {t.number: t for t in tasks}
        self.assertEqual(set(by_num), {1, 2})
        self.assertEqual(by_num[1].title, "First task")
        self.assertEqual(by_num[1].tier, "trivial")
        self.assertEqual(by_num[1].depends_on, [])
        self.assertEqual([c.command for c in by_num[1].acceptance_checks], ["true"])
        self.assertEqual(by_num[2].depends_on, [1])

    def test_checkbox_line_points_at_done_line(self):
        p = self._write(PLAN_PASS_JUSTIFIED)
        tasks = forge_run.parse_plan_tasks(p)
        with open(p) as f:
            lines = f.read().splitlines()
        idx = tasks[0].checkbox_line
        self.assertIn("[ ]", lines[idx])

    def test_wrong_level_heading_raises_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(self._write(PLAN_BAD_HEADING))
        msg = str(ctx.exception)
        self.assertIn("### Task 1:", msg)
        self.assertIn("## Task 1:", msg)

    def test_duplicate_task_number_raises_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(self._write(PLAN_DUP))
        self.assertIn("1", str(ctx.exception))
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_acceptance_checks_extracted_from_bulleted_form(self):
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:**\n"
            "- `python3 -m pytest -q tests/test_a.py` passes\n"
            "- the output reads well, see `docs/x.md`\n"
            "- `python3 -m pytest -q tests/test_b.py` exits 1\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        tasks = forge_run.parse_plan_tasks(self._write(plan))
        self.assertEqual(
            [(c.command, c.outcome, c.expected, c.stated) for c in tasks[0].acceptance_checks],
            [
                ("python3 -m pytest -q tests/test_a.py", "passes", None, "passes"),
                ("python3 -m pytest -q tests/test_b.py", "exits", 1, "exits 1"),
            ],
        )

    def test_acceptance_checks_stop_at_h4_heading(self):
        # #87 Task 5: an h4 inside an **Acceptance:** block terminates the
        # clause parser, so the clause after it is not a check.
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:**\n"
            "- `pytest -q` passes\n"
            "#### note\n"
            "- `ruff check` passes\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        tasks = forge_run.parse_plan_tasks(self._write(plan))
        self.assertEqual([c.command for c in tasks[0].acceptance_checks], ["pytest -q"])

    def test_acceptance_checks_extracted_from_single_line_form(self):
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:** `python3 -m pytest -q tests/test_a.py` passes\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        tasks = forge_run.parse_plan_tasks(self._write(plan))
        self.assertEqual(
            [c.command for c in tasks[0].acceptance_checks],
            ["python3 -m pytest -q tests/test_a.py"],
        )

    def test_malformed_command_clause_raises_naming_task_line_clause_and_outcomes(self):
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:**\n"
            "- `true` passes\n"
            "- `make test` succeeds\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(self._write(plan))
        msg = str(ctx.exception)
        self.assertIn("task 1", msg)
        self.assertIn("line 10", msg)
        self.assertIn("`make test` succeeds", msg)
        for outcome in forge_run.forge_plan.ACCEPTANCE_OUTCOMES:
            self.assertIn(outcome, msg)

    def test_malformed_single_line_clause_names_its_line(self):
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:** `true`\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(self._write(plan))
        self.assertIn("line 8", str(ctx.exception))

    def test_prose_only_acceptance_yields_no_checks(self):
        plan = (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:** The docs read well.\n\n"
            "**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        self.assertEqual(forge_run.parse_plan_tasks(self._write(plan))[0].acceptance_checks, [])


class ParseAcceptanceClauseTests(unittest.TestCase):
    def setUp(self):
        self.parse = forge_run.forge_plan.parse_acceptance_clause

    def test_passes(self):
        c = self.parse("`make test` passes")
        self.assertEqual((c.command, c.outcome, c.expected, c.stated), ("make test", "passes", None, "passes"))

    def test_exits_n(self):
        c = self.parse("`grep -q x f` exits 1")
        self.assertEqual((c.command, c.outcome, c.expected, c.stated), ("grep -q x f", "exits", 1, "exits 1"))

    def test_prints_nothing(self):
        c = self.parse("`grep -n slug f` prints nothing")
        self.assertEqual((c.outcome, c.expected), ("prints-nothing", None))

    def test_prints_text_second_span_is_not_a_command(self):
        c = self.parse("`python3 -V` prints `Python 3`")
        self.assertEqual((c.command, c.outcome, c.expected), ("python3 -V", "prints", "Python 3"))
        self.assertEqual(c.stated, "prints `Python 3`")

    def test_leading_whitespace_still_a_command_clause(self):
        c = self.parse("   `make test` passes")
        self.assertEqual((c.command, c.outcome), ("make test", "passes"))

    def test_prose_with_inline_code_is_none(self):
        self.assertIsNone(self.parse("The file `docs/x.md` reads well"))
        self.assertIsNone(self.parse("Output reports version `0.13.1`"))

    def test_bare_command_raises_naming_missing_outcome(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse("`make test`")
        self.assertIn("outcome", str(ctx.exception))

    def test_unknown_outcome_raises(self):
        with self.assertRaises(ValueError):
            self.parse("`make test` succeeds")

    def test_trailing_period_raises(self):
        with self.assertRaises(ValueError):
            self.parse("`make test` passes.")

    def test_exits_negative_and_word_raise(self):
        for bad in ("`x` exits -1", "`x` exits one", "`x` exits"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.parse(bad)

    def test_second_command_span_raises(self):
        with self.assertRaises(ValueError):
            self.parse("`a` `b` passes")

    def test_trailing_text_after_prints_raises(self):
        with self.assertRaises(ValueError):
            self.parse("`x` prints `y` and more")


class TierJustificationTests(unittest.TestCase):
    """Tier: field parses as <level>[ -- <justification>], split on the em
    dash. standard ignores/clears any justification; complex/trivial require a
    non-empty one, else RuntimeError naming the task. Presence only -- never
    justification quality (Classification contract, Enforcement)."""

    def _write(self, content):
        d = tempfile.mkdtemp(prefix="forge-run-tier-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w") as f:
            f.write(content)
        return p

    def _plan(self, tier_line):
        return (
            "# Fixture Plan\n\n"
            "**Goal:** Do the thing.\n\n"
            "### Task 1: First task\n"
            "- [ ] Done\n\n"
            "**Acceptance:** `true` passes\n\n"
            "**Tier:** {}\n\n"
            "**Depends on:** nothing\n"
        ).format(tier_line)

    def test_complex_with_justification_parses_level_and_justification(self):
        p = self._write(
            self._plan("complex — reconciles two retry semantics")
        )
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "complex")
        self.assertEqual(
            tasks[0].tier_justification, "reconciles two retry semantics"
        )

    def test_bare_standard_parses_with_no_justification(self):
        p = self._write(self._plan("standard"))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "standard")
        self.assertIsNone(tasks[0].tier_justification)

    def test_bare_complex_raises_naming_task(self):
        p = self._write(self._plan("complex"))
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(p)
        msg = str(ctx.exception)
        self.assertIn("1", msg)
        self.assertIn("justification", msg.lower())

    def test_trivial_without_justification_raises(self):
        p = self._write(self._plan("trivial"))
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(p)
        msg = str(ctx.exception)
        self.assertIn("1", msg)
        self.assertIn("justification", msg.lower())

    def test_standard_with_trailing_text_stores_none(self):
        p = self._write(self._plan("standard — anything"))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "standard")
        self.assertIsNone(tasks[0].tier_justification)

    def test_unknown_level_still_raises_existing_error(self):
        p = self._write(self._plan("bogus — with justification"))
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(p)
        msg = str(ctx.exception)
        self.assertIn("bogus", msg)
        self.assertIn("1", msg)

    def test_backticked_bare_tier_parses(self):
        p = self._write(self._plan("`standard`"))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "standard")
        self.assertIsNone(tasks[0].tier_justification)

    def test_backticked_tier_with_justification_parses_and_keeps_justification(
        self,
    ):
        p = self._write(
            self._plan("`complex` — reconciles two retry semantics")
        )
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "complex")
        self.assertEqual(
            tasks[0].tier_justification, "reconciles two retry semantics"
        )

    def test_trailing_period_on_bare_tier_parses(self):
        p = self._write(self._plan("complex — mechanical rationale."))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "complex")
        self.assertEqual(tasks[0].tier_justification, "mechanical rationale.")

    def test_trailing_period_on_bare_level_parses(self):
        p = self._write(self._plan("standard."))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "standard")
        self.assertIsNone(tasks[0].tier_justification)

    def test_backticked_level_with_trailing_period_parses(self):
        p = self._write(self._plan("`standard`."))
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "standard")
        self.assertIsNone(tasks[0].tier_justification)

    def test_backticked_level_with_trailing_period_and_justification_parses(
        self,
    ):
        p = self._write(
            self._plan("`trivial`. — single enum value, one call site")
        )
        tasks = forge_run.parse_plan_tasks(p)
        self.assertEqual(tasks[0].tier, "trivial")
        self.assertEqual(
            tasks[0].tier_justification, "single enum value, one call site"
        )

    def test_unknown_tier_after_normalization_still_raises_naming_value(self):
        p = self._write(self._plan("`bogus`. — with justification"))
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(p)
        msg = str(ctx.exception)
        self.assertIn("bogus", msg)
        self.assertIn("1", msg)

    def test_backticked_complex_without_justification_still_raises(self):
        p = self._write(self._plan("`complex`"))
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_plan_tasks(p)
        msg = str(ctx.exception)
        self.assertIn("1", msg)
        self.assertIn("justification", msg.lower())


class ParseEffortOverridesTests(unittest.TestCase):
    """parse_effort_overrides: repeatable --effort N=LEVEL entries -> {int: str}.
    Malformed entries and disallowed levels (including 'ultra') raise naming the
    cause; task-number existence is validated later, against the parsed plan."""

    def test_parses_single_override(self):
        overrides = forge_run.parse_effort_overrides(["3=max"])
        self.assertEqual(overrides, {3: "max"})

    def test_parses_multiple_overrides(self):
        overrides = forge_run.parse_effort_overrides(["1=low", "2=xhigh"])
        self.assertEqual(overrides, {1: "low", 2: "xhigh"})

    def test_empty_or_none_yields_empty_dict(self):
        self.assertEqual(forge_run.parse_effort_overrides([]), {})
        self.assertEqual(forge_run.parse_effort_overrides(None), {})

    def test_malformed_entry_raises_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_effort_overrides(["nope"])
        self.assertIn("nope", str(ctx.exception))

    def test_ultra_rejected_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_effort_overrides(["1=ultra"])
        msg = str(ctx.exception)
        self.assertIn("ultra", msg)

    def test_unknown_level_rejected_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_effort_overrides(["1=bogus"])
        self.assertIn("bogus", str(ctx.exception))


class OutcomeMetTests(unittest.TestCase):
    def setUp(self):
        self.parse = forge_run.forge_plan.parse_acceptance_clause
        self.met = forge_run.forge_plan.outcome_met

    def _met(self, clause, exit_code, output="", timed_out=False):
        return self.met(self.parse(clause), exit_code, output, timed_out)

    def test_passes_met_by_exit_0_not_exit_1(self):
        self.assertTrue(self._met("`t` passes", 0))
        self.assertFalse(self._met("`t` passes", 1))

    def test_exits_n_met_by_exactly_n(self):
        self.assertTrue(self._met("`t` exits 1", 1))
        self.assertFalse(self._met("`t` exits 1", 0))

    def test_prints_nothing_met_by_empty_output_any_exit(self):
        for code in (0, 1, 2):
            self.assertTrue(self._met("`t` prints nothing", code, ""))
            self.assertFalse(self._met("`t` prints nothing", code, "x"))

    def test_prints_text_needs_text_and_exit_0(self):
        self.assertTrue(self._met("`t` prints `ok`", 0, "all ok\n"))
        self.assertFalse(self._met("`t` prints `ok`", 1, "all ok\n"))
        self.assertFalse(self._met("`t` prints `ok`", 0, "nope\n"))

    def test_prints_text_found_before_the_tail_window(self):
        out = "ok\n" + "x" * 10000
        self.assertTrue(self._met("`t` prints `ok`", 0, out))

    def test_timed_out_meets_no_outcome(self):
        for clause in ("`t` passes", "`t` exits 1", "`t` prints nothing",
                       "`t` prints `ok`"):
            self.assertFalse(self._met(clause, None, "ok", timed_out=True))
