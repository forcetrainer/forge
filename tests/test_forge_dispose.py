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
                    autofix="auto", state_path=None):
        args = [
            "--verdict", verdict_path,
            "--base", self.base,
            "--attempt", str(attempt),
            "--acceptance-ok", acceptance_ok,
            "--autofix", autofix,
        ]
        if state_path:
            args += ["--state", state_path]
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


if __name__ == "__main__":
    unittest.main()
