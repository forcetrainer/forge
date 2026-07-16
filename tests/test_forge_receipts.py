"""Plan-checkbox ledger annotation."""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest

from _forge_support import *  # noqa: F401,F403


class WriteRunJsonProgressTests(unittest.TestCase):
    def _dir(self):
        d = tempfile.mkdtemp(prefix="forge-runjson-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_persists_progress_fields_when_given(self):
        d = self._dir()
        forge_run.write_run_json(
            d, "/p/plan.md", "/p/spec.md", "running",
            [{"number": 1, "title": "T", "tier": "trivial", "status": "passed",
              "attempts": 1, "commit": None, "started_at": "S0", "ended_at": "E0"}],
            "base", current_task=2, current_phase="review",
            started_at="RS", updated_at="RU", pid=4242,
        )
        with open(os.path.join(d, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["current_task"], 2)
        self.assertEqual(data["current_phase"], "review")
        self.assertEqual(data["started_at"], "RS")
        self.assertEqual(data["updated_at"], "RU")
        self.assertEqual(data["pid"], 4242)
        self.assertEqual(data["tasks"][0]["started_at"], "S0")
        self.assertEqual(data["tasks"][0]["ended_at"], "E0")

    def test_omits_progress_fields_when_none(self):
        d = self._dir()
        forge_run.write_run_json(d, "/p/plan.md", "/p/spec.md", "running", [], "base")
        with open(os.path.join(d, "run.json")) as f:
            data = json.load(f)
        for k in ("current_task", "current_phase", "started_at", "updated_at", "pid"):
            self.assertNotIn(k, data)


class AnnotateLedgerTests(unittest.TestCase):
    def test_checks_box_and_appends_status(self):
        d = tempfile.mkdtemp(prefix="forge-run-ledger-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w") as f:
            f.write(PLAN_PASS)
        task = forge_run.parse_plan_tasks(p)[0]
        forge_run.annotate_ledger(p, task, "passed, 1 attempt(s)")
        with open(p) as f:
            content = f.read()
        self.assertIn("[x] Done", content)
        self.assertIn("passed, 1 attempt(s)", content)

    def test_escalated_leaves_checkbox_unchecked(self):
        d = tempfile.mkdtemp(prefix="forge-run-ledger-esc-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w") as f:
            f.write(PLAN_PASS)
        task = forge_run.parse_plan_tasks(p)[0]
        forge_run.annotate_ledger(p, task, "escalated: worker exited 1")
        with open(p) as f:
            content = f.read()
        self.assertIn("[ ] Done", content)
        self.assertNotIn("[x] Done", content)
        self.assertIn("escalated: worker exited 1", content)
