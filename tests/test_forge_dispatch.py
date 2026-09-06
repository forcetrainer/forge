"""Worker dispatch argv/model/effort, acceptance-command execution, and worker/reviewer subprocess timeouts."""
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

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import forge_status  # noqa: E402


def _defer_msg(finding_id, summary):
    """A verdict carrying one deferrable improvement under an explicit
    finding id (``_findings_msg`` numbers ids positionally, and the resume
    tests need the SAME id re-reported across two invocations)."""
    return json.dumps({"verdict": "findings", "findings": [
        {"id": finding_id, "summary": summary, "location": None,
         "provenance": "in-diff", "impact": "improvement",
         "contract_ref": None, "convergence": None, "carried_from": None,
         "repair_task": None},
    ]})


def _defer_and_halt_msg(defer_id, defer_summary, halt_file, halt_summary):
    """One reviewer verdict carrying both a deferrable improvement and a
    pre-existing contract-breaking finding — the task stages a deferral AND
    halts, which is the shape a resume has to survive."""
    return json.dumps({"verdict": "findings", "findings": [
        {"id": defer_id, "summary": defer_summary, "location": None,
         "provenance": "in-diff", "impact": "improvement",
         "contract_ref": None, "convergence": None, "carried_from": None,
         "repair_task": None},
        {"id": "h1", "summary": halt_summary,
         "location": {"file": halt_file, "lines": "1"},
         "provenance": "in-diff", "impact": "contract-breaking",
         "contract_ref": "Spec \u00a7X", "convergence": None,
         "carried_from": None,
         "repair_task": {"title": "Fix the legacy bug", "tier": "standard"}},
    ]})


class DispatchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-dispatch-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("# Task brief\n")

    def test_tier_resolution_emits_exact_model_effort_argv(self):
        for tier, (model, effort) in forge_run.TIER_MAP.items():
            run_dir = os.path.join(self.d, "run-" + tier)
            os.makedirs(run_dir, exist_ok=True)
            task = forge_run.Task(number=1, title="t", tier=tier)
            res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
            argv = res.argv
            self.assertIn("exec", argv)
            self.assertIn("-m", argv)
            self.assertIn(model, argv)
            self.assertIn("-c", argv)
            self.assertIn("model_reasoning_effort=" + effort, argv)
            self.assertIn("--output-last-message", argv)

    def test_ultra_never_appears_in_emitted_argv(self):
        for tier in forge_run.TIER_MAP:
            run_dir = os.path.join(self.d, "runu-" + tier)
            os.makedirs(run_dir, exist_ok=True)
            task = forge_run.Task(number=1, title="t", tier=tier)
            res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
            self.assertNotIn("ultra", " ".join(res.argv))

    def test_prompt_carries_contract_preamble_and_brief(self):
        run_dir = os.path.join(self.d, "run-prompt")
        os.makedirs(run_dir, exist_ok=True)
        task = forge_run.Task(number=1, title="t", tier="trivial")
        res = forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)
        prompt = res.prompt
        self.assertIn("# Task brief", prompt)
        self.assertIn("forge execution worker", prompt)

    def test_missing_contract_source_raises(self):
        empty = tempfile.mkdtemp(prefix="forge-run-noagents-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        old = os.environ.get("FORGE_AGENTS_DIR")
        os.environ["FORGE_AGENTS_DIR"] = empty
        self.addCleanup(
            lambda: os.environ.__setitem__("FORGE_AGENTS_DIR", old)
            if old is not None
            else os.environ.pop("FORGE_AGENTS_DIR", None)
        )
        run_dir = os.path.join(self.d, "run-noagents")
        os.makedirs(run_dir, exist_ok=True)
        task = forge_run.Task(number=1, title="t", tier="trivial")
        with self.assertRaises(RuntimeError):
            forge_run.dispatch_worker(task, self.brief, self.fake, run_dir)


class RunAcceptanceTests(unittest.TestCase):
    def test_success_and_failure_recorded_per_command(self):
        d = tempfile.mkdtemp(prefix="forge-run-acc-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        task = forge_run.Task(
            number=1, title="t", tier="trivial",
            acceptance_commands=["true", "false"],
        )
        results = forge_run.run_acceptance(task, d)
        self.assertEqual([r.command for r in results], ["true", "false"])
        self.assertEqual(results[0].exit_code, 0)
        self.assertNotEqual(results[1].exit_code, 0)


class TimeoutTests(unittest.TestCase):
    """--timeout SECONDS bounds worker and reviewer codex subprocess calls. A
    worker timeout is a failed iteration (rework/escalation path); a reviewer
    timeout is a contract error (loud exit 1, no receipt)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-timeout-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self):
        # Ignore harness artifacts so the working tree is clean at run start
        # (the commit-discipline precondition halts on a dirty tree).
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

    def _run(self, plan_path, extra_args=(), responses=None):
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        if responses is not None:
            resp_path = os.path.join(self.d, "responses.json")
            with open(resp_path, "w") as f:
                json.dump(responses, f)
            env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def test_worker_timeout_counts_as_failed_iteration_then_escalates(self):
        # A worker timeout is an execution failure -> implicit fix-retry finding:
        # it reworks every attempt (never scope-halts) until the backstop
        # (MAX_ATTEMPTS_BACKSTOP == 5), then escalates with the timeout as the
        # outstanding finding.
        plan = self._plan(PLAN_PASS)  # trivial, no reviewer, no git repo needed
        res = self._run(
            plan,
            extra_args=["--timeout", "0.2"],
            responses=[{"exit": 0, "msg": "", "sleep": 2}],
        )
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-5.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "backstop")
        self.assertTrue(
            any("timed out" in f_ for f_ in receipt["outstanding_findings"])
        )

    def test_reviewer_timeout_exits_one_naming_cause(self):
        plan = self._plan(PLAN_STD)  # standard tier -> reviewer dispatched
        self._init_repo()
        res = self._run(
            plan,
            extra_args=["--timeout", "0.2"],
            responses=[
                {"exit": 0, "msg": ""},              # worker: fast
                {"exit": 0, "msg": _pass_msg(), "sleep": 2},  # reviewer: sleeps past timeout
            ],
        )
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("reviewer", res.stderr.lower())
        self.assertIn("timed out", res.stderr.lower())


class AutofixAndDeferralTests(unittest.TestCase):
    """--autofix flag threading (default `auto`, `gate` short-circuits, an
    invalid value rejected by argparse) and task-level deferral aggregation into
    run.json (Phase 7 Task 7: Autonomy flag + Deferral handling). Standard-tier
    plans need a git repo (the reviewer packet is a `git diff`)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-autofix-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self, tracked=()):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        for name in tracked:
            with open(os.path.join(self.d, name), "w") as f:
                f.write("base\n")
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

    def _run(self, plan_path, extra_args=(), responses=None):
        if os.path.exists(self.log):
            os.remove(self.log)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        if responses is not None:
            resp_path = os.path.join(self.d, "responses.json")
            with open(resp_path, "w") as f:
                json.dump(responses, f)
            env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake, *extra_args],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _worker_dispatch_count(self, marker):
        return sum(
            1 for a in _log_argvs(self.log) if _find_dispatch([a], marker) is not None
        )

    def test_invalid_autofix_value_rejected_by_argparse(self):
        # An out-of-`choices` value is an argparse error (exit 2) naming the flag
        # and the bad choice -- not an "unrecognized argument", which is what a
        # runner with no --autofix flag would emit.
        plan = self._plan(PLAN_STD)
        res = self._run(plan, extra_args=["--autofix", "bogus"])
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("invalid choice", res.stderr.lower())
        self.assertIn("autofix", res.stderr.lower())

    def test_default_autofix_mode_is_auto(self):
        # No --autofix flag -> mode defaults to `auto`, recorded in run.json.
        # Acceptance `true` changes nothing, so the whole-plan diff is empty and
        # no final review / doc-sync runs -- the terminal write still records it.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},           # worker
            {"exit": 0, "msg": _pass_msg()},  # reviewer
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["autofix_mode"], "auto")

    def test_gate_mode_halts_task_on_any_finding_without_fix_dispatch(self):
        # --autofix gate reaches execute_task: any reviewer finding (even an
        # improvement) halts at attempt 1 with halt_reason "gate", and no rework
        # worker is dispatched (the worker runs exactly once).
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, extra_args=["--autofix", "gate"], responses=[
            {"exit": 0, "msg": ""},                    # worker
            {"exit": 0, "msg": _findings_msg("nit")},  # reviewer: any finding
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1.json")) as f:
            receipt = json.load(f)
        self.assertEqual(receipt["status"], "escalated")
        self.assertEqual(receipt["halt_reason"], "gate")
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["autofix_mode"], "gate")
        self.assertEqual(self._worker_dispatch_count("task-1-worker-last"), 1)

    def test_gate_mode_threads_to_final_review(self):
        # --autofix gate reaches the final-review loop through run_plan (not a
        # hardcoded "auto"): the per-task review passes, but any final-review
        # finding halts the run with an escalated-final-review status.
        plan = self._plan(PLAN_STD_TRACKED)
        self._init_repo(tracked=["f1.txt"])
        res = self._run(plan, extra_args=["--autofix", "gate"], responses=[
            {"exit": 0, "msg": ""},                          # worker
            {"exit": 0, "msg": _pass_msg()},                 # task 1 review
            {"exit": 0, "msg": _findings_msg("final nit")},  # final review: gate halt
        ])
        self.assertEqual(res.returncode, 2, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(data["status"], "escalated-final-review")
        self.assertEqual(data["autofix_mode"], "gate")

    def test_task_deferrals_aggregate_into_run_json(self):
        # An improvement-only per-task finding defers (auto mode); the task passes
        # and its deferral is aggregated into run.json under `deferrals`, carrying
        # the summary/impact for the orchestrator's DEFERRALS.md write-back.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        # The passed task's ledger annotation commits a plan.md change, so the
        # whole-plan diff is non-empty and the final review runs -> pass it
        # explicitly (and let doc-sync clamp to that pass) so the only deferral
        # aggregated is the task's own.
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                             # worker
            {"exit": 0, "msg": _findings_msg("harmless nit")},  # reviewer: improvement
            {"exit": 0, "msg": _pass_msg()},                    # final review: pass
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        self.assertEqual(len(data["deferrals"]), 1)
        self.assertEqual(data["deferrals"][0]["summary"], "harmless nit")
        self.assertEqual(data["deferrals"][0]["impact"], "improvement")

    def test_staged_deferral_records_the_task_that_produced_it(self):
        # `from` is specified as plan path PLUS task number, and nothing
        # downstream of the runner can recover which task produced a finding —
        # so the task number must be staged onto the entry here, at staging
        # time, or the whole "<plan>, Task N" contract is unreachable.
        plan = self._plan(PLAN_STD)
        self._init_repo()
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                             # worker
            {"exit": 0, "msg": _findings_msg("harmless nit")},  # reviewer: defer
            {"exit": 0, "msg": _pass_msg()},                    # final review
            {"exit": 0, "msg": '{"doc_sync": "clean"}'},        # doc-sync
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        entry = data["deferrals"][0]
        self.assertEqual(entry["task_number"], 1)
        self.assertEqual(
            forge_status.deferral_provenance(
                data["plan"], entry, os.path.join(self.run_dir, "run.json")),
            "{}, Task 1".format(data["plan"]),
        )

    def test_final_review_deferral_records_the_stage_not_a_task_number(self):
        # A final-review finding belongs to no single task. It still must not
        # fall back to a bare plan path indistinguishable from a task entry,
        # and must never claim a task number it does not have.
        plan = self._plan(PLAN_STD_TRACKED)
        self._init_repo(tracked=["f1.txt"])
        res = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                           # worker
            {"exit": 0, "msg": _pass_msg()},                  # task 1 review
            {"exit": 0, "msg": _findings_msg("final nit")},   # final review: defer
            {"exit": 0, "msg": '{"doc_sync": "clean"}'},      # doc-sync
        ])
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.run_dir, "run.json")) as f:
            data = json.load(f)
        entry = next(d for d in data["deferrals"] if d["summary"] == "final nit")
        self.assertEqual(entry["stage"], "final-review")
        self.assertNotIn("task_number", entry)
        self.assertEqual(
            forge_status.deferral_provenance(
                data["plan"], entry, os.path.join(self.run_dir, "run.json")),
            "{}, final review".format(data["plan"]),
        )


class StageDeferralsTests(unittest.TestCase):
    """``stage_deferrals`` stamps the producing stage onto each entry and is
    LOSSLESS: the spec's guarantee is that a deferral, once staged, is never
    lost, so nothing a verdict raised may be silently collapsed away. The
    dedupe exists only to stop a resumed run from re-staging what a PRIOR
    invocation already staged."""

    def test_two_findings_sharing_an_id_in_one_verdict_both_survive(self):
        staged = forge_run.stage_deferrals([], [
            {"id": "F1", "summary": "first"},
            {"id": "F1", "summary": "second"},
        ], task_number=2)
        self.assertEqual([e["summary"] for e in staged], ["first", "second"])
        self.assertEqual([e["task_number"] for e in staged], [2, 2])

    def test_carried_keys_from_a_prior_invocation_are_not_staged_again(self):
        prior = forge_run.stage_deferrals(
            [], [{"id": "F1", "summary": "first"}], task_number=2)
        prior[0]["issue"] = "77"
        carried = forge_run.deferral_stage_keys(prior)
        staged = forge_run.stage_deferrals(
            prior, [{"id": "F1", "summary": "first"}], task_number=2,
            carried=carried,
        )
        self.assertEqual([e["summary"] for e in staged], ["first"])
        self.assertEqual(staged[0]["issue"], "77")

    def test_the_same_id_from_a_different_task_is_a_different_deferral(self):
        # Finding ids are per-review, so task 1's F1 and task 2's F1 name two
        # unrelated findings and both must be staged.
        staged = forge_run.stage_deferrals(
            [], [{"id": "F1", "summary": "task one"}], task_number=1)
        carried = forge_run.deferral_stage_keys(staged)
        staged = forge_run.stage_deferrals(
            staged, [{"id": "F1", "summary": "task two"}], task_number=2,
            carried=carried,
        )
        self.assertEqual([e["summary"] for e in staged], ["task one", "task two"])

    def test_findings_are_copied_not_mutated_in_place(self):
        # The same dicts are already persisted on the task's attempt receipt,
        # which records what the reviewer said, not staging bookkeeping.
        finding = {"id": "F1", "summary": "first"}
        forge_run.stage_deferrals([], [finding], task_number=2)
        self.assertNotIn("task_number", finding)


class DeferralResumePersistenceTests(unittest.TestCase):
    """Staged deferrals persist across a resume (Staging and idempotency
    spec). run_plan's accumulator starts empty each invocation and an
    already-passed task `continue`s before it is ever appended to, so a
    terminal write that did not read the prior list back would replace
    run.json's `deferrals` with only this invocation's entries — erasing
    earlier staged deferrals and any `issue` numbers already filed against
    them. Order matters as much as content: `--occurrence` is an ordinal
    into this list, so a shifted list mis-attributes a filing."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-defer-resume-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self, tracked=()):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        for name in tracked:
            with open(os.path.join(self.d, name), "w") as f:
                f.write("base\n")
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

    def _run(self, plan_path, responses):
        if os.path.exists(self.log):
            os.remove(self.log)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        resp_path = os.path.join(self.d, "responses.json")
        with open(resp_path, "w") as f:
            json.dump(responses, f)
        env["FORGE_FAKE_RESPONSES"] = resp_path
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), plan_path,
             "--spec", self.spec, "--run-dir", self.run_dir,
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def _run_json(self):
        with open(os.path.join(self.run_dir, "run.json")) as f:
            return json.load(f)

    def test_resume_keeps_earlier_deferrals_their_issues_and_their_order(self):
        plan = self._plan(PLAN_TWO_STD)
        # pre.txt is committed and never touched by the run, so a finding
        # located in it verifies pre-existing -> halt (a real scope decision),
        # which is how run 1 is made to stop at task 2.
        self._init_repo(tracked=["f1.txt", "pre.txt"])
        res1 = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                              # t1 worker
            {"exit": 0, "msg": _defer_msg("d1", "task one nit")},  # t1 review: defer
            {"exit": 0, "msg": ""},                              # t2 worker
            {"exit": 0, "msg": _defer_and_halt_msg(              # t2 review: defer + halt
                "d2", "task two nit", "pre.txt", "pre-existing scope call")},
        ])
        self.assertEqual(res1.returncode, 2, res1.stderr)
        staged = self._run_json()["deferrals"]
        self.assertEqual([d["summary"] for d in staged],
                         ["task one nit", "task two nit"])
        self.assertEqual([d["task_number"] for d in staged], [1, 2])

        # The close-out gate files the first one; the issue number is recorded
        # back onto the staged entry, exactly as `defer --run` writes it.
        path = os.path.join(self.run_dir, "run.json")
        data = self._run_json()
        data["deferrals"][0]["issue"] = "77"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        # The escalated attempt left the tree dirty (task 2's acceptance
        # created f2.txt); a human discards it before resuming, since the
        # runner requires a clean tree at run start.
        self._git("reset", "--hard")
        self._git("clean", "-fd")
        res2 = self._run(plan, responses=[
            {"exit": 0, "msg": ""},                             # t2 worker
            # Task 2 re-runs and re-reports the SAME deferrable finding. It is
            # already staged, so it must not be staged a second time — a
            # duplicate would be filed twice and would shift every later
            # --occurrence ordinal.
            {"exit": 0, "msg": _defer_msg("d2", "task two nit")},
            {"exit": 0, "msg": _pass_msg()},                    # final review
            {"exit": 0, "msg": '{"doc_sync": "clean"}'},        # doc-sync
        ])
        self.assertEqual(res2.returncode, 0, res2.stderr)
        after = self._run_json()["deferrals"]
        self.assertEqual([d["summary"] for d in after],
                         ["task one nit", "task two nit"])
        self.assertEqual([d["task_number"] for d in after], [1, 2])
        self.assertEqual(after[0]["issue"], "77")
        self.assertNotIn("issue", after[1])


class OversizedPromptTests(unittest.TestCase):
    """A brief/packet larger than ARG_MAX must still dispatch. Passing the
    prompt as the final argv element capped it at the OS limit (1 MiB on
    darwin, shared with the environment block) and failed as an opaque
    OSError/E2BIG far below the model's usable context."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-bigprompt-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.run_dir = os.path.join(self.d, "run")
        os.makedirs(self.run_dir, exist_ok=True)
        # Comfortably past ARG_MAX on darwin/linux so the argv path cannot
        # accidentally succeed on a machine with a roomier limit.
        self.big = "x" * (4 * 1024 * 1024)
        self.brief = os.path.join(self.d, "brief.md")
        with open(self.brief, "w") as f:
            f.write("# Task brief\n" + self.big)

    def test_worker_dispatches_a_brief_larger_than_arg_max(self):
        task = forge_run.Task(number=1, title="t", tier="trivial")
        res = forge_run.dispatch_worker(task, self.brief, self.fake, self.run_dir)
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)

    def test_reviewer_dispatches_a_packet_larger_than_arg_max(self):
        last_msg = os.path.join(self.run_dir, "last.txt")
        verdict = json.dumps({"verdict": "pass", "findings": []})
        responses = os.path.join(self.d, "responses.json")
        with open(responses, "w") as f:
            json.dump([{"exit": 0, "msg": verdict}], f)
        os.environ["FORGE_FAKE_RESPONSES"] = responses
        self.addCleanup(os.environ.pop, "FORGE_FAKE_RESPONSES", None)
        v = forge_run._dispatch_review_call(
            "gpt-5.6-luna", "low", "review preamble", self.brief, self.fake,
            last_msg, os.path.join(self.run_dir, "live.log"),
            os.path.join(self.run_dir, "events.jsonl"), "-- header --",
            "task-1-reviewer", {},
        )
        self.assertEqual(v.kind, "pass")

    def test_child_receives_the_whole_oversized_prompt(self):
        """Not just 'it did not crash' — the full text must actually arrive."""
        prompt_log = os.path.join(self.d, "received-prompt.txt")
        os.environ["FORGE_FAKE_PROMPT_LOG"] = prompt_log
        self.addCleanup(os.environ.pop, "FORGE_FAKE_PROMPT_LOG", None)
        task = forge_run.Task(number=1, title="t", tier="trivial")
        res = forge_run.dispatch_worker(task, self.brief, self.fake, self.run_dir)
        self.assertEqual(res.exit_code, 0)
        with open(prompt_log) as f:
            received = f.read()
        self.assertIn("forge execution worker", received)   # contract preamble
        self.assertIn("# Task brief", received)             # brief
        self.assertIn(self.big, received)                   # nothing truncated

    def test_prompt_is_not_passed_as_an_argv_element(self):
        """The prompt must be absent from argv entirely: `codex exec` appends
        piped stdin as a separate `<stdin>` block when a prompt argument is
        also present, which would duplicate the whole packet."""
        task = forge_run.Task(number=1, title="t", tier="trivial")
        res = forge_run.dispatch_worker(task, self.brief, self.fake, self.run_dir)
        self.assertNotIn(self.big, "".join(res.argv))
        self.assertNotIn("# Task brief", "".join(res.argv))
        # argv ends at the flag pair, with no trailing positional PROMPT
        self.assertEqual(res.argv[-2], "--output-last-message")

if __name__ == "__main__":
    unittest.main()
