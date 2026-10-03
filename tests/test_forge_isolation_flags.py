"""Worker isolation flags: every runner `codex exec` argv carries the shared isolation group; reviewers add a read-only sandbox override."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from _forge_support import *  # noqa: F401,F403
from _forge_support import _log_argvs

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import forge_common  # noqa: E402

DISABLES = ("multi_agent_v2", "memories")
AGENTS_OFF = "agents.enabled=false"
SANDBOX = 'sandbox_mode="read-only"'
PASS_MSG = json.dumps({"verdict": "pass", "findings": []})


def _has_pair(argv, flag, value):
    return any(argv[i] == flag and argv[i + 1] == value for i in range(len(argv) - 1))


def _sandbox_overrides(argv):
    return [a for a in argv if a.startswith("sandbox_mode") or a in ("-s", "--sandbox")]


class IsolationFlagsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-isolation-")
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
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.d, "fakelog")
        self.responses = os.path.join(self.d, "responses.json")
        with open(self.responses, "w") as f:
            json.dump([{"exit": 0, "msg": PASS_MSG}], f)
        self._old = {k: os.environ.get(k) for k in ("FORGE_FAKE_LOG", "FORGE_FAKE_RESPONSES")}
        self.addCleanup(self._restore_env)
        os.environ["FORGE_FAKE_LOG"] = self.log
        os.environ["FORGE_FAKE_RESPONSES"] = self.responses
        self.task = forge_run.Task(number=1, title="t", tier="standard")

    def _restore_env(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _all_shapes(self):
        """Run every dispatch shape once; return {shape: argv}."""
        calls = {
            "worker-cold": lambda: forge_run.dispatch_worker(
                self.task, self.brief, self.fake, self.run_dir),
            "worker-resume": lambda: forge_run.dispatch_worker(
                self.task, self.brief, self.fake, self.run_dir, resume_thread="th-1"),
            "reviewer-cold": lambda: forge_run.dispatch_reviewer(
                self.task, self.packet, self.fake, self.run_dir),
            "reviewer-resume": lambda: forge_run.dispatch_reviewer(
                self.task, self.packet, self.fake, self.run_dir, resume_thread="th-1"),
            "final-cold": lambda: forge_run.dispatch_final_review(
                self.packet, self.fake, self.run_dir, "standard"),
            "final-resume": lambda: forge_run.dispatch_final_review(
                self.packet, self.fake, self.run_dir, "standard", resume_thread="th-1"),
            "fixer-cold": lambda: forge_run.dispatch_final_review_fix(
                self.brief, self.fake, self.run_dir, "standard", 1),
            "fixer-resume": lambda: forge_run.dispatch_final_review_fix(
                self.brief, self.fake, self.run_dir, "standard", 1, resume_thread="th-1"),
            "docsync-cold": lambda: forge_run.dispatch_doc_sync(
                [self.spec], "deadbeef", "diff --git a b\n", self.run_dir, "standard",
                self.fake, self.d),
        }
        out = {}
        for name, call in calls.items():
            if os.path.exists(self.log):
                os.remove(self.log)
            call()
            argvs = _log_argvs(self.log)
            self.assertEqual(len(argvs), 1, name)
            out[name] = argvs[0]
        return out

    def test_constants_have_spec_values(self):
        self.assertEqual(
            forge_common.CODEX_ISOLATION_ARGS,
            ("-c", AGENTS_OFF, "--disable", "multi_agent_v2",
             "--disable", "memories"),
        )
        # `--disable multi_agent` alone is ignored by codex (the model catalog
        # outranks the feature flag); agents.enabled=false is the real switch.
        self.assertIn(AGENTS_OFF, forge_common.CODEX_ISOLATION_ARGS)
        self.assertNotIn("multi_agent", forge_common.CODEX_ISOLATION_ARGS)
        self.assertEqual(forge_common.CODEX_REVIEWER_SANDBOX_ARGS, ("-c", SANDBOX))

    def test_every_shape_carries_all_disable_pairs(self):
        for name, argv in self._all_shapes().items():
            self.assertTrue(_has_pair(argv, "-c", AGENTS_OFF), name)
            for feat in DISABLES:
                self.assertTrue(_has_pair(argv, "--disable", feat), (name, feat))

    def test_reviewers_read_only_writers_no_sandbox_override(self):
        for name, argv in self._all_shapes().items():
            if name.startswith(("reviewer", "final-")) and not name.startswith("fixer"):
                self.assertTrue(_has_pair(argv, "-c", SANDBOX), name)
            else:
                self.assertEqual(_sandbox_overrides(argv), [], name)

    def test_flags_built_from_the_two_constants(self):
        iso = ("--disable", "sentinel_feature")
        sbx = ("-c", 'sandbox_mode="sentinel"')
        with mock.patch.object(forge_common, "CODEX_ISOLATION_ARGS", iso), \
                mock.patch.object(forge_common, "CODEX_REVIEWER_SANDBOX_ARGS", sbx):
            shapes = self._all_shapes()
        for name, argv in shapes.items():
            self.assertTrue(_has_pair(argv, "--disable", "sentinel_feature"), name)
            self.assertFalse(_has_pair(argv, "--disable", "memories"), name)
            is_reviewer = name.startswith(("reviewer", "final-"))
            self.assertEqual(_has_pair(argv, "-c", 'sandbox_mode="sentinel"'), is_reviewer, name)

    def test_ultra_never_emitted(self):
        for name, argv in self._all_shapes().items():
            self.assertNotIn("ultra", " ".join(argv), name)


if __name__ == "__main__":
    unittest.main()
