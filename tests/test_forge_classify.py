"""Classification engine: verdict parsing into Finding objects, diff line-range
extraction, runner-verified provenance, the disposition matrix, and the combined
classify_findings pass. Pure functions — no codex, no git, no plan loop."""
import unittest

from _forge_support import *  # noqa: F401,F403
import forge_common


# --- unified-diff fixtures (git-style, a/ b/ prefixes) ----------------------

DIFF_SINGLE = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -10,3 +12,5 @@ def f():
 context
+added_a
+added_b
 context
"""

DIFF_MULTI_HUNK = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 a
+b
 c
@@ -10,2 +11,4 @@
 x
+y
+z
 w
"""

DIFF_MULTI_FILE = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,2 @@
 a
+b
diff --git a/bar.py b/bar.py
index 3333333..4444444 100644
--- a/bar.py
+++ b/bar.py
@@ -5,1 +7,3 @@
 x
+y
+z
"""

DIFF_ADDED_ONLY = """diff --git a/new.py b/new.py
new file mode 100644
index 0000000..5555555
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+line1
+line2
+line3
"""

DIFF_SINGLE_LINE_HUNK = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -5 +7 @@
-old
+new
"""


def _finding(**kw):
    """Build a Finding with sane defaults so each test names only the axes it
    exercises."""
    base = dict(
        id="f1",
        summary="one line",
        file="foo.py",
        lines="13",
        provenance="in-diff",
        impact="improvement",
        contract_ref=None,
    )
    base.update(kw)
    return forge_common.Finding(**base)


class DiffLineRangesTests(unittest.TestCase):
    """diff_line_ranges: new-side changed line ranges per file, from unified-diff
    hunk headers (@@ -a,b +c,d @@)."""

    def test_single_hunk(self):
        ranges = forge_run.diff_line_ranges(DIFF_SINGLE)
        self.assertEqual(ranges, {"foo.py": [(12, 16)]})

    def test_multiple_hunks_one_file(self):
        ranges = forge_run.diff_line_ranges(DIFF_MULTI_HUNK)
        self.assertEqual(ranges, {"foo.py": [(1, 3), (11, 14)]})

    def test_multiple_files(self):
        ranges = forge_run.diff_line_ranges(DIFF_MULTI_FILE)
        self.assertEqual(ranges, {"foo.py": [(1, 2)], "bar.py": [(7, 9)]})

    def test_added_only_hunk_against_dev_null(self):
        ranges = forge_run.diff_line_ranges(DIFF_ADDED_ONLY)
        self.assertEqual(ranges, {"new.py": [(1, 3)]})

    def test_single_line_hunk_header_no_count(self):
        # `@@ -5 +7 @@` — omitted counts default to 1, so the new side is line 7.
        ranges = forge_run.diff_line_ranges(DIFF_SINGLE_LINE_HUNK)
        self.assertEqual(ranges, {"foo.py": [(7, 7)]})

    def test_empty_diff(self):
        self.assertEqual(forge_run.diff_line_ranges(""), {})


class ParseLinesTests(unittest.TestCase):
    """_parse_lines: single number, single range, comma-separated combinations,
    whitespace tolerance, and genuinely malformed strings."""

    def test_single_number(self):
        self.assertEqual(forge_run._parse_lines("12"), [(12, 12)])

    def test_single_range(self):
        self.assertEqual(forge_run._parse_lines("12-20"), [(12, 20)])

    def test_comma_separated_ranges(self):
        self.assertEqual(
            forge_run._parse_lines("120-152,182-199"), [(120, 152), (182, 199)]
        )

    def test_mixed_numbers_and_ranges(self):
        self.assertEqual(
            forge_run._parse_lines("12-20,45,60-62"),
            [(12, 20), (45, 45), (60, 62)],
        )

    def test_whitespace_tolerated(self):
        self.assertEqual(
            forge_run._parse_lines(" 12-20 , 45 , 60-62 "),
            [(12, 20), (45, 45), (60, 62)],
        )

    def test_absent_is_none(self):
        self.assertIsNone(forge_run._parse_lines(None))
        self.assertIsNone(forge_run._parse_lines(""))

    def test_malformed_string_is_none(self):
        self.assertIsNone(forge_run._parse_lines("abc"))

    def test_double_dash_is_none(self):
        self.assertIsNone(forge_run._parse_lines("12--20"))

    def test_one_bad_token_invalidates_whole_field(self):
        # A mix of one good range and one bad one is not silently downgraded
        # to just the good half — the whole field is unparseable.
        self.assertIsNone(forge_run._parse_lines("12-20,abc"))


class VerifyProvenanceTests(unittest.TestCase):
    """verify_provenance: intersect the finding's lines with the diff's changed
    ranges for that file — in-diff on overlap, pre-existing otherwise, regardless
    of the reviewer's claim."""

    def setUp(self):
        self.ranges = {"foo.py": [(12, 16)]}

    def test_inside_range_is_in_diff(self):
        f = _finding(file="foo.py", lines="13-14")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "in-diff")

    def test_single_line_on_boundary_is_in_diff(self):
        f = _finding(file="foo.py", lines="12")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "in-diff")

    def test_outside_range_is_pre_existing(self):
        f = _finding(file="foo.py", lines="40-42")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "pre-existing")

    def test_file_not_in_diff_is_pre_existing(self):
        f = _finding(file="other.py", lines="13")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "pre-existing")

    def test_reviewer_in_diff_claim_overridden_when_lines_outside(self):
        # Reviewer optimistically labels it in-diff, but the lines fall outside
        # every changed range — the runner overrides to pre-existing.
        f = _finding(file="foo.py", lines="40", provenance="in-diff")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "pre-existing")

    def test_any_range_intersecting_is_in_diff(self):
        # The real-world motivating case: a comma-separated location where
        # only the second range falls inside the diff must still resolve to
        # in-diff — not silently fall through to pre-existing.
        f = _finding(file="foo.py", lines="1-5,13-14")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "in-diff")

    def test_no_range_intersecting_is_pre_existing(self):
        f = _finding(file="foo.py", lines="1-5,40-42")
        self.assertEqual(forge_run.verify_provenance(f, self.ranges), "pre-existing")


class DeriveDispositionTests(unittest.TestCase):
    """derive_disposition: the four-quadrant matrix over verified provenance and
    the contract_ref-gated impact."""

    def test_in_diff_contract_breaking_is_fix(self):
        f = _finding(provenance="in-diff", impact="contract-breaking",
                     contract_ref="AC1: acceptance passes")
        self.assertEqual(forge_run.derive_disposition(f), "fix")

    def test_in_diff_improvement_is_defer(self):
        f = _finding(provenance="in-diff", impact="improvement", contract_ref=None)
        self.assertEqual(forge_run.derive_disposition(f), "defer")

    def test_pre_existing_contract_breaking_is_halt(self):
        f = _finding(provenance="pre-existing", impact="contract-breaking",
                     contract_ref="§ Disposition matrix")
        self.assertEqual(forge_run.derive_disposition(f), "halt")

    def test_pre_existing_improvement_is_defer(self):
        f = _finding(provenance="pre-existing", impact="improvement", contract_ref=None)
        self.assertEqual(forge_run.derive_disposition(f), "defer")

    def test_null_contract_ref_downgrades_contract_breaking_to_defer(self):
        # in-diff + contract-breaking would be `fix`, but a null contract_ref
        # strips the contract-breaking claim (named-evidence rule) → improvement
        # → defer.
        f = _finding(provenance="in-diff", impact="contract-breaking", contract_ref=None)
        self.assertEqual(forge_run.derive_disposition(f), "defer")

    def test_null_contract_ref_downgrades_pre_existing_halt_to_defer(self):
        # pre-existing + contract-breaking would be `halt`, but a null contract_ref
        # downgrades it to improvement → defer, never a scope-halt on unnamed
        # evidence.
        f = _finding(provenance="pre-existing", impact="contract-breaking",
                     contract_ref=None)
        self.assertEqual(forge_run.derive_disposition(f), "defer")


class ParseVerdictTests(unittest.TestCase):
    """parse_verdict on the per-finding schema: pass, a well-formed findings
    verdict, last-object-wins, and the loud contract errors."""

    def test_bare_pass(self):
        v = forge_run.parse_verdict('{"verdict": "pass"}')
        self.assertEqual(v.kind, "pass")
        self.assertEqual(v.findings, [])

    def test_findings_parsed_into_finding_objects(self):
        msg = (
            "Here is my review.\n\n"
            "```json\n"
            '{"verdict": "findings", "findings": ['
            '{"id": "f1", "summary": "missing guard", '
            '"location": {"file": "a.py", "lines": "3-5"}, '
            '"provenance": "in-diff", "impact": "contract-breaking", '
            '"contract_ref": "AC1: guard present", '
            '"repair_task": null}]}\n'
            "```\nThat is all.\n"
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertEqual(len(v.findings), 1)
        f = v.findings[0]
        self.assertIsInstance(f, forge_common.Finding)
        self.assertEqual(f.id, "f1")
        self.assertEqual(f.summary, "missing guard")
        self.assertEqual(f.file, "a.py")
        self.assertEqual(f.lines, "3-5")
        self.assertEqual(f.provenance, "in-diff")
        self.assertEqual(f.impact, "contract-breaking")
        self.assertEqual(f.contract_ref, "AC1: guard present")

    def test_improvement_finding_may_omit_location(self):
        # Only contract-breaking findings must carry a location; an improvement
        # finding without one parses (file/lines default to None).
        msg = (
            '{"verdict": "findings", "findings": ['
            '{"id": "f1", "summary": "nit", "impact": "improvement", '
            '"contract_ref": null}]}'
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertIsNone(v.findings[0].file)
        self.assertIsNone(v.findings[0].lines)

    def test_last_matching_object_wins(self):
        msg = (
            '{"verdict": "pass"}\n'
            "on reflection...\n"
            '{"verdict": "findings", "findings": ['
            '{"id": "f1", "summary": "x", '
            '"location": {"file": "a.py", "lines": "1"}, '
            '"impact": "improvement", "contract_ref": null}]}'
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertEqual(v.findings[0].id, "f1")

    def test_unparseable_prose_raises_naming_cause(self):
        with self.assertRaises(RuntimeError) as ctx:
            forge_run.parse_verdict("Looks good to me, ship it.")
        self.assertIn("verdict", str(ctx.exception).lower())

    def test_malformed_json_raises(self):
        with self.assertRaises(RuntimeError):
            forge_run.parse_verdict('{"verdict": ')

    def test_contract_breaking_missing_location_parses_but_flags_as_defect(self):
        # Absent location no longer raises at parse time (Location parsing
        # spec, 2026-08-21) — it parses fine, with lines=None, and surfaces
        # as a validate_locations defect instead, retried the same way as a
        # coverage defect rather than crashing the whole review.
        msg = (
            '{"verdict": "findings", "findings": ['
            '{"id": "f1", "summary": "broken", '
            '"impact": "contract-breaking", "contract_ref": "AC1"}]}'
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.kind, "findings")
        self.assertIsNone(v.findings[0].lines)
        defects = forge_run.validate_locations(v)
        self.assertEqual(len(defects), 1)
        self.assertIn("f1", defects[0])
        self.assertIn("no location", defects[0])

    def test_contract_breaking_missing_lines_parses_but_flags_as_defect(self):
        # location present but lines omitted is still incomplete for a
        # contract-breaking finding — same defect treatment as fully absent.
        msg = (
            '{"verdict": "findings", "findings": ['
            '{"id": "f1", "summary": "broken", '
            '"location": {"file": "a.py"}, '
            '"impact": "contract-breaking", "contract_ref": "AC1"}]}'
        )
        v = forge_run.parse_verdict(msg)
        self.assertEqual(v.findings[0].file, "a.py")
        self.assertIsNone(v.findings[0].lines)
        defects = forge_run.validate_locations(v)
        self.assertEqual(len(defects), 1)


class ValidateLocationsTests(unittest.TestCase):
    """validate_locations: a verdict validation defect for a contract-breaking
    finding with an absent or unparseable location.lines; an improvement
    finding is never checked (it may carry no location and defers
    regardless of provenance)."""

    def test_pass_verdict_is_valid(self):
        v = forge_common.Verdict(kind="pass")
        self.assertEqual(forge_run.validate_locations(v), [])

    def test_contract_breaking_with_parseable_location_is_valid(self):
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(impact="contract-breaking", contract_ref="AC1",
                     lines="120-152,182-199"),
        ])
        self.assertEqual(forge_run.validate_locations(v), [])

    def test_contract_breaking_absent_location_is_defect(self):
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(impact="contract-breaking", contract_ref="AC1", lines=None),
        ])
        defects = forge_run.validate_locations(v)
        self.assertEqual(len(defects), 1)
        self.assertIn("no location", defects[0])

    def test_contract_breaking_unparseable_location_is_defect(self):
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(impact="contract-breaking", contract_ref="AC1", lines="abc"),
        ])
        defects = forge_run.validate_locations(v)
        self.assertEqual(len(defects), 1)
        self.assertIn("unparseable", defects[0])

    def test_improvement_with_no_location_is_not_a_defect(self):
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(impact="improvement", contract_ref=None, lines=None),
        ])
        self.assertEqual(forge_run.validate_locations(v), [])

    def test_improvement_with_unparseable_location_is_not_a_defect(self):
        # Only a contract-breaking claim requires a parseable location; an
        # improvement finding's location, if present at all, is never
        # validated.
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(impact="improvement", contract_ref=None, lines="abc"),
        ])
        self.assertEqual(forge_run.validate_locations(v), [])

    def test_multiple_defects_named_individually(self):
        v = forge_common.Verdict(kind="findings", findings=[
            _finding(id="f1", impact="contract-breaking", contract_ref="AC1",
                     lines=None),
            _finding(id="f2", impact="contract-breaking", contract_ref="AC2",
                     lines="abc"),
        ])
        defects = forge_run.validate_locations(v)
        self.assertEqual(len(defects), 2)


class ClassifyFindingsTests(unittest.TestCase):
    """classify_findings: end-to-end, sets each finding's runner-verified
    provenance and derived disposition against the actual diff."""

    def test_pass_verdict_returns_unchanged(self):
        v = forge_common.Verdict(kind="pass")
        out = forge_run.classify_findings(v, DIFF_SINGLE)
        self.assertIs(out, v)
        self.assertEqual(out.kind, "pass")

    def test_sets_verified_provenance_and_disposition(self):
        # f1: in the diff (foo.py 13 ∈ [12,16]) + contract-breaking → fix.
        # f2: claims in-diff but foo.py 40 is outside → overridden pre-existing;
        #     contract-breaking → halt.
        f1 = _finding(id="f1", file="foo.py", lines="13", provenance="pre-existing",
                      impact="contract-breaking", contract_ref="AC1")
        f2 = _finding(id="f2", file="foo.py", lines="40", provenance="in-diff",
                      impact="contract-breaking", contract_ref="AC2")
        v = forge_common.Verdict(kind="findings", findings=[f1, f2])
        out = forge_run.classify_findings(v, DIFF_SINGLE)
        self.assertEqual(f1.provenance, "in-diff")
        self.assertEqual(f1.disposition, "fix")
        self.assertEqual(f2.provenance, "pre-existing")
        self.assertEqual(f2.disposition, "halt")

    def test_null_contract_ref_defers_in_classify(self):
        f = _finding(id="f1", file="foo.py", lines="13", provenance="in-diff",
                     impact="contract-breaking", contract_ref=None)
        v = forge_common.Verdict(kind="findings", findings=[f])
        forge_run.classify_findings(v, DIFF_SINGLE)
        self.assertEqual(f.provenance, "in-diff")
        self.assertEqual(f.disposition, "defer")


class ClassifyFindingsResolvedLabelTests(unittest.TestCase):
    """classify_findings' resolved-label filter: a finding labeled
    convergence=="resolved" is dropped before disposition when its canonical
    id (carried_from or id) is a member of carried_ids — behaving identically
    to an omitted finding (Convergence label honored)."""

    def test_resolved_finding_dropped_when_canonical_id_carried(self):
        f = _finding(id="f1", convergence="resolved",
                     impact="contract-breaking", contract_ref="AC1")
        v = forge_common.Verdict(kind="findings", findings=[f])
        out = forge_run.classify_findings(v, DIFF_SINGLE, carried_ids={"f1"})
        self.assertEqual(out.findings, [])
        self.assertIsNone(f.disposition)  # never dispositioned — dropped first

    def test_resolved_label_ignored_when_id_not_in_carried_set(self):
        # The runner never tracked f1 as outstanding — a resolved label on it
        # is meaningless, so it's dispositioned normally rather than dropped.
        f = _finding(id="f1", convergence="resolved", file="foo.py", lines="13",
                     provenance="pre-existing", impact="contract-breaking",
                     contract_ref="AC1")
        v = forge_common.Verdict(kind="findings", findings=[f])
        out = forge_run.classify_findings(v, DIFF_SINGLE, carried_ids={"other"})
        self.assertEqual(out.findings, [f])
        self.assertEqual(f.provenance, "in-diff")
        self.assertEqual(f.disposition, "fix")

    def test_resolved_matched_via_carried_from_not_raw_id(self):
        # The reviewer re-issued the original finding under a new id; the
        # resolved label must match against carried_from, the canonical id
        # the runner actually tracked.
        f = _finding(id="f1-new", carried_from="orig1", convergence="resolved",
                     impact="contract-breaking", contract_ref="AC1")
        v = forge_common.Verdict(kind="findings", findings=[f])
        out = forge_run.classify_findings(v, DIFF_SINGLE, carried_ids={"orig1"})
        self.assertEqual(out.findings, [])

    def test_carried_ids_none_preserves_todays_behavior(self):
        # No carried_ids supplied (the default) -> the resolved label is
        # never honored, exactly like before this filter existed.
        f = _finding(id="f1", convergence="resolved", file="foo.py", lines="13",
                     provenance="pre-existing", impact="contract-breaking",
                     contract_ref="AC1")
        v = forge_common.Verdict(kind="findings", findings=[f])
        out = forge_run.classify_findings(v, DIFF_SINGLE)
        self.assertEqual(out.findings, [f])
        self.assertEqual(f.disposition, "fix")

    def test_falsely_resolved_finding_reappearing_trips_regression(self):
        # Attempt 1: a real fix finding f1 is outstanding after review.
        state = forge_run.ConvergenceState()
        f1_attempt1 = _finding(id="f1", file="foo.py", lines="13",
                                provenance="in-diff", impact="contract-breaking",
                                contract_ref="AC1")
        v1 = forge_common.Verdict(kind="findings", findings=[f1_attempt1])
        forge_run.classify_findings(v1, DIFF_SINGLE, carried_ids=state.carried_ids)
        forge_run.advance_state(state, v1.findings, True)
        self.assertIn("f1", state.carried_ids)

        # Attempt 2: the reviewer (falsely) labels f1 resolved; f1's canon is
        # a member of the carried set from attempt 1, so the guard honors the
        # label and drops it — this attempt converges with nothing carried.
        f1_attempt2 = _finding(id="f1", convergence="resolved", file="foo.py",
                                lines="13", impact="contract-breaking",
                                contract_ref="AC1")
        v2 = forge_common.Verdict(kind="findings", findings=[f1_attempt2])
        forge_run.classify_findings(v2, DIFF_SINGLE, carried_ids=state.carried_ids)
        self.assertEqual(v2.findings, [])
        action, halt_reason = forge_run.convergence_decision(
            v2.findings, state, True, 2, "auto"
        )
        self.assertEqual(action, "pass")
        forge_run.advance_state(state, v2.findings, True)
        self.assertIn("f1", state.resolved_ids)

        # Attempt 3: f1 reappears for real (the resolved claim was false) —
        # the existing regression rule catches it via the runner's own
        # resolved-id set, no second mechanism required.
        f1_attempt3 = _finding(id="f1", file="foo.py", lines="13",
                                provenance="in-diff", impact="contract-breaking",
                                contract_ref="AC1")
        v3 = forge_common.Verdict(kind="findings", findings=[f1_attempt3])
        forge_run.classify_findings(v3, DIFF_SINGLE, carried_ids=state.carried_ids)
        action, halt_reason = forge_run.convergence_decision(
            v3.findings, state, True, 3, "auto"
        )
        self.assertEqual(action, "halt")
        self.assertEqual(halt_reason, "regression")


if __name__ == "__main__":
    unittest.main()
