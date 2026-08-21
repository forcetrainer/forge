"""`--json` dispatch, thread-id capture, and live-log event rendering (Phase 13
Task 3: Codex mechanics / Live-log rendering specs).

Three layers, matching the spec's own testing list:
  - forge_common.run_json_teed: the JSON-aware tee primitive (thread capture,
    raw JSONL retention, plain-text rendering, non-JSON fallback) — no live
    `codex` binary involved, just a python -c child that prints JSONL lines.
  - forge_run._render_event_line: the event -> text mapping in isolation.
  - forge_run.dispatch_*: argv shape (`--json` present, `--ephemeral` absent)
    and threads-map population, via the fake codex harness.
  - forge_run.run_plan: threads persisted per role in run.json, cleared at the
    start of every invocation (never carried across a resume).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _forge_support import *  # noqa: F401,F403
import forge_common


def _tmp(suffix=".log"):
    fd, p = tempfile.mkstemp(suffix=suffix, prefix="forge-threads-")
    os.close(fd)
    return p


def _jsonl(events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _worker_event_stream(thread_id="th-abc123", text="Applied the fix."):
    """A representative `codex exec --json` event stream: thread start, a turn
    with a rendered agent message and a rendered command execution, plus a
    handful of control events that carry no text — the shape a real worker
    dispatch would tee."""
    return _jsonl([
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"item_type": "agent_message", "text": ""}},
        {"type": "item.completed", "item": {"item_type": "agent_message", "text": text}},
        {
            "type": "item.completed",
            "item": {
                "item_type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "3 passed\n",
            },
        },
        {"type": "turn.completed"},
    ])


# --- forge_common.run_json_teed ---------------------------------------------


class RunJsonTeedTests(unittest.TestCase):
    def setUp(self):
        self.live = _tmp(".log")
        self.events = _tmp(".jsonl")
        self.addCleanup(lambda: os.path.exists(self.live) and os.remove(self.live))
        self.addCleanup(lambda: os.path.exists(self.events) and os.remove(self.events))

    def _run(self, py_src, render_line=None, timeout=30):
        argv = [sys.executable, "-c", py_src]
        return forge_common.run_json_teed(
            argv, timeout=timeout, live_path=self.live, events_path=self.events,
            header="── worker · codex exec ──",
            render_line=render_line or forge_run._render_event_line,
        )

    def test_captures_thread_id_from_first_thread_started_event(self):
        res = self._run(
            "import json\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'th-1'}))\n"
            "print(json.dumps({'type': 'turn.completed'}))\n"
        )
        self.assertEqual(res.thread_id, "th-1")
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)

    def test_no_thread_started_yields_none_without_raising(self):
        res = self._run(
            "import json\nprint(json.dumps({'type': 'turn.completed'}))\n"
        )
        self.assertIsNone(res.thread_id)
        self.assertEqual(res.exit_code, 0)

    def test_first_thread_started_wins_over_a_later_one(self):
        res = self._run(
            "import json\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'first'}))\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'second'}))\n"
        )
        self.assertEqual(res.thread_id, "first")

    def test_rendered_live_log_has_no_raw_json_object_lines(self):
        stream = _worker_event_stream()
        src = "import sys\nsys.stdout.write({!r})\n".format(stream)
        self._run(src)
        content = open(self.live).read()
        # No raw JSON event line landed in the live log (the parity bar this
        # runner change exists to meet) -- only the header + rendered text.
        json_object_lines = [
            ln for ln in content.splitlines() if ln.strip().startswith("{")
        ]
        self.assertEqual(json_object_lines, [])
        self.assertIn("Applied the fix.", content)
        self.assertIn("$ pytest -q", content)
        self.assertIn("3 passed", content)

    def test_raw_jsonl_retained_separately(self):
        stream = _worker_event_stream(thread_id="th-retained")
        src = "import sys\nsys.stdout.write({!r})\n".format(stream)
        self._run(src)
        events_lines = [ln for ln in open(self.events).read().splitlines() if ln.strip()]
        self.assertEqual(len(events_lines), 6)  # one per event in the stream
        parsed = [json.loads(ln) for ln in events_lines]
        self.assertEqual(parsed[0]["type"], "thread.started")
        self.assertEqual(parsed[0]["thread_id"], "th-retained")

    def test_non_json_line_falls_back_to_verbatim_live_tee(self):
        res = self._run(
            "import sys\nsys.stdout.write('not json, e.g. stray stderr text\\n')\n"
        )
        content = open(self.live).read()
        self.assertIn("not json, e.g. stray stderr text", content)
        self.assertIsNone(res.thread_id)
        # A non-JSON line is not an event -- never written to events_path.
        self.assertEqual(open(self.events).read().strip(), "")

    def test_timeout_kills_and_flags(self):
        res = self._run("import time\ntime.sleep(10)\n", timeout=0.5)
        self.assertTrue(res.timed_out)
        self.assertIsNone(res.exit_code)
        self.assertIsNone(res.thread_id)


# --- forge_run._render_event_line -------------------------------------------


class RenderEventLineTests(unittest.TestCase):
    def test_agent_message_completed_renders_its_text(self):
        line = forge_run._render_event_line(
            {"type": "item.completed", "item": {"item_type": "agent_message", "text": "hello"}}
        )
        self.assertEqual(line, "hello")

    def test_command_execution_completed_renders_command_and_output(self):
        line = forge_run._render_event_line({
            "type": "item.completed",
            "item": {
                "item_type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "3 passed\n",
            },
        })
        self.assertIn("$ pytest -q", line)
        self.assertIn("3 passed", line)

    def test_empty_text_item_completed_renders_none(self):
        self.assertIsNone(forge_run._render_event_line(
            {"type": "item.completed", "item": {"item_type": "agent_message", "text": ""}}
        ))

    def test_control_events_render_none(self):
        for event in (
            {"type": "thread.started", "thread_id": "x"},
            {"type": "turn.started"},
            {"type": "turn.completed"},
            {"type": "item.started", "item": {"item_type": "agent_message", "text": "partial"}},
        ):
            self.assertIsNone(forge_run._render_event_line(event))

    def test_error_event_renders_a_line(self):
        line = forge_run._render_event_line({"type": "error", "message": "boom"})
        self.assertEqual(line, "error: boom")

    def test_non_dict_event_renders_none(self):
        self.assertIsNone(forge_run._render_event_line("not a dict"))
        self.assertIsNone(forge_run._render_event_line(None))


# --- dispatch argv: --json present, --ephemeral absent, threads populated ---


class DispatchJsonArgvTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-threads-dispatch-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("# Task brief\n")
        self.packet = os.path.join(self.d, "packet.md")
        with open(self.packet, "w") as f:
            f.write("# Packet\n")
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir, exist_ok=True)
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

    def _set_responses(self, responses):
        path = os.path.join(self.d, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        os.environ["FORGE_FAKE_RESPONSES"] = path
        os.environ["FORGE_FAKE_LOG"] = self.log

    def test_worker_argv_has_json_not_ephemeral_and_captures_thread(self):
        os.environ.pop("FORGE_FAKE_RESPONSES", None)
        self._set_responses([{"exit": 0, "msg": "", "stdout": _worker_event_stream("th-w")}])
        task = forge_run.Task(number=1, title="t", tier="trivial")
        threads = {}
        res = forge_run.dispatch_worker(task, self.brief, self.fake, self.run_dir, threads)
        self.assertIn("--json", res.argv)
        self.assertNotIn("--ephemeral", res.argv)
        self.assertEqual(res.thread_id, "th-w")
        self.assertEqual(threads, {"task-1-worker": "th-w"})

    def test_reviewer_argv_has_json_not_ephemeral_and_captures_thread(self):
        self._set_responses([
            {"exit": 0, "msg": _pass_msg(), "stdout": _worker_event_stream("th-r")}
        ])
        task = forge_run.Task(number=1, title="t", tier="standard")
        threads = {}
        verdict = forge_run.dispatch_reviewer(
            task, self.packet, self.fake, self.run_dir, threads
        )
        self.assertEqual(verdict.kind, "pass")
        argvs = _log_argvs(self.log)
        self.assertTrue(argvs)
        argv = argvs[-1]
        self.assertIn("--json", argv)
        self.assertNotIn("--ephemeral", argv)
        self.assertEqual(threads, {"task-1-reviewer": "th-r"})

    def test_final_review_argv_has_json_not_ephemeral_and_captures_thread(self):
        self._set_responses([
            {"exit": 0, "msg": _pass_msg(), "stdout": _worker_event_stream("th-f")}
        ])
        threads = {}
        verdict = forge_run.dispatch_final_review(
            self.packet, self.fake, self.run_dir, "standard", threads
        )
        self.assertEqual(verdict.kind, "pass")
        self.assertEqual(threads, {"final-reviewer": "th-f"})

    def test_final_review_fix_argv_has_json_not_ephemeral_and_captures_thread(self):
        self._set_responses([{"exit": 0, "msg": "", "stdout": _worker_event_stream("th-x")}])
        threads = {}
        res = forge_run.dispatch_final_review_fix(
            self.brief, self.fake, self.run_dir, "standard", 1, threads
        )
        self.assertIn("--json", res.argv)
        self.assertNotIn("--ephemeral", res.argv)
        self.assertEqual(res.thread_id, "th-x")
        self.assertEqual(threads, {"final-fixer": "th-x"})

    def test_doc_sync_argv_has_json_not_ephemeral(self):
        os.environ.pop("FORGE_FAKE_RESPONSES", None)
        log = os.path.join(self.d, "fakelog")
        os.environ["FORGE_FAKE_LOG"] = log
        forge_run.dispatch_doc_sync(
            self.spec, "deadbeef", "diff --git a b\n", self.run_dir, "standard",
            self.fake, self.d,
        )
        argvs = _log_argvs(log)
        self.assertTrue(argvs)
        argv = argvs[-1]
        self.assertIn("--json", argv)
        self.assertNotIn("--ephemeral", argv)


# --- run.json threads map: per-role, cleared at invocation start -----------


class RunJsonThreadsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-threads-runjson-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
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

    def test_threads_written_per_role_and_cleared_on_a_later_invocation(self):
        plan = self._plan(PLAN_PASS)  # single trivial task, no git repo required
        self._set_responses([{"exit": 0, "msg": "", "stdout": _worker_event_stream("th-run1")}])

        rc = forge_run.run_plan(plan, self.spec, self.run_dir, self.fake, self.d)
        self.assertEqual(rc, 0)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["threads"], {"task-1-worker": "th-run1"})
        # The raw JSONL and rendered live log both landed on disk, per role.
        events_path = os.path.join(self.run_dir, "task-1-worker-events.jsonl")
        self.assertTrue(os.path.exists(events_path))
        self.assertIn("th-run1", open(events_path).read())
        live_path = os.path.join(self.run_dir, "task-1-live.log")
        live_content = open(live_path).read()
        json_object_lines = [
            ln for ln in live_content.splitlines() if ln.strip().startswith("{")
        ]
        self.assertEqual(json_object_lines, [])

        # A later invocation over the same run-dir (a resume) starts with a
        # cleared threads map -- the task is already `passed`, so nothing
        # re-dispatches and no thread id from the first invocation survives.
        rc2 = forge_run.run_plan(plan, self.spec, self.run_dir, self.fake, self.d)
        self.assertEqual(rc2, 0)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data2 = json.load(f)
        self.assertEqual(data2["threads"], {})


if __name__ == "__main__":
    unittest.main()
