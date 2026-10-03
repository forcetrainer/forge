"""Tests for scripts/extract-brief.py.

Loaded via importlib since the script filename contains a hyphen and is not
a shared module (Global Constraints: no shared module between scripts).
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import tempfile
import unittest

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "extract-brief.py"

_spec = importlib.util.spec_from_file_location("extract_brief", SCRIPT_PATH)
extract_brief = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract_brief)


PLAN_NO_SPEC = """# Sample Plan

**Goal:** Ship a widget.
**Architecture:** Single module.
**Tech stack:** Python.
**Global Constraints:**
- Constraint one.
- Constraint two.

### Task 1: Build the widget
- [ ] Done

**Files:**
- Create: `widget.py`

**Tests:** unit tests.

**Acceptance:** `pytest` passes.

**Tier:** standard

**Depends on:** nothing

### Task 2: Ship the widget
- [ ] Done

**Files:**
- Modify: `ship.py`
"""

PLAN_WITH_SPEC = """# Sample Plan 2

**Goal:** Ship a gadget.
**Global Constraints:** Single global constraint line, all inline.

### Task 1: Build the gadget
- [ ] Done

**Files:**
- Create: `gadget.py`

**Spec:** Gadget Design, Gadget Testing

**Tests:** unit tests.

**Acceptance:** `pytest` passes.

### Task 2: Ship the gadget
- [ ] Done

**Files:**
- Modify: `ship.py`

**Spec:** Ship Design
"""

SPEC_CLEAN = """# Design Doc

## 1. Gadget Design (`gadget.py`)

Design details for the gadget go here.
Multiple lines of prose.

### Subsection detail

More nested detail.

## 2. Gadget Testing

Testing approach details.

## 3. Ship Design

Shipping design details.
"""

SPEC_AMBIGUOUS = """# Design Doc

## 1. Gadget Design (`gadget.py`)

Design details.

## 2. Gadget Design Review

Review details.
"""


class ExtractBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.plan_no_spec = self._write("plan_no_spec.md", PLAN_NO_SPEC)
        self.plan_with_spec = self._write("plan_with_spec.md", PLAN_WITH_SPEC)
        self.spec_clean = self._write("spec_clean.md", SPEC_CLEAN)
        self.spec_ambiguous = self._write("spec_ambiguous.md", SPEC_AMBIGUOUS)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _run(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = extract_brief.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_extracts_header_and_task_block_without_spec(self):
        code, out, err = self._run([self.plan_no_spec, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        brief_path = out.strip()
        self.assertEqual(os.path.basename(brief_path), "task-1-brief.md")
        with open(brief_path) as f:
            content = f.read()
        self.assertIn("**Goal:** Ship a widget.", content)
        self.assertIn("Constraint one.", content)
        self.assertIn("Constraint two.", content)
        self.assertIn("### Task 1: Build the widget", content)
        self.assertIn("`widget.py`", content)
        self.assertNotIn("### Task 2", content)
        self.assertNotIn("`ship.py`", content)

    def test_extracts_declared_spec_sections_in_order(self):
        code, out, err = self._run(
            [self.plan_with_spec, "1", "--spec", self.spec_clean, "--out", self.tmpdir.name]
        )
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        design_idx = content.index("Design details for the gadget")
        testing_idx = content.index("Testing approach details")
        self.assertLess(design_idx, testing_idx)

    def test_heading_match_case_insensitive_unique_prefix(self):
        sections = extract_brief.find_spec_sections(
            extract_brief.read_lines(self.spec_clean), ["gadget test"]
        )
        self.assertEqual(len(sections), 1)
        self.assertIn("Testing approach details.", sections[0][1])

    def test_ambiguous_prefix_exits_nonzero(self):
        with self.assertRaises(RuntimeError):
            extract_brief.find_spec_sections(
                extract_brief.read_lines(self.spec_ambiguous), ["Gadget Design"]
            )
        plan_ambig = self._write(
            "plan_ambig.md",
            PLAN_WITH_SPEC.replace("Gadget Design, Gadget Testing", "Gadget Design"),
        )
        code, out, err = self._run(
            [plan_ambig, "1", "--spec", self.spec_ambiguous, "--out", self.tmpdir.name]
        )
        self.assertNotEqual(code, 0)
        self.assertTrue(err.strip())

    def test_unmatched_section_exits_nonzero(self):
        plan_unmatched = self._write(
            "plan_unmatched.md",
            PLAN_WITH_SPEC.replace("Gadget Design, Gadget Testing", "Nonexistent Section"),
        )
        code, out, err = self._run(
            [plan_unmatched, "1", "--spec", self.spec_clean, "--out", self.tmpdir.name]
        )
        self.assertNotEqual(code, 0)
        self.assertTrue(err.strip())

    def test_unknown_task_number_exits_nonzero(self):
        code, out, err = self._run([self.plan_no_spec, "99", "--out", self.tmpdir.name])
        self.assertNotEqual(code, 0)
        self.assertTrue(err.strip())

    def test_wrong_level_task_heading_fails_loud_with_guidance(self):
        # '## Task N:' (two #) is not the convention — it must fail at brief
        # generation with a message that names the real cause, not a generic
        # "not found" that sends the reader hunting.
        plan = self._write(
            "plan_wrong_level.md",
            "**Goal:** Ship a widget.\n\n## Task 1: Build the widget\n- [ ] Done\n",
        )
        code, _, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertNotEqual(code, 0)
        self.assertIn("### Task 1:", err)
        self.assertIn("## Task 1:", err)
        self.assertNotIn("not found", err)

    # --- **Spec:**/**Goal:** must be single, well-formed lines (issue #9) ---

    _TASK = "### Task 1: Do it\n- [ ] Done\n"

    def _fail(self, name, body, argv_extra=None):
        plan = self._write(name, body)
        code, _, err = self._run([plan, "1", "--out", self.tmpdir.name] + (argv_extra or []))
        self.assertNotEqual(code, 0, "expected nonzero exit; got clean brief")
        self.assertTrue(err.strip())
        return err

    def test_wrapped_spec_line_fails_loud(self):
        # Second-line heading name would be silently dropped by first-line parsing.
        err = self._fail(
            "wrapped_spec.md",
            "**Goal:** Ship it.\n\n" + self._TASK + "\n**Spec:** Alpha, Beta,\nGamma\n",
        )
        self.assertIn("single line", err)
        self.assertIn("Gamma", err)

    def test_parenthetical_spec_fails_loud(self):
        err = self._fail(
            "paren_spec.md",
            "**Goal:** Ship it.\n\n" + self._TASK + "\n**Spec:** Alpha (the repo, part)\n",
        )
        self.assertIn("parenthetical", err.lower())

    def test_semicolon_spec_fails_loud(self):
        err = self._fail(
            "semi_spec.md",
            "**Goal:** Ship it.\n\n" + self._TASK + "\n**Spec:** Alpha; Beta\n",
        )
        self.assertIn(";", err)

    def test_wrapped_goal_line_fails_loud(self):
        err = self._fail(
            "wrapped_goal.md",
            "**Goal:** Ship it, and also\nhandle the edge cases.\n\n" + self._TASK,
        )
        self.assertIn("single line", err)

    def test_missing_goal_fails_loud(self):
        err = self._fail("no_goal.md", self._TASK)
        self.assertIn("Goal", err)

    # --- parsers must be fence- and bold-prose-aware (issue #12) ---

    def test_fenced_heading_does_not_terminate_task_block(self):
        # A fenced markdown example containing '## ...' must not end the task
        # block — truncating there emits a silently thin brief cut mid-fence.
        plan = self._write(
            "fenced_heading.md",
            "**Goal:** Ship it.\n\n"
            "### Task 1: Do it\n"
            "Steps:\n\n"
            "```markdown\n"
            "## Example section the worker should produce\n"
            "```\n\n"
            "- Acceptance: tests pass\n\n"
            "### Task 2: Other\nbody\n",
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("## Example section", content)
        self.assertIn("Acceptance: tests pass", content)
        self.assertNotIn("### Task 2", content)

    def test_fenced_goal_line_is_ignored(self):
        # A '**Goal:**' inside a fenced template example is content, not the
        # header field — the real Goal after it must win.
        plan = self._write(
            "fenced_goal.md",
            "Template example:\n\n"
            "```markdown\n"
            "**Goal:** EXAMPLE GOAL FROM TEMPLATE\n"
            "```\n\n"
            "**Goal:** The real goal.\n\n" + self._TASK,
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("**Goal:** The real goal.", content)
        self.assertNotIn("EXAMPLE GOAL", content)

    def test_fenced_spec_line_inside_task_is_ignored(self):
        plan = self._write(
            "fenced_spec.md",
            "**Goal:** Ship it.\n\n"
            "### Task 1: Do it\n"
            "```markdown\n"
            "**Spec:** Example Section\n"
            "```\n",
        )
        # No real **Spec:** declared, so no --spec needed and no spec error.
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)

    def test_wrapped_goal_starting_with_bold_fails_loud(self):
        # Bold prose is not a new '**Field:**' — a wrapped continuation that
        # happens to start with '**' must raise, not silently truncate.
        err = self._fail(
            "wrapped_goal_bold.md",
            "**Goal:** Do the thing\n**quickly** and correctly.\n\n" + self._TASK,
        )
        self.assertIn("single line", err)
        self.assertIn("quickly", err)

    def test_gc_block_keeps_bold_prose_line(self):
        # A constraints line beginning with bold prose belongs to the block;
        # only a real '**Field:**' line or heading ends it.
        plan = self._write(
            "gc_bold.md",
            "**Goal:** Ship it.\n\n"
            "**Global Constraints:**\n"
            "- constraint one\n"
            "**bold start** of constraint two\n"
            "- constraint three\n\n" + self._TASK,
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("constraint two", content)
        self.assertIn("constraint three", content)

    def test_gc_block_fenced_content_does_not_terminate(self):
        plan = self._write(
            "gc_fence.md",
            "**Goal:** Ship it.\n\n"
            "**Global Constraints:**\n"
            "- constraint one\n"
            "```\n"
            "## fenced example\n"
            "**Fenced:** field-looking line\n"
            "```\n"
            "- constraint two\n\n" + self._TASK,
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("constraint two", content)

    def test_fenced_spec_heading_not_matched_as_section(self):
        spec = self._write(
            "spec_fenced.md",
            "# Design Doc\n\n"
            "## Real Section\n\n"
            "Real details.\n\n"
            "```markdown\n"
            "## Real Section\n"
            "```\n",
        )
        # Without fence awareness the fenced duplicate makes this ambiguous.
        sections = extract_brief.find_spec_sections(
            extract_brief.read_lines(spec), ["Real Section"]
        )
        self.assertEqual(len(sections), 1)
        self.assertIn("Real details.", sections[0][1])

    # --- header scope, h1 terminator, duplicate task numbers (issue #13) ---

    def test_gc_line_inside_task_does_not_override_header(self):
        # Header fields live before the first task heading; a
        # '**Global Constraints:**' line inside a task block is task content,
        # never a header override.
        plan = self._write(
            "gc_in_task.md",
            "**Goal:** Ship it.\n\n"
            "**Global Constraints:**\n"
            "- real constraint\n\n"
            "### Task 1: Do it\n"
            "**Global Constraints:** bogus per-task line\n"
            "body\n",
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        header = content.split("# Task 1")[0]
        self.assertIn("real constraint", header)
        self.assertNotIn("bogus per-task line", header)

    def test_goal_inside_task_is_not_the_header_goal(self):
        # No header Goal + a '**Goal:**' line inside a task must fail loud,
        # not silently adopt the task's line as the plan goal.
        err = self._fail(
            "goal_in_task.md",
            "### Task 1: Do it\n**Goal:** task-local goal\nbody\n",
        )
        self.assertIn("missing", err)

    def test_duplicate_global_constraints_in_header_fails_loud(self):
        err = self._fail(
            "dup_gc.md",
            "**Goal:** Ship it.\n\n"
            "**Global Constraints:**\n- one\n\n"
            "**Global Constraints:**\n- two\n\n" + self._TASK,
        )
        self.assertIn("Global Constraints", err)

    def test_h1_heading_terminates_task_block(self):
        # '# Appendix' after the last task must end the block — otherwise the
        # brief silently swells with everything through EOF.
        plan = self._write(
            "h1_after_task.md",
            "**Goal:** Ship it.\n\n"
            "### Task 1: Do it\n"
            "task body\n\n"
            "# Appendix: unrelated dump\n"
            "appendix line\n",
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("task body", content)
        self.assertNotIn("appendix line", content)

    def test_h4_heading_does_not_terminate_task_block(self):
        plan = self._write(
            "h4_in_task.md",
            "**Goal:** Ship it.\n\n"
            "### Task 1: Do it\n"
            "task body\n\n"
            "#### Sub-detail\n"
            "sub-detail line\n",
        )
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("sub-detail line", content)

    def test_duplicate_task_number_fails_loud(self):
        err = self._fail(
            "dup_task.md",
            "**Goal:** Ship it.\n\n"
            "### Task 1: First version\nold body\n\n"
            "### Task 1: Second version\nnew body\n",
        )
        self.assertIn("Task 1", err)
        self.assertNotIn("not found", err)

    def test_spec_declared_but_no_spec_flag_exits_nonzero(self):
        code, out, err = self._run([self.plan_with_spec, "1", "--out", self.tmpdir.name])
        self.assertNotEqual(code, 0)
        self.assertTrue(err.strip())

    def test_out_dir_honored_and_default_out_dir_writable(self):
        custom_out = os.path.join(self.tmpdir.name, "custom-out")
        os.makedirs(custom_out)
        code, out, err = self._run([self.plan_no_spec, "1", "--out", custom_out])
        self.assertEqual(code, 0, err)
        brief_path = out.strip()
        self.assertEqual(os.path.dirname(brief_path), custom_out.rstrip("/"))
        self.assertTrue(os.path.isabs(brief_path))

        code2, out2, err2 = self._run([self.plan_no_spec, "1"])
        self.assertEqual(code2, 0, err2)
        default_path = out2.strip()
        self.assertTrue(os.path.isabs(default_path))
        with open(default_path) as f:
            self.assertTrue(f.read())

    # --- parse_test_cases (Task 1: Tests-line parser) ---
    # Legal form: the '**Tests:**' marker alone on its line, followed by
    # '-' bullets; the block ends at the first blank line or the next
    # '**Field:**' marker. The inline joined form is no longer legal.

    def test_parse_test_cases_multi_case_in_order(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- rejects empty email\n"
            "- retries 3 times then throws\n"
            "- logs a warning on retry\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block),
            [
                "rejects empty email",
                "retries 3 times then throws",
                "logs a warning on retry",
            ],
        )

    def test_parse_test_cases_stops_at_first_blank_line(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- case one\n"
            "- case two\n"
            "\n"
            "- not a test case, trailing prose after the blank line\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block), ["case one", "case two"]
        )

    def test_parse_test_cases_stops_at_next_field_marker(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- case one\n"
            "- case two\n"
            "**Acceptance:** `pytest` passes.\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block), ["case one", "case two"]
        )

    def test_parse_test_cases_absent_returns_empty_list(self):
        self.assertEqual(extract_brief.parse_test_cases(self._TASK), [])

    def test_parse_test_cases_none_with_dash_prose_returns_empty_list(self):
        block = self._TASK + "\n**Tests:** none — prose.\n"
        self.assertEqual(extract_brief.parse_test_cases(block), [])

    def test_parse_test_cases_none_with_any_trailing_reason_returns_empty_list(self):
        block = self._TASK + "\n**Tests:** none because it's a doc-only change\n"
        self.assertEqual(extract_brief.parse_test_cases(block), [])

    def test_parse_test_cases_ignores_fenced_tests_marker(self):
        block = (
            self._TASK + "\n"
            "```markdown\n"
            "**Tests:**\n"
            "- fenced case that must not count\n"
            "```\n"
        )
        self.assertEqual(extract_brief.parse_test_cases(block), [])

    def test_parse_test_cases_inline_joined_form_raises_naming_line(self):
        block = self._TASK + "\n**Tests:** a; b; c\n"
        with self.assertRaises(RuntimeError) as ctx:
            extract_brief.parse_test_cases(block)
        self.assertIn("a; b; c", str(ctx.exception))

    def test_parse_test_cases_marker_with_no_bullets_and_no_none_raises(self):
        block = self._TASK + "\n**Tests:**\nnot a bullet, not none\n"
        with self.assertRaises(RuntimeError) as ctx:
            extract_brief.parse_test_cases(block)
        self.assertIn("Tests", str(ctx.exception))

    def test_parse_test_cases_semicolon_inside_bullet_is_literal(self):
        block = self._TASK + "\n**Tests:**\n- rejects a; keeps b\n"
        self.assertEqual(extract_brief.parse_test_cases(block), ["rejects a; keeps b"])

    # --- heading terminates a clause block (#87) ---

    def test_parse_test_cases_heading_with_no_blank_line_yields_only_its_bullets(self):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
            "- rejects empty email\n"
            "- retries 3 times\n"
            "### Task 2: Next thing\n- [ ] Done\n"
        )
        self.assertEqual(
            extract_brief.parse_field_clauses(block, "Tests"),
            ["rejects empty email", "retries 3 times"],
        )

    def test_heading_text_not_merged_into_final_clause(self):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
            "- retries 3 times\n"
            "### Task 2: Next thing\n- [ ] Done\n"
        )
        clauses = extract_brief.parse_field_clauses(block, "Tests")
        self.assertNotIn("Task 2", " ".join(clauses))

    def test_checkbox_line_after_heading_is_not_a_phantom_clause(self):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
            "- retries 3 times\n"
            "### Task 2: Next thing\n- [ ] Done\n"
        )
        clauses = extract_brief.parse_field_clauses(block, "Tests")
        self.assertNotIn("[ ] Done", clauses)

    def test_bulleted_block_then_blank_line_then_heading_is_unchanged(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- case one\n"
            "- case two\n"
            "\n"
            "### Task 2: Next thing\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block), ["case one", "case two"]
        )

    def test_bulleted_block_then_next_field_marker_is_unchanged(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- case one\n"
            "- case two\n"
            "**Acceptance:** `pytest` passes.\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block), ["case one", "case two"]
        )

    def test_hash_inside_inline_code_span_does_not_terminate_block(self):
        block = (
            self._TASK + "\n**Tests:**\n"
            "- mentions the `#42` ticket reference in its message\n"
            "- rejects other input\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block),
            ["mentions the `#42` ticket reference in its message", "rejects other input"],
        )

    def test_clause_block_ends_at_h4_heading(self):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
            "- rejects empty email\n"
            "#### Sub-detail\n"
            "sub-detail line\n"
        )
        self.assertEqual(
            extract_brief.parse_field_clauses(block, "Tests"),
            ["rejects empty email"],
        )

    def test_clause_block_ends_at_h5_and_h6_headings(self):
        for marks in ("#####", "######"):
            block = (
                "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
                "- rejects empty email\n"
                "{} Sub-detail\n"
                "sub-detail line\n"
            ).format(marks)
            self.assertEqual(
                extract_brief.parse_field_clauses(block, "Tests"),
                ["rejects empty email"],
                "failed to terminate at {} heading".format(marks),
            )

    def test_heading_text_at_any_level_never_merged_into_clause(self):
        for marks in ("####", "#####", "######"):
            block = (
                "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
                "- retries 3 times\n"
                "{} Next thing\n"
            ).format(marks)
            clauses = extract_brief.parse_field_clauses(block, "Tests")
            self.assertNotIn("Next thing", " ".join(clauses))

    def test_parse_test_cases_matches_pre_regression_parser_for_heading_adjacent_block(
        self,
    ):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Tests:**\n"
            "- rejects empty email\n"
            "- retries 3 times\n"
            "### Task 2: Next thing\n- [ ] Done\n"
        )
        self.assertEqual(
            extract_brief.parse_test_cases(block),
            ["rejects empty email", "retries 3 times"],
        )

    def test_last_task_in_file_eof_terminated_extracts_fully(self):
        code, out, err = self._run([self.plan_no_spec, "2", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("### Task 2: Ship the widget", content)
        self.assertIn("`ship.py`", content)

    # --- parse_field_clause_lines (acceptance outcomes, Task 1) ---

    def test_clause_lines_single_line_form_gives_marker_line_index(self):
        block = "### Task 1: Thing\n- [ ] Done\n\n**Acceptance:** `make test` passes\n\n**Tier:** standard\n"
        self.assertEqual(
            extract_brief.parse_field_clause_lines(block, "Acceptance"),
            [(3, "`make test` passes")],
        )

    def test_clause_lines_bulleted_form_gives_each_first_line_index(self):
        block = (
            "### Task 1: Thing\n- [ ] Done\n\n**Acceptance:**\n"
            "- `a` passes\n"
            "- prose that wraps\n"
            "  onto a second line\n"
            "- `b` exits 1\n"
        )
        self.assertEqual(
            extract_brief.parse_field_clause_lines(block, "Acceptance"),
            [
                (4, "`a` passes"),
                (5, "prose that wraps onto a second line"),
                (7, "`b` exits 1"),
            ],
        )
        self.assertEqual(
            extract_brief.parse_field_clauses(block, "Acceptance"),
            [
                "`a` passes",
                "prose that wraps onto a second line",
                "`b` exits 1",
            ],
        )

    def test_clause_lines_absent_field_is_empty_and_bare_marker_raises(self):
        self.assertEqual(
            extract_brief.parse_field_clause_lines("### Task 1: T\n", "Acceptance"), []
        )
        with self.assertRaises(RuntimeError):
            extract_brief.parse_field_clause_lines(
                "### Task 1: T\n**Acceptance:**\nnot a bullet\n", "Acceptance"
            )


HEADER_PLAN_TWO = """# Two Spec Plan

**Goal:** Cover two specs.
**Spec files:**
- `specs/alpha.md`
- specs/beta.md
**Global Constraints:** One constraint.

### Task 1: Both
- [ ] Done

**Files:**
- Create: `x.py`

**Spec:** [alpha] Alpha Design, [beta] Beta Design
"""

HEADER_PLAN_ONE = """# One Spec Plan

**Goal:** Cover one spec.
**Spec files:** specs/alpha.md

### Task 1: One
- [ ] Done

**Spec:** [alpha] Alpha Design, Alpha Other
"""

SPEC_ALPHA = """---
system: alpha
---
# Alpha

## 1. Alpha Design

Alpha design text.

## Alpha Other

Other text.
"""

SPEC_BETA = """---
system: beta
---
# Beta

## Beta Design

Beta design text.
"""


class SpecSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "specs"))
        os.makedirs(os.path.join(self.root, ".git"))
        self.alpha = self._write("specs/alpha.md", SPEC_ALPHA)
        self.beta = self._write("specs/beta.md", SPEC_BETA)

    def _write(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = extract_brief.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _plan_lines(self, text):
        return text.splitlines(keepends=True)

    # --- parse_spec_files -------------------------------------------------

    def test_bulleted_spec_files_in_order_and_backticks_removed(self):
        self.assertEqual(
            extract_brief.parse_spec_files(self._plan_lines(HEADER_PLAN_TWO)),
            ["specs/alpha.md", "specs/beta.md"],
        )

    def test_single_line_spec_files_yields_one_path(self):
        self.assertEqual(
            extract_brief.parse_spec_files(self._plan_lines(HEADER_PLAN_ONE)),
            ["specs/alpha.md"],
        )

    def test_backticked_path_equals_plain_path(self):
        a = "**Goal:** g\n**Spec files:** `specs/a.md`\n"
        b = "**Goal:** g\n**Spec files:** specs/a.md\n"
        self.assertEqual(
            extract_brief.parse_spec_files(self._plan_lines(a)),
            extract_brief.parse_spec_files(self._plan_lines(b)),
        )

    def test_spec_files_block_ends_at_blank_field_or_heading(self):
        for ender in ("\n", "**Global Constraints:** c\n", "### Task 1: T\n"):
            text = "**Goal:** g\n**Spec files:**\n- a.md\n" + ender + "- not-a-path.md\n"
            self.assertEqual(
                extract_brief.parse_spec_files(self._plan_lines(text)),
                ["a.md"],
                ender,
            )

    def test_spec_files_continuation_line_joins_preceding_bullet(self):
        text = "**Goal:** g\n**Spec files:**\n- dir/a.md\n  more\n- b.md\n"
        self.assertEqual(
            extract_brief.parse_spec_files(self._plan_lines(text)),
            ["dir/a.md more", "b.md"],
        )

    def test_no_spec_files_header_yields_nothing(self):
        self.assertEqual(
            extract_brief.parse_spec_files(self._plan_lines(PLAN_NO_SPEC)), []
        )

    # --- load_spec_set ----------------------------------------------------

    def test_legacy_plan_spec_set_is_legacy_path_or_empty(self):
        plan = self._write("legacy.md", PLAN_NO_SPEC)
        self.assertEqual(extract_brief.load_spec_set(plan, None, self.root), [])
        got = extract_brief.load_spec_set(plan, self.alpha, self.root)
        self.assertEqual([f.path for f in got], [self.alpha])

    def test_legacy_spec_file_is_not_opened_when_no_task_names_a_section(self):
        plan = self._write("legacy.md", PLAN_NO_SPEC)
        missing = os.path.join(self.root, "specs", "missing.md")
        unterminated = self._write("unterminated.md", "---\nsystem: x\n# no close\n")
        for spec in (missing, unterminated):
            brief = extract_brief.build_brief(plan, 1, spec)
            self.assertIn("**Goal:** Ship a widget.", brief)
            code, _out, err = self._run([plan, "1", "--spec", spec, "--out", self.root])
            self.assertEqual(code, 0, err)

    def test_legacy_plan_naming_a_section_raises_when_spec_file_missing(self):
        plan = self._write("legacy.md", PLAN_WITH_SPEC)
        missing = os.path.join(self.root, "specs", "missing.md")
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.build_brief(plan, 1, missing)
        self.assertIn("missing.md", str(cm.exception))

    def test_declared_paths_resolve_against_repo_root_with_frontmatter_ids(self):
        plan = self._write("plan.md", HEADER_PLAN_TWO)
        cwd = os.getcwd()
        os.chdir(tempfile.gettempdir())
        self.addCleanup(os.chdir, cwd)
        got = extract_brief.load_spec_set(plan, None, self.root)
        self.assertEqual([f.path for f in got], [self.alpha, self.beta])
        self.assertEqual([f.spec_id for f in got], ["alpha", "beta"])

    def test_header_with_legacy_path_raises_naming_both(self):
        plan = self._write("plan.md", HEADER_PLAN_TWO)
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.load_spec_set(plan, self.alpha, self.root)
        self.assertIn("**Spec files:**", str(cm.exception))
        self.assertIn("--spec", str(cm.exception))

    def test_declared_path_naming_no_file_raises_naming_path(self):
        plan = self._write(
            "plan.md", "**Goal:** g\n**Spec files:** specs/missing.md\n"
        )
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.load_spec_set(plan, None, self.root)
        self.assertIn("specs/missing.md", str(cm.exception))

    def test_two_files_with_one_id_raise_naming_both(self):
        self._write("specs/dup.md", SPEC_ALPHA)
        plan = self._write(
            "plan.md",
            "**Goal:** g\n**Spec files:**\n- specs/alpha.md\n- specs/dup.md\n",
        )
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.load_spec_set(plan, None, self.root)
        self.assertIn("specs/alpha.md", str(cm.exception))
        self.assertIn("specs/dup.md", str(cm.exception))

    # --- parse_spec_entries -----------------------------------------------

    def test_entry_parsing(self):
        block = "### Task 1: T\n\n**Spec:** [execution] Plan lint, Bare Name\n"
        self.assertEqual(
            extract_brief.parse_spec_entries(block),
            [("execution", "Plan lint"), (None, "Bare Name")],
        )

    def test_entry_splits_at_first_bracket(self):
        block = "### Task 1: T\n\n**Spec:** [a] Name [x] tail\n"
        self.assertEqual(
            extract_brief.parse_spec_entries(block), [("a", "Name [x] tail")]
        )

    def test_whitespace_inside_brackets_raises_naming_entry(self):
        block = "### Task 1: T\n\n**Spec:** [ a ] Name\n"
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.parse_spec_entries(block)
        self.assertIn("[ a ] Name", str(cm.exception))

    # --- resolve_entries --------------------------------------------------

    def _two(self):
        plan = self._write("plan.md", HEADER_PLAN_TWO)
        return extract_brief.load_spec_set(plan, None, self.root)

    def _one(self):
        plan = self._write("plan.md", HEADER_PLAN_ONE)
        return extract_brief.load_spec_set(plan, None, self.root)

    def _legacy(self):
        plan = self._write("legacy.md", PLAN_NO_SPEC)
        return extract_brief.load_spec_set(plan, self.alpha, self.root)

    def test_case_differing_id_is_error_listing_declared_ids(self):
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.resolve_entries([("Alpha", "Alpha Design")], self._two(), 3)
        msg = str(cm.exception)
        self.assertIn("alpha", msg)
        self.assertIn("beta", msg)
        self.assertIn("task 3", msg)

    def test_bare_entry_in_two_spec_plan_lists_declared_ids(self):
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.resolve_entries([(None, "Alpha Design")], self._two(), 1)
        self.assertIn("alpha", str(cm.exception))
        self.assertIn("beta", str(cm.exception))

    def test_bracketed_entry_in_one_spec_plan(self):
        got = extract_brief.resolve_entries([("alpha", "Alpha Design")], self._one(), 1)
        self.assertEqual(got[0].heading, "1. Alpha Design")
        with self.assertRaises(RuntimeError):
            extract_brief.resolve_entries([("beta", "Alpha Design")], self._one(), 1)

    def test_bracketed_entry_in_legacy_plan_is_error_even_when_id_matches(self):
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.resolve_entries([("alpha", "Alpha Design")], self._legacy(), 2)
        self.assertIn("task 2", str(cm.exception))
        self.assertIn("[alpha] Alpha Design", str(cm.exception))

    def test_section_in_second_spec_is_taken_from_that_file(self):
        got = extract_brief.resolve_entries([("beta", "Beta Design")], self._two(), 1)
        self.assertEqual(got[0].spec.path, self.beta)
        self.assertIn("Beta design text.", "".join(got[0].lines))

    def test_unmatched_entry_names_task_and_entry(self):
        with self.assertRaises(RuntimeError) as cm:
            extract_brief.resolve_entries([("beta", "Nope")], self._two(), 4)
        self.assertIn("task 4", str(cm.exception))
        self.assertIn("[beta] Nope", str(cm.exception))

    # --- match_heading_names ----------------------------------------------

    def _spec_names(self, text, names):
        lines = text.splitlines(keepends=True)
        return [h for h, _ in extract_brief.find_spec_sections(lines, names)]

    def test_exact_name_beats_longer_prefix_heading(self):
        spec = "## Plan\n\na\n\n## Plan review\n\nb\n"
        self.assertEqual(self._spec_names(spec, ["plan"]), ["Plan"])

    def test_unique_prefix_resolves(self):
        spec = "## Plan review\n\nb\n\n## Other\n"
        self.assertEqual(self._spec_names(spec, ["Plan"]), ["Plan review"])

    def test_prefix_of_two_headings_is_error_naming_both(self):
        spec = "## Plan one\n\n## Plan two\n"
        with self.assertRaises(RuntimeError) as cm:
            self._spec_names(spec, ["Plan"])
        self.assertIn("Plan one", str(cm.exception))
        self.assertIn("Plan two", str(cm.exception))

    def test_identical_headings_make_exact_name_ambiguous(self):
        spec = "## Same\n\na\n\n## Same\n\nb\n"
        with self.assertRaises(RuntimeError) as cm:
            self._spec_names(spec, ["Same"])
        self.assertIn("ambiguous", str(cm.exception))

    def test_comparison_ignores_case_collapses_whitespace_strips_heading_numbering(self):
        spec = "## 2.3  Plan   Review\n\nb\n"
        self.assertEqual(
            self._spec_names(spec, ["plan  REVIEW"]), ["2.3 Plan Review"]
        )

    def test_name_beginning_with_digit_is_not_stripped(self):
        spec = "## 3 3 Plan\n\na\n\n## Plan\n\nb\n"
        self.assertEqual(self._spec_names(spec, ["3 Plan"]), ["3 3 Plan"])
        with self.assertRaises(RuntimeError):
            self._spec_names("## 3 Plan\n\na\n", ["3 Plan"])

    # --- build_brief and CLI ----------------------------------------------

    def test_two_spec_brief_labels_each_section(self):
        plan = self._write("plan.md", HEADER_PLAN_TWO)
        brief = extract_brief.build_brief(plan, 1)
        self.assertIn("# Spec: [alpha] 1. Alpha Design", brief)
        self.assertIn("# Spec: [beta] Beta Design", brief)

    def test_one_spec_and_legacy_briefs_are_unlabeled(self):
        plan = self._write("plan.md", HEADER_PLAN_ONE)
        brief = extract_brief.build_brief(plan, 1)
        self.assertIn("# Spec: 1. Alpha Design", brief)
        self.assertNotIn("[alpha]", brief.split("# Spec:", 1)[1])
        legacy = self._write("legacy.md", PLAN_WITH_SPEC.replace(
            "Gadget Design, Gadget Testing", "Alpha Design"))
        brief = extract_brief.build_brief(legacy, 1, self.alpha)
        self.assertIn("# Spec: 1. Alpha Design", brief)

    def test_cli_header_plan_without_spec_flag_has_both_specs(self):
        plan = self._write("plan.md", HEADER_PLAN_TWO)
        code, out, err = self._run([plan, "1", "--out", self.root])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("Alpha design text.", content)
        self.assertIn("Beta design text.", content)

    def test_cli_spec_line_in_plan_with_no_spec_exits_nonzero_naming_task(self):
        plan = self._write("plan.md", PLAN_WITH_SPEC)
        code, _out, err = self._run([plan, "2", "--out", self.root])
        self.assertNotEqual(code, 0)
        self.assertIn("task 2", err)


if __name__ == "__main__":
    unittest.main()
