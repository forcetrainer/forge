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

**Acceptance:** `true` passes

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
            k: os.environ.get(k) for k in (
                "FORGE_FAKE_LOG", "FORGE_FAKE_RESPONSES", "FORGE_FAKE_PROMPT_LOG")
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
        self.plog = self.log + ".prompts"
        os.environ["FORGE_FAKE_PROMPT_LOG"] = self.plog

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
                *forge_common.CODEX_ISOLATION_ARGS,
                "--output-last-message", last_msg_path,
                "-m", "gpt-6.1-sol",
                "-c", 'model_reasoning_effort="medium"',
                "th-fixer-1",
                # no trailing PROMPT: it rides stdin, unbounded by ARG_MAX
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
        self.assertNotEqual(cold.prompt, resumed.prompt)
        self.assertEqual(
            resumed.prompt,
            "## Final-review fix — resolve these findings\n\n- fix it\n",
        )
        self.assertGreater(len(cold.prompt), len(resumed.prompt))


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
        self.plog = self.log + ".prompts"
        self._set_env("FORGE_FAKE_PROMPT_LOG", self.plog)

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
        # Harness artifacts are ignored (as in every other repo fixture and in
        # real usage, where `.forge/` is gitignored): the review diff now
        # includes untracked files, so an unignored fake-codex log or run dir
        # would leak into the packet as a new-file hunk.
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
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

    def _reraised_halt_msg(self, carried_from="t1:h1"):
        # Line 99 is outside the reviewed diff -> pre-existing x
        # contract-breaking, the scope-decision cell.
        return _fix_findings_msg(
            "f1.txt", "99", "the legacy guard is wrong", id="h1",
            contract_ref="spec:Alpha section", carried_from=carried_from,
            repair_task={
                "title": "Fix the legacy guard", "files": ["f1.txt"],
                "spec": "Alpha section", "tests": ["the guard holds"],
                "acceptance": "`true`", "tier": "standard",
            },
        )

    def _seeded_h1(self):
        # The task-1 finding the final reviewer is given (as a replayed seed)
        # and re-raises by carrying it: only a prior finding the packet
        # carried may be named by a scoped carried_from.
        return [{
            "id": "t1:h1", "identity": "t1:h1", "summary": "the legacy guard",
            "location": {"file": "f1.txt", "lines": "99"},
            "provenance": "in-run", "impact": "unverifiable",
            "contract_ref": None, "convergence": None, "carried_from": None,
            "repair_task": None, "disposition": "seed",
        }]

    def test_unapproved_preexisting_finding_halts_scope_decision(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        self._responses([{"exit": 0, "msg": self._reraised_halt_msg()}])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
            seeded_findings=self._seeded_h1(),
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "scope-decision")

    def test_final_finding_carried_from_a_task_identity_no_seed_supplied_is_a_contract_error(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        msg = self._reraised_halt_msg()
        self._responses([{"exit": 0, "msg": msg}, {"exit": 0, "msg": msg}])
        with self.assertRaises(RuntimeError) as cm:
            forge_run.run_final_review_loop(
                [self.spec], run_base, self.run_dir, self.fake, self.d,
                "standard", "auto", {}, plan_path=plan,
                approved_ids=frozenset({"t1:h1"}),
            )
        self.assertIn("t1:h1", str(cm.exception))

    def test_approved_id_reraised_by_final_reviewer_passes(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        self._responses([{"exit": 0, "msg": self._reraised_halt_msg()}])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
            approved_ids=frozenset({"t1:h1"}),
            seeded_findings=self._seeded_h1(),
        )
        self.assertEqual(outcome.status, "passed")

    def test_approved_finding_contributes_no_repair_task_to_a_later_non_scope_halt(self):
        # gate mode halts on any finding (step 1) before the scope check; the
        # approved finding's repair_task must not ride along on that halt.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        self._responses([{"exit": 0, "msg": self._reraised_halt_msg()}])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "gate", {}, plan_path=plan,
            approved_ids=frozenset({"t1:h1"}),
            seeded_findings=self._seeded_h1(),
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "gate")
        self.assertIsNone(outcome.repair_task)

    def test_seed_carried_final_finding_keeps_its_identity_across_a_verification_lap(self):
        # A final finding carried from seed t2:f1 (reviewer id f9) comes back
        # on the verification packet under its identity, so echoing the
        # packet's id keeps t2:f1 (and its approval exemption), never final:f9.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        seed = self._seeded_h1()
        seed[0].update({"id": "t2:f1", "identity": "t2:f1"})
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "carried seed", id="f9", contract_ref="spec:Alpha section",
                carried_from="t2:f1")},
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FIXED\n"},
            {"exit": 0, "msg": self._reraised_halt_msg(carried_from="t2:f1").replace(
                '"id": "h1"', '"id": "f9"')},
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
            approved_ids=frozenset({"t2:f1"}), seeded_findings=seed,
        )
        with open(os.path.join(self.run_dir, "final-review.md")) as f:
            packet = f.read()
        block = packet.split("```json\n", 1)[1].split("```", 1)[0]
        prior = json.loads(block)
        self.assertEqual([(e["id"], e["carried_from"], e["identity"]) for e in prior],
                         [("t2:f1", None, "t2:f1")])
        self.assertEqual(outcome.status, "passed")

    def test_final_review_raising_the_same_local_id_unlinked_is_not_exempt(self):
        # An approval of t1:h1 exempts only a finding the final reviewer
        # carries from it; the same local id raised afresh is final:h1.
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        self._responses([
            {"exit": 0, "msg": self._reraised_halt_msg(carried_from=None)}])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
            approved_ids=frozenset({"t1:h1"}),
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "scope-decision")

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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
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

    def test_final_verification_packet_delta_over_formerly_untracked_files(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        for name, text in (("u_edit.txt", "e\n"), ("u_del.txt", "d\n"),
                           ("u_same.txt", "s\n")):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(text)
        ops = [
            {"op": "append", "path": os.path.join(self.d, "u_edit.txt"), "text": "EDITED\n"},
            {"op": "delete", "path": os.path.join(self.d, "u_del.txt")},
            {"op": "write", "path": os.path.join(self.d, "u_add.txt"), "text": "ADDED\n"},
        ]
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},
            {"exit": 0, "msg": "", "file_ops": ops, "stdout": _stream("th-fix1")},
            {"exit": 0, "msg": _pass_msg(), "stdout": _stream("th-rev1")},
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        with open(os.path.join(self.run_dir, "final-review.md")) as f:
            packet = f.read()
        self.assertEqual(packet.count("+EDITED"), 1, packet)
        self.assertEqual(packet.count("deleted file mode"), 1, packet)
        self.assertEqual(packet.count("+ADDED"), 1, packet)
        self.assertNotIn("u_same.txt", packet)

    def test_invalid_final_verdict_retries_by_resuming_final_reviewer_thread(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        bad = json.dumps({"verdict": "findings", "findings": [{
            "id": "f1", "summary": "BADLOC", "location": {"file": "f1.txt"},
            "provenance": "in-diff", "impact": "contract-breaking",
            "contract_ref": "spec:Alpha section", "convergence": None,
            "carried_from": None, "repair_task": None,
        }]})
        self._responses([
            {"exit": 0, "msg": bad, "stdout": _stream("th-fin1")},   # review (invalid)
            {"exit": 0, "msg": json.dumps({"verdict": "pass", "coverage": [
                {"id": cid, "status": "satisfied", "evidence": "stub"}
                for cid in ("spec:Alpha section", "t1")]})},  # retry (resumed)
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        calls = [
            (a, pr) for a, pr in zip(_log_argvs(self.log), _log_prompts(self.plog))
            if "--output-last-message" in a
            and "final-review-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("resume", calls[0][0])
        self.assertIn("resume", calls[1][0])
        self.assertIn("th-fin1", calls[1][0])
        self.assertNotIn("diff --git", calls[1][1])

    def test_verification_lap_retry_resumes_same_thread_as_verification_dispatch(self):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        f1 = os.path.join(self.d, "f1.txt")
        bad = json.dumps({"verdict": "findings", "findings": [{
            "id": "f2", "summary": "BADLOC", "location": {"file": "f1.txt"},
            "provenance": "in-diff", "impact": "contract-breaking",
            "contract_ref": "spec:Alpha section", "convergence": None,
            "carried_from": None, "repair_task": None,
        }]})
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue", contract_ref="spec:Alpha section",
            ), "stdout": _stream("th-rev1")},                        # a1 review
            {"exit": 0, "msg": "", "append_file": f1, "append_text": "FIXED\n",
             "stdout": _stream("th-fix1")},                          # a2 fix
            {"exit": 0, "msg": bad, "stdout": _stream("th-rev1")},   # a2 verification (invalid)
            {"exit": 0, "msg": _pass_msg()},                         # a2 retry
        ])
        threads = {}
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        calls = [
            a for a in _log_argvs(self.log)
            if "--output-last-message" in a
            and "final-review-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(calls), 3)
        self.assertIn("th-rev1", calls[1])   # verification resumed
        self.assertIn("resume", calls[2])
        self.assertIn("th-rev1", calls[2])   # the retry: the same thread

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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.attempts, 3)
        argvs = _log_argvs(self.log)
        prompts = _log_prompts(self.plog)
        fixer_calls = [
            (a, pr) for a, pr in zip(argvs, prompts)
            if "--output-last-message" in a
            and "final-review-fix-attempt" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(fixer_calls), 2)
        self.assertNotIn("resume", fixer_calls[0][0])
        self.assertIn("resume", fixer_calls[1][0])
        self.assertIn("th-fix1", fixer_calls[1][0])
        resume_prompt = fixer_calls[1][1]
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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
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
                [self.spec], run_base, self.run_dir, self.fake, self.d,
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
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", threads, plan_path=plan,
        )
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(outcome.halt_reason, "scope-decision")
        self.assertEqual(outcome.repair_task, repair)
        self.assertNotIn("fix: final-review", self._log_lines())
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["halt_reason"], "scope-decision")

    # --- repair_task rule judged on runner-derived provenance (classify_ctx) ---

    _REPAIR = {
        "title": "Fix the legacy guard", "files": ["f1.txt"], "spec": "x",
        "tests": ["the guard holds"], "acceptance": "`true`", "tier": "standard",
    }

    def _final_claimed_in_diff_outside_diff(self, first_repair_task, retry_repair_task):
        run_base = self._init_repo_with_task_work()
        plan = self._plan()
        msg = lambda rt: _fix_findings_msg(
            "f1.txt", "99", "the legacy guard is wrong", id="h1",
            contract_ref="spec:Alpha section", repair_task=rt)
        responses = [{"exit": 0, "msg": msg(first_repair_task)}]
        if first_repair_task is None:
            responses.append({"exit": 0, "msg": msg(retry_repair_task)})
        self._responses(responses)
        return forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
        )

    def test_final_review_retries_on_missing_repair_task_for_derived_pre_existing(self):
        outcome = self._final_claimed_in_diff_outside_diff(None, self._REPAIR)
        retry = os.path.join(self.run_dir, "final-coverage-retry.md")
        self.assertTrue(os.path.exists(retry), "no verdict-validation retry fired")
        with open(retry) as f:
            self.assertIn(
                "h1: repair_task is required on a pre-existing "
                "contract-breaking finding", f.read())
        self.assertEqual(outcome.halt_reason, "scope-decision")

    def test_final_review_does_not_retry_when_repair_task_supplied(self):
        outcome = self._final_claimed_in_diff_outside_diff(self._REPAIR, None)
        self.assertFalse(os.path.exists(
            os.path.join(self.run_dir, "final-coverage-retry.md")))
        self.assertEqual(outcome.halt_reason, "scope-decision")


# Same fixture plus a **Tests:** block, so the plan has real t<N>.t<M>
# grammar — the coverage source `build_final_checklist` deliberately does not
# emit, and therefore the one a replayed seeded finding can cite into a
# membership failure.
PLAN_FINAL_WITH_TESTS = PLAN_FINAL.replace(
    "**Acceptance:** `true` passes",
    "**Tests:**\n- the alpha path holds\n- the beta path holds\n"
    "\n**Acceptance:** `true` passes",
)


class FinalReviewCitableSetTests(unittest.TestCase):
    """The final review's citable set is the whole plan's, computed once and
    passed on every lap — so a replayed seeded finding may still cite the
    per-task test id it was raised against (`t<N>.t<M>`, which the final
    CHECKLIST never carries), and a verification lap's reduced checklist
    never narrows what a finding may name."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-final-citable-")
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
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
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

    def _plan(self, text):
        path = os.path.join(self.d, "plan.md")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_seeded_finding_may_recite_its_per_task_test_id(self):
        # A per-task finding dispositioned `seed` is replayed verbatim into
        # the final discovery packet, contract_ref included; the final
        # reviewer re-raises it citing that same `t1.t1`. That id is real plan
        # grammar but not a final CHECKLIST item, so a checklist-derived
        # citable set would reject a correct finding on a pointer technicality.
        run_base = self._init_repo_with_task_work()
        plan = self._plan(PLAN_FINAL_WITH_TESTS)
        self.assertNotIn(
            "t1.t1",
            {it.id for it in
             forge_checklist.build_final_checklist(plan, self.spec)},
        )
        f1 = os.path.join(self.d, "f1.txt")
        seeded = [{
            "id": "t1:s1", "identity": "t1:s1", "summary": "seeded from task 1",
            "location": {"file": "f1.txt", "lines": "2"},
            "provenance": "unverifiable", "impact": "contract-breaking",
            "contract_ref": "t1.t1", "convergence": None,
            "carried_from": None, "repair_task": None,
        }]
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "seeded issue", contract_ref="t1.t1",
            )},                                                   # discovery
            {"exit": 0, "msg": "", "append_file": f1,
             "append_text": "FIXED\n"},                           # fix
            {"exit": 0, "msg": _pass_msg()},                      # verification
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan, seeded_findings=seeded,
        )
        self.assertEqual(outcome.status, "passed")

    def test_verification_lap_still_accepts_a_whole_plan_ref(self):
        # The verification packet carries the REDUCED checklist; a finding
        # citing `t1` (a real final-checklist integration item, just not one
        # the outstanding findings referenced) must still be citable.
        run_base = self._init_repo_with_task_work()
        plan = self._plan(PLAN_FINAL)
        f1 = os.path.join(self.d, "f1.txt")
        self._responses([
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "first issue", contract_ref="spec:Alpha section",
            )},                                                   # discovery
            {"exit": 0, "msg": "", "append_file": f1,
             "append_text": "FIXED\n"},                           # fix 1
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "second issue", id="f2", contract_ref="t1",
            )},                                                   # verification
            {"exit": 0, "msg": "", "append_file": f1,
             "append_text": "FIXED2\n"},                          # fix 2
            {"exit": 0, "msg": _pass_msg()},                      # verification
        ])
        outcome = forge_run.run_final_review_loop(
            [self.spec], run_base, self.run_dir, self.fake, self.d,
            "standard", "auto", {}, plan_path=plan,
        )
        self.assertEqual(outcome.status, "passed")


class FinalPacketCitableTests(unittest.TestCase):
    def test_final_packet_contains_the_final_citable_set_including_task_test_ids(self):
        import forge_git
        d = tempfile.mkdtemp(prefix="forge-final-packet-citable-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        spec = os.path.join(d, "spec.md")
        with open(spec, "w") as f:
            f.write(SPEC_WITH_ALPHA)
        plan = os.path.join(d, "plan.md")
        with open(plan, "w") as f:
            f.write(PLAN_FINAL_WITH_TESTS)
        citable = forge_checklist.final_citable_refs(plan, spec)
        path = forge_git._final_packet(
            [spec], "HEAD", "", d, citable=citable,
        )
        with open(path) as f:
            packet = f.read()
        self.assertIn("## Citable refs", packet)
        self.assertIn("- t1.t1\n", packet)
        self.assertIn("- spec:Alpha section\n", packet)

    def test_final_packet_without_citable_has_no_section(self):
        import forge_git
        d = tempfile.mkdtemp(prefix="forge-final-packet-nocitable-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        spec = os.path.join(d, "spec.md")
        with open(spec, "w") as f:
            f.write(SPEC_WITH_ALPHA)
        path = forge_git._final_packet([spec], "HEAD", "", d)
        with open(path) as f:
            self.assertNotIn("Citable refs", f.read())


class PriorIdentitiesTests(unittest.TestCase):
    def test_collects_the_identity_of_every_prior_finding(self):
        self.assertEqual(
            forge_run._prior_identities(
                [{"id": "t2:f1", "identity": "t2:f1"},
                 {"id": "f3", "identity": "t4:f3"}]),
            frozenset({"t2:f1", "t4:f3"}))
        self.assertEqual(forge_run._prior_identities([]), frozenset())
        self.assertEqual(forge_run._prior_identities(None), frozenset())

    def test_an_entry_without_identity_raises_naming_it(self):
        for entry in ({"id": "f7", "summary": "x"},
                      {"id": "f7", "identity": None}):
            with self.assertRaises(ValueError) as cm:
                forge_run._prior_identities([entry])
            self.assertIn("f7", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


# --- reviewer write discipline: the final review ----------------------------

_FR_WORKER = {"exit": 0, "msg": ""}
_FR_TASK_PASS = {"exit": 0, "msg": '{"verdict": "pass"}'}


class FinalReviewerWroteTests(ReviewerWroteCase):
    """A final reviewer that changes the repository halts the run as a
    `final-review` stage escalation, class `reviewer-wrote` (exit 2)."""

    def _assert_stage_halt(self, res, final_reviews):
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertEqual(self.run_json()["status"], "escalated-final-review")
        halt = self.halt_record()
        self.assertEqual(halt["stage"], "final-review")
        self.assertEqual(halt["halt_reason"], "reviewer-wrote")
        self.assertIn("stray.txt", " ".join(halt["changes"]))
        # The task's edits are committed by the final review, so the
        # pre-review tree equals HEAD: nothing to freeze, no empty commit.
        self.assertIsNone(halt["freeze_commit"])
        self.assertNotEqual(subprocess.run(
            ["git", "rev-parse", "-q", "--verify", halt_ref(self, "final-review")],
            cwd=self.repo, capture_output=True).returncode, 0)
        self.assertEqual(self.porcelain(), "")
        self.assertFalse(os.path.exists(self.stray))
        self.assertEqual(len(self.dispatches("final-review-last")), final_reviews)
        self.assertEqual(self.commit_count(), 2)  # base + the passed task only
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/main")
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["halt_reason"], "reviewer-wrote")
        self.assertIn("stray.txt", " ".join(receipt["findings"]))

    def test_cold_final_reviewer_write_halts(self):
        res = self.run_cli([
            _FR_WORKER, _FR_TASK_PASS,
            {"exit": 0, "msg": '{"verdict": "pass"}', "file_ops": [self.stray_op()]},
        ])
        self._assert_stage_halt(res, final_reviews=1)

    def test_resumed_final_reviewer_write_halts_without_fallback(self):
        res = self.run_cli([
            _FR_WORKER, _FR_TASK_PASS,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-final1")},
            {"exit": 1, "msg": "", "file_ops": [self.stray_op()]},
            {"exit": 0, "msg": '{"verdict": "pass"}'},  # a cold fallback would land here
        ])
        self._assert_stage_halt(res, final_reviews=2)

    def test_final_reviewer_commit_is_restored(self):
        res = self.run_cli([
            _FR_WORKER, _FR_TASK_PASS,
            {"exit": 0, "msg": '{"verdict": "pass"}', "file_ops": [
                self.stray_op(),
                {"op": "git", "args": ["add", "-A"]},
                {"op": "git", "args": ["commit", "-m", "reviewer commit"]},
            ]},
        ])
        self._assert_stage_halt(res, final_reviews=1)
        halt = self.halt_record()
        self.assertEqual(
            self.git("log", "-1", "--format=%s", halt["reviewer_head"]).strip(),
            "reviewer commit")

    def test_final_reviewer_that_writes_nothing_passes_through(self):
        res = self.run_cli([_FR_WORKER, _FR_TASK_PASS,
                            {"exit": 0, "msg": '{"verdict": "pass"}'}])
        self.assertEqual(res.returncode, 0, res.stderr)


def halt_ref(case, stage):
    run_id = os.path.basename(os.path.normpath(case.run_dir))
    return forge_run.freeze_stage_ref_name(run_id, stage)


class FinalFreezeFailureTests(ReviewerWroteCase):
    def test_reset_failure_records_stage_halt_and_names_both(self):
        rc, err = self.main_failing_git([
            _FR_WORKER, _FR_TASK_PASS,
            {"exit": 0, "msg": '{"verdict": "pass"}', "file_ops": [self.stray_op()]},
        ], "reset")
        self.assertEqual(rc, 1, err)
        self.assertIn("reviewer", err)
        self.assertIn("git reset", err)
        halt = self.run_json()["halt"]
        self.assertEqual(halt["stage"], "final-review")
        self.assertEqual(halt["halt_reason"], "reviewer-wrote")
        self.assertIn("stray.txt", " ".join(halt["changes"]))
        self.assertIn("git reset", halt["freeze_error"])


class FingerprintErrorFinalTests(ReviewerWroteCase):
    """The task review spends calls 1 and 2; the final reviewer's first
    dispatch is calls 3 and 4, a resumed retry's 5 and 6."""

    def _assert_contract_error(self, rc, err, final_reviews):
        self.assertEqual(rc, 1, err)
        self.assertIn("git rev-parse HEAD", err)
        self.assertEqual(self.run_json()["status"], "contract-error")
        self.assertEqual(len(self.dispatches("final-review-last")), final_reviews)
        self.assertEqual(self.commit_count(), 2)  # base + the passed task
        self.assertNotIn("final-review", self.git("log", "--format=%s"))

    def _cold(self):
        return [_FR_WORKER, _FR_TASK_PASS, {"exit": 0, "msg": '{"verdict": "pass"}'}]

    def _resumed(self):
        return [
            _FR_WORKER, _FR_TASK_PASS,
            {"exit": 0, "msg": INVALID_LOCATION_MSG,
             "stdout": thread_stream("th-final1")},
            {"exit": 0, "msg": '{"verdict": "pass"}'},
            {"exit": 0, "msg": '{"verdict": "pass"}'},
        ]

    def test_failure_before_cold_final_reviewer(self):
        rc, err = self.main_failing_fingerprint(self._cold(), 3)
        self._assert_contract_error(rc, err, final_reviews=0)

    def test_failure_on_exit_of_cold_final_reviewer(self):
        rc, err = self.main_failing_fingerprint(self._cold(), 4)
        self._assert_contract_error(rc, err, final_reviews=1)

    def test_failure_before_resumed_final_reviewer(self):
        rc, err = self.main_failing_fingerprint(self._resumed(), 5)
        self._assert_contract_error(rc, err, final_reviews=1)

    def test_failure_on_exit_of_resumed_final_reviewer(self):
        rc, err = self.main_failing_fingerprint(self._resumed(), 6)
        self._assert_contract_error(rc, err, final_reviews=2)


class UnverifiedHaltTests(UnverifiedCase):
    """A final review that passes with an unverified entry lacking a human
    call is a stage halt of class `unverified` (Disposition matrix)."""

    def _fix_msg(self):
        return json.dumps({"verdict": "findings", "findings": [{
            "id": "fx", "summary": "needs a fix",
            "location": {"file": "f1.txt", "lines": "2"},
            "provenance": "in-diff", "impact": "contract-breaking",
            "contract_ref": "t1", "convergence": None,
            "carried_from": None, "repair_task": None,
        }, self.seed_finding("f1", "cannot run the migration here")]})

    def test_coverage_entry_without_findings_halts_unverified(self):
        responses = self.first_call(self.coverage_msg("needs a prod snapshot"))
        responses.append(self.DOC_SYNC_CLEAN)
        real = forge_run._freeze_stage_halt
        with mock.patch.object(forge_run, "_freeze_stage_halt", wraps=real) as spy:
            rc, _, err = self.run_main(responses)
        self.assertEqual(rc, 2, err)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[2:4], ("final-review", "unverified"))
        run = self.run_json()
        self.assertEqual(run["status"], "escalated-final-review")
        halt = run["halt"]
        self.assertEqual(halt["stage"], "final-review")
        self.assertEqual(halt["halt_reason"], "unverified")
        self.assertEqual(halt["outstanding"], [
            {"kind": "coverage", "id": "t1", "reason": "needs a prod snapshot"}])
        self.assertEqual(run["unverified"], [{
            "kind": "coverage", "id": "t1", "reason": "needs a prod snapshot",
            "call": None}])
        self.assertEqual(self.dispatches("doc-sync-last"), [])
        self.assertEqual(self.porcelain(), "")

    def test_seed_omitted_by_verification_verdict_stays_open(self):
        rc, _, err = self.run_main([
            self.WORKER, self.TASK_PASS, self.final(self._fix_msg()),
            {"exit": 0, "msg": ""},                      # final-review fixer
            self.final('{"verdict": "pass"}'),           # verification omits f1
            self.DOC_SYNC_CLEAN,
        ])
        self.assertEqual(rc, 2, err)
        run = self.run_json()
        self.assertEqual(run["halt"]["halt_reason"], "unverified")
        self.assertEqual([e["id"] for e in run["unverified"]], ["final:f1"])
        self.assertIsNone(run["unverified"][0]["call"])
        self.assertEqual(self.dispatches("doc-sync-last"), [])

    def test_second_invocation_accumulates_and_keeps_first_call(self):
        rc, _, err = self.run_main(
            self.first_call(self.seed_msg(("f1", "no prod data"))))
        self.assertEqual(rc, 2, err)
        rc, _, err = self.run_main(
            [self.final(self.seed_msg(("f2", "cannot time the race")))],
            ["--resolve", "f1=repair"])
        self.assertEqual(rc, 2, err)
        run = self.run_json()
        self.assertEqual([e["id"] for e in run["unverified"]], ["final:f1", "final:f2"])
        self.assertEqual(self.entry("final:f1")["call"],
                         {"verb": "repair", "evidence": None})
        self.assertIsNone(self.entry("final:f2")["call"])
        self.assertEqual([o["id"] for o in run["halt"]["outstanding"]], ["final:f2"])

    def test_final_review_receipt_lists_every_unverified_entry(self):
        self.run_main(self.first_call(self.seed_msg(
            ("f1", "no prod data"), ("f2", "race untimed"))))
        with open(os.path.join(self.run_dir, "final-review.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["unverified"], [
            {"kind": "finding", "id": "final:f1", "reason": "no prod data",
             "call": None},
            {"kind": "finding", "id": "final:f2", "reason": "race untimed",
             "call": None}])
