"""forge_dispose CLI: decision.json per quadrant, provenance override, null
contract_ref downgrade, --state round-trip across a convergence sequence,
--autofix gate, the execution-failure path, and fail-loud on malformed input.
Exercises the same decision logic as tests/test_forge_convergence.py and
tests/test_forge_classify.py, but through the CLI boundary the Claude
dispatch path actually calls."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from _forge_support import *  # noqa: F401,F403 — sets up sys.path + loads forge_run
import forge_common  # noqa: E402
import forge_dispose  # noqa: E402 — derive_disposition, validate_coverage unit tests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "forge_dispose.py")


class ForgeDisposeCLITests(unittest.TestCase):
    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix="forge-dispose-repo-")
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

        # Uncommitted edit touching new-side line 2 only — findings at
        # lines "2-2" are in-diff, findings at "50-51" are pre-existing.
        with open(self.src_path, "w") as f:
            f.write("line1\nCHANGED\nline3\n")

        self.workdir = tempfile.mkdtemp(prefix="forge-dispose-work-")
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
                    autofix="auto", state_path=None, checklist_path=None,
                    citable_path=None):
        args = [
            "--verdict", verdict_path,
            "--base", self.base,
            "--attempt", str(attempt),
            "--acceptance-ok", acceptance_ok,
            "--autofix", autofix,
        ]
        if state_path:
            args += ["--state", state_path]
        if checklist_path:
            args += ["--checklist", checklist_path]
        if citable_path:
            args += ["--citable", citable_path]
        return args

    # --- quadrant / decision.json shape -------------------------------------

    def test_in_diff_contract_breaking_fixes(self):
        v = self._write_json("v1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertIsNone(decision["halt_reason"])
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])
        self.assertEqual(decision["findings"]["defer"], [])
        self.assertEqual(decision["findings"]["halt"], [])

    def test_in_diff_improvement_defers_and_continues(self):
        v = self._write_json("v2.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "extract a helper",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "improvement"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual([f["id"] for f in decision["findings"]["defer"]], ["f1"])

    def test_pre_existing_contract_breaking_halts_scope_decision(self):
        v = self._write_json("v3.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "race condition",
             "location": {"file": "src.txt", "lines": "50-51"},
             "impact": "contract-breaking", "contract_ref": "AC-2",
             "repair_task": {"title": "fix race", "files": ["src.txt"],
                              "spec": "x", "tests": [], "acceptance": [],
                              "tier": "standard"}},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "halt")
        self.assertEqual(decision["halt_reason"], "scope-decision")
        self.assertEqual(decision["findings"]["halt"][0]["id"], "f1")
        self.assertEqual(
            decision["findings"]["halt"][0]["repair_task"]["title"], "fix race"
        )

    def test_pre_existing_improvement_defers(self):
        v = self._write_json("v4.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "stylistic nit",
             "location": {"file": "src.txt", "lines": "50-51"},
             "impact": "improvement"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual(decision["findings"]["defer"][0]["id"], "f1")

    def test_clean_pass_verdict(self):
        v = self._write_json("v5.json", {"verdict": "pass"})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual(decision["findings"], {"fix": [], "defer": [], "halt": []})

    # --- provenance override -------------------------------------------------

    def test_provenance_override_reviewer_claims_in_diff_but_lines_outside(self):
        v = self._write_json("v6.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "spurious",
             "location": {"file": "src.txt", "lines": "50-51"},
             "provenance": "in-diff", "impact": "contract-breaking",
             "contract_ref": "AC-3"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "halt")
        self.assertEqual(decision["halt_reason"], "scope-decision")

    # --- null contract_ref downgrade -----------------------------------------

    def test_null_contract_ref_downgrades_to_defer(self):
        v = self._write_json("v7.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "maybe an issue",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": None},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual(decision["findings"]["defer"][0]["id"], "f1")

    # --- state round-trip / convergence sequences via the CLI ---------------

    def test_state_round_trip_progress_then_pass(self):
        v1 = self._write_json("va1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        self.assertEqual(d1["action"], "rework")
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("va2.json", {"verdict": "pass"})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["action"], "pass")
        self.assertEqual(sorted(d2["state"]["resolved_ids"]), ["f1"])

    def test_regression_resolved_id_reappears_halts(self):
        v1 = self._write_json("vb1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("vb2.json", {"verdict": "pass"})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["action"], "pass")
        state_path2 = self._write_json("state2.json", d2["state"])

        v3 = self._write_json("vb3.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug again",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r3 = self.run_dispose(
            self._base_args(v3, attempt=3, state_path=state_path2)
        )
        d3 = json.loads(r3.stdout)
        self.assertEqual(d3["action"], "halt")
        self.assertEqual(d3["halt_reason"], "regression")

    def test_green_to_red_regression_halts(self):
        v1 = self._write_json("vc1.json", {"verdict": "pass"})
        r1 = self.run_dispose(
            self._base_args(v1, attempt=1, acceptance_ok="true")
        )
        d1 = json.loads(r1.stdout)
        self.assertEqual(d1["action"], "pass")
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("vc2.json", {"verdict": "pass"})
        r2 = self.run_dispose(self._base_args(
            v2, attempt=2, acceptance_ok="false", state_path=state_path
        ))
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["action"], "halt")
        self.assertEqual(d2["halt_reason"], "regression")

    def test_stuck_carried_twice_halts(self):
        v1 = self._write_json("vd1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        self.assertEqual(d1["action"], "rework")
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("vd2.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug still",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["action"], "halt")
        self.assertEqual(d2["halt_reason"], "stuck")

    # --- resolved label honored on the Claude path (carried_ids wiring) -----

    def test_resolved_label_in_carried_set_is_dropped_not_a_fix(self):
        v1 = self._write_json("ve1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        self.assertEqual(d1["action"], "rework")
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("ve2.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug, now resolved",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1",
             "convergence": "resolved"},
        ]})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["findings"]["fix"], [])
        self.assertEqual(d2["action"], "pass")

    def test_resolved_label_without_state_dispositions_normally(self):
        v = self._write_json("vf1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1",
             "convergence": "resolved"},
        ]})
        result = self.run_dispose(self._base_args(v, attempt=1))
        decision = json.loads(result.stdout)
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])
        self.assertEqual(decision["action"], "rework")

    def test_resolved_label_absent_from_carried_set_dispositions_normally(self):
        v1 = self._write_json("vg1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        state_path = self._write_json("state.json", d1["state"])

        # f2 was never in the carried set (only f1 was) — a self-labelled
        # "resolved" on it is meaningless and must not be honored.
        v2 = self._write_json("vg2.json", {"verdict": "findings", "findings": [
            {"id": "f2", "summary": "different bug, self-labelled resolved",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1",
             "convergence": "resolved"},
        ]})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual([f["id"] for f in d2["findings"]["fix"]], ["f2"])

    def test_carried_from_id_honored_where_own_id_is_not(self):
        v1 = self._write_json("vh1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("vh2.json", {"verdict": "findings", "findings": [
            {"id": "f2", "carried_from": "f1", "summary": "bug, now resolved",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1",
             "convergence": "resolved"},
        ]})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        self.assertEqual(d2["findings"]["fix"], [])
        self.assertEqual(d2["action"], "pass")

    def test_second_attempt_only_legitimately_resolved_finding_passes(self):
        v1 = self._write_json("vi1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        r1 = self.run_dispose(self._base_args(v1, attempt=1))
        d1 = json.loads(r1.stdout)
        state_path = self._write_json("state.json", d1["state"])

        v2 = self._write_json("vi2.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug, now resolved",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1",
             "convergence": "resolved"},
        ]})
        r2 = self.run_dispose(
            self._base_args(v2, attempt=2, state_path=state_path)
        )
        d2 = json.loads(r2.stdout)
        # Without the label honored, this halts "stuck" — the false halt
        # this task fixes.
        self.assertEqual(d2["action"], "pass")
        self.assertIsNone(d2["halt_reason"])

    def test_attempt_one_empty_carried_set_byte_identical(self):
        v = self._write_json("vj1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "bug",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        result = self.run_dispose(self._base_args(v, attempt=1))
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])

    def test_backstop_halts_at_attempt_five(self):
        # Each attempt surfaces a *different* fix id, so it never goes
        # stuck/regression — it just reworks until the backstop trips.
        state_path = None
        decision = None
        for i in range(1, 6):
            v = self._write_json("ve{}.json".format(i), {
                "verdict": "findings", "findings": [
                    {"id": "f{}".format(i), "summary": "bug",
                     "location": {"file": "src.txt", "lines": "2-2"},
                     "impact": "contract-breaking", "contract_ref": "AC-1"},
                ],
            })
            result = self.run_dispose(
                self._base_args(v, attempt=i, state_path=state_path)
            )
            decision = json.loads(result.stdout)
            state_path = self._write_json(
                "state{}.json".format(i), decision["state"]
            )
        self.assertEqual(decision["action"], "halt")
        self.assertEqual(decision["halt_reason"], "backstop")

    # --- autofix gate ---------------------------------------------------------

    def test_autofix_gate_halts_on_any_finding(self):
        v = self._write_json("vf1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "minor nit",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "improvement"},
        ]})
        result = self.run_dispose(self._base_args(v, autofix="gate"))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "halt")
        self.assertEqual(decision["halt_reason"], "gate")

    def test_autofix_gate_passes_clean(self):
        v = self._write_json("vf2.json", {"verdict": "pass"})
        result = self.run_dispose(self._base_args(v, autofix="gate"))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")

    # --- execution-failure path -----------------------------------------------

    def test_execution_failure_is_fix_retry_not_halt_or_defer(self):
        result = self.run_dispose([
            "--base", self.base,
            "--attempt", "1", "--acceptance-ok", "false", "--autofix", "auto",
            "--execution-failure", "--execution-detail", "worker crashed",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual(decision["findings"]["fix"][0]["summary"], "worker crashed")
        self.assertEqual(decision["findings"]["defer"], [])
        self.assertEqual(decision["findings"]["halt"], [])

    def test_execution_failure_subject_to_backstop(self):
        state_path = None
        decision = None
        for i in range(1, 6):
            result = self.run_dispose([
                "--base", self.base,
                "--attempt", str(i), "--acceptance-ok", "false",
                "--autofix", "auto", "--execution-failure",
                "--execution-detail", "worker timed out",
            ] + (["--state", state_path] if state_path else []))
            decision = json.loads(result.stdout)
            state_path = self._write_json(
                "exec-state{}.json".format(i), decision["state"]
            )
        self.assertEqual(decision["action"], "halt")
        self.assertEqual(decision["halt_reason"], "backstop")

    # --- malformed input fails loud --------------------------------------------

    def test_bad_git_ref_exits_nonzero(self):
        v = self._write_json("vg1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "x", "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-1"},
        ]})
        result = self.run_dispose([
            "--verdict", v, "--base", "not-a-real-ref-xyz",
            "--attempt", "1", "--acceptance-ok", "true", "--autofix", "auto",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not-a-real-ref-xyz", result.stderr)

    def test_unparseable_verdict_exits_nonzero(self):
        path = os.path.join(self.workdir, "bad.json")
        with open(path, "w") as f:
            f.write("not json")
        result = self.run_dispose(self._base_args(path))
        self.assertNotEqual(result.returncode, 0)

    def test_unlocated_contract_breaking_finding_exits_nonzero(self):
        v = self._write_json("vh1.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "no location", "impact": "contract-breaking",
             "contract_ref": "AC-1"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertNotEqual(result.returncode, 0)


class UnverifiableDispositionTests(unittest.TestCase):
    """Unit tests against derive_disposition/validate_coverage directly (Task
    3: `unverifiable` seeds regardless of provenance, and asymmetrically
    exempts a coverage entry from the backing-finding rule that still binds
    `violated`)."""

    def _finding(self, provenance, contract_ref="AC-1"):
        return forge_common.Finding(
            id="f1", summary="cannot tell from this diff alone", file="src.txt",
            lines="2-2", provenance=provenance, impact="unverifiable",
            contract_ref=contract_ref,
        )

    def test_unverifiable_seeds_in_diff(self):
        self.assertEqual(
            forge_dispose.derive_disposition(self._finding("in-diff")), "seed"
        )

    def test_unverifiable_seeds_in_run(self):
        self.assertEqual(
            forge_dispose.derive_disposition(self._finding("in-run")), "seed"
        )

    def test_unverifiable_seeds_pre_existing(self):
        self.assertEqual(
            forge_dispose.derive_disposition(self._finding("pre-existing")), "seed"
        )

    def test_unverifiable_seeds_with_null_contract_ref(self):
        finding = self._finding("pre-existing", contract_ref=None)
        self.assertEqual(forge_dispose.derive_disposition(finding), "seed")

    def test_coverage_unverifiable_with_evidence_is_valid_no_backing_finding(self):
        checklist = [{"id": "t3.a1"}]
        verdict = forge_common.Verdict(
            kind="findings",
            findings=[],
            coverage=[forge_common.CoverageEntry(
                id="t3.a1", status="unverifiable",
                evidence="the proof this needs lives outside this task's diff",
            )],
        )
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertEqual(defects, [])

    def test_coverage_unverifiable_empty_evidence_is_defect(self):
        checklist = [{"id": "t3.a1"}]
        verdict = forge_common.Verdict(
            kind="findings",
            findings=[],
            coverage=[forge_common.CoverageEntry(
                id="t3.a1", status="unverifiable", evidence="   ",
            )],
        )
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertTrue(
            any("empty evidence" in d and "t3.a1" in d for d in defects), defects
        )

    def test_coverage_violated_with_no_backing_finding_remains_defect(self):
        checklist = [{"id": "t3.a1"}]
        verdict = forge_common.Verdict(
            kind="findings",
            findings=[],
            coverage=[forge_common.CoverageEntry(
                id="t3.a1", status="violated", evidence="looks broken",
            )],
        )
        defects = forge_dispose.validate_coverage(verdict, checklist)
        self.assertTrue(
            any("violated" in d and "t3.a1" in d for d in defects), defects
        )


class ForgeDisposeCLIContractRefMembershipTests(unittest.TestCase):
    """Task 11: the Claude dispatch path's own CLI (this module's ``main``)
    enforces contract_ref membership before classifying, gated on a
    dedicated ``--citable`` flag — a JSON array of citable ref id strings,
    mirroring forge_checklist.citable_refs' output — mirroring forge-run.py's
    ``_verdict_defects`` wiring. Task 4 wired ``validate_contract_refs`` into
    the Codex runner only, so a bogus contract_ref still bought
    contract-breaking on this path.

    ``--citable`` is deliberately a separate flag from ``--checklist``:
    ``--checklist`` carries coverage items only and drives coverage
    validation alone (Contract checklist spec — covering and citing are
    different acts); the citable set is wider (coverage items plus declared
    ``spec:<slug>`` sections), so one flag cannot carry both without either
    rejecting a legitimate spec-section citation or silently narrowing
    membership to whatever a task's checklist happens to contain."""

    setUp = ForgeDisposeCLITests.setUp
    _git = ForgeDisposeCLITests._git
    _git_output = ForgeDisposeCLITests._git_output
    _write_json = ForgeDisposeCLITests._write_json
    run_dispose = ForgeDisposeCLITests.run_dispose
    _base_args = ForgeDisposeCLITests._base_args

    def test_citable_bad_contract_ref_exits_nonzero_naming_finding_and_ref(self):
        citable = self._write_json("citable.json", ["t3.a1", "spec:Foo"])
        v = self._write_json("v-bad-ref.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-NOPE"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("f1", result.stderr)
        self.assertIn("AC-NOPE", result.stderr)

    def test_citable_bad_contract_ref_produces_no_decision_output(self):
        citable = self._write_json("citable.json", ["t3.a1", "spec:Foo"])
        v = self._write_json("v-bad-ref2.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-NOPE"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertEqual(result.stdout.strip(), "")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(result.stdout)

    def test_citable_ref_naming_coverage_item_id_unaffected(self):
        citable = self._write_json("citable.json", ["t3.a1", "spec:Foo"])
        v = self._write_json("v-good-ref.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "t3.a1"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])

    def test_citable_ref_naming_declared_spec_section_unaffected(self):
        """A finding citing a declared spec:<slug> section — outside the
        coverage-item set but inside the wider citable set — is exactly the
        legitimate case a --checklist-driven check would wrongly reject
        (the halt behind this task: a real spec:<slug> ref bounced as
        non-citable because --checklist only carries coverage items)."""
        citable = self._write_json("citable.json", ["t3.a1", "spec:Foo"])
        v = self._write_json("v-spec-ref.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "contradicts the declared spec section",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "spec:Foo"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])

    def test_citable_null_contract_ref_unaffected(self):
        citable = self._write_json("citable.json", ["t3.a1"])
        v = self._write_json("v-null-ref.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "extract a helper",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "improvement"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual([f["id"] for f in decision["findings"]["defer"]], ["f1"])

    def test_checklist_without_citable_does_not_enforce_membership(self):
        """--checklist alone (no --citable) must not enforce membership, even
        for a ref absent from the checklist — --checklist drives coverage
        validation only."""
        checklist = self._write_json("checklist.json", [{"id": "AC-1"}])
        v = self._write_json("v-checklist-only.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-NOPE"},
        ]})
        result = self.run_dispose(
            self._base_args(v, checklist_path=checklist)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])

    def test_without_either_flag_bad_contract_ref_is_unaffected(self):
        v = self._write_json("v-no-flags.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "2-2"},
             "impact": "contract-breaking", "contract_ref": "AC-NOPE"},
        ]})
        result = self.run_dispose(self._base_args(v))
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["action"], "rework")
        self.assertEqual([f["id"] for f in decision["findings"]["fix"]], ["f1"])

    def test_validate_locations_ordering_preserved_with_citable(self):
        """An unlocated contract-breaking finding is still a location defect
        raised before any contract_ref check — even when --citable is
        supplied and the ref itself is fine — preserving validate_locations'
        existing precedence ahead of classify_findings."""
        citable = self._write_json("citable.json", ["t3.a1"])
        v = self._write_json("v-bad-location.json", {"verdict": "findings", "findings": [
            {"id": "f1", "summary": "null check missing",
             "location": {"file": "src.txt", "lines": "not-a-range"},
             "impact": "contract-breaking", "contract_ref": "t3.a1"},
        ]})
        result = self.run_dispose(
            self._base_args(v, citable_path=citable)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("location", result.stderr)
        self.assertNotIn("citable ref", result.stderr)


if __name__ == "__main__":
    unittest.main()
