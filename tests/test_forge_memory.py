"""forge_memory: budgets are enforced as hard character-count errors (not just
structural checks) so prose-expansion drift inside a valid field is caught;
`validate`/`fmt_check` report every defect in one pass, never just the first;
`render` is the sole source of record text and `parse` is its exact inverse,
including on a file a human hand-drifted (reordered fields) but that still
parses; and unparsable or budget-violating input fails loud naming the line."""
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import forge_memory as fm  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
