"""forge_memory: budgets are enforced as hard character-count errors (not just
structural checks) so prose-expansion drift inside a valid field is caught;
`validate`/`fmt_check` report every defect in one pass, never just the first;
`render` is the sole source of record text and `parse` is its exact inverse,
including on a file a human hand-drifted (reordered fields) but that still
parses; and unparsable or budget-violating input fails loud naming the line."""
import argparse
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
HOOK = os.path.join(REPO_ROOT, "hooks", "guard-memory-writes")
sys.path.insert(0, SCRIPTS_DIR)

import forge_memory as fm  # noqa: E402
import forge_memory_store as fms  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _valid_constraint_fields(**overrides):
    fields = {
        "id": "no-eval-in-hooks",
        "rule": "Hooks must never call eval on untrusted input.",
        "because": "Untrusted input reaching eval is an injection vector.",
        "scope": "hooks/",
        "added": "2026-09-05",
        "source": "issue-42",
    }
    fields.update(overrides)
    return fields


def _valid_deferral_fields(**overrides):
    fields = {
        "title": "Improve error messages in the deferral formatter",
        "why": "Nice-to-have polish, not required by the current spec.",
        "from": "user",
        "follow-up": "backlog",
    }
    fields.update(overrides)
    return fields


class ValidateTests(unittest.TestCase):
    def test_budget_accepted_at_exact_limit(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule="x" * 200,
        ))
        defects = fm.validate(record)
        self.assertEqual([d for d in defects if "rule" in d], [])

    def test_budget_rejected_one_over(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule="x" * 201,
        ))
        defects = fm.validate(record)
        matches = [d for d in defects if "rule" in d and "200" in d]
        self.assertEqual(len(matches), 1, defects)

    def test_missing_required_field_rejected(self):
        fields = _valid_constraint_fields()
        del fields["because"]
        record = fm.Record(type="constraint", fields=fields)
        defects = fm.validate(record)
        self.assertTrue(any("because" in d for d in defects), defects)

    def test_empty_string_field_rejected(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            because="",
        ))
        defects = fm.validate(record)
        self.assertTrue(any("because" in d for d in defects), defects)

    def test_id_accepts_kebab_case(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="abc-def-123",
        ))
        defects = fm.validate(record)
        self.assertEqual([d for d in defects if "id" in d], [])

    def test_id_rejects_spaces(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="abc def",
        ))
        defects = fm.validate(record)
        self.assertTrue(any("id" in d for d in defects), defects)

    def test_id_rejects_uppercase(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="AbcDef",
        ))
        defects = fm.validate(record)
        self.assertTrue(any("id" in d for d in defects), defects)

    def test_id_rejects_leading_trailing_hyphens(self):
        for bad in ("-abc-def", "abc-def-"):
            record = fm.Record(type="constraint", fields=_valid_constraint_fields(
                id=bad,
            ))
            defects = fm.validate(record)
            self.assertTrue(any("id" in d for d in defects), (bad, defects))

    def test_id_rejects_over_40_chars(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="a" * 41,
        ))
        defects = fm.validate(record)
        self.assertTrue(any("id" in d and "40" in d for d in defects), defects)

    def test_id_accepts_exactly_40_chars(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="a" * 40,
        ))
        defects = fm.validate(record)
        self.assertEqual([d for d in defects if "id" in d], [])

    def test_followup_accepts_valid_values(self):
        for good in ("backlog", "drop", "revisit-when:phase-15-lands"):
            record = fm.Record(type="deferral", fields=_valid_deferral_fields(**{
                "follow-up": good,
            }))
            defects = fm.validate(record)
            self.assertEqual([d for d in defects if "follow-up" in d], [], (good, defects))

    def test_followup_rejects_roadmap(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields(**{
            "follow-up": "roadmap",
        }))
        defects = fm.validate(record)
        self.assertTrue(any("follow-up" in d for d in defects), defects)

    def test_followup_rejects_unknown_value(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields(**{
            "follow-up": "someday",
        }))
        defects = fm.validate(record)
        self.assertTrue(any("follow-up" in d for d in defects), defects)

    def test_multiple_defects_all_reported(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields(
            title="x" * 81,
            why="",
            **{"follow-up": "roadmap"},
        ))
        defects = fm.validate(record)
        self.assertTrue(any("title" in d for d in defects), defects)
        self.assertTrue(any("why" in d for d in defects), defects)
        self.assertTrue(any("follow-up" in d for d in defects), defects)
        self.assertGreaterEqual(len(defects), 3)


class RenderParseRoundTripTests(unittest.TestCase):
    def test_round_trip_constraint(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields())
        text = fm.render(record)
        records = fm.parse(text, "constraint")
        self.assertEqual(len(records), 1)
        self.assertEqual(fm.render(records[0]), text)

    def test_round_trip_deferral(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields())
        text = fm.render(record)
        records = fm.parse(text, "deferral")
        self.assertEqual(len(records), 1)
        self.assertEqual(fm.render(records[0]), text)

    def test_drifted_but_parsable_file_normalizes_on_reparse(self):
        # Fields reordered by hand relative to the canonical SCHEMA order —
        # still parses (labels are matched by name, not position) but its
        # re-render must differ from the drifted input, proving normalization.
        drifted = (
            "## no-eval-in-hooks\n"
            "**Because:** Untrusted input reaching eval is an injection vector.\n"
            "**Rule:** Hooks must never call eval on untrusted input.\n"
            "**Scope:** hooks/\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
        )
        records = fm.parse(drifted, "constraint")
        self.assertEqual(len(records), 1)
        rerendered = fm.render(records[0])
        self.assertNotEqual(rerendered, drifted)
        # but the values themselves survived intact
        self.assertEqual(records[0].fields["rule"],
                          "Hooks must never call eval on untrusted input.")

    def test_parse_unparsable_text_raises_naming_line(self):
        text = (
            "## no-eval-in-hooks\n"
            "This line is just prose, not a recognized field.\n"
            "**Rule:** Hooks must never call eval.\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertIn("line 2", str(ctx.exception))

    def test_parse_duplicate_id_rejected(self):
        text = (
            "## no-eval-in-hooks\n"
            "**Rule:** Hooks must never call eval.\n"
            "**Because:** Reason one.\n"
            "**Scope:** hooks/\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
            "\n"
            "## no-eval-in-hooks\n"
            "**Rule:** A different rule text.\n"
            "**Because:** Reason two.\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-06\n"
            "**Source:** issue-43\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertIn("duplicate", str(ctx.exception).lower())


class SchemaErrorLineAttributeTests(unittest.TestCase):
    """The line number ``parse`` fails at travels as structured data on the
    raised ``SchemaError`` (a ``.line`` attribute), not by scraping it back
    out of the message text — a caller (FileStore.scan) that read it via
    regex would silently get ``None`` the moment a message was reworded,
    with no test anywhere catching it. Message text still reads
    "line N: ..." for humans; only where the DATA comes from changes."""

    def test_unrecognized_line_carries_its_line_number(self):
        text = (
            "## no-eval-in-hooks\n"
            "This line is just prose, not a recognized field.\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertEqual(ctx.exception.line, 2)

    def test_duplicate_heading_carries_its_line_number(self):
        text = (
            "## dup\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
            "\n"
            "## dup\n"
            "**Rule:** r2\n"
            "**Because:** b2\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-06\n"
            "**Source:** user\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertEqual(ctx.exception.line, 8)

    def test_unknown_field_label_carries_its_line_number(self):
        text = (
            "## some-id\n"
            "**Bogus:** value\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertEqual(ctx.exception.line, 2)

    def test_duplicate_field_carries_its_line_number(self):
        text = (
            "## some-id\n"
            "**Rule:** r\n"
            "**Rule:** r2\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertEqual(ctx.exception.line, 3)

    def test_schema_error_with_no_line_defaults_to_none(self):
        # Not every SchemaError comes from parse() — e.g. an unknown
        # record type — and those never claim a line number.
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse("## x\n", "not-a-real-type")
        self.assertIsNone(ctx.exception.line)


class RecordRefTests(unittest.TestCase):
    """``ref`` is store-assigned metadata (e.g. a GitHub issue number), not
    part of the record's data — two records with identical fields but
    different refs are the same record from ``render``/``parse``/
    ``validate``'s point of view, so equality and repr must ignore it."""

    def test_equal_fields_different_ref_compare_equal(self):
        fields = _valid_deferral_fields()
        a = fm.Record(type="deferral", fields=dict(fields), ref="1")
        b = fm.Record(type="deferral", fields=dict(fields), ref="2")
        self.assertEqual(a, b)

    def test_repr_omits_ref(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields(), ref="7")
        self.assertNotIn("7", repr(record))
        self.assertNotIn("ref", repr(record))

    def test_ref_is_settable_and_readable_and_untouched_by_render_parse_validate(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields())
        record.ref = "42"
        self.assertEqual(record.ref, "42")

        text = fm.render(record)
        self.assertNotIn("42", text)

        reparsed = fm.parse(text, "deferral")[0]
        self.assertIsNone(reparsed.ref)

        self.assertEqual(fm.validate(record), [])


class FmtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_fmt_check_reports_three_distinct_defects(self):
        path = os.path.join(self.tmp, "constraints.md")
        # One record, three independent defects: rule over budget, "because"
        # missing entirely, id not kebab-case.
        _write(path, (
            "## AbcDef\n"
            "**Rule:** {}\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
        ).format("x" * 201))
        defects = fm.fmt_check([path])
        self.assertGreaterEqual(len(defects), 3)
        joined = " | ".join(defects)
        self.assertIn("rule", joined)
        self.assertIn("because", joined)
        self.assertIn("id", joined)

    def test_fmt_write_rewrites_to_canonical_form_and_is_idempotent(self):
        path = os.path.join(self.tmp, "constraints.md")
        record = fm.Record(type="constraint", fields=_valid_constraint_fields())
        canonical = fm.render(record)
        drifted = (
            "## no-eval-in-hooks\n"
            "**Because:** Untrusted input reaching eval is an injection vector.\n"
            "**Rule:** Hooks must never call eval on untrusted input.\n"
            "**Scope:** hooks/\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
        )
        _write(path, drifted)
        fm.fmt_write([path])
        with open(path, encoding="utf-8") as f:
            first_pass = f.read()
        self.assertEqual(first_pass, canonical)

        fm.fmt_write([path])
        with open(path, encoding="utf-8") as f:
            second_pass = f.read()
        self.assertEqual(second_pass, first_pass)

    def test_fmt_write_fails_loud_on_budget_violation(self):
        path = os.path.join(self.tmp, "constraints.md")
        _write(path, (
            "## no-eval-in-hooks\n"
            "**Rule:** {}\n"
            "**Because:** Untrusted input reaching eval is an injection vector.\n"
            "**Scope:** hooks/\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
        ).format("x" * 201))
        with self.assertRaises(fm.SchemaError):
            fm.fmt_write([path])


def _completed(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


def _run_cli(argv):
    """Run ``fm.main(argv)``, returning ``(exit_code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fm.main(argv)
    return code, out.getvalue(), err.getvalue()


class CLITestCase(unittest.TestCase):
    """Every CLI test runs inside a throwaway repo root with the real
    process cwd pointed at it (``main`` derives ``repo_root`` from
    ``os.getcwd()``), and with ``gh`` never actually invoked unless a test
    explicitly stubs it — a spurious ``subprocess.run`` call fails the test
    instead of silently reaching the network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self._old_cwd)
        os.makedirs(os.path.join(self.tmp, "docs", "forge"), exist_ok=True)

    def _no_gh_guard(self):
        """Patch context: any ``gh`` invocation raises AssertionError."""
        def _forbidden(*a, **k):
            raise AssertionError("gh must not be invoked in this path: {}".format(a))
        return mock.patch.object(fms.subprocess, "run", side_effect=_forbidden)

    def _use_file_store_for_deferrals(self):
        with open(os.path.join(self.tmp, "docs", "forge", "config.json"), "w",
                   encoding="utf-8") as f:
            json.dump({"deferrals": {"store": "file"}}, f)


class SubcommandSurfaceTests(CLITestCase):
    def test_unknown_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            fm.main(["bogus-command"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_help_exits_zero(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fm.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_every_subcommand_present(self):
        parser = fm.build_parser()
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse.Action) and a.choices
        )
        self.assertEqual(set(sub_action.choices), {
            "add-constraint", "retire-constraint", "list-constraints",
            "defer", "list-deferrals", "resolve-deferral", "fmt",
            "install-guards",
        })

    # This is an ALLOW-list, not a denylist, and must stay one: a denylist
    # of banned names ({"body", "text"}) passes vacuously the instant
    # someone adds a free-form field under any other name (--notes,
    # --content, --description, --details, ...). The property this test
    # guards — that every record is composed from typed fields, with no
    # free-form text surface anywhere — is the project's central anti-
    # drift mechanism (the drift being fixed came from agents reading and
    # imitating prior prose), so it must be checked positively: each
    # subcommand's flags must be EXACTLY this set. Any newly added flag
    # fails this test until someone deliberately adds it below.
    _EXPECTED_DESTS = {
        "add-constraint": {"id", "rule", "because", "scope", "issue", "spec"},
        "retire-constraint": {"id"},
        "list-constraints": {"scope", "json"},
        "defer": {"title", "why", "follow_up", "from_", "run", "finding_id"},
        "list-deferrals": {"json"},
        "resolve-deferral": {"ref", "reason"},
        "fmt": {"check", "write", "paths", "local_only"},
        "install-guards": {"pre_commit", "ci"},
    }

    def test_no_subcommand_accepts_a_free_form_body_argument(self):
        parser = fm.build_parser()
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse.Action) and a.choices
        )
        for name, subparser in sub_action.choices.items():
            dests = {a.dest for a in subparser._actions if a.dest != "help"}
            self.assertEqual(
                dests, self._EXPECTED_DESTS[name],
                "{} flags changed — update _EXPECTED_DESTS deliberately "
                "if this is an intentional, reviewed typed field, never "
                "to admit a free-form body/text argument".format(name),
            )


class AddConstraintCLITests(CLITestCase):
    def test_composes_canonical_record_with_machine_added_date(self):
        code, out, err = _run_cli([
            "add-constraint", "--id", "no-eval-in-hooks",
            "--rule", "Hooks must never call eval on untrusted input.",
            "--because", "Untrusted input reaching eval is an injection vector.",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        records = fm.parse(text, "constraint")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["added"], fm._today_iso())
        self.assertEqual(records[0].fields["source"], "user")
        self.assertEqual(records[0].fields["scope"], "repo")

    def test_added_is_not_a_settable_flag(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
                "--added", "2020-01-01",
            ])

    def test_issue_and_spec_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
                "--issue", "7", "--spec", "docs/spec.md",
            ])

    def test_issue_flag_becomes_source(self):
        code, _, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            "--issue", "7",
        ])
        self.assertEqual(code, 0, err)
        records = fm.fmt_check  # sanity: module still importable
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        record = fm.parse(text, "constraint")[0]
        self.assertEqual(record.fields["source"], "issue-7")

    def test_budget_overrun_exits_nonzero_naming_field_and_limit_on_stderr(self):
        code, out, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "x" * 201, "--because", "b",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("rule", err)
        self.assertIn("200", err)

    def test_duplicate_id_exits_nonzero(self):
        code, _, _ = _run_cli([
            "add-constraint", "--id", "dup", "--rule", "r", "--because", "b",
        ])
        self.assertEqual(code, 0)
        code, out, err = _run_cli([
            "add-constraint", "--id", "dup", "--rule", "r2", "--because", "b2",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("dup", err)


class RetireConstraintCLITests(CLITestCase):
    def test_unknown_slug_exits_nonzero(self):
        code, out, err = _run_cli(["retire-constraint", "--id", "no-such-id"])
        self.assertNotEqual(code, 0)
        self.assertIn("no-such-id", err)

    def test_retire_removes_record(self):
        _run_cli([
            "add-constraint", "--id", "to-retire", "--rule", "r", "--because", "b",
        ])
        code, _, err = _run_cli(["retire-constraint", "--id", "to-retire"])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("to-retire", text)


class ListConstraintsCLITests(CLITestCase):
    def test_json_output_is_parseable(self):
        _run_cli([
            "add-constraint", "--id", "a", "--rule", "r", "--because", "b",
            "--scope", "hooks/",
        ])
        code, out, err = _run_cli(["list-constraints", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "a")

    def test_human_output_is_not_json(self):
        _run_cli([
            "add-constraint", "--id", "a", "--rule", "r", "--because", "b",
        ])
        code, out, err = _run_cli(["list-constraints"])
        self.assertEqual(code, 0, err)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)
        self.assertIn("a", out)

    def test_scope_filter(self):
        _run_cli([
            "add-constraint", "--id", "a", "--rule", "r", "--because", "b",
            "--scope", "hooks/",
        ])
        _run_cli([
            "add-constraint", "--id", "b", "--rule", "r", "--because", "b",
            "--scope", "scripts/",
        ])
        code, out, err = _run_cli(["list-constraints", "--json", "--scope", "hooks/"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual([d["id"] for d in data], ["a"])


class DeferCLITests(CLITestCase):
    def test_invalid_followup_exits_nonzero_naming_legal_values(self):
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli([
            "defer", "--title", "t", "--why", "w", "--follow-up", "roadmap",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("backlog", err)
        self.assertIn("drop", err)
        self.assertIn("revisit-when", err)

    def test_defer_against_file_store(self):
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli([
            "defer", "--title", "improve-x", "--why", "polish", "--follow-up", "backlog",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        records = fm.parse(text, "deferral")
        self.assertEqual(records[0].fields["from"], "user")

    def test_defer_against_github_store_invokes_gh(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/9\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "defer", "--title", "improve-x", "--why", "polish",
                "--follow-up", "backlog",
            ])
        self.assertEqual(code, 0, err)
        self.assertTrue(any(a[:2] == ["gh", "issue"] and "create" in a for a in calls))

    def test_list_deferrals_json(self):
        self._use_file_store_for_deferrals()
        _run_cli([
            "defer", "--title", "improve-x", "--why", "polish", "--follow-up", "backlog",
        ])
        code, out, err = _run_cli(["list-deferrals", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data[0]["title"], "improve-x")

    def test_list_deferrals_against_github_store_reports_every_bad_issue(self):
        # Default store (no config.json) is GitHubStore. cmd_list_deferrals
        # calls store.scan (not .list) so it never needs to know which
        # store it holds: scan always returns (records, errors) rather
        # than raising on the first bad body, so the CLI can surface every
        # bad issue's number itself, same as fmt --check does.
        good_body = fm.render(fm.Record(type="deferral", fields={
            "title": "good-one", "why": "w", "from": "user",
            "follow-up": "backlog",
        }))
        payload = json.dumps([
            {"number": 1, "body": "## bad-one\nnot a valid field line\n"},
            {"number": 2, "body": good_body},
            {"number": 3, "body": "## bad-two\nalso not valid\n"},
        ])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(["list-deferrals"])

        self.assertNotEqual(code, 0)
        self.assertIn("#1", err)
        self.assertIn("#3", err)

    def test_resolve_deferral_against_file_store(self):
        self._use_file_store_for_deferrals()
        _run_cli([
            "defer", "--title", "improve-x", "--why", "polish", "--follow-up", "backlog",
        ])
        code, out, err = _run_cli([
            "resolve-deferral", "--ref", "improve-x", "--reason", "done",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("improve-x", text)


class DeferRunJsonCLITests(CLITestCase):
    """``defer --run --finding-id`` writes the resolved issue number back
    into the matching run.json deferral entry, idempotently, without ever
    disturbing any other key in the file."""

    def _run_json_path(self):
        return os.path.join(self.tmp, "run.json")

    def _write_run_json(self, deferrals, status="passed"):
        """A run.json fixture carrying the full set of unrelated keys a
        real runner writes, so a test can assert every one of them survives
        a defer write-back byte-for-byte. ``status`` defaults to a terminal
        value ("passed") since filing is a close-out step; pass "running"
        to exercise the in-progress guard."""
        data = {
            "plan": "/x/plan.md",
            "spec": "/x/spec.md",
            "status": status,
            "base_commit": "abc123",
            "tasks": [{"number": 1, "status": "passed"}],
            "threads": {"task-1-worker": "thread-1"},
            "seeded_findings": [{"id": "seed-1", "summary": "s"}],
            "autofix_mode": "gate",
            "doc_sync": {"status": "clean"},
            "started_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:05:00Z",
            "deferrals": deferrals,
        }
        path = self._run_json_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return path, data

    def _defer_args(self, run=None, finding_id=None, title="improve-x"):
        args = [
            "defer", "--title", title, "--why", "polish", "--follow-up", "backlog",
        ]
        if run is not None:
            args += ["--run", run]
        if finding_id is not None:
            args += ["--finding-id", finding_id]
        return args

    def test_run_without_finding_id_exits_nonzero(self):
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1"}])
        code, out, err = _run_cli(self._defer_args(run=path))
        self.assertNotEqual(code, 0)

    def test_finding_id_without_run_exits_nonzero(self):
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli(self._defer_args(finding_id="f1"))
        self.assertNotEqual(code, 0)

    def test_successful_defer_records_issue_and_preserves_other_keys(self):
        path, original = self._write_run_json([
            {"id": "f1", "summary": "one"},
            {"id": "f2", "summary": "two"},
        ])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/42\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertEqual(code, 0, err)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"][0]["issue"], "42")
        self.assertIsInstance(data["deferrals"][0]["issue"], str)
        self.assertNotIn("issue", data["deferrals"][1])
        for key in (
            "plan", "spec", "status", "base_commit", "tasks", "threads",
            "seeded_findings", "autofix_mode", "doc_sync", "started_at",
            "updated_at",
        ):
            self.assertEqual(data[key], original[key], key)

    def test_refiling_already_filed_entry_exits_nonzero_and_creates_nothing(self):
        path, _ = self._write_run_json([{"id": "f1", "issue": "7"}])
        with self._no_gh_guard():
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        self.assertIn("7", err)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"][0]["issue"], "7")

    def test_defer_against_file_store_records_string_issue(self):
        # The FileStore write-back path is the one Task 2's original
        # review missed: FileStore.create returns the heading (title)
        # value, not a numeric string, so the pinned-string contract has
        # to hold there too, not only for GitHubStore's numeric ref.
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1"}])
        code, out, err = _run_cli(
            self._defer_args(run=path, finding_id="f1", title="improve-x")
        )
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"][0]["issue"], "improve-x")
        self.assertIsInstance(data["deferrals"][0]["issue"], str)

    def test_refuses_write_back_while_run_is_in_progress(self):
        path, _ = self._write_run_json([{"id": "f1"}], status="running")
        with self._no_gh_guard():
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        self.assertIn("running", err.lower())
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("issue", data["deferrals"][0])

    def test_unknown_finding_id_exits_nonzero_and_leaves_run_json_unmodified(self):
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1"}])
        with open(path, encoding="utf-8") as f:
            before = f.read()
        code, out, err = _run_cli(
            self._defer_args(run=path, finding_id="no-such-id")
        )
        self.assertNotEqual(code, 0)
        self.assertIn("no-such-id", err)
        with open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_malformed_run_json_exits_nonzero_naming_path(self):
        self._use_file_store_for_deferrals()
        path = self._run_json_path()
        _write(path, "not json")
        code, out, err = _run_cli(self._defer_args(run=path, finding_id="f1"))
        self.assertNotEqual(code, 0)
        self.assertIn(path, err)

    def test_permission_denied_run_json_exits_nonzero_naming_path_no_traceback(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission checks are bypassed when running as root")
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1"}])
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        # If this propagated as an uncaught exception (rather than being
        # returned as a non-zero code), _run_cli itself would raise here —
        # that IS the traceback-free assertion.
        code, out, err = _run_cli(self._defer_args(run=path, finding_id="f1"))
        self.assertNotEqual(code, 0)
        self.assertIn(path, err)

    def test_unrecognized_status_refuses_write_back_and_never_calls_gh(self):
        # forge_status.py's _STATE_MAP.get(raw_status, "running") treats
        # any status it doesn't recognize as still "running" (non-
        # terminal). defer must agree: fail-open here (treating an
        # unrecognized status as terminal) would let this command write
        # into a run forge_status still reports as in progress.
        path, _ = self._write_run_json([{"id": "f1"}], status="paused")
        with self._no_gh_guard():
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        self.assertIn("paused", err)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("issue", data["deferrals"][0])

    def test_no_second_copy_of_the_status_vocabulary(self):
        # The terminal/non-terminal split is forge_status.is_terminal's
        # one definition (implemented against forge_status._STATE_MAP);
        # cmd_defer must call it rather than re-deriving its own list of
        # terminal status strings, or the two would be free to drift.
        source = inspect.getsource(fm.cmd_defer)
        self.assertIn("is_terminal", source)
        self.assertNotIn("_KNOWN_TERMINAL_STATUSES", inspect.getsource(fm))
        self.assertNotIn("escalated-doc-sync", inspect.getsource(fm))

    def test_absent_run_json_exits_nonzero_naming_path(self):
        self._use_file_store_for_deferrals()
        path = os.path.join(self.tmp, "no-such-run.json")
        code, out, err = _run_cli(self._defer_args(run=path, finding_id="f1"))
        self.assertNotEqual(code, 0)
        self.assertIn(path, err)

    def test_issue_creation_failure_leaves_run_json_unmodified(self):
        path, _ = self._write_run_json([{"id": "f1"}])
        with open(path, encoding="utf-8") as f:
            before = f.read()

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=1, stderr="boom")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        with open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_post_creation_write_failure_surfaces_created_issue_number(self):
        # store.create() succeeds (issue #42 exists on GitHub) but the
        # run.json write-back itself fails (disk full / permissions /
        # path removed underneath us). The created issue must never be
        # lost to an uncaught traceback — retrying blind would file a
        # duplicate, exactly what the idempotency guarantee exists to
        # prevent, reached from the other direction.
        path, _ = self._write_run_json([{"id": "f1"}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/42\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(fm.os, "replace", side_effect=OSError("disk full")):
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        self.assertIn("42", err)
        # main() must never let this propagate as an uncaught traceback —
        # _run_cli would raise instead of returning a code if it did.
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("issue", data["deferrals"][0])

    def test_write_back_is_atomic_no_stray_temp_files(self):
        path, _ = self._write_run_json([{"id": "f1"}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/42\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["docs", "run.json"])

    def test_gh_unavailable_exits_nonzero_and_never_falls_back_to_file_store(self):
        path, _ = self._write_run_json([{"id": "f1"}])
        with open(path, encoding="utf-8") as f:
            before = f.read()

        with mock.patch.object(fms.shutil, "which", return_value=None):
            code, out, err = _run_cli(
                self._defer_args(run=path, finding_id="f1")
            )
        self.assertNotEqual(code, 0)
        self.assertIn("gh", err.lower())
        deferrals_md = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        self.assertFalse(os.path.exists(deferrals_md))
        with open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(before, after)


class FmtCLITests(CLITestCase):
    def test_neither_check_nor_write_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            fm.main(["fmt"])

    def test_check_and_write_together_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            fm.main(["fmt", "--check", "--write"])

    def _write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_check_drifted_file_nonzero_canonical_file_zero(self):
        path = os.path.join(self.tmp, "constraints.md")
        self._write(path, (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
        ))
        code, out, err = _run_cli(["fmt", "--check", path])
        self.assertNotEqual(code, 0)

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        self._write(path, fm.render(record))
        code, out, err = _run_cli(["fmt", "--check", path])
        self.assertEqual(code, 0, out + err)

    def test_explicit_path_does_not_call_gh(self):
        path = os.path.join(self.tmp, "constraints.md")
        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        self._write(path, fm.render(record))
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check", path])
        self.assertEqual(code, 0, out + err)

    def test_no_path_under_file_store_does_not_call_gh(self):
        self._use_file_store_for_deferrals()
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check"])
        self.assertEqual(code, 0, out + err)

    def test_no_path_checks_managed_files_and_open_issues(self):
        # A drifted managed constraints.md file...
        constraints_path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        self._write(constraints_path, (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
        ))

        # ...and a drifted open forge:deferral issue body (GitHub store is
        # the default — no config.json).
        broken_body = "## bad\nthis is not a valid field line\n"
        payload = json.dumps([{"number": 5, "body": broken_body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(["fmt", "--check"])

        self.assertNotEqual(code, 0)
        self.assertIn("bad id", out)
        self.assertIn("#5", out)

    def test_budget_overrunning_but_parsable_issue_body_is_reported(self):
        record_fields = {
            "title": "x" * 90,  # over the 80-char budget, still parsable
            "why": "w", "from": "user", "follow-up": "backlog",
        }
        body = "## {}\n**Why:** {}\n**From:** {}\n**Follow-up:** {}\n".format(
            record_fields["title"], record_fields["why"], record_fields["from"],
            record_fields["follow-up"],
        )
        payload = json.dumps([{"number": 6, "body": body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(["fmt", "--check"])

        self.assertNotEqual(code, 0)
        self.assertIn("#6", out)
        self.assertIn("budget", out)


def _run_hook(stdin_text, cwd=None, env=None):
    """Invoke hooks/guard-memory-writes as a real subprocess with ``stdin_text``
    fed on stdin. Returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [HOOK], input=stdin_text, cwd=cwd, capture_output=True, text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _load_hook_embedded_python():
    """The hook writes its matching logic to a temp file as a python
    heredoc body (see hooks/guard-memory-writes) rather than exposing an
    importable module. Extract that body and exec it into a fresh
    namespace so matches_managed/probe_case_insensitive can be unit
    tested directly, in addition to the subprocess-level tests above."""
    with open(HOOK, encoding="utf-8") as f:
        text = f.read()
    marker = "cat <<'PYEOF' > \"$pyfile\"\n"
    start = text.index(marker) + len(marker)
    end = text.index("\nPYEOF\n", start)
    source = text[start:end]
    # Drop the trailing top-level `main()` call: it reads sys.argv
    # expecting the hook's own argv (cwd/signal/marker_dir) and would
    # raise on import here. Only the function definitions are needed.
    source = source.rsplit("\nmain()", 1)[0]
    namespace = {}
    exec(compile(source, "guard-memory-writes-embedded", "exec"), namespace)
    return namespace


def _fs_is_case_insensitive(dir_path):
    """Probe whether ``dir_path``'s filesystem folds case (as APFS does by
    default): write a file with a lowercase name and check whether it is
    also visible under an uppercase spelling."""
    probe = os.path.join(dir_path, "case-probe-file")
    _write(probe, "x")
    try:
        return os.path.exists(os.path.join(dir_path, "CASE-PROBE-FILE"))
    finally:
        os.remove(probe)


def _hook_payload(tool_name, file_path, cwd):
    return json.dumps({
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "cwd": cwd,
    })


class GuardMemoryWritesHookTests(unittest.TestCase):
    """hooks/guard-memory-writes: the PreToolUse fast-feedback layer. Denies
    Edit/Write/MultiEdit to forge's machine-managed memory files, redirecting
    to the forge_memory.py CLI. Must emit nothing and exit 0 whenever it is
    not denying, including on its own internal failure."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="forge-guard-hook-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, "docs", "forge"))

    def _write_config(self, config_obj):
        _write(
            os.path.join(self.repo, "docs", "forge", "config.json"),
            json.dumps(config_obj),
        )

    def _deny_reason(self, stdout):
        payload = json.loads(stdout)
        return payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_write_to_constraints_md_denied_naming_add_constraint(self):
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("add-constraint", self._deny_reason(out))

    def test_edit_to_constraints_md_denied_identically(self):
        code, out, err = _run_hook(
            _hook_payload("Edit", "docs/forge/constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("add-constraint", self._deny_reason(out))

    def test_multiedit_to_constraints_md_denied_identically(self):
        code, out, err = _run_hook(
            _hook_payload("MultiEdit", "docs/forge/constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("add-constraint", self._deny_reason(out))

    def test_write_to_spec_file_allowed(self):
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/specs/foo.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_no_forge_signal_emits_nothing(self):
        plain_dir = tempfile.mkdtemp(prefix="forge-guard-hook-plain-")
        self.addCleanup(shutil.rmtree, plain_dir, ignore_errors=True)
        os.makedirs(os.path.join(plain_dir, ".git"))
        code, out, err = _run_hook(
            _hook_payload("Write", "constraints.md", plain_dir),
            cwd=plain_dir,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_relative_file_path_resolved_before_comparison(self):
        sub = os.path.join(self.repo, "docs", "forge")
        code, out, err = _run_hook(
            _hook_payload("Write", "constraints.md", sub),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )

    def test_substring_match_alone_is_allowed(self):
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/old-constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_deferrals_md_denied_only_when_file_store_configured(self):
        # No config.json => GitHub store is selected for deferrals => the
        # file is not machine-managed => allowed.
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/deferrals.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

        self._write_config({"deferrals": {"store": "file"}})
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/deferrals.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("defer", self._deny_reason(out))

    def test_malformed_stdin_exits_0_without_denying(self):
        code, out, err = _run_hook("not json at all {{{", cwd=self.repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_empty_stdin_exits_0_without_denying(self):
        code, out, err = _run_hook("", cwd=self.repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_missing_fields_exit_0_without_denying(self):
        code, out, err = _run_hook(json.dumps({"tool_name": "Write"}), cwd=self.repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_other_tool_names_ignored(self):
        code, out, err = _run_hook(
            _hook_payload("Bash", "docs/forge/constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_case_variant_of_existing_constraints_file_denied(self):
        # F1, samefile branch: on a case-insensitive filesystem (APFS by
        # default), docs/forge/Constraints.md and docs/forge/constraints.md
        # are the SAME file once it exists. A bare string compare misses
        # that; identity (os.path.samefile) must not. Skipped on a
        # case-sensitive filesystem, where the differently-cased path
        # really is a different, unmanaged file and must be allowed.
        constraints_path = os.path.join(self.repo, "docs", "forge", "constraints.md")
        _write(constraints_path, "## x\n**Rule:** r\n**Because:** b\n"
                                  "**Scope:** repo\n**Added:** 2026-09-05\n"
                                  "**Source:** user\n")
        if not _fs_is_case_insensitive(os.path.dirname(constraints_path)):
            self.skipTest(
                "filesystem is case-sensitive; the case-variant bypass "
                "does not apply here"
            )
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/Constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("add-constraint", self._deny_reason(out))

    def test_case_variant_of_absent_constraints_file_denied(self):
        # F1b, the fallback branch: the FIRST constraint in a fresh forge
        # repo, before constraints.md exists at all. samefile has nothing
        # to compare against here, so the fallback must itself fold case
        # -- os.path.normcase is a no-op on POSIX and would silently let
        # this exact case through, which is the bug this test pins down.
        # Skipped on a case-sensitive filesystem, where this variant path
        # really is a different, unmanaged file.
        docs_forge = os.path.join(self.repo, "docs", "forge")
        if not _fs_is_case_insensitive(docs_forge):
            self.skipTest(
                "filesystem is case-sensitive; the case-variant bypass "
                "does not apply here"
            )
        constraints_path = os.path.join(docs_forge, "constraints.md")
        self.assertFalse(os.path.exists(constraints_path))
        code, out, err = _run_hook(
            _hook_payload("Write", "docs/forge/Constraints.md", self.repo),
            cwd=self.repo,
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny",
        )
        self.assertIn("add-constraint", self._deny_reason(out))

    def test_no_forge_signal_never_spawns_python_or_writes_a_tempfile(self):
        # F2: the signal-directory walk must happen in bash, before stdin
        # is read and before python3 is ever spawned, so this hook is
        # near-zero-cost in every non-forge repo where the plugin is
        # installed. Proven here by shadowing python3 on PATH with a fake
        # that leaves a marker if invoked, and pointing TMPDIR at an
        # empty directory the hook would have to use for its temp file.
        plain_dir = tempfile.mkdtemp(prefix="forge-guard-hook-plain-")
        self.addCleanup(shutil.rmtree, plain_dir, ignore_errors=True)
        os.makedirs(os.path.join(plain_dir, ".git"))

        fake_bin = tempfile.mkdtemp(prefix="forge-guard-hook-fakebin-")
        self.addCleanup(shutil.rmtree, fake_bin, ignore_errors=True)
        marker = os.path.join(fake_bin, "python3-was-called")
        fake_python3 = os.path.join(fake_bin, "python3")
        _write(fake_python3, "#!/bin/sh\ntouch \"{}\"\nexit 0\n".format(marker))
        os.chmod(fake_python3, 0o755)

        tmp_home = tempfile.mkdtemp(prefix="forge-guard-hook-tmphome-")
        self.addCleanup(shutil.rmtree, tmp_home, ignore_errors=True)

        env = dict(os.environ)
        env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
        env["TMPDIR"] = tmp_home

        code, out, err = _run_hook(
            _hook_payload("Write", "constraints.md", plain_dir),
            cwd=plain_dir, env=env,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertFalse(
            os.path.exists(marker),
            "python3 must never be invoked when there is no forge signal",
        )
        self.assertEqual(
            os.listdir(tmp_home), [],
            "no temp file may be created when there is no forge signal",
        )


class MatchesManagedFallbackTests(unittest.TestCase):
    """F1b, at the unit level: the hook's matches_managed() fallback branch
    (managed file absent, so nothing to os.path.samefile against) must
    itself fold case when probe_case_insensitive says the filesystem
    folds -- os.path.normcase is a no-op on POSIX and would silently make
    this branch equivalent to plain string equality, which is the exact
    bypass this pins down. probe_case_insensitive is stubbed directly so
    each assertion is about matches_managed's own fold logic, independent
    of what any real filesystem happens to do."""

    def setUp(self):
        self.mod = _load_hook_embedded_python()
        # Neither path need exist on disk for the fallback branch: it is
        # reached precisely because os.path.exists(managed_path) is False.
        self.resolved = "/nonexistent-forge-guard-probe/docs/forge/CONSTRAINTS.MD"
        self.managed = "/nonexistent-forge-guard-probe/docs/forge/constraints.md"
        self.marker_dir = "/nonexistent-forge-guard-probe/docs/forge"

    def test_fallback_folds_case_when_filesystem_folds(self):
        self.mod["probe_case_insensitive"] = lambda marker_dir: True
        self.assertTrue(
            self.mod["matches_managed"](self.resolved, self.managed, self.marker_dir)
        )

    def test_fallback_is_exact_match_when_filesystem_does_not_fold(self):
        self.mod["probe_case_insensitive"] = lambda marker_dir: False
        self.assertFalse(
            self.mod["matches_managed"](self.resolved, self.managed, self.marker_dir)
        )


class InstallGuardsCLITests(CLITestCase):
    """``install-guards`` is the only path that ever writes a git hook or a
    CI workflow into a repo — always human-initiated, never called by any
    other forge stage (session-start, guard-memory-writes, forge_lint.py)."""

    def setUp(self):
        super().setUp()
        subprocess.run(
            ["git", "init", "--quiet"], cwd=self.tmp, check=True,
            capture_output=True,
        )
        self.hook_path = os.path.join(self.tmp, ".git", "hooks", "pre-commit")
        self.workflow_path = os.path.join(
            self.tmp, ".github", "workflows", "forge-memory-check.yml",
        )

    def test_no_flag_exits_nonzero_and_installs_nothing(self):
        code, out, err = _run_cli(["install-guards"])
        self.assertNotEqual(code, 0)
        self.assertIn("--pre-commit", err + out)
        self.assertFalse(os.path.exists(self.hook_path))
        self.assertFalse(os.path.exists(self.workflow_path))

    def test_pre_commit_writes_executable_hook(self):
        code, out, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.exists(self.hook_path))
        self.assertTrue(os.access(self.hook_path, os.X_OK))

    def test_pre_commit_hook_runs_fmt_check(self):
        # File store for deferrals so the hook never shells out to gh.
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, out + err)

        constraints_path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        _write(constraints_path, (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
        ))
        proc = subprocess.run([self.hook_path], cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        _write(constraints_path, fm.render(record))
        proc = subprocess.run([self.hook_path], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_pre_commit_hook_never_invokes_gh(self):
        # Deliberately leave the default deferral store (GitHub, no
        # config.json) selected, and prove at the process level — the same
        # way Task 5's zero-spawn test pins down a network-shaped call
        # site — that the installed hook never reaches for `gh`, even
        # though `fmt --check` with no restriction would. A stub `gh` put
        # first on PATH records whether it was ever invoked; the hook must
        # get the right exit code on both a drifted and a canonical
        # constraints.md without ever touching the stub.
        code, _, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, err)

        stub_dir = os.path.join(self.tmp, "stub-bin")
        os.makedirs(stub_dir)
        marker = os.path.join(self.tmp, "gh-was-called")
        gh_stub = os.path.join(stub_dir, "gh")
        _write(gh_stub, "#!/bin/sh\ntouch \"{}\"\nexit 1\n".format(marker))
        os.chmod(gh_stub, 0o755)
        env = dict(os.environ)
        env["PATH"] = stub_dir + os.pathsep + env.get("PATH", "")

        constraints_path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        _write(constraints_path, (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
        ))
        proc = subprocess.run(
            [self.hook_path], cwd=self.tmp, capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(
            os.path.exists(marker),
            "the pre-commit hook must never invoke gh",
        )

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        _write(constraints_path, fm.render(record))
        proc = subprocess.run(
            [self.hook_path], cwd=self.tmp, capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_pre_commit_hook_works_offline_with_no_remote_and_no_gh(self):
        # self.tmp is `git init`-ed with no remote configured (see setUp).
        # Confirm that, and additionally strip `gh` (and everything else)
        # off PATH except the directory holding the real python3, so the
        # hook cannot find a `gh` binary at all — it must still work.
        remotes = subprocess.run(
            ["git", "remote"], cwd=self.tmp, capture_output=True, text=True,
        )
        self.assertEqual(remotes.stdout.strip(), "")

        env = dict(os.environ)
        env["PATH"] = os.path.dirname(os.path.realpath(sys.executable))

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        constraints_path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        _write(constraints_path, fm.render(record))

        code, _, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, err)
        proc = subprocess.run(
            [self.hook_path], cwd=self.tmp, capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_pre_commit_refuses_to_clobber_foreign_hook(self):
        os.makedirs(os.path.dirname(self.hook_path), exist_ok=True)
        _write(self.hook_path, "#!/bin/sh\necho not forge\n")
        code, out, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertNotEqual(code, 0)
        with open(self.hook_path, encoding="utf-8") as f:
            self.assertIn("not forge", f.read())

    def test_pre_commit_reinstall_over_own_hook_is_idempotent(self):
        code, _, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, err)
        with open(self.hook_path, encoding="utf-8") as f:
            first = f.read()
        code, _, err = _run_cli(["install-guards", "--pre-commit"])
        self.assertEqual(code, 0, err)
        with open(self.hook_path, encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)

    def _make_engine_repo(self):
        """Make ``self.tmp`` look like the repo the CI workflow can actually
        run in: one where ``scripts/forge_memory.py`` *is* this engine, which
        is the exact precondition the workflow's repo-relative command needs.
        A symlink, so ``samefile`` sees one file, not a stale copy."""
        scripts_dir = os.path.join(self.tmp, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        os.symlink(
            os.path.join(SCRIPTS_DIR, "forge_memory.py"),
            os.path.join(scripts_dir, "forge_memory.py"),
        )

    def test_ci_refuses_in_a_repo_without_the_engine(self):
        # A downstream repo has no scripts/forge_memory.py, so the workflow's
        # `python3 scripts/forge_memory.py fmt --check` could only ever fail
        # "No such file". Refuse loudly and name the fix rather than install
        # a workflow that is red on its first run.
        code, out, err = _run_cli(["install-guards", "--ci"])
        self.assertEqual(code, 1, out + err)
        msg = out + err
        self.assertIn("scripts/forge_memory.py", msg)
        self.assertIn("--pre-commit", msg)
        self.assertFalse(os.path.exists(self.workflow_path))

    def test_ci_refusal_does_not_block_pre_commit_in_the_same_run(self):
        code, out, err = _run_cli(["install-guards", "--pre-commit", "--ci"])
        self.assertEqual(code, 1, out + err)
        self.assertTrue(os.path.exists(self.hook_path))
        self.assertFalse(os.path.exists(self.workflow_path))

    def test_ci_writes_workflow_and_creates_directory(self):
        self._make_engine_repo()
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".github")))
        code, out, err = _run_cli(["install-guards", "--ci"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.exists(self.workflow_path))

    def test_ci_installed_workflow_command_resolves_in_that_repo(self):
        self._make_engine_repo()
        code, out, err = _run_cli(["install-guards", "--ci"])
        self.assertEqual(code, 0, out + err)
        with open(self.workflow_path, encoding="utf-8") as f:
            workflow = f.read()
        run_line = next(
            line for line in workflow.splitlines()
            if "python3" in line and "fmt --check" in line
        )
        script_rel = run_line.split("python3", 1)[1].split()[0]
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, script_rel)),
            "workflow runs {!r}, which does not exist in the repo it was "
            "installed into".format(script_rel),
        )

    def test_ci_rerun_is_idempotent(self):
        self._make_engine_repo()
        _run_cli(["install-guards", "--ci"])
        with open(self.workflow_path, encoding="utf-8") as f:
            first = f.read()
        code, _, err = _run_cli(["install-guards", "--ci"])
        self.assertEqual(code, 0, err)
        with open(self.workflow_path, encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)

    def test_ci_workflow_source_runs_fmt_check(self):
        template_path = os.path.join(REPO_ROOT, "templates", "forge-memory-check.yml")
        with open(template_path, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("name:"))
        self.assertIn("jobs:", text)
        self.assertIn("fmt --check", text)
        self.assertNotIn("\t", text, "YAML must not contain literal tabs")
        # The check runs `fmt --check` with no paths, which (with no
        # config.json, the default) selects GitHubStore and calls `gh` to
        # read open forge:deferral issues — CI must authenticate that call
        # or the gate is always red. Explicit read-only permissions rather
        # than relying on default GITHUB_TOKEN scopes, which some orgs
        # restrict below read-all.
        self.assertIn("GH_TOKEN", text)
        self.assertIn("permissions:", text)
        self.assertIn("contents: read", text)
        self.assertIn("issues: read", text)
        # No matrix builds, caching, or extra jobs — keep it minimal.
        self.assertNotIn("matrix:", text)
        self.assertNotIn("cache", text.lower())

    _OTHER_FORGE_SOURCES = (
        os.path.join(REPO_ROOT, "hooks", "session-start"),
        os.path.join(REPO_ROOT, "hooks", "guard-memory-writes"),
        os.path.join(REPO_ROOT, "scripts", "forge_lint.py"),
    )

    # A line that mentions a subcommand *and* one of these is shelling out to
    # it; a mention with none of them nearby is advice text (the layer-1 deny
    # message names `add-constraint` on purpose) or a comment.
    _INVOCATION_MARKERS = (
        "subprocess", "Popen", "os.system", "check_call", "check_output",
        "sys.executable", "os.exec", "python3", "sh -c", "$(",
    )

    def test_install_guards_not_invoked_by_any_other_forge_stage(self):
        for path in self._OTHER_FORGE_SOURCES:
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(
                    "install-guards", f.read(),
                    "{} must never invoke install-guards — installation is "
                    "always human-initiated".format(path),
                )

    def test_no_forge_stage_writes_a_constraint_unattended(self):
        """Spec, constraint records: "Creation requires user approval. Agents
        may propose at an approval gate; no forge stage writes a constraint
        unattended." Same source scan as install-guards, over the two
        subcommands that mutate ``constraints.md``."""
        for path in self._OTHER_FORGE_SOURCES:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            for lineno, line in enumerate(lines):
                for subcommand in ("add-constraint", "retire-constraint"):
                    if subcommand not in line:
                        continue
                    window = "\n".join(lines[max(0, lineno - 3):lineno + 4])
                    for marker in self._INVOCATION_MARKERS:
                        self.assertNotIn(
                            marker, window,
                            "{}:{} looks like it invokes {} ({!r} nearby) — no "
                            "forge stage may write a constraint unattended; a "
                            "stage may only name the command as advice".format(
                                path, lineno + 1, subcommand, marker,
                            ),
                        )


class HooksJsonTests(unittest.TestCase):
    def test_hooks_json_is_valid_and_keeps_session_start(self):
        hooks_json_path = os.path.join(REPO_ROOT, "hooks", "hooks.json")
        with open(hooks_json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("SessionStart", data["hooks"])
        self.assertIn("PreToolUse", data["hooks"])
        pretooluse = data["hooks"]["PreToolUse"][0]
        self.assertEqual(pretooluse["matcher"], "Edit|Write|MultiEdit")


SCRIPT = os.path.join(SCRIPTS_DIR, "forge_memory.py")

# Probe that reproduces, in-process, exactly what `python3 forge_memory.py`
# does: load the file under the module name ``__main__``. If the module body
# runs under that name, forge_memory_store's ``import forge_memory`` loads a
# SECOND copy and every class in it (SchemaError, Record) exists twice, so the
# CLI's ``except SchemaError`` cannot catch what the store raises. Run in a
# subprocess because it replaces ``sys.modules["__main__"]``.
_MAIN_IDENTITY_PROBE = r'''
import importlib.util, sys

scripts_dir, script = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)
sys.argv = ["forge_memory.py", "list-constraints"]

spec = importlib.util.spec_from_file_location("__main__", script)
mod = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = mod
try:
    spec.loader.exec_module(mod)
except SystemExit:
    pass

import forge_memory
import forge_memory_store

dupes = [
    name for name in ("SchemaError", "Record", "SCHEMA")
    if hasattr(mod, name) and getattr(mod, name) is not getattr(forge_memory, name)
]
if dupes:
    print("DUPLICATED:" + ",".join(dupes))
elif forge_memory_store.forge_memory.SchemaError is not forge_memory.SchemaError:
    print("DUPLICATED:store")
else:
    print("SINGLE")
'''


class ScriptEntrypointIdentityTests(unittest.TestCase):
    """``forge_memory.py`` is both an importable module and the executable
    entry point the pre-commit hook and the CI workflow invoke. Running it as
    a script must not give its classes a second identity, or the CLI's
    ``except SchemaError`` stops catching what the store raises and the user
    gets a traceback instead of the named error (DECISIONS 2026-07-14: one
    ``sys.modules`` object, one class identity)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-script-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, "docs", "forge"))

    def _write_config(self, **deferrals):
        with open(os.path.join(self.tmp, "docs", "forge", "config.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"deferrals": deferrals}, f)

    def _run_script(self, argv):
        return subprocess.run(
            [sys.executable, SCRIPT] + argv, cwd=self.tmp,
            capture_output=True, text=True,
        )

    def test_store_schema_error_prints_cleanly_when_run_as_a_script(self):
        self._write_config(store="file")
        _write(
            os.path.join(self.tmp, "docs", "forge", "deferrals.md"),
            "this is not a valid record\n",
        )

        proc = self._run_script(["list-deferrals"])

        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("line 1", proc.stderr)
        self.assertIn("unparsable", proc.stderr)

    def test_fmt_write_schema_error_prints_cleanly_when_run_as_a_script(self):
        self._write_config(store="file")
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        _write(path, "this is not a valid record\n")

        proc = self._run_script(["fmt", "--write", "--local-only"])

        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)

    def test_running_as_a_script_creates_no_second_module_copy(self):
        proc = subprocess.run(
            [sys.executable, "-c", _MAIN_IDENTITY_PROBE, SCRIPTS_DIR, SCRIPT],
            cwd=self.tmp, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout.strip().splitlines()[-1], "SINGLE",
            "the classes the store raises must be the same objects the CLI "
            "catches; stderr: {}".format(proc.stderr),
        )


class FmtBranchExclusivityTests(CLITestCase):
    """``fmt``'s three branches — explicit paths, ``--local-only``, and the
    no-PATH CI branch — are mutually exclusive and exhaustive. ``--local-only``
    with explicit paths named neither: it silently took the explicit-paths
    branch. Two scope selectors at once is a user error with no correct
    reading, so it is rejected by name rather than resolved by accident."""

    def test_local_only_with_explicit_paths_is_rejected(self):
        path = os.path.join(self.tmp, "constraints.md")
        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "because": "b", "scope": "repo",
            "added": "2026-09-05", "source": "user",
        })
        _write(path, fm.render(record))

        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check", "--local-only", path])

        self.assertNotEqual(code, 0)
        self.assertIn("--local-only", err)
        self.assertEqual(out, "")

    def test_local_only_write_with_explicit_paths_does_not_write(self):
        path = os.path.join(self.tmp, "constraints.md")
        drifted = "## good-id\n**Because:** b\n**Rule:** r\n**Scope:** repo\n" \
                  "**Added:** 2026-09-05\n**Source:** user\n"
        _write(path, drifted)

        with self._no_gh_guard():
            code, _, err = _run_cli(["fmt", "--write", "--local-only", path])

        self.assertNotEqual(code, 0)
        self.assertIn("--local-only", err)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), drifted)

    def test_local_only_checks_managed_files_without_gh(self):
        self._use_file_store_for_deferrals()
        _write(os.path.join(self.tmp, "docs", "forge", "constraints.md"), (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Added:** 2026-09-05\n"
            "**Source:** user\n"
        ))
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check", "--local-only"])
        self.assertNotEqual(code, 0)
        self.assertIn("bad id", out)

    def test_local_only_never_reaches_gh_under_the_github_store(self):
        # No config.json: the GitHub store is selected for deferrals, and
        # --local-only must still make no gh call at all.
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check", "--local-only"])
        self.assertEqual(code, 0, out + err)


class ManagedPathsSharedHelperTests(unittest.TestCase):
    """One definition of "what are the managed local paths", called by both
    ``forge_memory``'s fmt and ``forge_lint.check_memory_files`` — a third
    managed file must be impossible to add to one and forget in the other."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-managed-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, "docs", "forge"))

    def test_forge_lint_and_fmt_read_the_same_helper(self):
        import forge_lint

        self.assertIs(
            forge_lint.forge_memory_store.managed_paths, fms.managed_paths
        )
        source = inspect.getsource(forge_lint.check_memory_files)
        self.assertIn("managed_paths", source)
        self.assertIn("managed_paths", inspect.getsource(fm.cmd_fmt))

    def test_managed_paths_includes_constraints_and_file_backed_deferrals(self):
        constraints = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        deferrals = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        _write(constraints, "")
        _write(deferrals, "")
        with open(os.path.join(self.tmp, "docs", "forge", "config.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"deferrals": {"store": "file"}}, f)

        self.assertEqual(
            fms.managed_paths(self.tmp), [constraints, deferrals],
        )

    def test_managed_paths_omits_deferrals_under_the_github_store(self):
        constraints = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        _write(constraints, "")
        _write(os.path.join(self.tmp, "docs", "forge", "deferrals.md"), "")

        self.assertEqual(fms.managed_paths(self.tmp), [constraints])

    def test_managed_paths_omits_absent_files(self):
        self.assertEqual(fms.managed_paths(self.tmp), [])



class SingleLineFieldTests(unittest.TestCase):
    """A newline (or any other control character) in a field value renders a
    record whose own parser cannot read it back: the CLI would exit 0, write
    the file, and every later add/list/retire/fmt on that file would fail
    "unparsable" — with layer 1 denying the direct edit needed to repair it.
    ``validate`` rejects it as a budget-class error naming the field, so the
    CLI can never emit a record ``parse`` chokes on."""

    def _defects_with(self, record_type, fields_fn, field_name, value):
        record = fm.Record(type=record_type, fields=fields_fn(**{field_name: value}))
        return fm.validate(record)

    def test_newline_rejected_in_every_constraint_field(self):
        for name in [spec.name for spec in fm.SCHEMA["constraint"]]:
            with self.subTest(field=name):
                defects = self._defects_with(
                    "constraint", _valid_constraint_fields, name, "a\nb",
                )
                self.assertTrue(
                    any(name in d and "single line" in d for d in defects),
                    "no single-line defect naming {!r}: {}".format(name, defects),
                )

    def test_newline_rejected_in_every_deferral_field(self):
        for name in [spec.name for spec in fm.SCHEMA["deferral"]]:
            with self.subTest(field=name):
                fields = _valid_deferral_fields()
                fields[name] = "a\nb"
                if name == "follow-up":
                    fields[name] = "revisit-when:a\nb"
                record = fm.Record(type="deferral", fields=fields)
                defects = fm.validate(record)
                self.assertTrue(
                    any(name in d and "single line" in d for d in defects),
                    "no single-line defect naming {!r}: {}".format(name, defects),
                )

    def test_carriage_return_and_tab_rejected(self):
        for value in ("a\rb", "a\tb", "a\x00b", "a\x1bb"):
            with self.subTest(value=value):
                defects = self._defects_with(
                    "constraint", _valid_constraint_fields, "rule", value,
                )
                self.assertTrue(
                    any("rule" in d and "single line" in d for d in defects),
                    defects,
                )

    def test_ordinary_punctuation_still_accepted(self):
        # The check is control characters only — colons, asterisks, markdown
        # and unicode in a value are all legal and must not be rejected.
        defects = self._defects_with(
            "constraint", _valid_constraint_fields, "rule",
            "Never use **eval**: not even for “safe” input — ever.",
        )
        self.assertEqual(defects, [])

    def test_every_validated_record_round_trips_through_parse(self):
        """Property: whatever ``validate`` accepts, ``render`` writes and
        ``parse`` reads back unchanged. This is the invariant F1 broke."""
        candidates = [
            ("constraint", _valid_constraint_fields()),
            ("constraint", _valid_constraint_fields(
                rule="Never use **eval**: ## not a heading, either.")),
            ("constraint", _valid_constraint_fields(
                because="Because: **Rule:** looks like a field label.")),
            ("constraint", _valid_constraint_fields(scope="")),
            ("constraint", _valid_constraint_fields(scope="  spaced  ")),
            ("constraint", _valid_constraint_fields(rule="x" * 200)),
            ("deferral", _valid_deferral_fields()),
            ("deferral", _valid_deferral_fields(title="## heading-shaped title")),
            ("deferral", _valid_deferral_fields(why="Why: **not** a label.")),
            ("deferral", dict(_valid_deferral_fields(),
                              **{"follow-up": "revisit-when: the *audit* lands"})),
        ]
        for record_type, fields in candidates:
            with self.subTest(fields=fields):
                record = fm.Record(type=record_type, fields=fields)
                self.assertEqual(
                    fm.validate(record), [],
                    "candidate must be valid for the property to apply",
                )
                text = fm.render(record)
                parsed = fm.parse(text, record_type)
                self.assertEqual(len(parsed), 1)
                self.assertEqual(fm.render(parsed[0]), text)


class NewlineArgumentCLITests(CLITestCase):
    """The CLI must never write a record its own parser cannot read: a
    newline in any text argument is rejected before the store is touched,
    leaving no file behind to brick."""

    def test_add_constraint_with_newline_in_rule_is_rejected(self):
        code, out, err = _run_cli([
            "add-constraint", "--id", "multi-line",
            "--rule", "first line\nsecond line",
            "--because", "Because.",
        ])
        self.assertEqual(code, 1)
        self.assertIn("rule", err)
        self.assertIn("single line", err)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "docs", "forge", "constraints.md")),
            "a rejected record must not have been written",
        )

    def test_add_constraint_newline_in_because_leaves_file_parsable(self):
        code, _, _ = _run_cli([
            "add-constraint", "--id", "good-one", "--rule", "A rule.",
            "--because", "A reason.",
        ])
        self.assertEqual(code, 0)
        code, out, err = _run_cli([
            "add-constraint", "--id", "bad-one", "--rule", "A rule.",
            "--because", "line one\nline two",
        ])
        self.assertEqual(code, 1)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        code, out, err = _run_cli(["fmt", "--check", path])
        self.assertEqual(code, 0, out + err)

    def test_defer_with_newline_in_why_is_rejected_before_gh(self):
        self._use_file_store_for_deferrals()
        with self._no_gh_guard():
            code, out, err = _run_cli([
                "defer", "--title", "Something", "--why", "one\ntwo",
                "--follow-up", "backlog",
            ])
        self.assertEqual(code, 1)
        self.assertIn("why", err)
        self.assertIn("single line", err)


class MalformedConfigCLITests(CLITestCase):
    """A malformed ``docs/forge/config.json`` is a named error, never a raw
    traceback: ``select_store`` sits inside the same try/except as the store
    call it feeds — in every command, not only the ones that read config
    today."""

    def setUp(self):
        super().setUp()
        _write(os.path.join(self.tmp, "docs", "forge", "config.json"), "{not json")

    def _assert_named(self, argv):
        try:
            code, out, err = _run_cli(argv)
        except Exception as e:  # noqa: BLE001 — a traceback escaping is the bug
            self.fail("{} raised {!r} instead of failing loud".format(argv, e))
        self.assertEqual(code, 1, out + err)
        self.assertIn("config.json", err)

    def test_defer(self):
        self._assert_named([
            "defer", "--title", "t", "--why", "w", "--follow-up", "backlog",
        ])

    def test_list_deferrals(self):
        self._assert_named(["list-deferrals"])

    def test_resolve_deferral(self):
        self._assert_named(["resolve-deferral", "--ref", "7", "--reason", "done"])

    def test_fmt_still_names_the_file(self):
        code, out, err = _run_cli(["fmt", "--check"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("config.json", err)

    def test_every_command_selects_its_store_inside_the_try(self):
        """Constraint commands are hardwired to the file store today, so a
        malformed config cannot reach them — but the ConfigError-catching
        try/except must still enclose their ``select_store`` call, or the day
        store selection grows a config read they regress to a traceback."""
        commands = [
            fm.cmd_add_constraint, fm.cmd_retire_constraint,
            fm.cmd_list_constraints, fm.cmd_defer, fm.cmd_list_deferrals,
            fm.cmd_resolve_deferral,
        ]
        for func in commands:
            with self.subTest(command=func.__name__):
                lines = inspect.getsource(func).splitlines()
                select = next(
                    i for i, line in enumerate(lines) if "select_store" in line
                )
                preceding = [line.strip() for line in lines[:select]]
                self.assertIn(
                    "try:", preceding,
                    "{}: select_store is outside the try/except that catches "
                    "ConfigError".format(func.__name__),
                )
                self.assertIn(
                    "fms.ConfigError",
                    "\n".join(lines[select:]),
                )


class MissingPathFmtTests(CLITestCase):
    """``fmt`` on a path that does not exist names the missing file, the same
    way every other bad input here does — not an uncaught FileNotFoundError."""

    def test_check_missing_path_names_it(self):
        missing = os.path.join(self.tmp, "docs", "forge", "nope-constraints.md")
        code, out, err = _run_cli(["fmt", "--check", missing])
        self.assertEqual(code, 1)
        self.assertIn("nope-constraints.md", out + err)

    def test_write_missing_path_names_it(self):
        missing = os.path.join(self.tmp, "docs", "forge", "nope-deferrals.md")
        code, out, err = _run_cli(["fmt", "--write", missing])
        self.assertEqual(code, 1)
        self.assertIn("nope-deferrals.md", out + err)

    def test_fmt_check_raises_schemaerror_directly(self):
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.fmt_check([os.path.join(self.tmp, "gone-constraints.md")])
        self.assertIn("gone-constraints.md", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
