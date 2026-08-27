"""Task 5: runner wiring — lint at run start, ``carried_ids``/``run_diff``
threaded into both ``classify_findings`` call sites, and the ``seed``
disposition's ``run.json`` bookkeeping + final-review discovery seeding.

Tasks 1/2/4 built lint, the resolved-label filter, and the seed disposition
as tested, callable, but unwired capability — nothing in forge-run.py called
``forge_lint.lint_plan``, and neither ``classify_findings`` call site passed
``carried_ids``/``run_diff``. This file proves each wiring is live end-to-end
(the behavior changes in a real run), not merely that the parameter is
accepted.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from _forge_support import *  # noqa: F401,F403

import forge_common
import forge_lint

rp = forge_run.rp


# --- fixtures ----------------------------------------------------------------

# A grammar defect (wrong heading level) — one lint error, nothing else.
# _forge_support.PLAN_BAD_HEADING already provides this shape.

# Single standard task appending to an already-tracked file (f1.txt), like
# PLAN_STD_TRACKED, but no **Spec:** — the checklist is legally empty
# (a lint warning, never an error).
PLAN_ONE_STD_TRACKED = PLAN_STD_TRACKED

# Task 1 (trivial) writes f1.txt and commits; task 2 (standard) touches only
# f2.txt, so a reviewer finding located in f1.txt is outside task 2's own
# diff but inside the run's cumulative diff — in-run, not in-diff. Task 3
# (trivial) depends on task 2, proving the run continues past the seed.
PLAN_SEED_THEN_CONTINUE = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo TASK1MARK >> f1.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TASK2MARK >> f2.txt`

**Tier:** standard

**Depends on:** Task 1

### Task 3: Third task
- [ ] Done

**Acceptance:** `echo TASK3MARK >> f3.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 2
"""


def _resolved_fix_msg(file, lines, summary, id="f1",
                      contract_ref="Acceptance: `true`"):
    """A ``findings`` verdict with one contract-breaking finding labeled
    ``convergence: "resolved"`` — the reviewer's claim that a prior
    outstanding fix finding (canonical id ``id``, no ``carried_from``) is now
    fixed. Deliberately still shaped as a real fix candidate (valid location,
    impact, contract_ref) so a runner that ignores the label would classify
    it right back to disposition ``fix`` and reach 'stuck' — only a runner
    that actually honors ``carried_ids`` drops it before disposition."""
    finding = {
        "id": id,
        "summary": summary,
        "location": {"file": file, "lines": lines},
        "provenance": "in-diff",
        "impact": "contract-breaking",
        "contract_ref": contract_ref,
        "convergence": "resolved",
        "carried_from": None,
        "repair_task": None,
    }
    return json.dumps({"verdict": "findings", "findings": [finding]})


class _GitFixtureCase(unittest.TestCase):
    """Shared git-repo + fake-codex plumbing for the in-process run_plan /
    execute_task / run_final_review_loop tests below."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-seed-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.log = os.path.join(self.d, "fakelog")
        self._old_env = {
            k: os.environ.get(k)
            for k in ("FORGE_FAKE_LOG", "FORGE_FAKE_RESPONSES")
        }
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
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

    def _set_responses(self, responses):
        path = os.path.join(self.d, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        os.environ["FORGE_FAKE_RESPONSES"] = path
        os.environ["FORGE_FAKE_LOG"] = self.log


# --- 1: lint blocks a run before any dispatch --------------------------------


class LintBlocksRunTests(unittest.TestCase):
    """run_plan calls forge_lint.lint_plan after the clean-tree precondition
    and before any dispatch (Plan lint spec)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-seed-lint-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")
        old = os.environ.get("FORGE_FAKE_LOG")
        os.environ["FORGE_FAKE_LOG"] = self.log
        self.addCleanup(
            lambda: os.environ.__setitem__("FORGE_FAKE_LOG", old)
            if old is not None else os.environ.pop("FORGE_FAKE_LOG", None)
        )

    def _plan(self, content):
        p = os.path.join(self.d, "plan.md")
        with open(p, "w") as f:
            f.write(content)
        return p

    def test_error_defect_raises_naming_it_and_dispatches_nothing(self):
        plan = self._plan(PLAN_BAD_HEADING)  # '## Task 1:' — wrong heading level
        with self.assertRaises(RuntimeError) as cm:
            forge_run.run_plan(plan, self.spec, self.run_dir, self.fake, self.d)
        self.assertIn("three #", str(cm.exception))
        self.assertIn("Task 1", str(cm.exception))
        # The fake codex was never invoked — no log line was ever appended.
        self.assertFalse(os.path.exists(self.log))
        # No run dir/run.json either — the contract error precedes both.
        self.assertFalse(os.path.isdir(self.run_dir))

    def test_multiple_error_defects_all_named_in_one_failure(self):
        plan = self._plan(PLAN_DUP)  # duplicate '### Task 1:' headings
        with self.assertRaises(RuntimeError) as cm:
            forge_run.run_plan(plan, self.spec, self.run_dir, self.fake, self.d)
        self.assertIn("duplicate task number 1", str(cm.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_warning_only_plan_proceeds_and_dispatches(self):
        # PLAN_PASS has no **Spec:**/global constraints and a command-only
        # **Acceptance:** — a legal plan whose checklist is empty (a lint
        # warning, never an error; Phase 13 spec).
        plan = self._plan(PLAN_PASS)
        defects = forge_lint.lint_plan(plan, self.spec)
        self.assertTrue(defects)
        self.assertTrue(all(d.severity == "warning" for d in defects))

        self._set_responses_here()
        rc = forge_run.run_plan(plan, self.spec, self.run_dir, self.fake, self.d)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.log))  # the worker DID dispatch

    def _set_responses_here(self):
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump([{"exit": 0, "msg": ""}], f)
        os.environ["FORGE_FAKE_RESPONSES"] = resp_path


# --- 2: carried_ids honors a reviewer-labeled resolved finding ---------------


class CarriedIdsHonoredTests(_GitFixtureCase):
    """Both classify_findings call sites pass carried_ids=state.carried_ids
    (read before advance_state runs), so Task 2's resolved-label filter is
    actually live — without this wiring the filter is dead code."""

    def test_resolved_label_converges_to_pass_instead_of_stuck(self):
        plan = self._plan(PLAN_ONE_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": ""},                                        # t1 worker a1
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},  # t1 review a1 -> fix
            {"exit": 0, "msg": ""},                                        # t1 worker a2 (rework)
            # Attempt 2's reviewer re-lists the SAME finding (id "f1") but
            # labels it resolved. Deliberately still shaped as a real fix
            # candidate: a runner that ignored the label would classify it
            # right back to "fix", find it carried from attempt 1 with
            # nothing resolved this round, and halt "stuck" — only honoring
            # carried_ids drops it before disposition and converges to pass.
            {"exit": 0, "msg": _resolved_fix_msg("f1.txt", "2", "issue")},  # t1 review a2
            {"exit": 0, "msg": _pass_msg()},                                # final review
        ])
        rc = forge_run.run_plan(plan, self.spec, os.path.join(self.d, "run"),
                                self.fake, self.d)
        self.assertEqual(rc, 0)
        with open(os.path.join(self.d, "run", "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["attempt"], 2)
        # A 3rd attempt receipt would only exist if this had reworked/halted
        # again instead of converging at attempt 2.
        self.assertFalse(
            os.path.exists(os.path.join(self.d, "run", "task-1-attempt-3.json"))
        )


# --- 3: run_diff -> in-run provenance -> the seed disposition ---------------


class SeedDispositionTests(_GitFixtureCase):
    """Both classify_findings call sites pass run_diff (the run's cumulative
    diff from run-start HEAD), so a finding against an earlier task's
    committed work classifies in-run, is seeded (logged, never reworked or
    halted), and the run continues to the next task."""

    def test_cross_task_finding_seeds_and_run_continues(self):
        plan = self._plan(PLAN_SEED_THEN_CONTINUE)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        run_dir = os.path.join(self.d, "run")
        self._set_responses([
            {"exit": 0, "msg": ""},                                       # t1 worker
            {"exit": 0, "msg": ""},                                       # t2 worker
            # Located at f1.txt:2 — task 1's committed line, not task 2's
            # own diff (task 2 only touches f2.txt) — but inside the run's
            # cumulative diff from run-start HEAD.
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "SEEDMARKER cross-task issue",
                contract_ref="Acceptance: `echo TASK2MARK >> f2.txt`",
            )},                                                            # t2 review -> seed
            {"exit": 0, "msg": ""},                                       # t3 worker
            {"exit": 0, "msg": _pass_msg()},                               # final review
        ])
        rc = forge_run.run_plan(plan, self.spec, run_dir, self.fake, self.d)
        self.assertEqual(rc, 0)

        # Task 2 never reworked (a "seed" disposition never triggers rework)
        # and task 3 (dependent on task 2) ran — proof the run continued
        # rather than halting over another task's committed work.
        with open(os.path.join(run_dir, "task-2-attempt-1.json")) as f:
            t2_receipt = json.load(f)
        self.assertEqual(t2_receipt["status"], "passed")
        self.assertTrue(os.path.exists(
            os.path.join(run_dir, "task-3-attempt-1.json")
        ))
        with open(os.path.join(run_dir, "task-3-attempt-1.json")) as f:
            t3_receipt = json.load(f)
        self.assertEqual(t3_receipt["status"], "passed")

        with open(os.path.join(run_dir, "run.json")) as f:
            run_json = json.load(f)
        self.assertEqual(run_json["status"], "passed")
        seeded = run_json.get("seeded_findings") or []
        self.assertEqual(len(seeded), 1)
        self.assertIn("SEEDMARKER", seeded[0]["summary"])
        self.assertEqual(seeded[0]["disposition"], "seed")
        self.assertEqual(seeded[0]["provenance"], "in-run")

        # A brand-new run dir that never ran has no seeded findings — the
        # accumulator starts fresh for a fresh invocation.
        fresh_dir = os.path.join(self.d, "run-fresh-never-invoked")
        self.assertIsNone(forge_run._read_seeded_findings(fresh_dir))
        # And the completed run's own run.json is where they persisted.
        self.assertEqual(
            len(forge_run._read_seeded_findings(run_dir) or []), 1
        )


# --- 4: seeded findings pre-seed the final review's discovery packet only ---


class FinalReviewSeedPacketTests(_GitFixtureCase):
    """run_final_review_loop's discovery packet carries the run's seeded
    findings as pre-seeded prior findings; a later verification packet
    (delta-scoped, after a real repair) carries only that lap's own
    outstanding findings — never the seeds."""

    def _init_repo_with_task_work(self):
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._git("add", "-A")
        self._git("commit", "-m", "base")
        run_base = forge_run._git_head(self.d)
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("NEEDFIX\n")
        self._git("add", "-A")
        self._git("commit", "-m", "task work")
        return run_base

    def test_seed_in_discovery_not_in_verification(self):
        run_base = self._init_repo_with_task_work()
        run_dir = os.path.join(self.d, "run")
        os.makedirs(run_dir)
        f1 = os.path.join(self.d, "f1.txt")

        seeded = [forge_common.finding_to_dict(forge_common.Finding(
            id="seed1", summary="SEEDMARKER earlier-task issue", file="other.py",
            lines="5", provenance="in-run", impact="contract-breaking",
            contract_ref="Acceptance: `true`", disposition="seed",
        ))]

        self._set_responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "real issue", contract_ref="Acceptance: `true`",
            )},                                                             # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FIXED\n"},  # a2 fix
            {"exit": 0, "msg": _pass_msg()},                                # a2 review (verification)
        ])

        orig_build_packet = rp.build_packet
        orig_build_verification_packet = rp.build_verification_packet
        discovery_calls = []
        verification_calls = []

        def _spy_packet(*a, **kw):
            discovery_calls.append(kw.get("prior_findings"))
            return orig_build_packet(*a, **kw)

        def _spy_verification(*a, **kw):
            verification_calls.append(a[0] if a else kw.get("findings"))
            return orig_build_verification_packet(*a, **kw)

        with mock.patch.object(rp, "build_packet", side_effect=_spy_packet), \
             mock.patch.object(
                 rp, "build_verification_packet", side_effect=_spy_verification
             ):
            outcome = forge_run.run_final_review_loop(
                self.spec, run_base, run_dir, self.fake, self.d, "standard",
                "auto", {}, seeded_findings=seeded,
            )

        self.assertEqual(outcome.status, "passed")
        self.assertEqual(len(discovery_calls), 1)
        self.assertEqual(discovery_calls[0], seeded)
        self.assertEqual(len(verification_calls), 1)
        verification_summaries = [f["summary"] for f in verification_calls[0]]
        self.assertNotIn("SEEDMARKER earlier-task issue", verification_summaries)
        self.assertIn("real issue", verification_summaries)


if __name__ == "__main__":
    unittest.main()
