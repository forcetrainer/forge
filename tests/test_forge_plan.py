"""Plan parsing, task ordering, and --effort override parsing."""
import json
import os
import pathlib
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
        self.assertEqual(by_num[1].acceptance_commands, ["true"])
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
            "**Acceptance:** `true`\n\n"
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


class ParsePlanTasksRealPlansTests(unittest.TestCase):
    """Regression proof against the actual shipped plans that motivated the
    tier-normalization fix -- both use the backticked **Tier:** template form
    that the un-normalized parser rejected."""

    def _plan_path(self, name):
        return str(
            pathlib.Path(__file__).resolve().parent.parent
            / "docs"
            / "forge"
            / "plans"
            / name
        )

    def test_phase13_review_continuity_plan_parses(self):
        tasks = forge_run.parse_plan_tasks(
            self._plan_path("2026-08-21-phase13-review-continuity.md")
        )
        self.assertGreater(len(tasks), 0)

    def test_phase12b_claude_dispatch_parity_plan_parses(self):
        tasks = forge_run.parse_plan_tasks(
            self._plan_path("2026-07-17-phase12b-claude-dispatch-parity.md")
        )
        self.assertGreater(len(tasks), 0)


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
