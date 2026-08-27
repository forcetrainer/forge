"""Resume dispatch + cold-spawn fallback (Phase 13 Task 5: Session continuity /
Continuity scope and failure specs).

Two layers:
  - Pure argv-shape unit tests: `dispatch_worker`/`dispatch_reviewer` called
    directly with `resume_thread` set, asserting the resume argv shape/order
    (`codex exec resume --json --output-last-message <path> -m <model> -c
    'model_reasoning_effort="<effort>"' <thread_id> <prompt>`), tier pinning,
    and last-message capture preserved.
  - `execute_task`-level integration tests (direct call, no subprocess, so the
    per-task `threads` dict can be pre-seeded and inspected): rework laps
    resume the worker with a findings-only prompt; discovery reviews never
    resume even when a reviewer thread id already exists; verification laps
    resume the reviewer; a missing thread id or a failed resume falls back to
    exactly one cold spawn with the full brief/packet and records
    `resume_fallback: true`; a fallback that also fails still yields the
    ordinary execution-failure finding path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _forge_support import *  # noqa: F401,F403


def _worker_event_stream(thread_id, text="ok"):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"item_type": "agent_message", "text": text}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


# --- argv shape: resume_thread set -------------------------------------------


class DispatchResumeArgvTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-resume-argv-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("# findings only\n- fix the thing\n")
        self.packet = os.path.join(self.d, "packet.md")
        with open(self.packet, "w") as f:
            f.write("# Packet\n")
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

    def test_worker_resume_argv_shape_and_ordering(self):
        self._set_responses([{"exit": 0, "msg": ""}])
        task = forge_run.Task(number=1, title="t", tier="standard")
        threads = {}
        last_msg_path = os.path.join(self.run_dir, "task-1-worker-last.txt")
        res = forge_run.dispatch_worker(
            task, self.brief, self.fake, self.run_dir, threads,
            resume_thread="th-worker-1",
        )
        self.assertEqual(
            res.argv,
            [
                self.fake, "exec", "resume", "--json",
                "--output-last-message", last_msg_path,
                "-m", "gpt-5.6-terra",
                "-c", 'model_reasoning_effort="medium"',
                "th-worker-1",
                # no trailing PROMPT: it rides stdin, unbounded by ARG_MAX
            ],
        )
        self.assertEqual(res.prompt, "# findings only\n- fix the thing\n")

    def test_worker_resume_prompt_has_no_contract_preamble(self):
        # Cold spawn prepends the tier's contract preamble; resume does not —
        # the worker already holds it from the cold-spawn attempt.
        self._set_responses([{"exit": 0, "msg": ""}])
        task = forge_run.Task(number=1, title="t", tier="standard")
        cold = forge_run.dispatch_worker(
            task, self.brief, self.fake, self.run_dir, {},
        )
        resumed = forge_run.dispatch_worker(
            task, self.brief, self.fake, self.run_dir, {}, resume_thread="th-x",
        )
        self.assertNotEqual(cold.prompt, resumed.prompt)
        self.assertEqual(resumed.prompt, "# findings only\n- fix the thing\n")
        self.assertGreater(len(cold.prompt), len(resumed.prompt))

    def test_reviewer_resume_argv_shape_and_ordering(self):
        self._set_responses([{"exit": 0, "msg": _pass_msg()}])
        task = forge_run.Task(number=1, title="t", tier="standard")
        threads = {}
        last_msg_path = os.path.join(self.run_dir, "task-1-review-last.txt")
        forge_run.dispatch_reviewer(
            task, self.packet, self.fake, self.run_dir, threads,
            resume_thread="th-reviewer-1",
        )
        argvs = _log_argvs(self.log)
        argv = argvs[-1]
        # The fake codex's own log strips argv[0] (its own path, sys.argv[1:]),
        # so this starts at "exec".
        self.assertEqual(argv[0:5], [
            "exec", "resume", "--json", "--output-last-message", last_msg_path,
        ])
        self.assertEqual(argv[5:9], [
            "-m", "gpt-5.6-terra", "-c", 'model_reasoning_effort="medium"',
        ])
        self.assertEqual(argv[9], "th-reviewer-1")

    def test_resume_output_last_message_still_captures_verdict(self):
        self._set_responses([{"exit": 0, "msg": _pass_msg()}])
        task = forge_run.Task(number=1, title="t", tier="standard")
        verdict = forge_run.dispatch_reviewer(
            task, self.packet, self.fake, self.run_dir, {}, resume_thread="th-1",
        )
        self.assertEqual(verdict.kind, "pass")


# --- execute_task-level: rework/verification resume + fallback --------------


class ExecuteTaskResumeTests(unittest.TestCase):
    """Direct `execute_task` calls (no subprocess) so the per-task `threads`
    dict can be pre-seeded and read back after the run."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-resume-exec-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec_marker = "SPECMARKERUNIQUE"
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write("# Spec\n\n{}\n".format(self.spec_marker))
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
        self.plog = self.log + ".prompts"
        os.environ["FORGE_FAKE_PROMPT_LOG"] = self.plog

    def _task1(self, plan_path):
        tasks = forge_run.parse_plan_tasks(plan_path)
        return forge_run.order_tasks(tasks)[0]

    def test_rework_resumes_worker_with_findings_only_prompt(self):
        # A fix finding on attempt 1 reworks; attempt 2's worker dispatch must
        # resume (using the thread captured on attempt 1) with a prompt that
        # carries the finding text but neither the brief nor the spec.
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream("th-w1")},  # a1 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "GUARDXYZ needed here")},                      # a1 review
            {"exit": 0, "msg": ""},                                          # a2 worker (resume)
            {"exit": 0, "msg": _pass_msg()},                                 # a2 review
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(threads.get("task-1-worker"), "th-w1")
        argvs = _log_argvs(self.log)
        prompts = _log_prompts(self.plog)
        worker_calls = [
            (a, pr) for a, pr in zip(argvs, prompts)
            if "--output-last-message" in a
            and "task-1-worker-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(worker_calls), 2)
        worker_a2, prompt = worker_calls[-1]
        self.assertIn("resume", worker_a2)
        self.assertIn("th-w1", worker_a2)
        self.assertIn("GUARDXYZ", prompt)
        self.assertNotIn(self.spec_marker, prompt)
        self.assertNotIn("Do the thing", prompt)  # the plan's Goal text (brief re-paste)
        self.assertNotIn("Files:", prompt)

    def test_discovery_review_dispatches_cold_even_with_a_preexisting_thread_id(self):
        plan = self._plan(PLAN_STD)
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # reviewer (discovery)
        ])
        task = self._task1(plan)
        # A stale reviewer thread id pre-seeded for this task's role -- must be
        # ignored on attempt 1 (discovery is always cold), even though it's
        # sitting right there.
        threads = {"task-1-reviewer": "th-stale-preexisting"}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        argvs = _log_argvs(self.log)
        rev = _find_dispatch(argvs, "task-1-review-last")
        self.assertIsNotNone(rev, argvs)
        self.assertNotIn("resume", rev)
        self.assertNotIn("th-stale-preexisting", rev)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertFalse(receipt["resume_fallback"])

    def test_verification_review_resumes_when_thread_id_exists(self):
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream("th-w1")},  # a1 worker
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "issue"), "stdout": _worker_event_stream("th-r1")},  # a1 review
            {"exit": 0, "msg": ""},           # a2 worker (resume)
            {"exit": 0, "msg": _pass_msg()},  # a2 review (verification, resume)
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(threads.get("task-1-reviewer"), "th-r1")
        argvs = _log_argvs(self.log)
        review_calls = [
            a for a in argvs
            if "--output-last-message" in a
            and "task-1-review-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(review_calls), 2)
        self.assertNotIn("resume", review_calls[0])   # discovery: cold
        self.assertIn("resume", review_calls[1])       # verification: resumed
        self.assertIn("th-r1", review_calls[1])

    def test_missing_worker_thread_id_falls_back_cold_with_resume_flag(self):
        # Attempt 1's worker never emits a thread.started event (no captured
        # id), so attempt 2's rework lap has nothing to resume -- it must
        # dispatch cold with the full brief and record the fallback.
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": ""},                                          # a1 worker (no thread)
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},    # a1 review
            {"exit": 0, "msg": ""},                                          # a2 worker (cold fallback)
            {"exit": 0, "msg": _pass_msg()},                                 # a2 review
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        argvs = _log_argvs(self.log)
        worker_calls = [
            a for a in argvs
            if "--output-last-message" in a
            and "task-1-worker-last" in a[a.index("--output-last-message") + 1]
        ]
        self.assertEqual(len(worker_calls), 2)  # no doubled resume-then-cold call
        self.assertNotIn("resume", worker_calls[1])
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertTrue(receipt["resume_fallback"])
        self.assertEqual(receipt["status"], "passed")

    def test_failed_worker_resume_retries_cold_once_and_converges(self):
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream("th-w1")},  # a1 worker
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},    # a1 review
            {"exit": 9, "msg": ""},                                          # a2 worker resume: fails
            {"exit": 0, "msg": ""},                                          # a2 worker cold fallback: ok
            {"exit": 0, "msg": _pass_msg()},                                 # a2 review
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        argvs = _log_argvs(self.log)
        worker_calls = [
            a for a in argvs
            if "--output-last-message" in a
            and "task-1-worker-last" in a[a.index("--output-last-message") + 1]
        ]
        # Attempt 1 (cold) + attempt 2 resume (fails) + attempt 2 cold fallback.
        self.assertEqual(len(worker_calls), 3)
        self.assertIn("resume", worker_calls[1])
        self.assertNotIn("resume", worker_calls[2])
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertTrue(receipt["resume_fallback"])
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["worker_exit_code"], 0)  # the cold fallback's exit code

    def test_failed_cold_fallback_worker_still_yields_execution_failure_finding(self):
        # Both the resume attempt AND its cold-spawn fallback fail: the
        # execution-failure finding path (worker crash -> implicit fix-retry
        # finding, rework) is exercised exactly as it was before Task 5 —
        # continuity never changes what a genuine dispatch failure means.
        plan = self._plan(PLAN_STD_TRACKED)
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream("th-w1")},  # a1 worker
            {"exit": 0, "msg": _fix_findings_msg("f1.txt", "2", "issue")},    # a1 review
            {"exit": 9, "msg": ""},                                          # a2 worker resume: fails
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        # Every remaining call clamps to the last scripted response (exit 9),
        # so the worker never recovers and the backstop eventually halts.
        self.assertEqual(outcome.status, "escalated")
        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "rework")
        self.assertTrue(receipt["resume_fallback"])
        self.assertEqual(receipt["worker_exit_code"], 9)
        self.assertEqual(receipt["outstanding_findings"], [])
        self.assertIsNone(receipt["review_verdict"])  # execution failure preempts review


if __name__ == "__main__":
    unittest.main()
