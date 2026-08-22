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

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)
from _forge_support import (  # noqa: E402
    forge_run, write_fake_codex, SCRIPT_PATH,
)


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


class ValidateLocationsUnitTests(unittest.TestCase):
    """validate_locations's defect catalogue (mirrors ValidateCoverageTests'
    shape) plus proof that forge-run.py's _review_with_coverage reuses it
    through the existing coverage-retry mechanism — no second retry path."""

    def _finding(self, **kw):
        base = dict(
            id="f1", summary="x", file="a.py", lines="12-20",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="AC1",
        )
        base.update(kw)
        return forge_common.Finding(**base)

    def test_contract_breaking_absent_location_is_defect(self):
        v = forge_common.Verdict(
            kind="findings", findings=[self._finding(lines=None)]
        )
        defects = forge_dispose.validate_locations(v)
        self.assertEqual(len(defects), 1)
        self.assertIn("f1", defects[0])

    def test_contract_breaking_unparseable_location_is_defect(self):
        v = forge_common.Verdict(
            kind="findings", findings=[self._finding(lines="12--20")]
        )
        defects = forge_dispose.validate_locations(v)
        self.assertEqual(len(defects), 1)

    def test_contract_breaking_comma_separated_location_is_valid(self):
        v = forge_common.Verdict(
            kind="findings",
            findings=[self._finding(lines="120-152,182-199")],
        )
        self.assertEqual(forge_dispose.validate_locations(v), [])

    def test_improvement_no_location_defers_not_a_defect(self):
        v = forge_common.Verdict(kind="findings", findings=[
            self._finding(impact="improvement", contract_ref=None, lines=None),
        ])
        self.assertEqual(forge_dispose.validate_locations(v), [])

    def test_review_with_coverage_reuses_existing_retry_no_second_mechanism(self):
        # forge-run.py's _review_with_coverage (Phase 13's coverage retry)
        # must be the sole mechanism location defects flow through — no
        # parallel "_review_with_location"-style function was added.
        self.assertTrue(hasattr(forge_run, "_review_with_coverage"))
        location_retry_fns = [
            name for name in dir(forge_run)
            if "location" in name.lower() and "retry" in name.lower()
        ]
        self.assertEqual(
            location_retry_fns, [],
            "a second retry mechanism was added: {}".format(location_retry_fns),
        )

        bad_msg = json.dumps({
            "verdict": "findings",
            "coverage": [],
            "findings": [{
                "id": "f1", "summary": "broken",
                "impact": "contract-breaking", "contract_ref": "AC1",
                "location": {"file": "a.py"},
            }],
        })
        good_msg = json.dumps({"verdict": "pass", "coverage": []})
        calls = []

        def dispatch_call(packet_path):
            calls.append(packet_path)
            msg = bad_msg if len(calls) == 1 else good_msg
            return forge_run.parse_verdict(msg)

        with tempfile.TemporaryDirectory() as d:
            packet_path = os.path.join(d, "packet.md")
            with open(packet_path, "w") as f:
                f.write("packet body")
            verdict, retried = forge_run._review_with_coverage(
                dispatch_call, packet_path, checklist=None, run_dir=d,
                label="task-1",
            )
        self.assertTrue(retried)
        self.assertEqual(verdict.kind, "pass")
        self.assertEqual(len(calls), 2)

    def test_review_with_coverage_second_invalid_location_is_contract_error(self):
        bad_msg = json.dumps({
            "verdict": "findings",
            "coverage": [],
            "findings": [{
                "id": "f1", "summary": "broken",
                "impact": "contract-breaking", "contract_ref": "AC1",
                "location": {"file": "a.py"},
            }],
        })

        def dispatch_call(packet_path):
            return forge_run.parse_verdict(bad_msg)

        with tempfile.TemporaryDirectory() as d:
            packet_path = os.path.join(d, "packet.md")
            with open(packet_path, "w") as f:
                f.write("packet body")
            with self.assertRaises(RuntimeError) as ctx:
                forge_run._review_with_coverage(
                    dispatch_call, packet_path, checklist=None, run_dir=d,
                    label="task-1",
                )
        self.assertIn("still invalid after one retry", str(ctx.exception))
        self.assertIn("f1", str(ctx.exception))


class ReviewKindGatingTests(unittest.TestCase):
    """Coverage on discovery only (Task 6): _verdict_defects/_review_with_
    coverage validate coverage only when review_kind="discovery"; a
    verification verdict with no coverage is accepted (no defect, no
    retry); a verification verdict that DOES carry coverage is still
    accepted, not penalized for the extra; location validation runs on
    both kinds regardless."""

    def _checklist_verdict_missing_coverage(self):
        checklist = _checklist(["spec:A"])
        verdict = forge_common.Verdict(kind="pass", findings=[], coverage=[])
        return checklist, verdict

    def test_discovery_missing_coverage_is_a_defect(self):
        checklist, verdict = self._checklist_verdict_missing_coverage()
        defects = forge_run._verdict_defects(verdict, checklist, review_kind="discovery")
        self.assertEqual(len(defects), 1)
        self.assertIn("spec:A", defects[0])
        self.assertIn("missing", defects[0])

    def test_discovery_is_the_default_review_kind(self):
        checklist, verdict = self._checklist_verdict_missing_coverage()
        defects = forge_run._verdict_defects(verdict, checklist)
        self.assertEqual(len(defects), 1)

    def test_verification_missing_coverage_is_accepted(self):
        checklist, verdict = self._checklist_verdict_missing_coverage()
        defects = forge_run._verdict_defects(
            verdict, checklist, review_kind="verification"
        )
        self.assertEqual(defects, [])

    def test_verification_with_coverage_is_accepted_not_rejected_for_extra(self):
        checklist = _checklist(["spec:A"])
        verdict = forge_common.Verdict(
            kind="pass", findings=[], coverage=[
                forge_common.CoverageEntry(
                    id="spec:A", status="satisfied", evidence="foo.py:2"
                ),
            ],
        )
        defects = forge_run._verdict_defects(
            verdict, checklist, review_kind="verification"
        )
        self.assertEqual(defects, [])

    def test_verification_bad_coverage_still_accepted(self):
        # Even coverage that WOULD be a defect on discovery (unknown id,
        # duplicate, empty evidence...) is simply not looked at on
        # verification — the coverage sweep itself is skipped, not merely
        # forgiven for specific defect kinds.
        checklist = _checklist(["spec:A"])
        verdict = forge_common.Verdict(
            kind="pass", findings=[], coverage=[
                forge_common.CoverageEntry(
                    id="spec:ZZZ", status="satisfied", evidence=""
                ),
            ],
        )
        defects = forge_run._verdict_defects(
            verdict, checklist, review_kind="verification"
        )
        self.assertEqual(defects, [])

    def test_location_defects_checked_on_both_kinds(self):
        finding = forge_common.Finding(
            id="f1", summary="x", file="a.py", lines=None,
            provenance="in-diff", impact="contract-breaking",
            contract_ref="AC1",
        )
        verdict = forge_common.Verdict(kind="findings", findings=[finding], coverage=[])
        for kind in ("discovery", "verification"):
            defects = forge_run._verdict_defects(verdict, None, review_kind=kind)
            self.assertEqual(len(defects), 1, kind)
            self.assertIn("f1", defects[0])

    def test_discovery_after_verification_still_demands_coverage(self):
        # A verification lap's leniency must not leak into the next
        # discovery lap (e.g. a fresh task after a prior task's rework).
        checklist, verdict = self._checklist_verdict_missing_coverage()
        self.assertEqual(
            forge_run._verdict_defects(verdict, checklist, review_kind="verification"),
            [],
        )
        defects = forge_run._verdict_defects(verdict, checklist, review_kind="discovery")
        self.assertEqual(len(defects), 1)

    def test_review_with_coverage_verification_no_retry_no_defect(self):
        checklist = _checklist(["spec:A"])
        msg = json.dumps({"verdict": "pass"})  # no coverage key at all
        calls = []

        def dispatch_call(packet_path):
            calls.append(packet_path)
            return forge_run.parse_verdict(msg)

        with tempfile.TemporaryDirectory() as d:
            packet_path = os.path.join(d, "packet.md")
            with open(packet_path, "w") as f:
                f.write("packet body")
            verdict, retried = forge_run._review_with_coverage(
                dispatch_call, packet_path, checklist, run_dir=d,
                label="task-1", review_kind="verification",
            )
        self.assertFalse(retried)
        self.assertEqual(verdict.kind, "pass")
        self.assertEqual(len(calls), 1)

    def test_review_with_coverage_discovery_missing_coverage_retries_then_errors(self):
        checklist = _checklist(["spec:A"])
        msg = json.dumps({"verdict": "pass"})  # no coverage key -> discovery defect

        def dispatch_call(packet_path):
            return forge_run.parse_verdict(msg)

        with tempfile.TemporaryDirectory() as d:
            packet_path = os.path.join(d, "packet.md")
            with open(packet_path, "w") as f:
                f.write("packet body")
            with self.assertRaises(RuntimeError) as ctx:
                forge_run._review_with_coverage(
                    dispatch_call, packet_path, checklist, run_dir=d,
                    label="task-1", review_kind="discovery",
                )
        self.assertIn("still invalid after one retry", str(ctx.exception))
        self.assertIn("spec:A", str(ctx.exception))


class ReviewKindPacketMarkerTests(unittest.TestCase):
    """Packets carry an explicit '## Review kind' marker naming discovery or
    verification, so the reviewer knows which verdict contract applies
    (Coverage on discovery only spec)."""

    def test_discovery_packet_carries_discovery_marker(self):
        packet = forge_common.rp.build_packet(
            "### Task 1: X\nbody\n", "abc123", "diff --git a/f b/f\n+x\n",
            review_kind="discovery",
        )
        self.assertIn("## Review kind\n\ndiscovery\n", packet)

    def test_verification_packet_carries_verification_marker_by_default(self):
        findings = [{"id": "f1", "summary": "x"}]
        packet = forge_common.rp.build_verification_packet(
            findings, "diff --git a/f b/f\n+x\n", None
        )
        self.assertIn("## Review kind\n\nverification\n", packet)

    def test_build_packet_omits_marker_when_review_kind_none(self):
        # The CLI (review-packet.py's main()) never passes review_kind, so
        # its output stays byte-identical to pre-Task-6 behavior.
        packet = forge_common.rp.build_packet(
            "### Task 1: X\nbody\n", "abc123", "diff --git a/f b/f\n+x\n",
        )
        self.assertNotIn("Review kind", packet)


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


SPEC_WITH_SECTION = "# Spec\n\n## Some Section\n\nDetails.\n"

# No **Spec:**, no plan **Global Constraints:**, acceptance is a bare
# inline-code command -> build_task_checklist raises "is empty", which the
# runner-layer skip catches (Contract checklist spec, 2026-08-21 amendment).
PLAN_EMPTY_CHECKLIST = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true`

**Tier:** standard

**Depends on:** nothing
"""

# **Spec:** + a prose acceptance clause -> a non-empty checklist, so the
# runner validates coverage.
PLAN_NONEMPTY_CHECKLIST = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Spec:** Some Section

**Acceptance:** must handle X correctly; `true`

**Tier:** standard

**Depends on:** nothing
"""


class RunnerCoverageWiringTests(unittest.TestCase):
    """End-to-end coverage wiring through forge-run.py's execute_task: an
    empty checklist is a runner-level skip (receipt ``coverage_skipped``
    True, packet unchanged, no validation, no retry); a non-empty checklist
    is validated, with the coverage-stubbing fake codex (added to
    _forge_support.py for this task) either auto-satisfying coverage or
    driving the one-retry-then-contract-error path when a canned response's
    coverage is deliberately incomplete."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-coverage-runner-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(SPEC_WITH_SECTION)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog\nresponses.json\nrun/\n.forge/\n")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "-A")
        self._git("commit", "-m", "base")

    def _plan(self, content, name="plan.md"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(content)
        return p

    def _run(self, plan_path, responses):
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _receipt(self, task_number=1, attempt=1):
        path = os.path.join(
            self.run_dir, "task-{}-attempt-{}.json".format(task_number, attempt)
        )
        with open(path) as f:
            return json.load(f)

    def test_empty_checklist_skips_coverage_receipt_flag_set(self):
        plan = self._plan(PLAN_EMPTY_CHECKLIST)
        self._init_repo()
        res = self._run(plan, [
            {"exit": 0, "msg": ""},                      # worker
            {"exit": 0, "msg": '{"verdict": "pass"}'},    # reviewer
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        receipt = self._receipt()
        self.assertTrue(receipt["coverage_skipped"])
        self.assertFalse(receipt["coverage_retry"])
        with open(os.path.join(self.run_dir, "task-1-review.md")) as f:
            packet = f.read()
        self.assertNotIn("## Contract checklist", packet)

    def test_nonempty_checklist_validated_first_try_via_fake_stub(self):
        plan = self._plan(PLAN_NONEMPTY_CHECKLIST)
        self._init_repo()
        res = self._run(plan, [
            {"exit": 0, "msg": ""},                      # worker
            {"exit": 0, "msg": '{"verdict": "pass"}'},    # reviewer: fake auto-stubs coverage
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        receipt = self._receipt()
        self.assertFalse(receipt["coverage_skipped"])
        self.assertFalse(receipt["coverage_retry"])
        with open(os.path.join(self.run_dir, "task-1-review.md")) as f:
            packet = f.read()
        self.assertIn("## Contract checklist", packet)
        self.assertIn("spec:Some Section", packet)

    def test_incomplete_coverage_triggers_one_retry_naming_missing_ids(self):
        plan = self._plan(PLAN_NONEMPTY_CHECKLIST)
        self._init_repo()
        res = self._run(plan, [
            {"exit": 0, "msg": ""},  # worker
            # attempt 1: coverage key present but empty -> the fake only
            # fills in a *missing* key, so this is left as-is -> every
            # checklist id is flagged missing.
            {"exit": 0, "msg": '{"verdict": "pass", "coverage": []}'},
            # retry: no coverage key -> the fake auto-stubs it fully from
            # the retry packet's (carried-over) checklist section.
            {"exit": 0, "msg": '{"verdict": "pass"}'},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        receipt = self._receipt()
        self.assertFalse(receipt["coverage_skipped"])
        self.assertTrue(receipt["coverage_retry"])
        with open(os.path.join(self.run_dir, "task-1-coverage-retry.md")) as f:
            retry_packet = f.read()
        self.assertIn("Coverage retry", retry_packet)
        self.assertIn("missing coverage for checklist id(s)", retry_packet)

    def test_second_invalid_coverage_is_contract_error_naming_defects(self):
        plan = self._plan(PLAN_NONEMPTY_CHECKLIST)
        self._init_repo()
        res = self._run(plan, [
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": '{"verdict": "pass", "coverage": []}'},
            {"exit": 0, "msg": '{"verdict": "pass", "coverage": []}'},
        ])
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("coverage", res.stderr.lower())
        self.assertIn("still invalid after one retry", res.stderr)
        self.assertIn("missing coverage for checklist id(s)", res.stderr)

    def test_retry_does_not_advance_attempt_counter_or_state(self):
        # The coverage retry must not look like a rework attempt: exactly one
        # attempt-1 receipt, status passed, attempt == 1.
        plan = self._plan(PLAN_NONEMPTY_CHECKLIST)
        self._init_repo()
        res = self._run(plan, [
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": '{"verdict": "pass", "coverage": []}'},
            {"exit": 0, "msg": '{"verdict": "pass"}'},
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        receipt = self._receipt()
        self.assertEqual(receipt["attempt"], 1)
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["coverage_retry"])
        self.assertFalse(
            os.path.exists(
                os.path.join(self.run_dir, "task-1-attempt-2.json")
            )
        )


class FakeCodexCoverageStubTests(unittest.TestCase):
    """Direct exercise of the coverage-stubbing behavior added to
    _forge_support.py's FAKE_CODEX_SRC for this task: it injects a coverage
    array when the dispatched prompt carries a '## Contract checklist'
    section and the canned message has none; it never overrides an explicit
    coverage array; and it passes a response through untouched when the
    prompt carries no checklist section at all (a worker dispatch, or a
    reviewer packet in the empty-checklist skip case)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="fake-codex-stub-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)

    def _invoke(self, prompt, msg):
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump([{"exit": 0, "msg": msg}], f)
        last_msg_path = os.path.join(self.d, "last.txt")
        env = os.environ.copy()
        env["FORGE_FAKE_RESPONSES"] = resp_path
        env.pop("FORGE_FAKE_LOG", None)
        subprocess.run(
            [sys.executable, self.fake, "exec", "--json",
             "--output-last-message", last_msg_path, prompt],
            check=True, capture_output=True, text=True, env=env,
        )
        with open(last_msg_path) as f:
            return json.load(f)

    def test_injects_coverage_when_checklist_present_and_msg_has_none(self):
        prompt = (
            "some preamble\n\n"
            "## Contract checklist\n\n"
            "- spec:A — Section A\n"
            "- g1 — No new deps\n\n"
            "## Prior findings\n\nirrelevant\n"
        )
        out = self._invoke(prompt, '{"verdict": "pass"}')
        self.assertEqual(out["verdict"], "pass")
        self.assertEqual(
            sorted(e["id"] for e in out["coverage"]), ["g1", "spec:A"]
        )
        for entry in out["coverage"]:
            self.assertEqual(entry["status"], "satisfied")
            self.assertTrue(entry["evidence"].strip())

    def test_passes_through_untouched_without_checklist_section(self):
        prompt = "some preamble with no checklist section\n\n```diff\n```\n"
        out = self._invoke(prompt, '{"verdict": "pass"}')
        self.assertNotIn("coverage", out)

    def test_does_not_override_explicit_coverage(self):
        prompt = "## Contract checklist\n\n- spec:A — Section A\n"
        out = self._invoke(prompt, json.dumps({
            "verdict": "pass",
            "coverage": [{"id": "spec:A", "status": "n/a", "evidence": "manual"}],
        }))
        self.assertEqual(
            out["coverage"],
            [{"id": "spec:A", "status": "n/a", "evidence": "manual"}],
        )


if __name__ == "__main__":
    unittest.main()
