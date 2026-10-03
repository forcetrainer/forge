"""Tests for scripts/forge_planreview.py — plan review verdict validation and
disposition (spec: `pipeline` "Plan review", `execution` "Plan review
verdict").

Loaded the same way the other scripts/*.py suites load their module: scripts/
is not a package, so it is put on sys.path.
"""
import copy
import pathlib
import sys
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
import forge_checklist  # noqa: E402
import forge_planreview as p  # noqa: E402

SECTION_TABLE = [
    forge_checklist.SectionEntry("Alpha", [1]),
    forge_checklist.SectionEntry("Beta", [2]),
]
PROMISE_IDS = ["g1", "t1.t1", "t2.a1"]
SPEC_HEADINGS = ["Alpha", "Beta", "Gamma"]
TASK_NUMBERS = [1, 2]


def _valid_verdict():
    return {
        "verdict": "pass",
        "coverage": [
            {"section": "Alpha", "requirements": [
                {"requirement": "r1", "covered_by": ["g1", "t1.t1"], "na": None},
            ]},
            {"section": "Beta", "requirements": [
                {"requirement": "r2", "covered_by": ["t2.a1"], "na": None},
                {"requirement": "r3", "covered_by": [], "na": "not built here"},
            ]},
        ],
        "findings": [],
    }


def _finding(**overrides):
    base = {
        "id": "f1", "summary": "s", "kind": "contradiction", "section": "Alpha",
        "task": 1, "evidence": "e", "proposed_amendment": "pa",
    }
    base.update(overrides)
    return base


def _uncovered_verdict():
    """Beta's r2 is uncovered, with the matching `uncovered` finding."""
    v = _valid_verdict()
    v["verdict"] = "findings"
    v["coverage"][1]["requirements"][0]["covered_by"] = []
    v["findings"] = [_finding(kind="uncovered", section="Beta", task=2)]
    return v


def _validate(verdict):
    return p.validate_verdict(
        verdict, SECTION_TABLE, PROMISE_IDS, SPEC_HEADINGS, TASK_NUMBERS,
    )


def _with_findings(*findings):
    v = _valid_verdict()
    v["verdict"] = "findings"
    v["findings"] = list(findings)
    return v


class CoverageTests(unittest.TestCase):
    def test_pass_with_every_section_answered_is_valid(self):
        result = _validate(_valid_verdict())
        self.assertTrue(result.valid, result.defects)
        self.assertEqual(result.defects, [])
        self.assertEqual(result.findings, [])

    def test_missing_coverage_entry_names_the_section(self):
        v = _valid_verdict()
        del v["coverage"][1]
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("Beta" in d and "missing" in d for d in result.defects))

    def test_duplicate_coverage_entry_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"].append(copy.deepcopy(v["coverage"][0]))
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("duplicate" in d and "Alpha" in d for d in result.defects))

    def test_coverage_entry_for_section_outside_table_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"].append({"section": "Gamma", "requirements": [
            {"requirement": "r", "covered_by": ["g1"], "na": None},
        ]})
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("Gamma" in d and "not in" in d for d in result.defects))

    def test_empty_requirements_list_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][0]["requirements"] = []
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("Alpha" in d and "requirements" in d for d in result.defects))

    def test_covered_by_id_not_in_promise_table_names_the_id(self):
        v = _valid_verdict()
        v["coverage"][0]["requirements"][0]["covered_by"] = ["g99"]
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("g99" in d for d in result.defects))

    def test_covered_by_id_differing_only_in_case_is_not_normalized(self):
        v = _valid_verdict()
        v["coverage"][0]["requirements"][0]["covered_by"] = ["G1"]
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("'G1'" in d for d in result.defects))

    def test_na_with_non_empty_covered_by_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][0]["requirements"][0]["na"] = "reason"
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("na" in d and "covered_by" in d for d in result.defects))

    def test_empty_na_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][1]["requirements"][1]["na"] = ""
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("na" in d for d in result.defects))

    def test_whitespace_na_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][1]["requirements"][1]["na"] = "   "
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("na" in d for d in result.defects))

    def test_uncovered_requirement_without_finding_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][1]["requirements"][0]["covered_by"] = []
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("Beta" in d and "uncovered" in d for d in result.defects))

    def test_uncovered_finding_without_uncovered_requirement_is_a_defect(self):
        v = _with_findings(_finding(kind="uncovered", section="Alpha", task=1))
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("f1" in d and "no uncovered" in d for d in result.defects))

    def test_uncovered_requirement_with_matching_finding_is_valid(self):
        result = _validate(_uncovered_verdict())
        self.assertTrue(result.valid, result.defects)
        self.assertEqual(len(result.findings), 1)


class VerdictEnvelopeTests(unittest.TestCase):
    def test_pass_with_a_finding_is_a_defect(self):
        v = _valid_verdict()
        v["findings"] = [_finding()]
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("pass" in d and "findings" in d for d in result.defects))

    def test_pass_with_an_uncovered_requirement_is_a_defect(self):
        v = _valid_verdict()
        v["coverage"][1]["requirements"][0]["covered_by"] = []
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("pass" in d and "uncovered" in d for d in result.defects))

    def test_findings_verdict_with_no_findings_is_a_defect(self):
        v = _valid_verdict()
        v["verdict"] = "findings"
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("'findings'" in d and "empty" in d for d in result.defects))

    def test_verdict_value_outside_enum_is_a_defect(self):
        v = _valid_verdict()
        v["verdict"] = "banana"
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("banana" in d for d in result.defects))

    def test_non_object_verdict_is_a_defect_without_raising(self):
        for bad in ([], "x", None, 42, True):
            result = _validate(bad)
            self.assertFalse(result.valid)
            self.assertTrue(result.defects)

    def test_non_list_coverage_is_a_defect_without_raising(self):
        v = _valid_verdict()
        v["coverage"] = "nope"
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("coverage" in d for d in result.defects))

    def test_non_list_findings_is_a_defect_without_raising(self):
        v = _valid_verdict()
        v["findings"] = {"id": "f1"}
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("findings" in d for d in result.defects))

    def test_three_independent_defects_are_all_reported(self):
        v = _valid_verdict()
        del v["coverage"][1]                                           # missing Beta
        v["coverage"][0]["requirements"][0]["covered_by"] = ["g99"]    # unknown id
        v["findings"] = [_finding()]                                   # pass + finding
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("Beta" in d for d in result.defects))
        self.assertTrue(any("g99" in d for d in result.defects))
        self.assertTrue(any("pass" in d and "findings" in d for d in result.defects))
        self.assertGreaterEqual(len(result.defects), 3)


class FindingTests(unittest.TestCase):
    def test_kind_outside_the_three_values_is_a_defect_and_not_reclassified(self):
        finding = _finding(kind="sufficiency")
        result = _validate(_with_findings(finding))
        self.assertFalse(result.valid)
        self.assertTrue(any("sufficiency" in d for d in result.defects))
        self.assertEqual(finding["kind"], "sufficiency")
        self.assertTrue(all(f["kind"] == "sufficiency" for f in result.findings))

    def test_duplicate_finding_ids_are_a_defect(self):
        result = _validate(_with_findings(_finding(), _finding()))
        self.assertFalse(result.valid)
        self.assertTrue(any("f1" in d and "duplicate" in d for d in result.defects))

    def test_uncovered_finding_on_heading_outside_table_is_a_defect(self):
        v = _with_findings(_finding(kind="uncovered", section="Gamma", task=None))
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("f1" in d and "Gamma" in d for d in result.defects))

    def test_contradiction_citing_heading_outside_table_is_valid(self):
        result = _validate(_with_findings(_finding(section="Gamma", task=None)))
        self.assertTrue(result.valid, result.defects)

    def test_spec_defect_citing_heading_outside_table_is_valid(self):
        result = _validate(
            _with_findings(_finding(kind="spec-defect", section="Gamma", task=None))
        )
        self.assertTrue(result.valid, result.defects)

    def test_section_matching_no_spec_heading_is_a_defect(self):
        result = _validate(_with_findings(_finding(section="Nowhere")))
        self.assertFalse(result.valid)
        self.assertTrue(any("Nowhere" in d for d in result.defects))

    def test_task_naming_no_task_in_the_plan_is_a_defect(self):
        result = _validate(_with_findings(_finding(task=9)))
        self.assertFalse(result.valid)
        self.assertTrue(any("f1" in d and "9" in d for d in result.defects))

    def test_null_task_is_accepted(self):
        result = _validate(_with_findings(_finding(task=None)))
        self.assertTrue(result.valid, result.defects)

    def test_uncovered_finding_task_not_naming_its_section_is_a_defect(self):
        v = _uncovered_verdict()
        v["findings"][0]["task"] = 1  # Beta is named by task 2 only
        result = _validate(v)
        self.assertFalse(result.valid)
        self.assertTrue(any("f1" in d and "task" in d for d in result.defects))

    def test_uncovered_finding_with_null_task_is_valid(self):
        v = _uncovered_verdict()
        v["findings"][0]["task"] = None
        result = _validate(v)
        self.assertTrue(result.valid, result.defects)

    def test_missing_or_blank_required_fields_name_the_finding(self):
        for field in ("summary", "evidence", "proposed_amendment"):
            missing = _finding()
            del missing[field]
            blank = _finding(**{field: "  "})
            for finding in (missing, blank):
                result = _validate(_with_findings(finding))
                self.assertFalse(result.valid)
                self.assertTrue(
                    any("f1" in d and field in d for d in result.defects),
                    (field, result.defects),
                )


class DisposeTests(unittest.TestCase):
    def test_routes_uncovered_and_contradiction_to_amend_and_spec_defect_to_surface(self):
        findings = [
            _finding(id="a", kind="uncovered"),
            _finding(id="b", kind="contradiction"),
            _finding(id="c", kind="spec-defect"),
        ]
        disp = p.dispose(findings)
        self.assertEqual([f["id"] for f in disp.amend], ["a", "b"])
        self.assertEqual([f["id"] for f in disp.surface], ["c"])


if __name__ == "__main__":
    unittest.main()
