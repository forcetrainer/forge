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
import forge_common


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
        for k in (
            "current_task", "current_phase", "started_at", "updated_at", "pid",
            "deferrals", "autofix_mode", "doc_sync",
        ):
            self.assertNotIn(k, data)

    def test_persists_deferrals_autofix_mode_doc_sync_when_given(self):
        d = self._dir()
        deferrals = [
            {"summary": "unused import", "location": {"file": "a.py", "lines": "1"},
             "provenance": "pre-existing", "why_harmless": "dead code, no contract impact"},
        ]
        doc_sync = {"status": "reconciled", "commit": "abc123", "reconciled": ["README.md"]}
        forge_run.write_run_json(
            d, "/p/plan.md", "/p/spec.md", "completed", [], "base",
            deferrals=deferrals, autofix_mode="auto", doc_sync=doc_sync,
        )
        with open(os.path.join(d, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["deferrals"], deferrals)
        self.assertEqual(data["autofix_mode"], "auto")
        self.assertEqual(data["doc_sync"], doc_sync)

    def test_write_watch_launcher(self):
        d = self._dir()
        p = forge_run.write_watch_launcher(d, "/abs/scripts/forge-monitor.py")
        self.assertEqual(p, os.path.join(d, ".forge", "watch"))
        with open(p) as f:
            content = f.read()
        self.assertIn("/abs/scripts/forge-monitor.py", content)
        self.assertIn("--follow", content)
        self.assertTrue(os.access(p, os.X_OK))


class WriteFinalReviewReceiptTests(unittest.TestCase):
    def test_carries_finding_classification(self):
        d = tempfile.mkdtemp(prefix="forge-final-review-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        finding = forge_common.Finding(
            id="f1", summary="missing null check", file="scripts/foo.py",
            lines="12-20", provenance="in-diff", impact="contract-breaking",
            contract_ref="Acceptance: `pytest -q`", disposition="fix",
        )
        verdict = forge_common.Verdict(kind="findings", findings=[finding])
        path = forge_run.write_final_review_receipt(d, verdict)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["verdict"], "findings")
        got = data["findings"][0]
        self.assertEqual(got["provenance"], "in-diff")
        self.assertEqual(got["impact"], "contract-breaking")
        self.assertEqual(got["disposition"], "fix")

    def test_persists_halt_reason_when_given(self):
        d = tempfile.mkdtemp(prefix="forge-final-review-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        verdict = forge_common.Verdict(kind="pass", findings=[])
        path = forge_run.write_final_review_receipt(d, verdict, halt_reason="scope-decision")
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["halt_reason"], "scope-decision")

    def test_omits_halt_reason_when_not_given(self):
        d = tempfile.mkdtemp(prefix="forge-final-review-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        verdict = forge_common.Verdict(kind="pass", findings=[])
        path = forge_run.write_final_review_receipt(d, verdict)
        with open(path) as f:
            data = json.load(f)
        self.assertNotIn("halt_reason", data)


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
