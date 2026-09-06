"""forge_memory: budgets are enforced as hard character-count errors (not just
structural checks) so prose-expansion drift inside a valid field is caught;
`validate`/`fmt_check` report every defect in one pass, never just the first;
`render` is the sole source of record text and `parse` is its exact inverse,
including on a file a human hand-drifted (reordered fields) but that still
parses; and unparsable or budget-violating input fails loud naming the line."""
import argparse
import contextlib
import dataclasses
import inspect
import io
import json
import os
import re
import shlex
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

from _forge_support import forge_run  # noqa: E402
import forge_memory as fm  # noqa: E402
import forge_status  # noqa: E402
import forge_memory_store as fms  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _valid_constraint_fields(**overrides):
    fields = {
        "id": "no-eval-in-hooks",
        "rule": "Hooks must never call eval on untrusted input.",
        "scope": "hooks/",
        "because": "Untrusted input reaching eval is an injection vector.",
        "source": "issue-42",
    }
    fields.update(overrides)
    return fields


def _valid_deferral_fields(**overrides):
    fields = {
        "title": "Improve error messages in the deferral formatter",
        "why": "Nice-to-have polish, not required by the current spec.",
        "from": "user",
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

    def test_source_is_required(self):
        fields = _valid_constraint_fields()
        del fields["source"]
        record = fm.Record(type="constraint", fields=fields)
        defects = fm.validate(record)
        self.assertTrue(any("source" in d for d in defects), defects)

    def test_scope_is_required(self):
        fields = _valid_constraint_fields()
        del fields["scope"]
        record = fm.Record(type="constraint", fields=fields)
        defects = fm.validate(record)
        self.assertTrue(any("scope" in d for d in defects), defects)

    def test_scope_budget_rejected_one_over(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            scope="x" * 81,
        ))
        defects = fm.validate(record)
        matches = [d for d in defects if "scope" in d and "80" in d]
        self.assertEqual(len(matches), 1, defects)

    def test_scope_budget_accepted_at_exact_limit(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            scope="x" * 80,
        ))
        defects = fm.validate(record)
        self.assertEqual([d for d in defects if "scope" in d], [])

    def test_source_budget_rejected_one_over(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            source="x" * 121,
        ))
        defects = fm.validate(record)
        matches = [d for d in defects if "source" in d and "120" in d]
        self.assertEqual(len(matches), 1, defects)

    def test_source_budget_accepted_at_exact_limit(self):
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            source="x" * 120,
        ))
        defects = fm.validate(record)
        self.assertEqual([d for d in defects if "source" in d], [])

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

    def test_constraint_record_is_id_rule_scope_because_source(self):
        # `added` is retired: it served retirement deliberation, and
        # constraints.md holds only what is currently true — there is no
        # deliberation to support. `source` stays: it's read-time material,
        # since a constraint written in one phase may be applied in another.
        self.assertEqual(
            [spec.name for spec in fm.SCHEMA["constraint"]],
            ["id", "rule", "scope", "because", "source"],
        )
        self.assertNotIn("_today_iso", dir(fm))

    def test_deferral_record_is_title_why_from(self):
        # `follow-up` is retired. A deferral IS an open issue nobody is
        # working on, so `backlog` only restated the record's own
        # existence; `drop` meant "do not file this at all", which is a
        # decision taken at the review gate BEFORE the record exists; and
        # `revisit-when:<condition>` is a comment on the issue. The field
        # bought nothing and forced an unbounded condition string into a
        # bounded label set.
        self.assertEqual(
            [spec.name for spec in fm.SCHEMA["deferral"]],
            ["title", "why", "from"],
        )
        self.assertEqual(
            [spec.form for spec in fm.SCHEMA["deferral"]],
            [None, None, None],
        )
        self.assertNotIn("_FOLLOWUP_RE", inspect.getsource(fm))

    def test_a_stray_followup_key_is_never_rendered(self):
        # render/parse are SCHEMA-driven, so a caller that still passes the
        # retired key writes a record without it rather than a record the
        # parser would reject as an unknown field.
        record = fm.Record(type="deferral", fields=dict(
            _valid_deferral_fields(), **{"follow-up": "backlog"},
        ))
        self.assertNotIn("Follow-up", fm.render(record))
        self.assertEqual(
            fm.parse(fm.render(record), "deferral")[0].fields,
            _valid_deferral_fields(),
        )

    def test_multiple_defects_all_reported(self):
        fields = _valid_deferral_fields(title="x" * 81, why="")
        del fields["from"]
        record = fm.Record(type="deferral", fields=fields)
        defects = fm.validate(record)
        self.assertTrue(any("title" in d for d in defects), defects)
        self.assertTrue(any("why" in d for d in defects), defects)
        self.assertTrue(any("from" in d for d in defects), defects)
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
            "**Scope:** hooks/\n"
            "**Because:** Reason one.\n"
            "**Source:** issue-42\n"
            "\n"
            "## no-eval-in-hooks\n"
            "**Rule:** A different rule text.\n"
            "**Scope:** repo\n"
            "**Because:** Reason two.\n"
            "**Source:** issue-43\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_added_field_is_removed_from_schema(self):
        # `added` served retirement deliberation, and there is none: a
        # constraint that stops being true is deleted, not annotated. A
        # record carrying an `**Added:**` line is now simply unparsable.
        self.assertNotIn("added", [spec.name for spec in fm.SCHEMA["constraint"]])
        text = (
            "## no-eval-in-hooks\n"
            "**Rule:** Hooks must never call eval.\n"
            "**Scope:** hooks/\n"
            "**Because:** Reason one.\n"
            "**Added:** 2026-09-05\n"
            "**Source:** issue-42\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertIn("added", str(ctx.exception).lower())


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
            "**Scope:** repo\n"
            "**Because:** b\n"
            "**Source:** user\n"
            "\n"
            "## dup\n"
            "**Rule:** r2\n"
            "**Scope:** repo\n"
            "**Because:** b2\n"
            "**Source:** user\n"
        )
        with self.assertRaises(fm.SchemaError) as ctx:
            fm.parse(text, "constraint")
        self.assertEqual(ctx.exception.line, 7)

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


class RecordShapeTests(unittest.TestCase):
    """A ``Record`` is its type and its fields, and nothing else. ``ref``
    carried a store-assigned issue number for a record read back from
    GitHub; ``GitHubStore.scan`` was its only writer, and the GitHub read
    path is gone. A field nothing can set is what misleads the next
    reader — and ``field(compare=False, repr=False)``, which existed so a
    ref could not affect record equality, guards nothing once no ref
    exists."""

    def test_record_has_no_ref_field(self):
        self.assertEqual(
            [f.name for f in dataclasses.fields(fm.Record)],
            ["type", "fields"],
        )
        with self.assertRaises(TypeError):
            fm.Record(type="deferral", fields=_valid_deferral_fields(), ref="7")

    def test_records_with_equal_fields_compare_equal(self):
        # The property the ref exclusion existed to protect. It now holds
        # by construction rather than by a dataclass flag.
        fields = _valid_deferral_fields()
        self.assertEqual(
            fm.Record(type="deferral", fields=dict(fields)),
            fm.Record(type="deferral", fields=dict(fields)),
        )

    def test_render_parse_round_trip_is_unaffected(self):
        record = fm.Record(type="deferral", fields=_valid_deferral_fields())
        text = fm.render(record)
        reparsed = fm.parse(text, "deferral")[0]
        self.assertEqual(reparsed, record)
        self.assertEqual(fm.render(reparsed), text)
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
            "**Scope:** hooks/\n"
            "**Because:** Untrusted input reaching eval is an injection vector.\n"
            "**Source:** issue-42\n"
        ).format("x" * 201))
        with self.assertRaises(fm.SchemaError):
            fm.fmt_write([path])


def _gh_label_preflight(args):
    """The label step ``GitHubStore.create`` runs before every filing:
    ``gh label list --json name`` to decide whether the origin label
    exists, then ``gh label create`` only when it does not. A stub that
    answers just ``auth`` and ``issue create`` starves that step — it would
    hand the label query an issue URL, which is not the JSON the store
    asked for. Returns a stubbed result for a label call, or None when
    ``args`` is not one, so a caller can fall through to its own answer."""
    if args[:3] == ["gh", "label", "list"]:
        return _completed(returncode=0, stdout="[]")
    if args[:3] == ["gh", "label", "create"]:
        return _completed(returncode=0)
    return None


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
            "add-constraint", "update-constraint", "retire-constraint",
            "list-constraints", "defer", "resolve", "fmt",
            "install-guards", "add-program", "add-phase", "audit-issues",
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
        "add-constraint": {"id", "rule", "because", "scope", "source"},
        "update-constraint": {"id", "rule", "because", "scope", "source"},
        "retire-constraint": {"id"},
        "list-constraints": {"scope", "json"},
        # --occurrence added deliberately (typed, closed-vocabulary
        # selector: a 1-based ordinal among the staged deferrals sharing a
        # finding id), never a free-form text surface.
        # --by is a closed two-value origin selector (human|agent), not a
        # free-form field; --occurrence is a 1-based ordinal among the
        # staged deferrals sharing a finding id. Both typed, both added
        # deliberately.
        "defer": {"title", "why", "by", "from_", "run", "finding_id",
                  "occurrence"},
        "resolve": {"ref", "reason"},
        "fmt": {"check", "write", "paths"},
        "install-guards": {"pre_commit", "ci"},
        # No --by on either: origin is by:human unconditionally for both
        # (a decomposition exists only because a human approved it at a
        # brainstorming gate), so there is no flag to guess wrong.
        "add-program": {"name", "why", "kind"},
        "add-phase": {"epic", "seq", "of", "title", "why", "kind"},
        # audit-issues takes no flags: it walks every open issue, no
        # selector to narrow or guess at.
        "audit-issues": set(),
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
    def test_composes_canonical_record_defaulting_scope(self):
        code, out, err = _run_cli([
            "add-constraint", "--id", "no-eval-in-hooks",
            "--rule", "Hooks must never call eval on untrusted input.",
            "--because", "Untrusted input reaching eval is an injection vector.",
            "--source", "issue-42",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        records = fm.parse(text, "constraint")
        self.assertEqual(len(records), 1)
        self.assertNotIn("added", records[0].fields)
        self.assertEqual(records[0].fields["source"], "issue-42")
        self.assertEqual(records[0].fields["scope"], "repo")

    def test_added_is_not_a_settable_flag(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
                "--source", "issue-1", "--added", "2020-01-01",
            ])

    def test_source_is_required(self):
        # No default exists — a placeholder like "user" would point
        # nowhere, defeating the field's purpose (following a constraint
        # back to the spec/PR/issue that motivated it). Missing --source
        # must fail at argparse, before cmd_add_constraint ever runs.
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            ])

    def test_issue_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
                "--source", "issue-7", "--issue", "7",
            ])

    def test_spec_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
                "--source", "spec:docs/spec.md", "--spec", "docs/spec.md",
            ])

    def test_source_accepts_an_issue_style_ref_and_round_trips_through_update(self):
        code, _, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            "--source", "issue-7",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(record.fields["source"], "issue-7")

        code, _, err = _run_cli([
            "update-constraint", "--id", "x", "--source", "issue-9",
        ])
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(record.fields["source"], "issue-9")

    def test_source_accepts_a_pr_style_ref_and_round_trips_through_update(self):
        # source is "issue, PR, or spec path" — --issue/--spec (Phase 1
        # machinery for a machine-set field) could never express a PR at
        # all; a plain --source string can.
        code, _, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            "--source", "PR #38",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(record.fields["source"], "PR #38")

        code, _, err = _run_cli([
            "update-constraint", "--id", "x", "--source", "PR #41",
        ])
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(record.fields["source"], "PR #41")

    def test_source_accepts_a_spec_path_and_round_trips_through_update(self):
        code, _, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            "--source", "docs/forge/specs/no-eval.md",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(record.fields["source"], "docs/forge/specs/no-eval.md")

        code, _, err = _run_cli([
            "update-constraint", "--id", "x",
            "--source", "docs/forge/specs/no-eval-v2.md",
        ])
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "constraint")[0]
        self.assertEqual(
            record.fields["source"], "docs/forge/specs/no-eval-v2.md",
        )

    def test_budget_overrun_exits_nonzero_naming_field_and_limit_on_stderr(self):
        code, out, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "x" * 201, "--because", "b",
            "--source", "issue-1",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("rule", err)
        self.assertIn("200", err)

    def test_constraints_md_created_on_first_add_not_scaffolded_before(self):
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        self.assertFalse(os.path.exists(path))
        code, _, err = _run_cli([
            "add-constraint", "--id", "x", "--rule", "r", "--because", "b",
            "--source", "issue-1",
        ])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(path))

    def test_duplicate_id_exits_nonzero(self):
        code, _, _ = _run_cli([
            "add-constraint", "--id", "dup", "--rule", "r", "--because", "b",
            "--source", "issue-1",
        ])
        self.assertEqual(code, 0)
        code, out, err = _run_cli([
            "add-constraint", "--id", "dup", "--rule", "r2", "--because", "b2",
            "--source", "issue-1",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("dup", err)


class UpdateConstraintCLITests(CLITestCase):
    def _add(self, id="to-update", rule="r", because="b", scope="repo",
             source="issue-1"):
        code, _, err = _run_cli([
            "add-constraint", "--id", id, "--rule", rule, "--because", because,
            "--scope", scope, "--source", source,
        ])
        self.assertEqual(code, 0, err)

    def _read(self):
        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_updates_only_the_named_fields(self):
        self._add()
        before = fm.parse(self._read(), "constraint")[0]

        code, _, err = _run_cli([
            "update-constraint", "--id", "to-update", "--rule", "new rule text",
        ])
        self.assertEqual(code, 0, err)

        after = fm.parse(self._read(), "constraint")[0]
        self.assertEqual(after.fields["rule"], "new rule text")
        self.assertEqual(after.fields["because"], before.fields["because"])
        self.assertEqual(after.fields["scope"], before.fields["scope"])
        self.assertEqual(after.fields["source"], before.fields["source"])

    def test_unnamed_fields_survive_byte_identical(self):
        self._add()
        code, _, err = _run_cli([
            "update-constraint", "--id", "to-update", "--because", "new because",
        ])
        self.assertEqual(code, 0, err)
        record = fm.parse(self._read(), "constraint")[0]
        self.assertEqual(record.fields["rule"], "r")
        self.assertEqual(record.fields["scope"], "repo")
        self.assertEqual(record.fields["source"], "issue-1")

    def test_no_field_flags_exits_nonzero(self):
        self._add()
        before = self._read()
        code, _, err = _run_cli(["update-constraint", "--id", "to-update"])
        self.assertNotEqual(code, 0)
        self.assertEqual(self._read(), before)

    def test_unknown_id_exits_nonzero_naming_id_file_untouched(self):
        self._add()
        before = self._read()
        code, out, err = _run_cli([
            "update-constraint", "--id", "no-such-id", "--rule", "x",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("no-such-id", err)
        self.assertEqual(self._read(), before)

    def test_id_is_not_a_settable_flag(self):
        parser = fm.build_parser()
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse.Action) and a.choices
        )
        dests = {
            a.dest for a in sub_action.choices["update-constraint"]._actions
            if a.dest != "help"
        }
        # --id is the selector, not a content field: there is no flag that
        # could rename a constraint's id, since a rename breaks every
        # citation pointing at the old one.
        self.assertEqual(dests, {"id", "rule", "because", "scope", "source"})

    def test_budget_overrun_leaves_file_byte_identical(self):
        self._add()
        before = self._read()
        code, out, err = _run_cli([
            "update-constraint", "--id", "to-update", "--rule", "x" * 201,
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("rule", err)
        self.assertIn("200", err)
        self.assertEqual(self._read(), before)


class ConstraintSoftCapTests(CLITestCase):
    def _add(self, id):
        code, _, err = _run_cli([
            "add-constraint", "--id", id, "--rule", "r", "--because", "b",
            "--source", "issue-1",
        ])
        self.assertEqual(code, 0, err)

    def test_no_notice_at_or_under_twelve(self):
        out = ""
        for n in range(12):
            code, out, err = _run_cli([
                "add-constraint", "--id", "c{}".format(n),
                "--rule", "r", "--because", "b", "--source", "issue-1",
            ])
            self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "")

    def test_thirteenth_succeeds_with_a_notice_listing_the_current_set(self):
        # The spec's soft cap says the note "lists the current set", not just
        # the count. A bare count is not actionable: deciding whether a rule
        # has stopped being true means looking at WHICH rules are there, and
        # a reader who has to run list-constraints to find out will not.
        for n in range(12):
            self._add("c{}".format(n))
        code, out, err = _run_cli([
            "add-constraint", "--id", "c12", "--rule", "r", "--because", "b",
            "--source", "issue-1",
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("13", out)
        for n in range(13):
            self.assertIn("c{}".format(n), out)

        path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        with open(path, encoding="utf-8") as f:
            records = fm.parse(f.read(), "constraint")
        self.assertEqual(len(records), 13)


class RetireConstraintCLITests(CLITestCase):
    def test_unknown_slug_exits_nonzero(self):
        code, out, err = _run_cli(["retire-constraint", "--id", "no-such-id"])
        self.assertNotEqual(code, 0)
        self.assertIn("no-such-id", err)

    def test_retire_removes_record(self):
        _run_cli([
            "add-constraint", "--id", "to-retire", "--rule", "r", "--because", "b",
            "--source", "issue-1",
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
            "--scope", "hooks/", "--source", "issue-1",
        ])
        code, out, err = _run_cli(["list-constraints", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "a")

    def test_human_output_is_not_json(self):
        _run_cli([
            "add-constraint", "--id", "a", "--rule", "r", "--because", "b",
            "--source", "issue-1",
        ])
        code, out, err = _run_cli(["list-constraints"])
        self.assertEqual(code, 0, err)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)
        self.assertIn("a", out)

    def test_scope_filter(self):
        _run_cli([
            "add-constraint", "--id", "a", "--rule", "r", "--because", "b",
            "--scope", "hooks/", "--source", "issue-1",
        ])
        _run_cli([
            "add-constraint", "--id", "b", "--rule", "r", "--because", "b",
            "--scope", "scripts/", "--source", "issue-1",
        ])
        code, out, err = _run_cli(["list-constraints", "--json", "--scope", "hooks/"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual([d["id"] for d in data], ["a"])


class DeferCLITests(CLITestCase):
    def test_by_is_required(self):
        # Origin is one of exactly two human-meaningful values and the
        # engine cannot infer it, so it is never defaulted: a filing that
        # guessed would put a wrong `by:` label on a real issue.
        self._use_file_store_for_deferrals()
        with self.assertRaises(SystemExit):
            fm.main(["defer", "--title", "t", "--why", "w"])

    def test_by_rejects_an_unknown_origin(self):
        self._use_file_store_for_deferrals()
        with self.assertRaises(SystemExit):
            fm.main([
                "defer", "--title", "t", "--why", "w", "--by", "robot",
            ])

    def test_follow_up_flag_is_gone(self):
        self._use_file_store_for_deferrals()
        with self.assertRaises(SystemExit):
            fm.main([
                "defer", "--title", "t", "--why", "w", "--by", "human",
                "--follow-up", "backlog",
            ])

    def test_defer_against_file_store(self):
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli([
            "defer", "--title", "improve-x", "--why", "polish", "--by", "human",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        records = fm.parse(text, "deferral")
        self.assertEqual(records[0].fields["from"], "user")
        self.assertEqual(
            set(records[0].fields), {"title", "why", "from"},
        )

    def test_defer_against_github_store_invokes_gh(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            label = _gh_label_preflight(args)
            if label is not None:
                return label
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/9\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "defer", "--title", "improve-x", "--why", "polish",
                "--by", "agent",
            ])
        self.assertEqual(code, 0, err)
        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:agent"])
        self.assertFalse(
            [a for a in create if a.startswith("forge:")],
            "no forge: label survives the label rework: {}".format(create),
        )

    def test_resolve_deferral_against_file_store(self):
        self._use_file_store_for_deferrals()
        _run_cli([
            "defer", "--title", "improve-x", "--why", "polish", "--by", "human",
        ])
        code, out, err = _run_cli([
            "resolve", "--ref", "improve-x", "--reason", "done",
        ])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("improve-x", text)


class AddProgramCLITests(CLITestCase):
    def test_prints_issue_number_alone_on_last_line_and_applies_labels(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            label = _gh_label_preflight(args)
            if label is not None:
                return label
            return _completed(
                returncode=0, stdout="https://github.com/o/r/issues/42\n",
            )

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-program", "--name", "Structured memory",
                "--why", "Track work across phases.", "--kind", "feature",
            ])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip().splitlines()[-1], "42")

        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        title_idx = create.index("--title")
        self.assertEqual(create[title_idx + 1], "Structured memory")
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:human", "feature"])

    def test_missing_kind_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-program", "--name", "x", "--why", "y",
            ])

    def test_no_by_flag(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-program", "--name", "x", "--why", "y", "--kind", "feature",
                "--by", "human",
            ])

    def test_overbudget_name_rejected_before_any_network_call(self):
        with mock.patch.object(
            fms.subprocess, "run",
            side_effect=AssertionError("gh must not be invoked"),
        ):
            code, out, err = _run_cli([
                "add-program", "--name", "x" * 81, "--why", "y",
                "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("name", err)

    def test_overbudget_why_rejected_before_any_network_call(self):
        with mock.patch.object(
            fms.subprocess, "run",
            side_effect=AssertionError("gh must not be invoked"),
        ):
            code, out, err = _run_cli([
                "add-program", "--name", "x", "--why", "y" * 301,
                "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("why", err)


def _phase_gh_fake_run(calls, epic, program_title, sub_issues,
                        new_issue_number, attach_fails=False, block_fails=False):
    """A ``subprocess.run`` stub covering everything ``add-phase`` calls
    through ``gh``: auth, label preflight, the epic's sub-issues and title
    reads, issue creation, and the two hierarchy-edge POSTs (each of which
    first resolves an issue number to its REST id via a GET)."""
    epic_issue_path = "repos/{owner}/{repo}/issues/" + str(epic)
    new_issue_path = "repos/{owner}/{repo}/issues/" + str(new_issue_number)

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["gh", "auth"]:
            return _completed(returncode=0, stdout="Logged in")
        label = _gh_label_preflight(args)
        if label is not None:
            return label
        if args[:3] == ["gh", "api", epic_issue_path + "/sub_issues"] and "--method" not in args:
            return _completed(returncode=0, stdout=json.dumps(sub_issues))
        if args[:3] == ["gh", "api", epic_issue_path] and "--method" not in args:
            return _completed(
                returncode=0,
                stdout=json.dumps({"id": 1000 + epic, "title": program_title}),
            )
        if args[:2] == ["gh", "issue"] and "create" in args:
            return _completed(
                returncode=0,
                stdout="https://github.com/o/r/issues/{}\n".format(new_issue_number),
            )
        if args[:3] == ["gh", "api", epic_issue_path + "/sub_issues"] and "--method" in args:
            return (
                _completed(returncode=1, stderr="boom") if attach_fails
                else _completed(returncode=0, stdout="{}")
            )
        if (args[:3] == ["gh", "api", new_issue_path + "/dependencies/blocked_by"]
                and "--method" in args):
            return (
                _completed(returncode=1, stderr="boom") if block_fails
                else _completed(returncode=0, stdout="{}")
            )
        if args[:3] == ["gh", "api", new_issue_path] and "--method" not in args:
            return _completed(
                returncode=0,
                stdout=json.dumps({"id": 2000 + new_issue_number, "title": "n/a"}),
            )
        for entry in sub_issues:
            blocker_path = "repos/{owner}/{repo}/issues/" + str(entry["number"])
            if args[:3] == ["gh", "api", blocker_path] and "--method" not in args:
                return _completed(
                    returncode=0,
                    stdout=json.dumps({"id": 3000 + entry["number"], "title": "n/a"}),
                )
        raise AssertionError("unexpected gh invocation: {}".format(args))

    return fake_run


class AddPhaseCLITests(CLITestCase):
    def test_seq_1_renders_title_and_files_no_blocked_by_edge(self):
        calls = []
        fake_run = _phase_gh_fake_run(
            calls, epic=5, program_title="Structured memory",
            sub_issues=[], new_issue_number=20,
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "Read path removal", "--why", "w", "--kind", "feature",
            ])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip().splitlines()[-1], "20")

        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        title_idx = create.index("--title")
        self.assertEqual(
            create[title_idx + 1], "Structured memory 1/5: Read path removal",
        )
        self.assertFalse(
            [a for a in calls if "dependencies/blocked_by" in " ".join(a)],
            "seq 1 must file no blocked-by edge",
        )

    def test_seq_2_blocks_on_the_epics_last_sub_issue(self):
        calls = []
        fake_run = _phase_gh_fake_run(
            calls, epic=5, program_title="Structured memory",
            sub_issues=[{"number": 20}], new_issue_number=21,
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "2", "--of", "5",
                "--title", "Second phase", "--why", "w", "--kind", "feature",
            ])
        self.assertEqual(code, 0, err)
        block_post = next(
            a for a in calls if "dependencies/blocked_by" in " ".join(a)
        )
        self.assertIn("issue_id=3020", block_post)

    def test_seq_mismatch_exits_nonzero_naming_both_numbers_creates_no_issue(self):
        calls = []
        fake_run = _phase_gh_fake_run(
            calls, epic=5, program_title="Structured memory",
            sub_issues=[{"number": 20}], new_issue_number=21,
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "9", "--of", "5",
                "--title", "Second phase", "--why", "w", "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("9", err)
        self.assertIn("2", err)
        self.assertFalse(
            [a for a in calls if a[:2] == ["gh", "issue"] and "create" in a],
            "a seq mismatch must create no issue",
        )

    def test_missing_kind_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "t", "--why", "w",
            ])

    def test_no_by_flag(self):
        with self.assertRaises(SystemExit):
            fm.main([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "t", "--why", "w", "--kind", "feature",
                "--by", "human",
            ])

    def test_overbudget_title_rejected_before_any_network_call(self):
        with mock.patch.object(
            fms.subprocess, "run",
            side_effect=AssertionError("gh must not be invoked"),
        ):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "x" * 81, "--why", "w", "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("title", err)

    def test_overbudget_why_rejected_before_any_network_call(self):
        with mock.patch.object(
            fms.subprocess, "run",
            side_effect=AssertionError("gh must not be invoked"),
        ):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "t", "--why", "y" * 301, "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("why", err)

    def test_failing_attach_sub_issue_after_creation_reports_created_number(self):
        calls = []
        fake_run = _phase_gh_fake_run(
            calls, epic=5, program_title="Structured memory",
            sub_issues=[], new_issue_number=20, attach_fails=True,
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "1", "--of", "5",
                "--title", "Read path removal", "--why", "w", "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("20", err)
        self.assertIn("attach", err.lower())

    def test_failing_add_blocked_by_after_attach_reports_created_number(self):
        calls = []
        fake_run = _phase_gh_fake_run(
            calls, epic=5, program_title="Structured memory",
            sub_issues=[{"number": 20}], new_issue_number=21, block_fails=True,
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli([
                "add-phase", "--epic", "5", "--seq", "2", "--of", "5",
                "--title", "Second phase", "--why", "w", "--kind", "feature",
            ])
        self.assertNotEqual(code, 0)
        self.assertIn("21", err)
        self.assertIn("blocked", err.lower())


_GH_WRITE_MARKERS = (
    ("issue", "edit"), ("issue", "close"), ("label", "create"),
    ("label", "edit"), ("label", "delete"),
)


def _assert_no_gh_write(args):
    """Raise if ``args`` (a ``gh`` invocation) is any write subcommand
    audit-issues must never reach: issue edit/close, or label
    create/edit/delete. Used as a hard assertion inside the stub, not a
    comment, so a regression that adds a write call fails the test."""
    for noun, verb in _GH_WRITE_MARKERS:
        if noun in args and verb in args:
            raise AssertionError(
                "audit-issues invoked a gh WRITE subcommand: {}".format(args)
            )


def _open_issues_fake_run(issues):
    """A ``subprocess.run`` stub answering exactly the one call
    ``GitHubStore.open_issues`` makes (``gh issue list --state open
    --json ... --limit ...``), asserting every invocation is read-only.
    ``issues`` is a list of ``{"number", "title", "labels"}`` with
    ``labels`` a plain list of label name strings."""
    def fake_run(args, **kwargs):
        _assert_no_gh_write(args)
        if args[:3] == ["gh", "issue", "list"]:
            return _completed(returncode=0, stdout=json.dumps([
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "labels": [{"name": name} for name in issue["labels"]],
                }
                for issue in issues
            ]))
        raise AssertionError("unexpected gh invocation: {}".format(args))

    return fake_run


class AuditIssuesCLITests(CLITestCase):
    def _run(self, issues):
        fake_run = _open_issues_fake_run(issues)
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            return _run_cli(["audit-issues"])

    def test_no_kind_label_is_reported(self):
        code, out, err = self._run([
            {"number": 1, "title": "No kind", "labels": ["by:human"]},
        ])
        self.assertEqual(code, 1)
        self.assertIn("#1", out)
        self.assertIn("kind label count 0", out)

    def test_two_kind_labels_is_reported(self):
        code, out, err = self._run([
            {"number": 1, "title": "Two kinds",
             "labels": ["by:human", "feature", "defect"]},
        ])
        self.assertEqual(code, 1)
        self.assertIn("kind label count 2", out)

    def test_exactly_one_kind_label_is_not_reported(self):
        code, out, err = self._run([
            {"number": 1, "title": "Clean", "labels": ["by:human", "feature"]},
        ])
        self.assertEqual(code, 0)
        self.assertNotIn("#1", out)

    def test_missing_origin_label_is_reported(self):
        code, out, err = self._run([
            {"number": 2, "title": "No origin", "labels": ["feature"]},
        ])
        self.assertEqual(code, 1)
        self.assertIn("#2", out)
        self.assertIn("origin label count 0", out)

    def test_each_retired_label_is_reported_by_name(self):
        for retired in fm.RETIRED_LABELS:
            code, out, err = self._run([
                {"number": 3, "title": "Retired",
                 "labels": ["by:human", "feature", retired]},
            ])
            self.assertEqual(code, 1, retired)
            self.assertIn(retired, out, retired)

    def test_one_issue_failing_several_checks_produces_one_line_naming_all(self):
        code, out, err = self._run([
            {"number": 4, "title": "Multi-fail",
             "labels": ["forge:deferral", "forge:backlog"]},
        ])
        self.assertEqual(code, 1)
        lines = [line for line in out.splitlines() if line.startswith("#4")]
        self.assertEqual(len(lines), 1, out)
        line = lines[0]
        self.assertIn("kind label count 0", line)
        self.assertIn("origin label count 0", line)
        self.assertIn("forge:deferral", line)
        self.assertIn("forge:backlog", line)

    def test_clean_issue_set_exits_0_with_a_single_all_clear_line(self):
        code, out, err = self._run([
            {"number": 5, "title": "Clean one", "labels": ["by:human", "feature"]},
            {"number": 6, "title": "Clean two", "labels": ["by:agent", "debt"]},
        ])
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, out)

    def test_any_offender_exits_1(self):
        code, out, err = self._run([
            {"number": 5, "title": "Clean", "labels": ["by:human", "feature"]},
            {"number": 6, "title": "Bad", "labels": []},
        ])
        self.assertEqual(code, 1)

    def test_no_gh_write_subcommand_is_ever_invoked(self):
        # The stub itself asserts this on every call (_assert_no_gh_write);
        # this test additionally proves the run actually exercised gh at
        # all, so a vacuously-passing no-op path can't hide behind it.
        calls = []
        issues = [{"number": 1, "title": "x", "labels": ["by:human", "feature"]}]

        def fake_run(args, **kwargs):
            calls.append(args)
            return _open_issues_fake_run(issues)(args, **kwargs)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            code, out, err = _run_cli(["audit-issues"])
        self.assertEqual(code, 0, out)
        self.assertTrue(calls)
        for args in calls:
            _assert_no_gh_write(args)

    def test_more_than_30_open_issues_are_all_examined(self):
        issues = [
            {"number": n, "title": "Issue {}".format(n), "labels": ["by:human"]}
            for n in range(1, 36)
        ]
        code, out, err = self._run(issues)
        self.assertEqual(code, 1)
        offending_numbers = {
            line.split()[0] for line in out.splitlines() if line.startswith("#")
        }
        self.assertEqual(len(offending_numbers), 35, out)


def _deferral_record():
    return fm.Record("deferral", {"title": "t", "why": "w", "from": "user"})


class GitHubStorePrimitivesTests(unittest.TestCase):
    """``GitHubStore``'s kind-label support and the three read/edge
    primitives ``program``/``phase``/``audit-issues`` need. Unlike the
    CLI-driven ``DeferCLITests`` above, these call ``GitHubStore`` directly:
    the subcommands that will use them (``add-program``, ``add-phase``,
    ``audit-issues``) are a later task, but the store primitives are this
    one's whole scope."""

    def setUp(self):
        self.store = fms.GitHubStore("/repo")

    def test_create_with_kind_applies_origin_and_kind_label(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            label = _gh_label_preflight(args)
            if label is not None:
                return label
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/9\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            number = self.store.create(_deferral_record(), by="human", kind="debt")
        self.assertEqual(number, "9")
        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:human", "debt"])

    def test_create_with_no_kind_applies_exactly_one_label(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            label = _gh_label_preflight(args)
            if label is not None:
                return label
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/9\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.store.create(_deferral_record(), by="human")
        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:human"])

    def test_create_uses_the_identifying_field_for_a_program_title(self):
        # A program's heading field is "name", not "title" — create() must
        # read the issue title from record type's identifying field
        # (SCHEMA[type][0]) rather than the literal key "title", or a
        # program would be filed with an empty title.
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            label = _gh_label_preflight(args)
            if label is not None:
                return label
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/12\n")

        record = fm.Record("program", {"name": "Structured memory", "why": "w"})
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.store.create(record, by="human", kind="feature")
        create = next(
            a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
        )
        title_idx = create.index("--title")
        self.assertEqual(create[title_idx + 1], "Structured memory")

    def test_create_still_uses_title_field_for_deferral_and_phase(self):
        # The fix above must not change behavior for the two types that
        # already used "title".
        for record_type in ("deferral", "phase"):
            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                if args[:2] == ["gh", "auth"]:
                    return _completed(returncode=0, stdout="Logged in")
                label = _gh_label_preflight(args)
                if label is not None:
                    return label
                return _completed(
                    returncode=0, stdout="https://github.com/o/r/issues/13\n",
                )

            record = fm.Record(record_type, {"title": "A real title", "why": "w"})
            if record_type == "deferral":
                record.fields["from"] = "user"
            with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
                self.store.create(record, by="human")
            create = next(
                a for a in calls if a[:2] == ["gh", "issue"] and "create" in a
            )
            title_idx = create.index("--title")
            self.assertEqual(create[title_idx + 1], "A real title", record_type)

    def test_create_with_unknown_kind_raises_before_any_network_call(self):
        with mock.patch.object(fms.subprocess, "run", side_effect=AssertionError(
            "gh must not be invoked when kind is invalid"
        )):
            with self.assertRaises(fm.SchemaError):
                self.store.create(_deferral_record(), by="human", kind="bogus")

    def test_issue_title_returns_title_from_structured_output(self):
        def fake_run(args, **kwargs):
            self.assertEqual(args, ["gh", "api", "repos/{owner}/{repo}/issues/9"])
            return _completed(returncode=0, stdout=json.dumps({"id": 111, "title": "Epic A"}))

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.assertEqual(self.store.issue_title(9), "Epic A")

    def test_sub_issues_returns_numbers_in_insertion_order(self):
        def fake_run(args, **kwargs):
            self.assertEqual(
                args, ["gh", "api", "repos/{owner}/{repo}/issues/9/sub_issues"],
            )
            return _completed(returncode=0, stdout=json.dumps([
                {"number": 10, "id": 1}, {"number": 11, "id": 2},
            ]))

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.assertEqual(
                self.store.sub_issues(9), [{"number": 10}, {"number": 11}],
            )

    def test_sub_issues_of_an_epic_with_none_is_empty(self):
        def fake_run(args, **kwargs):
            return _completed(returncode=0, stdout="[]")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.assertEqual(self.store.sub_issues(9), [])

    def test_attach_sub_issue_resolves_id_then_posts_once(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["gh", "api", "repos/{owner}/{repo}/issues/10"]:
                return _completed(returncode=0, stdout=json.dumps({"id": 555}))
            return _completed(returncode=0, stdout="{}")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.store.attach_sub_issue(9, 10)
        posts = [a for a in calls if "--method" in a]
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post[:3], ["gh", "api", "repos/{owner}/{repo}/issues/9/sub_issues"])
        self.assertIn("sub_issue_id=555", post)

    def test_add_blocked_by_resolves_id_then_posts_once(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["gh", "api", "repos/{owner}/{repo}/issues/5"]:
                return _completed(returncode=0, stdout=json.dumps({"id": 777}))
            return _completed(returncode=0, stdout="{}")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            self.store.add_blocked_by(6, 5)
        posts = [a for a in calls if "--method" in a]
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(
            post[:3],
            ["gh", "api", "repos/{owner}/{repo}/issues/6/dependencies/blocked_by"],
        )
        self.assertIn("issue_id=777", post)

    def test_issue_title_failure_raises_through_raise_for_gh_failure(self):
        def fake_run(args, **kwargs):
            return _completed(returncode=1, stderr="boom")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                self.store.issue_title(9)
        self.assertIn("boom", str(cm.exception))

    def test_sub_issues_failure_raises_through_raise_for_gh_failure(self):
        def fake_run(args, **kwargs):
            return _completed(returncode=1, stderr="boom")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                self.store.sub_issues(9)
        self.assertIn("boom", str(cm.exception))

    def test_open_issues_failure_raises_through_raise_for_gh_failure(self):
        def fake_run(args, **kwargs):
            return _completed(returncode=1, stderr="boom")

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                self.store.open_issues()
        self.assertIn("boom", str(cm.exception))

    def test_attach_sub_issue_failure_raises_through_raise_for_gh_failure(self):
        def fake_run(args, **kwargs):
            if "--method" in args:
                return _completed(returncode=1, stderr="boom")
            return _completed(returncode=0, stdout=json.dumps({"id": 1}))

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                self.store.attach_sub_issue(9, 10)
        self.assertIn("boom", str(cm.exception))

    def test_add_blocked_by_failure_raises_through_raise_for_gh_failure(self):
        def fake_run(args, **kwargs):
            if "--method" in args:
                return _completed(returncode=1, stderr="boom")
            return _completed(returncode=0, stdout=json.dumps({"id": 1}))

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                self.store.add_blocked_by(6, 5)
        self.assertIn("boom", str(cm.exception))

    def test_open_issues_passes_explicit_limit_and_parses_fields(self):
        def fake_run(args, **kwargs):
            self.assertEqual(args[:4], ["gh", "issue", "list", "--state"])
            self.assertIn("--json", args)
            self.assertIn("number,title,labels", args)
            self.assertIn("--limit", args)
            limit_idx = args.index("--limit")
            self.assertEqual(args[limit_idx + 1], str(fms.GitHubStore._LABEL_LIST_LIMIT))
            return _completed(returncode=0, stdout=json.dumps([
                {"number": 3, "title": "Fix it", "labels": [
                    {"name": "defect"}, {"name": "by:human"},
                ]},
            ]))

        with mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            issues = self.store.open_issues()
        self.assertEqual(issues, [
            {"number": 3, "title": "Fix it", "labels": ["defect", "by:human"]},
        ])

    def test_select_store_returns_github_for_program_and_phase(self):
        tmp = tempfile.mkdtemp(prefix="forge-memory-select-store-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "docs", "forge"), exist_ok=True)
        with open(os.path.join(tmp, "docs", "forge", "config.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"deferrals": {"store": "file"}}, f)
        self.assertIsInstance(fms.select_store(tmp, "program"), fms.GitHubStore)
        self.assertIsInstance(fms.select_store(tmp, "phase"), fms.GitHubStore)
        # The config's file-store request for deferrals still holds — it is
        # config.json's own scope, ``program``/``phase`` simply never
        # consult it.
        self.assertIsInstance(fms.select_store(tmp, "deferral"), fms.FileStore)


# The fixture run.json's plan path: what a runner-staged deferral's ``from``
# must record (plan path, plus task number when the entry carries one).
_PROVENANCE = "/x/plan.md"


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

    def _defer_args(self, run=None, finding_id=None, title="improve-x",
                    from_=_PROVENANCE, occurrence=None):
        """``--from`` defaults to real run provenance (the fixture's plan
        path), never to nothing: an omitted ``--from`` used to bake
        ``from: user`` into every write-back test, and ``from: user`` is
        the marker the spec reserves for a deferral that deliberately
        SKIPS the close-out review gate. Pass ``from_=None`` to exercise
        the omitted-flag path deliberately."""
        args = [
            "defer", "--title", title, "--why", "polish", "--by", "agent",
        ]
        if from_ is not None:
            args += ["--from", from_]
        if run is not None:
            args += ["--run", run]
        if finding_id is not None:
            args += ["--finding-id", finding_id]
        if occurrence is not None:
            args += ["--occurrence", str(occurrence)]
        return args

    def _filed_records(self):
        """Every deferral the file store holds, parsed back through the one
        renderer/parser pair — so a test asserts the RECORD, not a substring
        of the file."""
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(path, encoding="utf-8") as f:
            return fm.parse(f.read(), "deferral")

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
            label = _gh_label_preflight(args)
            if label is not None:
                return label
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

    def test_omitted_from_on_a_run_filing_records_provenance_not_user(self):
        # `from: user` is the spec's marker for a deferral that skipped the
        # close-out review gate. A --run filing is by definition a
        # runner-staged deferral that went THROUGH the gate, so an omitted
        # --from must resolve to the run's provenance, never to "user".
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1", "summary": "one"}])
        code, out, err = _run_cli(
            self._defer_args(run=path, finding_id="f1", from_=None)
        )
        self.assertEqual(code, 0, err)
        record = self._filed_records()[0]
        self.assertEqual(record.fields["from"], "/x/plan.md")

    def test_omitted_from_on_a_run_filing_includes_the_task_number(self):
        # Staged through the runner's own path rather than hand-written, so
        # this asserts the shape a real run produces.
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json(
            forge_run.stage_deferrals(
                [], [{"id": "f1", "summary": "one"}], task_number=3)
        )
        code, out, err = _run_cli(
            self._defer_args(run=path, finding_id="f1", from_=None)
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self._filed_records()[0].fields["from"], "/x/plan.md, Task 3")

    def test_explicit_from_still_wins_over_the_derived_value(self):
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "f1", "summary": "one"}])
        code, out, err = _run_cli(
            self._defer_args(run=path, finding_id="f1", from_="user")
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self._filed_records()[0].fields["from"], "user")

    def test_no_second_copy_of_the_run_provenance_rule(self):
        # forge_status.deferral_provenance is the one definition of the
        # `from` value for a runner-staged deferral — the same function
        # that renders the emitted template. cmd_defer must call it rather
        # than assembling its own "<plan>, Task N" string, or the value the
        # template advertises and the value a bare filing records could
        # drift apart.
        source = inspect.getsource(fm.cmd_defer)
        self.assertIn("deferral_provenance", source)
        self.assertNotIn("Task {}", source)

    def test_two_staged_deferrals_sharing_an_id_can_both_be_filed(self):
        # Finding ids are reviewer-authored per review and never namespaced,
        # so a run's deferrals list can hold two entries with id "F1". Both
        # must be filable, each recording ITS OWN issue number.
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([
            {"id": "F1", "summary": "first thing"},
            {"id": "F1", "summary": "second thing"},
        ])
        first = _run_cli(self._defer_args(
            run=path, finding_id="F1", occurrence=1, title="first-title"))
        second = _run_cli(self._defer_args(
            run=path, finding_id="F1", occurrence=2, title="second-title"))
        self.assertEqual(first[0], 0, first[2])
        self.assertEqual(second[0], 0, second[2])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"][0]["issue"], "first-title")
        self.assertEqual(data["deferrals"][1]["issue"], "second-title")

    def test_ambiguous_finding_id_without_occurrence_refuses_loudly(self):
        # The floor: never a silent wrong match. The message must name the
        # ambiguity, every colliding entry, and the flag that resolves it.
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([
            {"id": "F1", "summary": "first thing"},
            {"id": "F1", "summary": "second thing"},
        ])
        with open(path, encoding="utf-8") as f:
            before = f.read()
        code, out, err = _run_cli(self._defer_args(run=path, finding_id="F1"))
        self.assertNotEqual(code, 0)
        self.assertIn("F1", err)
        self.assertIn("--occurrence", err)
        self.assertIn("first thing", err)
        self.assertIn("second thing", err)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_filing_one_of_two_colliding_ids_never_marks_the_other(self):
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([
            {"id": "F1", "summary": "first thing"},
            {"id": "F1", "summary": "second thing"},
        ])
        code, out, err = _run_cli(self._defer_args(
            run=path, finding_id="F1", occurrence=2, title="second-title"))
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("issue", data["deferrals"][0])
        self.assertEqual(data["deferrals"][1]["issue"], "second-title")

    def test_occurrence_out_of_range_exits_nonzero_naming_the_count(self):
        self._use_file_store_for_deferrals()
        path, _ = self._write_run_json([{"id": "F1", "summary": "only one"}])
        code, out, err = _run_cli(self._defer_args(
            run=path, finding_id="F1", occurrence=2))
        self.assertNotEqual(code, 0)
        self.assertIn("F1", err)

    def test_occurrence_without_run_exits_nonzero(self):
        self._use_file_store_for_deferrals()
        code, out, err = _run_cli(self._defer_args(occurrence=1))
        self.assertNotEqual(code, 0)

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
            label = _gh_label_preflight(args)
            if label is not None:
                return label
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
            label = _gh_label_preflight(args)
            if label is not None:
                return label
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
            label = _gh_label_preflight(args)
            if label is not None:
                return label
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
            "**Source:** user\n"
        ))
        code, out, err = _run_cli(["fmt", "--check", path])
        self.assertNotEqual(code, 0)

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "scope": "repo", "because": "b",
            "source": "user",
        })
        self._write(path, fm.render(record))
        code, out, err = _run_cli(["fmt", "--check", path])
        self.assertEqual(code, 0, out + err)

    def test_explicit_path_does_not_call_gh(self):
        path = os.path.join(self.tmp, "constraints.md")
        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "scope": "repo", "because": "b",
            "source": "user",
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

    def test_no_path_checks_managed_files_and_never_calls_gh(self):
        # Read-back retrieval is gone: with no PATH argument, fmt covers the
        # managed FILES and nothing else. The GitHub store is the default
        # here (no config.json), and fmt must still make no gh call —
        # re-finding forge's own issues duplicated the GitHub UI, and the
        # write-time validation in GitHubStore.create is the half that
        # actually keeps a malformed record off the network.
        constraints_path = os.path.join(self.tmp, "docs", "forge", "constraints.md")
        self._write(constraints_path, (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Source:** user\n"
        ))

        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check"])

        self.assertNotEqual(code, 0)
        self.assertIn("bad id", out)


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
                                  "**Scope:** repo\n**Source:** user\n")
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
            "**Source:** user\n"
        ))
        proc = subprocess.run([self.hook_path], cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        record = fm.Record(type="constraint", fields={
            "id": "good-id", "rule": "r", "scope": "repo", "because": "b",
            "source": "user",
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
            "id": "good-id", "rule": "r", "scope": "repo", "because": "b",
            "source": "user",
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
            "id": "good-id", "rule": "r", "scope": "repo", "because": "b",
            "source": "user",
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
        # `fmt --check` reads managed FILES only — read-back retrieval of
        # open issues is gone — so the workflow needs neither a token nor
        # issue read access. Granting either would be standing permission
        # for a call this check can no longer make.
        self.assertNotIn("GH_TOKEN", text)
        self.assertNotIn("issues:", text)
        self.assertIn("permissions:", text)
        self.assertIn("contents: read", text)
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

        proc = self._run_script([
            "resolve", "--ref", "anything", "--reason", "done",
        ])

        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("line 1", proc.stderr)
        self.assertIn("unparsable", proc.stderr)

    def test_fmt_write_schema_error_prints_cleanly_when_run_as_a_script(self):
        self._write_config(store="file")
        path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        _write(path, "this is not a valid record\n")

        proc = self._run_script(["fmt", "--write"])

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
    """``fmt`` has exactly two branches now — explicit paths, and no PATH
    (every managed local file). ``--local-only`` existed only to name the
    branch that skipped the open-issue check; with read-back retrieval gone
    the two branches it distinguished are byte-for-byte the same work, so
    the flag is retired rather than kept as a synonym for the default."""

    def test_local_only_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            fm.main(["fmt", "--check", "--local-only"])

    def test_no_path_checks_managed_files_without_gh(self):
        self._use_file_store_for_deferrals()
        _write(os.path.join(self.tmp, "docs", "forge", "constraints.md"), (
            "## bad id\n"
            "**Rule:** r\n"
            "**Because:** b\n"
            "**Scope:** repo\n"
            "**Source:** user\n"
        ))
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check"])
        self.assertNotEqual(code, 0)
        self.assertIn("bad id", out)

    def test_no_path_never_reaches_gh_under_the_github_store(self):
        # No config.json: the GitHub store is selected for deferrals, and
        # fmt must still make no gh call at all.
        with self._no_gh_guard():
            code, out, err = _run_cli(["fmt", "--check"])
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
            ("constraint", _valid_constraint_fields(scope="  spaced  ")),
            ("constraint", _valid_constraint_fields(rule="x" * 200)),
            ("deferral", _valid_deferral_fields()),
            ("deferral", _valid_deferral_fields(title="## heading-shaped title")),
            ("deferral", _valid_deferral_fields(why="Why: **not** a label.")),
            ("deferral", _valid_deferral_fields(
                **{"from": "docs/forge/plans/p.md, Task 2"})),
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
            "--because", "Because.", "--source", "issue-1",
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
            "--because", "A reason.", "--source", "issue-1",
        ])
        self.assertEqual(code, 0)
        code, out, err = _run_cli([
            "add-constraint", "--id", "bad-one", "--rule", "A rule.",
            "--because", "line one\nline two", "--source", "issue-1",
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
                "--by", "human",
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
            "defer", "--title", "t", "--why", "w", "--by", "human",
        ])

    def test_resolve_deferral(self):
        self._assert_named(["resolve", "--ref", "7", "--reason", "done"])

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
            fm.cmd_list_constraints, fm.cmd_defer,
            fm.cmd_resolve,
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


class EmittedTemplateEndToEndTests(CLITestCase):
    """The seam the substring assertions in tests/test_forge_status.py could
    never catch: an emitted template that reads right but files WRONG. Here
    the command `forge_status.render_staged_deferrals` emits is filled in and
    actually RUN, and the resulting record — not a substring of the command —
    is asserted."""

    def _run_dir(self, deferrals, plan="docs/forge/plans/p.md"):
        run_dir = os.path.join(self.tmp, ".forge", "runs", "r1")
        os.makedirs(run_dir, exist_ok=True)
        path = os.path.join(run_dir, "run.json")
        data = {
            "status": "passed",
            "tasks": [{"number": 1, "status": "passed"}],
            "deferrals": deferrals,
        }
        if plan is not None:
            data["plan"] = plan
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return run_dir, path

    def _staged(self, findings, **stage):
        """Staged deferral entries built through the RUNNER's own staging
        path. Hand-writing the dict here is what let this suite assert the
        `from` contract against a key production never wrote — an entry shape
        that cannot occur, exercising a branch no real run can reach."""
        return forge_run.stage_deferrals([], findings, **stage)

    def _emitted_commands(self, run_dir, run_json_path):
        state = forge_status.read_run_state(run_dir)
        lines = forge_status.render_staged_deferrals(state, run_json_path)
        return [l.strip() for l in lines if "forge_memory.py defer" in l]

    def _fill_in(self, command, title, why):
        """Shell-split the emitted line, drop the script name, and replace the
        two visible placeholders with authored values — exactly what a human
        or an agent does at the review gate."""
        argv = shlex.split(command)
        self.assertEqual(argv[0], "forge_memory.py")
        argv = argv[1:]
        self.assertIn("<title>", argv)
        self.assertIn("<why>", argv)
        return [
            title if a == "<title>" else (why if a == "<why>" else a)
            for a in argv
        ]

    def _gh_stub(self, bodies):
        """A stubbed gh that records every created issue body and hands back
        an incrementing issue URL. No network, no real gh."""
        counter = {"n": 100}

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            if args[1:3] == ["issue", "create"]:
                bodies.append(args[args.index("--body") + 1])
                counter["n"] += 1
                return _completed(
                    returncode=0,
                    stdout="https://github.com/o/r/issues/{}\n".format(counter["n"]),
                )
            return _completed(returncode=0, stdout="")

        return mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
            mock.patch.object(fms.subprocess, "run", side_effect=fake_run)

    def test_emitted_template_files_with_run_provenance_not_user(self):
        run_dir, path = self._run_dir(
            self._staged([{"id": "F1", "summary": "s" * 120}], task_number=2)
        )
        commands = self._emitted_commands(run_dir, path)
        self.assertEqual(len(commands), 1)
        argv = self._fill_in(commands[0], "shorten the deferral formatter",
                             "polish, not required by the current spec")
        bodies = []
        which, run = self._gh_stub(bodies)
        with which, run:
            code, out, err = _run_cli(argv)
        self.assertEqual(code, 0, err)
        record = fm.parse(bodies[0], "deferral")[0]
        self.assertEqual(record.fields["from"], "docs/forge/plans/p.md, Task 2")
        self.assertNotEqual(record.fields["from"], "user")
        self.assertNotIn("follow-up", record.fields)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["deferrals"][0]["issue"], "101")

    def test_both_emitted_commands_for_a_colliding_id_file_their_own_entry(self):
        run_dir, path = self._run_dir([
            {"id": "F1", "summary": "first colliding finding"},
            {"id": "F1", "summary": "second colliding finding"},
        ])
        commands = self._emitted_commands(run_dir, path)
        self.assertEqual(len(commands), 2)
        bodies = []
        which, run = self._gh_stub(bodies)
        with which, run:
            for i, command in enumerate(commands, start=1):
                argv = self._fill_in(command, "title {}".format(i), "why {}".format(i))
                code, out, err = _run_cli(argv)
                self.assertEqual(code, 0, err)
        self.assertEqual(len(bodies), 2)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"][0]["issue"], "101")
        self.assertEqual(data["deferrals"][1]["issue"], "102")
        titles = [fm.parse(b, "deferral")[0].fields["title"] for b in bodies]
        self.assertEqual(titles, ["title 1", "title 2"])

    def test_plan_less_run_json_files_the_same_from_the_template_advertises(self):
        # With no `plan` key the provenance falls back to the run.json path
        # itself — and the two callers reach that fallback by different
        # routes: the emitter derives run_dir + "run.json", while a filing
        # uses whatever path the user typed. A relative --run must therefore
        # still record exactly what the emitted template promised.
        self._use_file_store_for_deferrals()
        run_dir, path = self._run_dir(
            self._staged([{"id": "F1", "summary": "s" * 120}], task_number=2),
            plan=None,
        )
        advertised = self._emitted_commands(run_dir, path)[0]
        argv = shlex.split(advertised)
        promised = argv[argv.index("--from") + 1]
        code, out, err = _run_cli([
            "defer", "--title", "a title", "--why", "a why", "--by", "agent",
            "--run", os.path.relpath(path), "--finding-id", "F1",
        ])
        self.assertEqual(code, 0, err)
        store_path = os.path.join(self.tmp, "docs", "forge", "deferrals.md")
        with open(store_path, encoding="utf-8") as f:
            record = fm.parse(f.read(), "deferral")[0]
        self.assertEqual(record.fields["from"], promised)
        self.assertNotEqual(record.fields["from"], "user")

    def test_rerunning_an_emitted_command_files_nothing_a_second_time(self):
        run_dir, path = self._run_dir([{"id": "F1", "summary": "one finding"}])
        command = self._emitted_commands(run_dir, path)[0]
        argv = self._fill_in(command, "a title", "a why")
        bodies = []
        which, run = self._gh_stub(bodies)
        with which, run:
            first = _run_cli(argv)
            second = _run_cli(argv)
        self.assertEqual(first[0], 0, first[2])
        self.assertNotEqual(second[0], 0)
        self.assertEqual(len(bodies), 1)


class StatusVocabularyCopyTests(unittest.TestCase):
    """One definition of the run-status terminal/non-terminal split across
    the whole ``scripts/`` package — not just forge_memory.py, which is all
    the original test greped, leaving forge-monitor.py's private copy free
    to drift."""

    # A membership test against a set of mapped terminal states, in any
    # order and across line breaks: `state["state"] in ("completed",
    # "halted", "contract-error")` — the exact shape forge-monitor.py's
    # private copy took. Deliberately narrower than "mentions the states":
    # the monitor's state -> display-label map is a different question
    # (what to PRINT), and naming a single status (`st == "contract-error"`)
    # is not a copy of the split.
    _MEMBERSHIP_RE = re.compile(
        r'in\s*[\(\[\{][^)\]\}]*"contract-error"[^)\]\}]*[\)\]\}]', re.S
    )

    def _scripts(self):
        d = os.path.join(REPO_ROOT, "scripts")
        return [
            os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.endswith(".py") and n != "forge_status.py"
        ]

    def test_only_forge_status_defines_is_terminal(self):
        for path in self._scripts():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for definition in ("def is_terminal", "def _is_terminal"):
                self.assertNotIn(
                    definition, source,
                    "{} defines its own {} — forge_status.is_terminal is the "
                    "one definition of that split".format(path, definition),
                )

    def test_no_script_reimplements_the_mapped_terminal_state_set(self):
        # The monitor's drifted copy was `state["state"] in ("completed",
        # "halted", "contract-error")` — the same question is_terminal
        # answers, asked of the mapped state instead of the raw status.
        for path in self._scripts():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            match = self._MEMBERSHIP_RE.search(source)
            self.assertIsNone(
                match,
                "{} re-derives the terminal-state vocabulary ({!r}) — call "
                "forge_status.is_terminal instead".format(
                    path, match.group(0) if match else "",
                ),
            )


SESSION_START_HOOK = os.path.join(REPO_ROOT, "hooks", "session-start")


def _run_session_start_hook(cwd, env=None):
    """Invoke hooks/session-start as a real subprocess. Returns
    (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [SESSION_START_HOOK], input="", cwd=cwd, capture_output=True,
        text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class SessionStartHookTests(unittest.TestCase):
    # A substring check on the literal "DECISIONS" cannot see semantics: it
    # passes just as happily on lowercase prose that still instructs an
    # agent to "log new decisions". These match on the instruction itself,
    # case-insensitively, regardless of how it is spelled or capitalized.
    _LOG_A_DECISION_RE = re.compile(
        r"log\w*[^.]{0,30}decisions?", re.IGNORECASE | re.DOTALL,
    )
    _DEFERRAL_VIA_SKILL_RE = re.compile(
        r"deferral[^.]{0,60}project-memory|project-memory[^.]{0,60}deferral",
        re.IGNORECASE | re.DOTALL,
    )

    FLOW_SENTENCE = (
        "This project uses the forge flow: brainstorm -> spec "
        "(docs/forge/specs/) -> plan (docs/forge/plans/) -> TDD execution, "
        "with user approval gates between stages."
    )
    ROADMAP_SENTENCE = (
        "Open GitHub issues are the work backlog; check them for the current phase."
    )
    LEGACY_FLOW_SENTENCE = (
        "This project uses the forge flow via the legacy docs/theforge/ "
        "(or .theforge/) signal directory: brainstorm -> spec -> plan -> "
        "TDD execution, with user approval gates between stages."
    )
    LEGACY_ROADMAP_SENTENCE = (
        "Open GitHub issues are the work backlog; check them for the current phase."
    )

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        self.addCleanup(self._tmpdir.cleanup)

    def _mkforge(self, legacy=False):
        base = "theforge" if legacy else "forge"
        os.makedirs(os.path.join(self.tmp, "docs", base), exist_ok=True)
        return os.path.join(self.tmp, "docs", base)

    def _get_context(self, stdout):
        payload = json.loads(stdout)
        return payload["hookSpecificOutput"]["additionalContext"]

    def _assert_no_decision_or_skill_deferral_instruction(self, context):
        match = self._LOG_A_DECISION_RE.search(context)
        self.assertIsNone(
            match, "context still instructs logging a decision: {!r}".format(
                match.group(0) if match else None
            )
        )
        match = self._DEFERRAL_VIA_SKILL_RE.search(context)
        self.assertIsNone(
            match,
            "context still names the project-memory skill as where "
            "deferrals are recorded: {!r}".format(
                match.group(0) if match else None
            )
        )

    def test_no_forge_signal_emits_nothing(self):
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_no_constraints_file_emits_flow_context_only(self):
        self._mkforge()
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("forge flow", context)
        self._assert_no_decision_or_skill_deferral_instruction(context)

    def test_flow_and_roadmap_sentences_survive_unchanged(self):
        self._mkforge()
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn(self.FLOW_SENTENCE, context)
        self.assertIn(self.ROADMAP_SENTENCE, context)
        self.assertNotIn("ROADMAP", context)

    def test_legacy_flow_and_roadmap_sentences_survive_unchanged(self):
        self._mkforge(legacy=True)
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn(self.LEGACY_FLOW_SENTENCE, context)
        self.assertIn(self.LEGACY_ROADMAP_SENTENCE, context)
        self.assertNotIn("ROADMAP", context)

    def test_constraints_present_includes_each_rule(self):
        forge_dir = self._mkforge()
        record1 = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="rule-one", rule="Never call eval on untrusted input.",
        ))
        record2 = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="rule-two", rule="Always validate before writing.",
        ))
        _write(
            os.path.join(forge_dir, "constraints.md"),
            fm.render(record1) + "\n" + fm.render(record2),
        )
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("Never call eval on untrusted input.", context)
        self.assertIn("Always validate before writing.", context)
        self._assert_no_decision_or_skill_deferral_instruction(context)

    def test_never_mentions_decisions_even_with_constraints(self):
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields())
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertNotIn("DECISIONS", out)
        self._assert_no_decision_or_skill_deferral_instruction(context)

    def test_unparsable_constraints_file_does_not_break_hook(self):
        forge_dir = self._mkforge()
        _write(
            os.path.join(forge_dir, "constraints.md"),
            "this is not a valid constraint record at all\n\x00\x01 binary junk",
        )
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("forge flow", context)

    def test_empty_constraints_file_emits_flow_context_only(self):
        forge_dir = self._mkforge()
        _write(os.path.join(forge_dir, "constraints.md"), "")
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("forge flow", context)

    def test_legacy_theforge_path_behaves_the_same(self):
        forge_dir = self._mkforge(legacy=True)
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule="Legacy rule text.",
        ))
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("Legacy rule text.", context)
        self._assert_no_decision_or_skill_deferral_instruction(context)

    def test_output_is_valid_json_with_quote_backslash_newline_tab_in_rule(self):
        forge_dir = self._mkforge()
        tricky_rule = 'Quote " and backslash \\ and tab\tend, plus a "escaped" case.'
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule=tricky_rule,
        ))
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)  # raises if invalid JSON
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(tricky_rule, context)

    def test_makes_no_gh_call_and_no_network_call(self):
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields())
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))

        bindir = os.path.join(self.tmp, "_fakebin")
        os.makedirs(bindir, exist_ok=True)
        marker = os.path.join(self.tmp, "gh-was-called")
        for name in ("gh", "curl", "wget"):
            fake = os.path.join(bindir, name)
            _write(fake, "#!/usr/bin/env bash\ntouch \"{}\"\nexit 1\n".format(marker))
            os.chmod(fake, 0o755)

        real_python3 = shutil.which("python3")
        real_bash = shutil.which("bash")
        real_dirs = os.pathsep.join(sorted({
            os.path.dirname(real_python3), os.path.dirname(real_bash),
            "/bin", "/usr/bin",
        }))
        env = dict(os.environ)
        env["PATH"] = bindir + os.pathsep + real_dirs

        code, out, err = _run_session_start_hook(cwd=self.tmp, env=env)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(marker))

    def test_python3_stub_exit0_garbage_stdout_falls_back_to_flow_json(self):
        # A wrapped or shimmed python3 on PATH could exit 0 while writing
        # something other than JSON to stdout (a banner, a deprecation
        # notice, anything). The hook must not trust python3's exit code
        # alone and print that verbatim -- it must validate the captured
        # output is actually parseable JSON before emitting it, and fall
        # back to the plain-printf flow-only path (which never invokes
        # python3) when it is not.
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields())
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))

        bindir = os.path.join(self.tmp, "_fakebin")
        os.makedirs(bindir, exist_ok=True)
        fake_python3 = os.path.join(bindir, "python3")
        _write(
            fake_python3,
            "#!/usr/bin/env bash\n"
            "echo 'not valid json, just some banner text'\n"
            "exit 0\n",
        )
        os.chmod(fake_python3, 0o755)

        real_bash = shutil.which("bash")
        real_dirs = os.pathsep.join(sorted({
            os.path.dirname(real_bash), "/bin", "/usr/bin",
        }))
        env = dict(os.environ)
        env["PATH"] = bindir + os.pathsep + real_dirs

        code, out, err = _run_session_start_hook(cwd=self.tmp, env=env)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)  # raises if the hook emitted the garbage
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(self.FLOW_SENTENCE, context)
        self.assertNotIn("banner", context)

    def test_constraint_extraction_goes_through_forge_memory_not_regex(self):
        # Proves the hook parses constraints.md via forge_memory.parse
        # rather than regex-matching the rendered "**Rule:**" line shape:
        # the constraints file below contains no "**Rule:**" text at all,
        # and the stub forge_memory.py's parse() ignores the file's real
        # content entirely, returning one hardcoded record instead. A
        # regex over rendered prose would find nothing here; only an
        # actual `import forge_memory` + `fm.parse(...)` call can surface
        # the stub's canned text. If someone reintroduces regex parsing,
        # this test stops seeing the stub's output and fails.
        fake_root = os.path.join(self.tmp, "_fakeplugin")
        os.makedirs(os.path.join(fake_root, "hooks"), exist_ok=True)
        os.makedirs(os.path.join(fake_root, "scripts"), exist_ok=True)
        with open(SESSION_START_HOOK, encoding="utf-8") as f:
            hook_source = f.read()
        fake_hook = os.path.join(fake_root, "hooks", "session-start")
        _write(fake_hook, hook_source)
        os.chmod(fake_hook, 0o755)

        stub = (
            "class _Rec:\n"
            "    def __init__(self, fields):\n"
            "        self.fields = fields\n"
            "\n"
            "def parse(text, type):\n"
            "    return [_Rec({'rule': 'STUB_RULE_FROM_FORGE_MEMORY', "
            "'scope': 'repo'})]\n"
        )
        _write(os.path.join(fake_root, "scripts", "forge_memory.py"), stub)

        forge_dir = self._mkforge()
        _write(
            os.path.join(forge_dir, "constraints.md"),
            "totally unstructured text with no ** markers whatsoever\n",
        )

        proc = subprocess.run(
            [fake_hook], input="", cwd=self.tmp, capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        context = self._get_context(proc.stdout)
        self.assertIn("STUB_RULE_FROM_FORGE_MEMORY", context)

    def test_non_repo_scope_is_shown_alongside_the_rule(self):
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule="Never call eval on untrusted input.", scope="hooks/",
        ))
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("Never call eval on untrusted input.", context)
        self.assertIn("hooks/", context)

    def test_id_and_because_are_emitted_but_source_is_not(self):
        # `because` is why the field exists at all: an agent that does not
        # understand a rule routes around it while technically complying.
        # `id` is what lets a "(constraint: <id>)" citation in the code
        # resolve back to a rule. Both are read-time material at exactly
        # this moment. `source` is a follow-it-when-you-need-it pointer,
        # not something worth carrying in every session's context.
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            id="parsers-fail-loud",
            rule="Parsers raise on malformed input, naming the cause.",
            because="A tolerant parser turns a syntax error into a wrong answer.",
            source="docs/forge/specs/UNIQUE-SOURCE-MARKER.md",
        ))
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("parsers-fail-loud", context)
        self.assertIn(
            "A tolerant parser turns a syntax error into a wrong answer.",
            context,
        )
        self.assertNotIn("UNIQUE-SOURCE-MARKER", context)

    def test_repo_scope_is_not_shown_redundantly(self):
        forge_dir = self._mkforge()
        record = fm.Record(type="constraint", fields=_valid_constraint_fields(
            rule="Never call eval on untrusted input.", scope="repo",
        ))
        _write(os.path.join(forge_dir, "constraints.md"), fm.render(record))
        code, out, err = _run_session_start_hook(cwd=self.tmp)
        self.assertEqual(code, 0, err)
        context = self._get_context(out)
        self.assertIn("Never call eval on untrusted input.", context)
        self.assertNotIn("repo]", context)
        self.assertNotIn("[repo", context)


if __name__ == "__main__":
    unittest.main()
