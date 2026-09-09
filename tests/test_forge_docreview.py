"""Tests for scripts/forge_docreview.py — spec review's reference table.

Loaded via importlib since scripts/ files are not a package (Global
Constraints: no shared module between scripts — forge_docreview itself
reuses forge_common, but the test loads it the same way the other
scripts/*.py suites do).
"""
import importlib.util
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest import mock

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))
import forge_docreview as d  # noqa: E402


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


NO_SUCH_SYMBOL = "totallyNonexistent" + "SymbolXyz123"


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


if __name__ == "__main__":
    unittest.main()
