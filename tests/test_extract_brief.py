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
        code, out, err = self._run([plan, "1", "--out", self.tmpdir.name])
        self.assertNotEqual(code, 0)
        self.assertIn("### Task 1:", err)
        self.assertIn("## Task 1:", err)
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

    def test_last_task_in_file_eof_terminated_extracts_fully(self):
        code, out, err = self._run([self.plan_no_spec, "2", "--out", self.tmpdir.name])
        self.assertEqual(code, 0, err)
        with open(out.strip()) as f:
            content = f.read()
        self.assertIn("### Task 2: Ship the widget", content)
        self.assertIn("`ship.py`", content)


if __name__ == "__main__":
    unittest.main()
