"""Final-review fixer continuity + de-pasted brief (Phase 13 Task 7).

Three layers:
  - `_final_review_fix_brief` / `_final_review_fix_resume_prompt`: pure unit
    tests of the de-pasted cold brief (findings + affected paths + the spec
    sections named by contract_ref — no whole-plan diff, no full spec body)
    and the findings-only resume prompt.
  - `dispatch_final_review_fix`: argv-shape unit tests for the resume form
    (`codex exec resume --json --output-last-message <path> -m <model> -c
    'model_reasoning_effort="<effort>"' <thread_id> <prompt>`), mirroring
    `dispatch_worker`'s resume-argv coverage in test_forge_resume.py.
  - `run_final_review_loop` integration: the final reviewer is cold on
    discovery and resumed on every verification lap against the repair
    delta; the final-review fixer is a cold spawn on its first repair and
    resumes that same thread on every later repair; commit discipline (one
    `fix: final-review` commit) holds across multiple repair laps.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from _forge_support import *  # noqa: F401,F403

import forge_checklist
import forge_common

rp = forge_run.rp


# A standard task whose **Spec:** names a real spec section — the fix
# findings below reference it via contract_ref="spec:Alpha section" so
# `_final_review_fix_brief`'s checklist-reduction path has something to
# resolve, and `build_final_checklist` has real plan/spec grammar to walk.
PLAN_FINAL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Spec:** Alpha section

**Acceptance:** `true`

**Tier:** standard

**Depends on:** nothing
"""

SPEC_WITH_ALPHA = "# Spec\n\n## Alpha section\n\nALPHASECTIONMARKER contract text.\n"


def _stream(thread_id, text="ok"):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"item_type": "agent_message", "text": text}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


# --- _final_review_fix_brief / _final_review_fix_resume_prompt -------------


class FinalReviewFixBriefTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-final-fix-brief-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)

    def _checklist(self):
        return [
            forge_checklist.ChecklistItem(
                id="spec:Alpha section", source="spec",
                text="ALPHASECTIONMARKER contract text.",
            ),
            forge_checklist.ChecklistItem(
                id="g1", source="global", text="an unrelated global constraint",
            ),
        ]

    def test_contains_findings_and_affected_files(self):
        findings = [
            forge_common.Finding(
                id="f1", summary="issue one", file="scripts/foo.py", lines="12",
                provenance="in-diff", impact="contract-breaking",
                contract_ref="spec:Alpha section",
            ),
            forge_common.Finding(
                id="f2", summary="issue two", file="scripts/bar.py", lines="4",
                provenance="in-diff", impact="contract-breaking", contract_ref=None,
            ),
        ]
        brief_path = forge_run._final_review_fix_brief(
            findings, self.run_dir, 1, self._checklist()
        )
        with open(brief_path) as f:
            brief = f.read()
        self.assertIn("issue one", brief)
        self.assertIn("issue two", brief)
        self.assertIn("scripts/foo.py", brief)
        self.assertIn("scripts/bar.py", brief)

    def test_contains_referenced_spec_section_excludes_unreferenced(self):
        findings = [forge_common.Finding(
            id="f1", summary="issue", file="f1.txt", lines="2",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="spec:Alpha section",
        )]
        brief_path = forge_run._final_review_fix_brief(
            findings, self.run_dir, 1, self._checklist()
        )
        with open(brief_path) as f:
            brief = f.read()
        self.assertIn("ALPHASECTIONMARKER", brief)
        # A checklist item no finding's contract_ref names is not rendered.
        self.assertNotIn("an unrelated global constraint", brief)

    def test_no_diff_git_line_and_no_full_spec_body(self):
        # The whole-plan diff and the full spec are the exact things this
        # task removes from the fixer's cold brief (Session continuity spec:
        # "brief carries findings + affected paths only").
        findings = [forge_common.Finding(
            id="f1", summary="issue", file="f1.txt", lines="2",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="spec:Alpha section",
        )]
        brief_path = forge_run._final_review_fix_brief(
            findings, self.run_dir, 1, self._checklist()
        )
        with open(brief_path) as f:
            brief = f.read()
        self.assertNotIn("diff --git", brief)
        # The checklist item's flattened prose is present, but not a whole
        # spec document heading/preamble.
        self.assertNotIn("# Spec", brief)

    def test_no_checklist_still_writes_findings_and_files(self):
        findings = [forge_common.Finding(
            id="f1", summary="issue", file="f1.txt", lines="2",
            provenance="in-diff", impact="contract-breaking",
        )]
        brief_path = forge_run._final_review_fix_brief(findings, self.run_dir, 1, None)
        with open(brief_path) as f:
            brief = f.read()
        self.assertIn("issue", brief)
        self.assertIn("f1.txt", brief)
        self.assertNotIn("diff --git", brief)


class FinalReviewFixResumePromptTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-final-fix-resume-prompt-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)

    def test_carries_only_the_new_findings(self):
        findings = [forge_common.Finding(
            id="f1", summary="new issue", file="f1.txt", lines="3",
            provenance="in-diff", impact="contract-breaking",
            contract_ref="spec:Alpha section",
        )]
        path = forge_run._final_review_fix_resume_prompt(findings, self.run_dir, 2)
        with open(path) as f:
            prompt = f.read()
        self.assertIn("new issue", prompt)
        self.assertNotIn("## Affected files", prompt)
        self.assertNotIn("## Referenced spec sections", prompt)
        self.assertNotIn("ALPHASECTIONMARKER", prompt)


# --- dispatch_final_review_fix: resume argv shape ---------------------------


class DispatchFinalReviewFixResumeArgvTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-final-fix-argv-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("## Final-review fix — resolve these findings\n\n- fix it\n")
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir, exist_ok=True)
        self.log = os.path.join(self.d, "fakelog")
        self._old_env = {
            k: os.environ.get(k) for k in ("FORGE_FAKE_LOG", "FORGE_FAKE_RESPONSES")
        }
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _set_responses(self, responses):
        path = os.path.join(self.d, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        os.environ["FORGE_FAKE_RESPONSES"] = path
        os.environ["FORGE_FAKE_LOG"] = self.log

    def test_resume_argv_shape_and_ordering(self):
        self._set_responses([{"exit": 0, "msg": ""}])
        threads = {}
        last_msg_path = os.path.join(
            self.run_dir, "final-review-fix-attempt-2-last.txt"
        )
        res = forge_run.dispatch_final_review_fix(
            self.brief, self.fake, self.run_dir, "standard", 2, threads,
            resume_thread="th-fixer-1",
        )
        self.assertEqual(
            res.argv,
            [
                self.fake, "exec", "resume", "--json",
                "--output-last-message", last_msg_path,
                "-m", "gpt-5.6-terra",
                "-c", 'model_reasoning_effort="medium"',
                "th-fixer-1",
                "## Final-review fix — resolve these findings\n\n- fix it\n",
            ],
        )

    def test_resume_prompt_has_no_contract_preamble(self):
        self._set_responses([{"exit": 0, "msg": ""}])
        cold = forge_run.dispatch_final_review_fix(
            self.brief, self.fake, self.run_dir, "standard", 1, {},
        )
        resumed = forge_run.dispatch_final_review_fix(
            self.brief, self.fake, self.run_dir, "standard", 2, {},
            resume_thread="th-x",
        )
        self.assertNotEqual(cold.argv[-1], resumed.argv[-1])
        self.assertEqual(
            resumed.argv[-1],
            "## Final-review fix — resolve these findings\n\n- fix it\n",
        )
        self.assertGreater(len(cold.argv[-1]), len(resumed.argv[-1]))


# --- run_final_review_loop: continuity integration --------------------------


class RunFinalReviewLoopContinuityTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-final-loop-continuity-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(SPEC_WITH_ALPHA)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.d, "fakelog")
        self._set_env("FORGE_FAKE_LOG", self.log)

    def _set_env(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(
            lambda: os.environ.__setitem__(key, old)
            if old is not None
            else os.environ.pop(key, None)
        )

    def _responses(self, responses):
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        self._set_env("FORGE_FAKE_RESPONSES", resp_path)

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

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

    def _plan(self):
        path = os.path.join(self.d, "plan.md")
        with open(path, "w") as f:
            f.write(PLAN_FINAL)
        return path

    def _log_lines(self):
        return subprocess.run(
            ["git", "log", "--oneline"], cwd=self.d,
            capture_output=True, text=True, check=True,
        ).stdout

    def test_reviewer_cold_at_discovery_resumed_at_verification(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                       # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FIXED\n",
             "stdout": _stream("th-fix1")},                          # a2 fix dispatch (cold)
            {"exit": 0, "msg": _pass_msg(), "stdout": _stream("th-rev1")},  # a2 review (verification)
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        argvs = _log_argvs(self.log)
        review_calls = [
            a for a in argvs
            if "--output-last-message" in a
            and "final-review-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(review_calls), 2)
        self.assertNotIn("resume", review_calls[0])
        self.assertIn("resume", review_calls[1])
        self.assertIn("th-rev1", review_calls[1])
        self.assertEqual(threads.get("final-reviewer"), "th-rev1")

    def test_verification_packet_has_no_whole_plan_diff_or_full_spec(self):
        # Demonstrates the cost claim directly: the packet the resumed
        # reviewer sees on a verification lap carries the repair delta and
        # findings only — never a `diff --git a/unrelated...` hunk from the
        # whole-plan diff, and never the full spec body.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        with open(os.path.join(self.d, "unrelated.py"), "w") as f:
            f.write("import os\n")
        self._git("add", "-A")
        self._git("commit", "-m", "unrelated file")
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue", contract_ref="spec:Alpha section",
            )},                                                       # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FIXED\n"},  # a2 fix
            {"exit": 0, "msg": _pass_msg()},                          # a2 review (verification)
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        with open(os.path.join(self.run_dir, "final-review.md")) as f:
            packet = f.read()
        # Not the full spec document (its own H1) — only the reduced
        # checklist's single, already-flattened item, which is expected
        # (Delta-scoped verification packets spec: "+ the reduced checklist").
        self.assertNotIn("# Spec", packet)
        self.assertNotIn("b/unrelated.py", packet)
        self.assertIn("+FIXED", packet)

    def test_second_repair_resumes_fixer_and_single_commit_across_laps(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "first issue", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                        # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "PARTIAL\n",
             "stdout": _stream("th-fix1")},                           # a2 fix (cold)
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "second issue", id="f2", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                        # a2 review (verification) -> still fix
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FINAL\n"},  # a3 fix (resume)
            {"exit": 0, "msg": _pass_msg()},                          # a3 review (verification) -> pass
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.attempts, 3)
        argvs = _log_argvs(self.log)
        fixer_calls = [
            a for a in argvs
            if "--output-last-message" in a
            and "final-review-fix-attempt" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(fixer_calls), 2)
        self.assertNotIn("resume", fixer_calls[0])
        self.assertIn("resume", fixer_calls[1])
        self.assertIn("th-fix1", fixer_calls[1])
        resume_prompt = fixer_calls[1][-1]
        self.assertIn("second issue", resume_prompt)
        self.assertNotIn("## Affected files", resume_prompt)
        self.assertNotIn("## Referenced spec sections", resume_prompt)
        log = self._log_lines()
        self.assertEqual(log.count("fix: final-review"), 1)

    def test_no_repair_applied_no_commit(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        self._responses([
            {"exit": 0, "msg": _pass_msg()},  # a1 review (discovery) -> pass immediately
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertNotIn("fix: final-review", self._log_lines())

    def test_missing_fixer_thread_on_second_repair_falls_back_cold_with_flag(self):
        # The first repair is intentionally cold (no fallback flag); a second
        # repair with no captured fixer thread (never emitted one) must fall
        # back to a cold spawn and record resume_fallback on the receipt.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "first issue", contract_ref="spec:Alpha section",
            )},                                                       # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "PARTIAL\n"},  # a2 fix (cold, no thread)
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "second issue", id="f2", contract_ref="spec:Alpha section",
            )},                                                       # a2 review -> still fix
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FINAL\n"},  # a3 fix (cold fallback)
            {"exit": 0, "msg": _pass_msg()},                          # a3 review -> pass
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertTrue(receipt["resume_fallback"])

    def test_fix_dispatch_resume_and_cold_fallback_both_crash_still_writes_receipt(self):
        # A fixer resume that fails, falls back cold, and the cold fallback
        # ALSO crashes (exec_ok=False) with convergence resolving to rework
        # (not halt) must still record that attempt's state — matching
        # execute_task's unconditional per-attempt receipt write — rather
        # than silently dropping the resume_fallback flag for that attempt.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "first issue", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                        # a1 review (discovery)
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "PARTIAL\n",
             "stdout": _stream("th-fix1")},                           # a2 fix (cold)
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "second issue", id="f2", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                        # a2 review (verification) -> still fix
            {"exit": 1, "msg": ""},                                   # a3 fix resume -> crash
            {"exit": 1, "msg": ""},                                   # a3 fix cold fallback -> crash too
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FINAL\n"},  # a4 fix (cold, thread lost)
            {"exit": 0, "msg": _pass_msg()},                          # a4 review (verification) -> pass
        ])
        threads = {}
        calls = []
        real_write = forge_run.write_final_review_receipt

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real_write(*args, **kwargs)

        with mock.patch.object(
            forge_run, "write_final_review_receipt", side_effect=spy,
        ):
            outcome = forge_run.run_final_review_loop(
                self.spec, run_base, self.run_dir, self.fake, self.d,
                "standard", "auto", threads, plan_path=plan,
            )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.attempts, 4)
        # One receipt write per attempt (1, 2, 3, 4) — attempt 3 is the
        # double-crash (exec_ok=False, rework) attempt that was previously
        # dropped entirely.
        self.assertEqual(len(calls), 4)
        self.assertTrue(calls[2]["resume_fallback"])

    def test_halt_payload_and_repair_task_unchanged(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        repair = {"title": "Fix legacy bug", "tier": "standard"}
        self._responses([
            # line 99 is well outside the diff -> verified pre-existing -> halt.
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "99", "legacy bug", contract_ref="spec:Alpha section",
                repair_task=repair,
            )},
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            self.spec, run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "scope-decision")
        self.assertEqual(outcome.repair_task, repair)
        self.assertNotIn("fix: final-review", self._log_lines())
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["halt_reason"], "scope-decision")


if __name__ == "__main__":
    unittest.main()
