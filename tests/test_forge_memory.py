"""forge_memory: budgets are enforced as hard character-count errors (not just
structural checks) so prose-expansion drift inside a valid field is caught;
`validate`/`fmt_check` report every defect in one pass, never just the first;
`render` is the sole source of record text and `parse` is its exact inverse,
including on a file a human hand-drifted (reordered fields) but that still
parses; and unparsable or budget-violating input fails loud naming the line."""
import argparse
import contextlib
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
        "defer": {"title", "why", "follow_up", "from_"},
        "list-deferrals": {"json"},
        "resolve-deferral": {"ref", "reason"},
        "fmt": {"check", "write", "paths"},
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


class HooksJsonTests(unittest.TestCase):
    def test_hooks_json_is_valid_and_keeps_session_start(self):
        hooks_json_path = os.path.join(REPO_ROOT, "hooks", "hooks.json")
        with open(hooks_json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("SessionStart", data["hooks"])
        self.assertIn("PreToolUse", data["hooks"])
        pretooluse = data["hooks"]["PreToolUse"][0]
        self.assertEqual(pretooluse["matcher"], "Edit|Write|MultiEdit")


if __name__ == "__main__":
    unittest.main()
