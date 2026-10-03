"""Tests for scripts/forge_planreview.py — plan review verdict validation and
disposition (spec: `pipeline` "Plan review", `execution` "Plan review
verdict").

Loaded the same way the other scripts/*.py suites load their module: scripts/
is not a package, so it is put on sys.path.
"""
import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
DOCREVIEW = str(SCRIPTS_DIR / "forge_docreview.py")

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


SPEC_FIXTURE = """# Spec

## Alpha

Alpha requires things.

### Inner rules

Detail requires more.

## Beta

Beta requires others.

## Gamma

Unnamed by any task.
"""

PLAN_FIXTURE = """# Plan

**Goal:** goal text
**Global Constraints:**
- Stay stdlib only.

### Task 1: One
- [ ] Done

**Files:**
- Modify: `x.py`

**Spec:** Alpha, Inner rules

**Tests:**
- the first case
- the second case

**Acceptance:**
- `python3 -c "print(1)"` prints `1`
- the prose clause holds

**Tier:** standard

### Task 2: Two
- [ ] Done

**Files:**
- Modify: `y.py`

**Spec:** Alpha

**Tests:**
- another case

**Acceptance:**
- `python3 -c "print(2)"` passes

**Tier:** standard
"""


class PlanCliMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-planreview-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec_path = self.write("spec.md", SPEC_FIXTURE)
        self.plan_path = self.write("plan.md", PLAN_FIXTURE)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, DOCREVIEW] + args, capture_output=True, text=True,
        )


class BuildPacketTests(PlanCliMixin, unittest.TestCase):
    def packet(self):
        return p.build_packet(self.plan_path, self.spec_path)

    def test_packet_names_both_paths_and_no_body_text(self):
        packet = self.packet()
        self.assertIn(self.plan_path, packet)
        self.assertIn(self.spec_path, packet)
        self.assertNotIn("Alpha requires things", packet)
        self.assertNotIn("goal text", packet)
        self.assertNotIn("Modify: `x.py`", packet)

    def test_section_table_lists_each_heading_with_its_tasks(self):
        packet = self.packet()
        self.assertIn("- Alpha — tasks 1, 2", packet)
        self.assertIn("- Inner rules — tasks 1", packet)
        self.assertNotIn("Gamma", packet)

    def test_promise_table_lists_every_id_with_text(self):
        packet = self.packet()
        for pid, text in [
            ("g1", "Stay stdlib only."),
            ("t1.t1", "the first case"),
            ("t1.t2", "the second case"),
            ("t1.c1", 'python3 -c "print(1)"'),
            ("t1.a1", "the prose clause holds"),
            ("t2.t1", "another case"),
            ("t2.c1", 'python3 -c "print(2)"'),
        ]:
            self.assertRegex(packet, r"(?m)^- `{}` .*{}".format(
                pid.replace(".", r"\."), __import__("re").escape(text)))

    def test_packet_states_both_reviewer_questions(self):
        packet = self.packet()
        self.assertIn("Does any plan element contradict the spec", packet)
        self.assertIn(
            "Is each requirement in a named section covered by a promise", packet
        )

    def test_packet_states_only_promises_cover(self):
        packet = self.packet()
        self.assertIn(
            "Only a test case, an acceptance clause or a global constraint "
            "covers a requirement", packet,
        )

    def test_packet_lists_verdict_fields_kinds_and_na_rule(self):
        packet = self.packet()
        for token in ("verdict", "coverage", "requirements", "covered_by", "na",
                      "findings", "proposed_amendment", "task"):
            self.assertIn("`{}`".format(token), packet)
        for kind in ("uncovered", "contradiction", "spec-defect"):
            self.assertIn(kind, packet)
        self.assertIn("`na` is a non-empty reason with an empty `covered_by`", packet)

    def test_plan_naming_no_section_raises_naming_cause_and_fix(self):
        plan = PLAN_FIXTURE.replace("**Spec:** Alpha, Inner rules\n\n", "")
        plan = plan.replace("**Spec:** Alpha\n\n", "")
        path = self.write("nospec.md", plan)
        with self.assertRaises(RuntimeError) as ctx:
            p.build_packet(path, self.spec_path)
        self.assertIn("**Spec:**", str(ctx.exception))


class PlanReviewCliTests(PlanCliMixin, unittest.TestCase):
    def verdict_file(self, verdict, name="verdict.json"):
        return self.write(name, json.dumps(verdict))

    def pass_verdict(self):
        reqs = lambda: [{"requirement": "r", "covered_by": ["t1.t1"], "na": None}]
        return {"verdict": "pass", "findings": [], "coverage": [
            {"section": "Alpha", "requirements": reqs()},
            {"section": "Inner rules", "requirements": reqs()},
        ]}

    def test_plan_with_spec_and_out_writes_packet_and_exits_zero(self):
        out = os.path.join(self.tmp, "packet.md")
        result = self.run_cli(
            ["--plan", self.plan_path, "--spec", self.spec_path, "--out", out])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertIn("t1.c1", f.read())

    def test_plan_naming_no_section_exits_one_without_packet(self):
        plan = PLAN_FIXTURE.replace("**Spec:** Alpha, Inner rules\n\n", "")
        plan = plan.replace("**Spec:** Alpha\n\n", "")
        path = self.write("nospec.md", plan)
        out = os.path.join(self.tmp, "packet.md")
        result = self.run_cli(["--plan", path, "--spec", self.spec_path, "--out", out])
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(out))
        self.assertIn("no task names a spec section", result.stderr)
        self.assertIn("add **Spec:** lines naming the sections the tasks implement",
                      result.stderr)

    def test_unparseable_acceptance_clause_exits_one_naming_task_and_clause(self):
        plan = PLAN_FIXTURE.replace("`python3 -c \"print(2)\"` passes",
                                    "`python3 -c \"print(2)\"` works fine")
        path = self.write("badacc.md", plan)
        result = self.run_cli(["--plan", path, "--spec", self.spec_path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("task 2", result.stderr)
        self.assertIn("works fine", result.stderr)

    def test_valid_pass_verdict_writes_empty_amend_and_surface(self):
        out = os.path.join(self.tmp, "decision.json")
        result = self.run_cli([
            "--plan", self.plan_path, "--spec", self.spec_path,
            "--verdict", self.verdict_file(self.pass_verdict()), "--out", out])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as f:
            decision = json.load(f)
        self.assertEqual(decision, {
            "valid": True, "defects": [], "amend": [], "surface": []})

    def test_valid_findings_verdict_places_each_kind(self):
        v = self.pass_verdict()
        v["verdict"] = "findings"
        v["coverage"][0]["requirements"].append(
            {"requirement": "u", "covered_by": [], "na": None})
        def finding(fid, kind, section, task):
            return {"id": fid, "summary": "s", "kind": kind, "section": section,
                    "task": task, "evidence": "e", "proposed_amendment": "pa"}
        v["findings"] = [
            finding("f1", "uncovered", "Alpha", 1),
            finding("f2", "contradiction", "Beta", 2),
            finding("f3", "spec-defect", "Gamma", None),
        ]
        out = os.path.join(self.tmp, "decision.json")
        result = self.run_cli([
            "--plan", self.plan_path, "--spec", self.spec_path,
            "--verdict", self.verdict_file(v), "--out", out])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, encoding="utf-8") as f:
            decision = json.load(f)
        self.assertEqual([x["id"] for x in decision["amend"]], ["f1", "f2"])
        self.assertEqual([x["id"] for x in decision["surface"]], ["f3"])

    def test_invalid_verdict_lists_every_defect_and_omits_dispositions(self):
        v = self.pass_verdict()
        v["coverage"][0]["requirements"][0]["covered_by"] = ["t9.t9"]
        v["coverage"].pop()
        out = os.path.join(self.tmp, "decision.json")
        result = self.run_cli([
            "--plan", self.plan_path, "--spec", self.spec_path,
            "--verdict", self.verdict_file(v), "--out", out])
        self.assertEqual(result.returncode, 1)
        self.assertIn("t9.t9", result.stderr)
        self.assertIn("Inner rules", result.stderr)
        with open(out, encoding="utf-8") as f:
            decision = json.load(f)
        self.assertFalse(decision["valid"])
        self.assertEqual(len(decision["defects"]), 2)
        self.assertNotIn("amend", decision)
        self.assertNotIn("surface", decision)

    def test_verdict_file_not_an_object_exits_one_with_named_error(self):
        path = self.write("list.json", "[1, 2]")
        result = self.run_cli([
            "--plan", self.plan_path, "--spec", self.spec_path, "--verdict", path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not contain a JSON object", result.stderr)

    def test_plan_without_spec_exits_nonzero(self):
        result = self.run_cli(["--plan", self.plan_path])
        self.assertNotEqual(result.returncode, 0)
