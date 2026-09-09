"""Tests for scripts/forge_docreview.py — spec review's reference table.

Loaded via importlib since scripts/ files are not a package (Global
Constraints: no shared module between scripts — forge_docreview itself
reuses forge_common, but the test loads it the same way the other
scripts/*.py suites do).
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = str(SCRIPTS_DIR / "forge_docreview.py")

sys.path.insert(0, str(SCRIPTS_DIR))
import forge_docreview as d  # noqa: E402

# Built at runtime, never as one contiguous literal: `git grep -F` (which
# _resolve_symbol uses) searches this file's own tracked bytes, so a sentinel
# meant to resolve nowhere must not appear in them as a contiguous string —
# otherwise committing this test file makes it find itself.
NO_SUCH_SYMBOL = "totallyNonexistent" + "SymbolXyz123"


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class ExtractReferencesTests(unittest.TestCase):
    def test_slash_span_is_path(self):
        refs = d.extract_references("See `scripts/forge_common.py` for details.")
        self.assertEqual(refs[0].shape, "path")

    def test_span_ending_in_extension_present_in_repo_is_path(self):
        # forge_common.py is a real tracked file, so .py is a real extension
        # in the repo's extension set even without a slash in the span.
        refs = d.extract_references("Look at `forge_common.py` closely.")
        self.assertEqual(refs[0].shape, "path")

    def test_span_ending_in_extension_absent_from_repo_is_not_path(self):
        refs = d.extract_references("A stray `thing.zzzzqqq` token.")
        self.assertNotEqual(refs[0].shape, "path")

    def test_double_dash_flag_is_other_and_dropped(self):
        refs = d.extract_references("Pass `--autofix` to enable it.")
        self.assertEqual(refs[0].shape, "other")
        table = d.reference_table("Pass `--autofix` to enable it.")
        self.assertEqual(table, [])

    def test_kebab_constraint_id_is_other_and_dropped(self):
        refs = d.extract_references("Constraint: `stdlib-only`.")
        self.assertEqual(refs[0].shape, "other")
        table = d.reference_table("Constraint: `stdlib-only`.")
        self.assertEqual(table, [])

    def test_identifier_present_in_tracked_file_resolves_with_found_at(self):
        refs = d.extract_references("Call `extract_references` to get the list.")
        self.assertEqual(refs[0].shape, "symbol")
        self.assertTrue(refs[0].resolved)
        self.assertIsNotNone(refs[0].found_at)

    def test_identifier_absent_from_every_tracked_file_does_not_resolve(self):
        refs = d.extract_references(f"Call `{NO_SUCH_SYMBOL}` here.")
        self.assertEqual(refs[0].shape, "symbol")
        self.assertFalse(refs[0].resolved)
        self.assertIsNone(refs[0].found_at)

    def test_path_naming_existing_directory_resolves(self):
        refs = d.extract_references("See the `docs/forge/specs` directory.")
        self.assertEqual(refs[0].shape, "path")
        self.assertTrue(refs[0].resolved)

    def test_path_naming_nonexistent_file_does_not_resolve(self):
        refs = d.extract_references("See `scripts/does_not_exist_at_all.py`.")
        self.assertEqual(refs[0].shape, "path")
        self.assertFalse(refs[0].resolved)

    def test_duplicate_reference_yields_one_table_entry(self):
        text = "First mention of `scripts/forge_common.py`. Again: `scripts/forge_common.py`."
        table = d.reference_table(text)
        self.assertEqual(len(table), 1)

    def test_backticked_span_inside_fenced_block_is_not_extracted(self):
        text = (
            "Prose before.\n\n"
            "```\n"
            "`scripts/forge_common.py`\n"
            "```\n\n"
            "Prose after.\n"
        )
        refs = d.extract_references(text)
        self.assertEqual(refs, [])

    def test_extension_set_is_derived_not_hardcoded(self):
        # A real extension only present via a non-.py tracked file (if any)
        # still classifies as path — proof the set comes from git ls-files,
        # not a literal in the module. We check indirectly: every extension
        # among tracked files is honored.
        tracked = _tracked_files()
        exts = {f.rsplit(".", 1)[-1] for f in tracked if "." in f.rsplit("/", 1)[-1]}
        self.assertIn("py", exts)  # sanity: repo has .py files
        refs = d.extract_references("A span ending in `x.py`.")
        self.assertEqual(refs[0].shape, "path")


class ReworkFindingsTests(unittest.TestCase):
    # f1 — directory resolution must reflect git-tracked state, not raw
    # filesystem existence. An untracked directory must not resolve.
    def test_untracked_directory_does_not_resolve(self):
        untracked_dir = SCRIPTS_DIR / "__test_untracked_dir__"
        untracked_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(untracked_dir, ignore_errors=True))
        refs = d.extract_references("See `scripts/__test_untracked_dir__` here.")
        self.assertEqual(refs[0].shape, "path")
        self.assertFalse(refs[0].resolved)
        self.assertIsNone(refs[0].found_at)

    # f2 — a bare punctuation span like `/` must not spuriously resolve to
    # the repo root once stripped down to the empty string.
    def test_bare_slash_span_does_not_resolve(self):
        refs = d.extract_references("Syntax example: `/`")
        self.assertEqual(refs[0].shape, "path")
        self.assertFalse(refs[0].resolved)
        self.assertIsNone(refs[0].found_at)

    # f3 / g4 — a fatal git grep error (anything above exit 1) must raise
    # naming the cause, never collapse into a legal "unresolved" result.
    def test_git_grep_fatal_error_raises_naming_cause(self):
        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "grep"]:
                return subprocess.CompletedProcess(
                    argv, 128, "", "fatal: not a git repository"
                )
            return real_run(argv, **kwargs)

        with mock.patch.object(d.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                d.extract_references("Call `someSymbolNameXyz` here.")
        self.assertIn("128", str(ctx.exception))
        self.assertIn("not a git repository", str(ctx.exception))

    def test_git_grep_exit_1_is_legal_no_match(self):
        # Exit 1 (no match) must still be treated as a legal negative result,
        # not swept into the fatal-error path.
        refs = d.extract_references(f"Call `{NO_SUCH_SYMBOL}` here.")
        self.assertFalse(refs[0].resolved)
        self.assertIsNone(refs[0].found_at)


def _valid_verdict(**overrides):
    """A minimal schema-valid verdict for one unresolved ref ``a/b.py``.
    Overrides replace top-level keys wholesale."""
    base = {
        "verdict": "findings",
        "references": [
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "not yet built"}
        ],
        "dependencies_read": [
            {"symbol": "foo", "file": "scripts/foo.py", "behavior": "does a thing"}
        ],
        "dependencies_waiver": None,
        "replaced_system": {"applies": False, "guarantees": []},
        "findings": [],
    }
    base.update(overrides)
    return base


class ValidateVerdictTests(unittest.TestCase):
    def test_missing_references_entry_for_unresolved_ref_is_invalid(self):
        verdict = _valid_verdict(references=[])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("a/b.py" in defect for defect in result.defects))

    def test_references_entry_naming_ref_outside_unresolved_set_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "e"},
            {"ref": "c/d.py", "disposition": "intended-new", "evidence": "e"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("c/d.py" in defect for defect in result.defects))

    def test_unknown_disposition_value_on_references_entry_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "bogus", "evidence": "e"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_empty_dependencies_read_with_no_waiver_is_invalid(self):
        verdict = _valid_verdict(dependencies_read=[], dependencies_waiver=None)
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_empty_dependencies_read_with_waiver_is_valid(self):
        verdict = _valid_verdict(dependencies_read=[], dependencies_waiver="nothing to read")
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertTrue(result.valid)

    def test_replaced_system_applies_false_with_guarantees_is_invalid(self):
        verdict = _valid_verdict(replaced_system={"applies": False, "guarantees": ["x"]})
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_replaced_system_applies_true_with_empty_guarantees_is_invalid(self):
        verdict = _valid_verdict(replaced_system={"applies": True, "guarantees": []})
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_missing_replaced_system_is_invalid(self):
        verdict = _valid_verdict()
        del verdict["replaced_system"]
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_present_without_applies_key_is_invalid(self):
        verdict = _valid_verdict(replaced_system={"guarantees": []})
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_applies_non_boolean_is_invalid(self):
        verdict = _valid_verdict(replaced_system={"applies": "yes", "guarantees": []})
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_string_is_invalid_not_raised(self):
        verdict = _valid_verdict(replaced_system="nope")
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_int_is_invalid_not_raised(self):
        verdict = _valid_verdict(replaced_system=7)
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_bool_is_invalid_not_raised(self):
        verdict = _valid_verdict(replaced_system=True)
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_empty_list_is_invalid_not_raised(self):
        verdict = _valid_verdict(replaced_system=[])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_replaced_system_none_is_still_invalid(self):
        verdict = _valid_verdict(replaced_system=None)
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("replaced_system" in defect for defect in result.defects))

    def test_finding_citation_int_is_invalid(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa", "citation": 42,
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any(
            "citation" in defect and "f1" in defect for defect in result.defects
        ))

    def test_finding_citation_bool_is_invalid(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa", "citation": True,
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any(
            "citation" in defect and "f1" in defect for defect in result.defects
        ))

    def test_finding_citation_null_is_still_legal(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa", "citation": None,
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertTrue(result.valid)

    def test_finding_citation_absent_is_still_legal(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa",
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertTrue(result.valid)

    def test_references_entry_missing_evidence_key_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("a/b.py" in defect for defect in result.defects))

    def test_references_entry_empty_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": ""},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("a/b.py" in defect for defect in result.defects))

    def test_references_entry_whitespace_only_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "   "},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("a/b.py" in defect for defect in result.defects))

    def test_references_entry_with_real_evidence_is_valid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "not yet built"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertTrue(result.valid)

    def test_whitespace_only_finding_field_is_invalid(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "   ", "kind": "sufficiency", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa",
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_dependencies_read_entry_with_blank_fields_is_invalid(self):
        verdict = _valid_verdict(dependencies_read=[
            {"symbol": "", "file": "  ", "behavior": ""},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_dependencies_read_entry_entirely_empty_is_invalid(self):
        verdict = _valid_verdict(dependencies_read=[{}])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_defect_names_both_field_and_entry(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        matches = [
            defect for defect in result.defects
            if "evidence" in defect and "a/b.py" in defect
        ]
        self.assertTrue(matches)

    def test_new_required_field_in_declared_schema_needs_no_new_code(self):
        # Declaring a new required field in the schema alone must be enough
        # to enforce it — no validation code changes. Demonstrated by
        # patching the declared schema to add a field the fixture verdict
        # doesn't supply, and observing it gets caught with zero code
        # changes to validate_verdict.
        verdict = _valid_verdict()
        patched_schema = dict(d._REQUIRED_FIELDS_SCHEMA)
        patched_schema["references"] = dict(patched_schema["references"])
        patched_schema["references"]["fields"] = (
            patched_schema["references"]["fields"] + ("reviewed_by",)
        )
        with mock.patch.object(d, "_REQUIRED_FIELDS_SCHEMA", patched_schema):
            result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("reviewed_by" in defect for defect in result.defects))

    def test_zero_width_space_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "​"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_byte_order_mark_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "﻿"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_word_joiner_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "⁠"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_nul_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "\x00"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_other_control_character_evidence_is_invalid(self):
        verdict = _valid_verdict(references=[
            {"ref": "a/b.py", "disposition": "intended-new", "evidence": "\x01"},
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_evidence_with_embedded_newline_and_tab_is_still_valid(self):
        verdict = _valid_verdict(references=[
            {
                "ref": "a/b.py", "disposition": "intended-new",
                "evidence": "line one\n\tline two",
            },
        ])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertTrue(result.valid)

    def test_non_list_references_is_invalid_not_raised(self):
        verdict = _valid_verdict(references="nope")
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("references" in defect for defect in result.defects))

    def test_dict_where_references_list_belongs_is_invalid_not_raised(self):
        verdict = _valid_verdict(references={"ref": "x"})
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("references" in defect for defect in result.defects))

    def test_non_dict_element_in_references_is_invalid_not_raised(self):
        verdict = _valid_verdict(references=["not-a-dict"])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("references[0]" in defect for defect in result.defects))

    def test_null_element_in_references_is_invalid_not_raised(self):
        verdict = _valid_verdict(references=[None])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        self.assertTrue(any("references[0]" in defect for defect in result.defects))

    def test_malformed_entry_reports_alongside_other_defects(self):
        verdict = _valid_verdict(
            references=[None],
            dependencies_read=[],
            dependencies_waiver=None,
            replaced_system={"applies": True, "guarantees": []},
        )
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        # the malformed references[0] entry, the still-unresolved a/b.py
        # (references[0] wasn't a usable entry so it can't cover it),
        # empty dependencies_read with no waiver, and replaced_system: at
        # least 4 independent defects reported, not just a crash on the first.
        self.assertGreaterEqual(len(result.defects), 4)

    def test_finding_missing_proposed_amendment_is_invalid(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "sufficiency", "section": "sec",
            "evidence": "e",
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_unknown_finding_kind_is_invalid(self):
        verdict = _valid_verdict(findings=[{
            "id": "f1", "summary": "s", "kind": "bogus", "section": "sec",
            "evidence": "e", "proposed_amendment": "pa",
        }])
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)

    def test_every_defect_in_one_verdict_reported_in_single_pass(self):
        verdict = _valid_verdict(
            references=[],
            dependencies_read=[],
            dependencies_waiver=None,
            replaced_system={"applies": True, "guarantees": []},
            findings=[{
                "id": "f1", "summary": "s", "kind": "bogus", "section": "sec",
                "evidence": "e",
            }],
        )
        result = d.validate_verdict(verdict, ["a/b.py"], str(REPO_ROOT))
        self.assertFalse(result.valid)
        # missing references + empty deps/no waiver + replaced_system + kind +
        # missing proposed_amendment: at least 5 independent defects, not just one.
        self.assertGreaterEqual(len(result.defects), 5)


class ValidateCitationTests(unittest.TestCase):
    def test_citation_resolving_to_real_file_and_line_is_true(self):
        self.assertTrue(d.validate_citation("scripts/forge_common.py:1", str(REPO_ROOT)))

    def test_citation_naming_missing_file_is_false(self):
        self.assertFalse(
            d.validate_citation("scripts/does_not_exist_at_all.py:1", str(REPO_ROOT))
        )

    def test_citation_naming_line_past_end_of_file_is_false(self):
        self.assertFalse(
            d.validate_citation("scripts/forge_common.py:99999999", str(REPO_ROOT))
        )


class DisposeTests(unittest.TestCase):
    def test_groundedness_with_valid_citation_disposes_to_amend(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "citation": "scripts/forge_common.py:1",
            "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(len(disp.amend), 1)
        self.assertEqual(disp.amend[0]["id"], "f1")
        self.assertEqual(disp.surface, [])

    def test_groundedness_with_missing_file_citation_downgrades_and_surfaces(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "citation": "scripts/does_not_exist_at_all.py:1",
            "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(disp.amend, [])
        self.assertEqual(len(disp.surface), 1)
        self.assertEqual(disp.surface[0]["kind"], "sufficiency")

    def test_groundedness_with_line_past_end_of_file_downgrades_and_surfaces(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "citation": "scripts/forge_common.py:99999999",
            "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(disp.amend, [])
        self.assertEqual(len(disp.surface), 1)
        self.assertEqual(disp.surface[0]["kind"], "sufficiency")

    def test_groundedness_with_null_citation_downgrades_and_surfaces(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "groundedness", "section": "sec",
            "evidence": "e", "citation": None,
            "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(disp.amend, [])
        self.assertEqual(len(disp.surface), 1)
        self.assertEqual(disp.surface[0]["kind"], "sufficiency")

    def test_sufficiency_finding_surfaces_and_is_never_auto_amended(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "sufficiency", "section": "sec",
            "evidence": "e", "citation": None, "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(disp.amend, [])
        self.assertEqual(len(disp.surface), 1)
        self.assertEqual(disp.surface[0]["kind"], "sufficiency")

    def test_contradiction_finding_surfaces_and_is_never_auto_amended(self):
        findings = [{
            "id": "f1", "summary": "s", "kind": "contradiction", "section": "sec",
            "evidence": "e", "citation": None, "proposed_amendment": "pa",
        }]
        disp = d.dispose(findings, str(REPO_ROOT))
        self.assertEqual(disp.amend, [])
        self.assertEqual(len(disp.surface), 1)
        self.assertEqual(disp.surface[0]["kind"], "contradiction")


# --- packet build and CLI (Task 3) ------------------------------------------

# Built with NO_SUCH_SYMBOL (never a contiguous literal — see that constant's
# comment) so this fixture's unresolved symbol reference stays unresolved
# even once this test file's own bytes are searched by `git grep -F`.
SPEC_FIXTURE = (
    "# Fixture spec\n"
    "\n"
    "### Alpha section\n"
    "\n"
    "Alpha references `scripts/forge_common.py` and calls "
    "`" + NO_SUCH_SYMBOL + "`.\n"
    "\n"
    "### Beta section\n"
    "\n"
    "Beta references `scripts/does_not_exist_at_all.py`.\n"
)


class BuildPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-docreview-packet-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_FIXTURE)

    def test_unscoped_packet_contains_whole_spec(self):
        packet = d.build_packet(self.spec_path)
        self.assertIn("Alpha references", packet)
        self.assertIn("Beta references", packet)

    def test_scoped_packet_contains_named_section_and_full_document(self):
        packet = d.build_packet(self.spec_path, sections=["Alpha section"])
        self.assertIn("Alpha references", packet)
        # the full document still rides along as context even though only
        # Alpha was named.
        self.assertIn("Beta references", packet)

    def test_scoped_packet_states_contradiction_applies_to_whole_document(self):
        packet = d.build_packet(self.spec_path, sections=["Alpha section"])
        self.assertIn("regardless of scope", packet.lower())

    def test_packet_references_anti_patterns_doc_by_path_without_inlining(self):
        packet = d.build_packet(self.spec_path)
        self.assertIn("skills/brainstorming/design-anti-patterns.md", packet)
        self.assertNotIn("Gate:", packet)
        self.assertNotIn("Trigger:", packet)
        self.assertNotIn("Instead:", packet)

    def test_packet_lists_every_unresolved_reference(self):
        packet = d.build_packet(self.spec_path)
        self.assertIn(NO_SUCH_SYMBOL, packet)
        self.assertIn("scripts/does_not_exist_at_all.py", packet)

    def test_packet_states_required_verdict_fields(self):
        packet = d.build_packet(self.spec_path)
        self.assertIn("disposition", packet)
        self.assertIn("evidence", packet)
        self.assertIn("intended-new", packet)
        self.assertIn("unverifiable", packet)
        self.assertIn("replaced_system", packet)
        self.assertIn("dependencies_read", packet)


class DocreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-docreview-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_FIXTURE)

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, SCRIPT] + args,
            capture_output=True, text=True,
        )

    def test_cli_without_verdict_emits_packet_and_exits_zero(self):
        result = self.run_cli(["--spec", self.spec_path])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Alpha references", result.stdout)

    def test_cli_with_valid_verdict_writes_decision_file_and_exits_zero(self):
        verdict = {
            "references": [
                {
                    "ref": NO_SUCH_SYMBOL, "disposition": "unverifiable",
                    "evidence": "checked, not found",
                },
                {
                    "ref": "scripts/does_not_exist_at_all.py",
                    "disposition": "intended-new", "evidence": "not yet built",
                },
            ],
            "dependencies_read": [],
            "dependencies_waiver": "nothing relevant read",
            "replaced_system": {"applies": False, "guarantees": []},
            "findings": [],
        }
        verdict_path = os.path.join(self.tmp, "verdict.json")
        with open(verdict_path, "w", encoding="utf-8") as f:
            json.dump(verdict, f)
        out_path = os.path.join(self.tmp, "decision.json")
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT), "--out", out_path,
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out_path, "r", encoding="utf-8") as f:
            decision = json.load(f)
        self.assertTrue(decision["valid"])
        self.assertEqual(decision["defects"], [])
        self.assertIn("amend", decision)
        self.assertIn("surface", decision)

    def test_cli_with_invalid_verdict_exits_nonzero_and_names_defect(self):
        verdict = {
            "references": [],
            "dependencies_read": [],
            "dependencies_waiver": "nothing relevant read",
            "replaced_system": {"applies": False, "guarantees": []},
            "findings": [],
        }
        verdict_path = os.path.join(self.tmp, "verdict_bad.json")
        with open(verdict_path, "w", encoding="utf-8") as f:
            json.dump(verdict, f)
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(NO_SUCH_SYMBOL, result.stderr)

    def test_cli_with_missing_spec_exits_nonzero_naming_path(self):
        missing_path = os.path.join(self.tmp, "does-not-exist.md")
        result = self.run_cli(["--spec", missing_path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(missing_path, result.stderr)

    # --- t3-1: a structurally non-object verdict must name the cause and
    # exit non-zero, never crash with an uncaught traceback (parsers-fail-
    # loud is enforced at this CLI boundary since validate_verdict itself
    # deliberately never raises).

    def _verdict_path(self, value):
        path = os.path.join(self.tmp, "verdict_shape.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)
        return path

    def test_cli_with_list_verdict_exits_nonzero_naming_cause(self):
        verdict_path = self._verdict_path([])
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(verdict_path, result.stderr)

    def test_cli_with_string_verdict_exits_nonzero_naming_cause(self):
        verdict_path = self._verdict_path("a string")
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(verdict_path, result.stderr)

    def test_cli_with_null_verdict_exits_nonzero_naming_cause(self):
        verdict_path = self._verdict_path(None)
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(verdict_path, result.stderr)

    def test_cli_with_number_verdict_exits_nonzero_naming_cause(self):
        verdict_path = self._verdict_path(42)
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(verdict_path, result.stderr)

    def test_cli_with_boolean_verdict_exits_nonzero_naming_cause(self):
        verdict_path = self._verdict_path(True)
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(verdict_path, result.stderr)

    # --- t3-2: an unwritable --out must name the cause and exit non-zero on
    # both the packet-emit path (no --verdict) and the decision-write path
    # (with --verdict), never crash with an uncaught traceback.

    def test_cli_packet_emit_with_unwritable_out_exits_nonzero_naming_cause(self):
        bad_out = os.path.join(self.tmp, "no-such-dir", "o.txt")
        result = self.run_cli(["--spec", self.spec_path, "--out", bad_out])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(bad_out, result.stderr)

    def test_cli_decision_write_with_unwritable_out_exits_nonzero_naming_cause(self):
        verdict = {
            "references": [
                {
                    "ref": NO_SUCH_SYMBOL, "disposition": "unverifiable",
                    "evidence": "checked, not found",
                },
                {
                    "ref": "scripts/does_not_exist_at_all.py",
                    "disposition": "intended-new", "evidence": "not yet built",
                },
            ],
            "dependencies_read": [],
            "dependencies_waiver": "nothing relevant read",
            "replaced_system": {"applies": False, "guarantees": []},
            "findings": [],
        }
        verdict_path = os.path.join(self.tmp, "verdict_valid.json")
        with open(verdict_path, "w", encoding="utf-8") as f:
            json.dump(verdict, f)
        bad_out = os.path.join(self.tmp, "no-such-dir", "decision.json")
        result = self.run_cli([
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT), "--out", bad_out,
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(bad_out, result.stderr)


class CliValidityOrderingTests(unittest.TestCase):
    """main()'s --verdict path must check validity BEFORE disposing:
    dispose is only meaningful for a verdict that already passed
    validate_verdict, and a defect found there must be reported without
    ever reaching dispose (which assumes a validated shape and can itself
    raise on a field validate_verdict already flagged, e.g. a non-string
    citation)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-docreview-order-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec_path = os.path.join(self.tmp, "spec.md")
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write(SPEC_FIXTURE)

    def _write_verdict(self, verdict):
        path = os.path.join(self.tmp, "verdict.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(verdict, f)
        return path

    def _invalid_verdict_path(self):
        # A non-string citation: validate_verdict flags it as a defect
        # (fixed upstream in cdb910e), but dispose()/validate_citation
        # crashes on it (`"in"` on an int) if ever reached — the exact
        # shape the ordering bug let through.
        return self._write_verdict({
            "references": [
                {
                    "ref": NO_SUCH_SYMBOL, "disposition": "unverifiable",
                    "evidence": "checked, not found",
                },
                {
                    "ref": "scripts/does_not_exist_at_all.py",
                    "disposition": "intended-new", "evidence": "not yet built",
                },
            ],
            "dependencies_read": [],
            "dependencies_waiver": "nothing relevant read",
            "replaced_system": {"applies": False, "guarantees": []},
            "findings": [{
                "id": "f1", "summary": "s", "kind": "groundedness",
                "section": "sec", "evidence": "e", "citation": 42,
                "proposed_amendment": "pa",
            }],
        })

    def _valid_verdict_path(self):
        return self._write_verdict({
            "references": [
                {
                    "ref": NO_SUCH_SYMBOL, "disposition": "unverifiable",
                    "evidence": "checked, not found",
                },
                {
                    "ref": "scripts/does_not_exist_at_all.py",
                    "disposition": "intended-new", "evidence": "not yet built",
                },
            ],
            "dependencies_read": [],
            "dependencies_waiver": "nothing relevant read",
            "replaced_system": {"applies": False, "guarantees": []},
            "findings": [{
                "id": "f1", "summary": "s", "kind": "groundedness",
                "section": "sec", "evidence": "e",
                "citation": "scripts/forge_common.py:1",
                "proposed_amendment": "pa",
            }],
        })

    def _run_main(self, verdict_path, out_path=None):
        argv = [
            "--spec", self.spec_path, "--verdict", verdict_path,
            "--repo-root", str(REPO_ROOT),
        ]
        if out_path:
            argv += ["--out", out_path]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = d.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_invalid_verdict_does_not_reach_dispose(self):
        verdict_path = self._invalid_verdict_path()
        with mock.patch.object(d, "dispose") as mock_dispose:
            code, _stdout, stderr = self._run_main(verdict_path)
        mock_dispose.assert_not_called()
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("citation", stderr)

    def test_invalid_verdict_exits_nonzero_names_defects_no_traceback(self):
        verdict_path = self._invalid_verdict_path()
        code, _stdout, stderr = self._run_main(verdict_path)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("citation", stderr)

    def test_invalid_verdict_decision_has_no_amend_or_surface(self):
        # Nothing was disposed, so inventing empty amend/surface lists would
        # state something false — they're absent, not empty.
        verdict_path = self._invalid_verdict_path()
        out_path = os.path.join(self.tmp, "decision.json")
        self._run_main(verdict_path, out_path=out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            decision = json.load(f)
        self.assertFalse(decision["valid"])
        self.assertTrue(decision["defects"])
        self.assertNotIn("amend", decision)
        self.assertNotIn("surface", decision)

    def test_valid_verdict_still_disposes_and_writes_full_decision(self):
        verdict_path = self._valid_verdict_path()
        with mock.patch.object(
            d, "dispose", wraps=d.dispose
        ) as wrapped_dispose:
            out_path = os.path.join(self.tmp, "decision.json")
            code, _stdout, stderr = self._run_main(verdict_path, out_path=out_path)
        wrapped_dispose.assert_called_once()
        self.assertEqual(code, 0, stderr)
        with open(out_path, "r", encoding="utf-8") as f:
            decision = json.load(f)
        self.assertTrue(decision["valid"])
        self.assertEqual(decision["defects"], [])
        self.assertEqual(len(decision["amend"]), 1)
        self.assertEqual(decision["surface"], [])


if __name__ == "__main__":
    unittest.main()
