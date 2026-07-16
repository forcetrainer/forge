"""Tee helper: stream a subprocess to a live log while returning exit/tail.

run_teed streams a child's merged stdout+stderr line-by-line into a live-log
file (prefixed with a phase header) and returns the exit code, a timed-out
flag, and the output tail the runner loop needs — a behavior-preserving
replacement for subprocess.run(capture_output=True).
"""
import os
import sys
import tempfile
import time
import unittest

from _forge_support import *  # noqa: F401,F403 — sets up sys.path + loads forge_run
import forge_common


def _tmp(suffix=".log"):
    fd, p = tempfile.mkstemp(suffix=suffix, prefix="forge-tee-")
    os.close(fd)
    return p


class RunTeedTests(unittest.TestCase):
    def setUp(self):
        self.p = _tmp()
        self.addCleanup(lambda: os.path.exists(self.p) and os.remove(self.p))

    def test_writes_header_then_stdout(self):
        argv = [sys.executable, "-c", "print('hello'); print('world')"]
        res = forge_common.run_teed(
            argv, timeout=30, live_path=self.p, header="── worker · codex exec ──"
        )
        content = open(self.p).read()
        self.assertIn("── worker · codex exec ──", content)
        self.assertIn("hello", content)
        self.assertIn("world", content)
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)

    def test_merges_stderr_into_stream(self):
        argv = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('errline\\n'); print('outline')",
        ]
        forge_common.run_teed(argv, timeout=30, live_path=self.p, header="h")
        content = open(self.p).read()
        self.assertIn("errline", content)
        self.assertIn("outline", content)

    def test_returns_nonzero_exit(self):
        argv = [sys.executable, "-c", "import sys; sys.exit(3)"]
        res = forge_common.run_teed(argv, timeout=30, live_path=self.p, header="h")
        self.assertEqual(res.exit_code, 3)
        self.assertFalse(res.timed_out)

    def test_tail_is_capped_to_last_chars(self):
        argv = [sys.executable, "-c", "print('A' * 5000)"]
        res = forge_common.run_teed(argv, timeout=30, live_path=self.p, header="h")
        self.assertEqual(len(res.tail), forge_common._ACC_TAIL_CHARS)
        self.assertEqual(set(res.tail.strip()), {"A"})

    def test_timeout_kills_and_flags(self):
        argv = [sys.executable, "-c", "import time; time.sleep(10)"]
        start = time.monotonic()
        res = forge_common.run_teed(argv, timeout=0.5, live_path=self.p, header="h")
        elapsed = time.monotonic() - start
        self.assertTrue(res.timed_out)
        self.assertIsNone(res.exit_code)
        self.assertLess(elapsed, 5, "timed-out child should be killed promptly")

    def test_second_call_appends(self):
        forge_common.run_teed(
            [sys.executable, "-c", "print('one')"],
            timeout=30, live_path=self.p, header="── first ──",
        )
        forge_common.run_teed(
            [sys.executable, "-c", "print('two')"],
            timeout=30, live_path=self.p, header="── second ──",
        )
        content = open(self.p).read()
        self.assertIn("── first ──", content)
        self.assertIn("── second ──", content)
        self.assertLess(content.index("── first ──"), content.index("── second ──"))
        self.assertIn("one", content)
        self.assertIn("two", content)

    def test_shell_command_string(self):
        res = forge_common.run_teed(
            "echo shellhello", shell=True, timeout=30, live_path=self.p,
            header="── acceptance ──",
        )
        content = open(self.p).read()
        self.assertIn("shellhello", content)
        self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
