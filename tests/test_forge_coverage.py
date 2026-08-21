"""Coverage in the reviewer verdict contract: CoverageEntry/Verdict.coverage
parsing (tolerant at parse time) and validate_coverage's defect detection
(missing/unknown/duplicate ids, empty evidence, unbacked "violated"), plus the
forge_dispose.py CLI's optional --checklist flag and its decision.json fields.
Mirrors tests/test_forge_dispose.py's CLI style for the subprocess-boundary
cases; the parse/validate unit tests call forge_dispose directly (no codex, no
git needed for those)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS_DIR, "forge_dispose.py")

sys.path.insert(0, SCRIPTS_DIR)
import forge_common  # noqa: E402
import forge_dispose  # noqa: E402


def _checklist(ids):
    return [{"id": i, "source": "spec", "text": i} for i in ids]


class ParseVerdictCoverageTests(unittest.TestCase):
    """parse_verdict / _verdict_from_obj populate Verdict.coverage from JSON,
    and tolerate absent coverage as an empty list at parse time — validation,
    not parsing, is what rejects it."""

    def test_parse_verdict_populates_coverage_on_pass(self):
        msg = json.dumps({
            "verdict": "pass",
            "coverage": [
                {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:2"},
            ],
        })
        verdict = forge_dispose.parse_verdict(msg)
        self.assertEqual(verdict.kind, "pass")
        self.assertEqual(len(verdict.coverage), 1)
        entry = verdict.coverage[0]
        self.assertIsInstance(entry, forge_common.CoverageEntry)
        self.assertEqual(entry.id, "spec:A")
        self.assertEqual(entry.status, "satisfied")
        self.assertEqual(entry.evidence, "foo.py:2")

    def test_parse_verdict_populates_coverage_on_findings(self):
        msg = json.dumps({
            "verdict": "findings",
            "coverage": [
                {"id": "spec:A", "status": "violated", "evidence": "see f1"},
            ],
            "findings": [
                {"id": "f1", "summary": "bug",
                 "location": {"file": "foo.py", "lines": "2-2"},
                 "impact": "contract-breaking", "contract_ref": "spec:A"},
            ],
        })
        verdict = forge_dispose.parse_verdict(msg)
        self.assertEqual(verdict.kind, "findings")
        self.assertEqual([e.id for e in verdict.coverage], ["spec:A"])

    def test_parse_verdict_tolerates_absent_coverage_as_empty_list(self):
        msg = json.dumps({"verdict": "pass"})
        verdict = forge_dispose.parse_verdict(msg)
        self.assertEqual(verdict.coverage, [])

    def test_parse_verdict_tolerates_absent_coverage_on_findings(self):
        msg = json.dumps({
            "verdict": "findings",
            "findings": [
                {"id": "f1", "summary": "bug",
                 "location": {"file": "foo.py", "lines": "2-2"},
                 "impact": "improvement"},
            ],
        })
        verdict = forge_dispose.parse_verdict(msg)
        self.assertEqual(verdict.kind, "findings")
        self.assertEqual(verdict.coverage, [])


class ValidateCoverageTests(unittest.TestCase):
    """validate_coverage's defect catalogue, independent of the CLI."""

    def _verdict(self, coverage, findings=None):
        return forge_common.Verdict(
            kind="findings" if findings else "pass",
            findings=findings or [],
            coverage=[
                forge_common.CoverageEntry(
                    id=e["id"], status=e["status"], evidence=e.get("evidence", "")
                )
                for e in coverage
            ],
        )

    def test_complete_coverage_is_valid(self):
        checklist = _checklist(["spec:A", "spec:B"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:2"},
            {"id": "spec:B", "status": "n/a", "evidence": "not touched by diff"},
        ])
        self.assertEqual(forge_dispose.validate_coverage(verdict, checklist), [])

    def test_missing_id_is_defect(self):
        checklist = _checklist(["spec:A", "spec:B"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:2"},
        ])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:B", defects[0])
        self.assertIn("missing", defects[0])

    def test_unknown_id_is_defect(self):
        checklist = _checklist(["spec:A"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:2"},
            {"id": "spec:ZZZ", "status": "satisfied", "evidence": "foo.py:5"},
        ])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:ZZZ", defects[0])
        self.assertIn("unknown", defects[0])

    def test_duplicate_id_is_defect(self):
        checklist = _checklist(["spec:A"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:2"},
            {"id": "spec:A", "status": "satisfied", "evidence": "foo.py:3"},
        ])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:A", defects[0])
        self.assertIn("duplicate", defects[0])

    def test_empty_evidence_is_defect(self):
        checklist = _checklist(["spec:A"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "satisfied", "evidence": "   "},
        ])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:A", defects[0])
        self.assertIn("evidence", defects[0])

    def test_violated_without_backing_finding_is_defect(self):
        checklist = _checklist(["spec:A"])
        verdict = self._verdict([
            {"id": "spec:A", "status": "violated", "evidence": "see review"},
        ])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:A", defects[0])
        self.assertIn("violated", defects[0])

    def test_violated_backed_by_finding_is_valid(self):
        checklist = _checklist(["spec:A"])
        finding = forge_common.Finding(
            id="f1", summary="bug", file="foo.py", lines="2-2",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="spec:A",
        )
        verdict = self._verdict(
            [{"id": "spec:A", "status": "violated", "evidence": "see f1"}],
            findings=[finding],
        )
        self.assertEqual(forge_dispose.validate_coverage(verdict, checklist), [])

    def test_coverage_required_on_pass_verdict_absent_is_defect(self):
        checklist = _checklist(["spec:A"])
        verdict = self._verdict([])
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:A", defects[0])
        self.assertIn("missing", defects[0])


class ForgeDisposeCLIChecklistTests(unittest.TestCase):
    """--checklist through the CLI boundary: decision.json fields, and byte-
    identical output to today's shape when the flag is absent."""

    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix="forge-coverage-repo-")
        self.addCleanup(shutil.rmtree, self.repo_dir, ignore_errors=True)
        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

        self.src_path = os.path.join(self.repo_dir, "src.txt")
        with open(self.src_path, "w") as f:
            f.write("line1\nline2\nline3\n")
        self._git("add", ".")
        self._git("commit", "-m", "base")
        self.base = self._git_output("rev-parse", "HEAD").strip()

        with open(self.src_path, "w") as f:
            f.write("line1\nCHANGED\nline3\n")

        self.workdir = tempfile.mkdtemp(prefix="forge-coverage-work-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)

    def _git(self, *args):
        subprocess.run(
            ["git"] + list(args), cwd=self.repo_dir,
            check=True, capture_output=True, text=True,
        )

    def _git_output(self, *args):
        result = subprocess.run(
            ["git"] + list(args), cwd=self.repo_dir,
            check=True, capture_output=True, text=True,
        )
        return result.stdout

    def _write_json(self, name, obj):
        path = os.path.join(self.workdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def run_dispose(self, args):
        return subprocess.run(
            [sys.executable, SCRIPT] + args,
            cwd=self.repo_dir, capture_output=True, text=True,
        )

    def _base_args(self, verdict_path, attempt=1, acceptance_ok="true",
                    autofix="auto", checklist_path=None):
        args = [
            "--verdict", verdict_path,
            "--base", self.base,
            "--attempt", str(attempt),
            "--acceptance-ok", acceptance_ok,
            "--autofix", autofix,
        ]
        if checklist_path:
            args += ["--checklist", checklist_path]
        return args

    def test_checklist_absent_omits_coverage_fields_byte_identical(self):
        v = self._write_json("v1.json", {"verdict": "pass"})
        without = self.run_dispose(self._base_args(v))
        self.assertEqual(without.returncode, 0, without.stderr)
        decision = json.loads(without.stdout)
        self.assertNotIn("coverage_valid", decision)
        self.assertNotIn("coverage_defects", decision)

        # Byte-identical to a second run with the exact same inputs (today's
        # output is deterministic for the same verdict/base/attempt/etc).
        again = self.run_dispose(self._base_args(v))
        self.assertEqual(without.stdout, again.stdout)

    def test_checklist_present_adds_fields_action_unchanged(self):
        v = self._write_json("v2.json", {
            "verdict": "findings",
            "coverage": [
                {"id": "spec:A", "status": "satisfied", "evidence": "src.txt:2"},
            ],
            "findings": [
                {"id": "f1", "summary": "bug",
                 "location": {"file": "src.txt", "lines": "2-2"},
                 "impact": "improvement"},
            ],
        })
        checklist = self._write_json("checklist.json", _checklist(["spec:A"]))

        without = self.run_dispose(self._base_args(v))
        d_without = json.loads(without.stdout)

        with_checklist = self.run_dispose(
            self._base_args(v, checklist_path=checklist)
        )
        self.assertEqual(with_checklist.returncode, 0, with_checklist.stderr)
        d_with = json.loads(with_checklist.stdout)

        self.assertEqual(d_with["action"], d_without["action"])
        self.assertTrue(d_with["coverage_valid"])
        self.assertEqual(d_with["coverage_defects"], [])

    def test_checklist_present_reports_defects_action_still_unchanged(self):
        v = self._write_json("v3.json", {
            "verdict": "findings",
            "coverage": [],
            "findings": [
                {"id": "f1", "summary": "bug",
                 "location": {"file": "src.txt", "lines": "2-2"},
                 "impact": "contract-breaking", "contract_ref": "AC-1"},
            ],
        })
        checklist = self._write_json("checklist2.json", _checklist(["spec:A"]))

        without = self.run_dispose(self._base_args(v))
        d_without = json.loads(without.stdout)

        with_checklist = self.run_dispose(
            self._base_args(v, checklist_path=checklist)
        )
        d_with = json.loads(with_checklist.stdout)

        self.assertEqual(d_with["action"], d_without["action"])
        self.assertEqual(d_with["action"], "rework")
        self.assertFalse(d_with["coverage_valid"])
        self.assertEqual(len(d_with["coverage_defects"]), 1)
        self.assertIn("spec:A", d_with["coverage_defects"][0])


if __name__ == "__main__":
    unittest.main()
