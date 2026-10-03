"""Spec sets through the runner (Task 5: Runner reads the spec set, records it and
checks it on resume).

Every test drives the real CLI (`forge-run.py`) over a temp git repo with the
fake `codex`. The repo root is the temp dir, so a plan's `**Spec files:**` paths
are relative to it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _forge_support import *  # noqa: F401,F403
from _forge_support import _pass_msg, _log_prompts

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import forge_receipts  # noqa: E402
import forge_status  # noqa: E402

ALPHA = "docs/forge/specs/alpha.md"
BETA = "docs/forge/specs/beta.md"

SPEC_TEMPLATE = """---
system: {system}
---
# {title}

## {heading}

{marker}

## Changelog

2026-10-03: created
"""

TASK_ONE = """### Task 1: First
- [ ] Done

**Spec:** [alpha] Intro, [beta] Rules

**Acceptance:** `echo CHANGE >> f1.txt` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

TASK_ONE_ALPHA = TASK_ONE.replace("[alpha] Intro, [beta] Rules", "[alpha] Intro")

TASK_TWO = """### Task 2: Second
- [ ] Done

**Acceptance:** `test -f ok.flag` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1
"""


def header_plan(paths, tasks=(TASK_ONE,)):
    block = "\n".join("- {}".format(p) for p in paths)
    return "# Plan\n\n**Goal:** g\n**Spec files:**\n{}\n\n{}".format(
        block, "\n".join(tasks)
    )


LEGACY_PLAN = "# Plan\n\n**Goal:** g\n\n" + TASK_TWO.replace("Task 2", "Task 1").replace(
    "**Depends on:** Task 1", "**Depends on:** nothing"
)


class SpecSetRunTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-run-specs-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.run_dir = os.path.join(self.d, "run")
        self.log = os.path.join(self.d, "fakelog")
        self.prompt_log = os.path.join(self.d, "fakeprompts")
        self.write("docs/forge/specs/alpha.md", SPEC_TEMPLATE.format(
            system="alpha", title="Alpha", heading="Intro", marker="ALPHA-BODY"))
        self.write("docs/forge/specs/beta.md", SPEC_TEMPLATE.format(
            system="beta", title="Beta", heading="Rules", marker="BETA-BODY"))
        self.write("f1.txt", "base\n")
        self.write("spec.md", MINIMAL_SPEC)
        self.write(".gitignore", "fake*\nresponses.json\nrun/\n.forge/\nok.flag\n")
        for args in (["init"], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "Test"]):
            self.git(*args)
        self.commit("base")

    def write(self, rel, text):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.d, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-m", msg)

    def run_cli(self, *args, responses=None):
        for p in (self.log, self.prompt_log):
            if os.path.exists(p):
                os.remove(p)
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.prompt_log
        resp = os.path.join(self.d, "responses.json")
        with open(resp, "w") as f:
            json.dump(responses or [{"exit": 0, "msg": _pass_msg()}], f)
        env["FORGE_FAKE_RESPONSES"] = resp
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args, "--run-dir", self.run_dir,
             "--codex-bin", self.fake],
            cwd=self.d, capture_output=True, text=True, env=env,
        )

    def run_json(self):
        with open(os.path.join(self.run_dir, "run.json")) as f:
            return json.load(f)

    def plan(self, text):
        return self.write("plan.md", text)

    # --- briefs, lint, checklist, packet ----------------------------------

    def test_two_spec_plan_runs_without_spec_flag_and_brief_has_both_sections(self):
        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        with open(os.path.join(self.run_dir, "task-1-attempt-1-brief.md")) as f:
            brief = f.read()
        self.assertIn("ALPHA-BODY", brief)
        self.assertIn("BETA-BODY", brief)

    def test_lint_and_final_checklist_receive_both_specs(self):
        # lint: a task naming a section missing from the SECOND spec fails
        # lint naming it; checklist: a good plan's final-review prompt carries
        # a checklist item per spec.
        bad = TASK_ONE.replace("[beta] Rules", "[beta] Nonexistent")
        plan = self.plan(header_plan([ALPHA, BETA], tasks=(bad,)))
        self.commit("bad plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("Nonexistent", res.stdout + res.stderr)
        self.assertFalse(os.path.exists(self.run_dir))

        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("good plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        final = [p for p in _log_prompts(self.prompt_log)
                 if "## Contract checklist" in p]
        self.assertTrue(final, "no final-review prompt carried a checklist")
        self.assertIn("spec:[alpha] Intro", final[0])
        self.assertIn("spec:[beta] Rules", final[0])

    # --- the --spec contract error ----------------------------------------

    def test_spec_flag_with_header_plan_exits_1_naming_both_and_creates_no_run_dir(self):
        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("plan")
        res = self.run_cli(plan, "--spec", "spec.md")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        out = res.stdout + res.stderr
        self.assertIn("**Spec files:**", out)
        self.assertIn("spec.md", out)
        self.assertFalse(os.path.exists(self.run_dir))
        self.assertEqual(_log_prompts(self.prompt_log), [])

    # --- run.json ---------------------------------------------------------

    def test_legacy_plan_with_spec_records_that_path_under_specs(self):
        plan = self.plan(LEGACY_PLAN)
        self.write("ok.flag", "")
        self.commit("plan")
        res = self.run_cli(plan, "--spec", "spec.md")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = self.run_json()
        self.assertEqual(data["specs"], [os.path.realpath(os.path.join(self.d, "spec.md"))])
        self.assertNotIn("spec", data)

    def test_plan_with_no_spec_runs_and_records_empty_specs(self):
        plan = self.plan(LEGACY_PLAN)
        self.write("ok.flag", "")
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.run_json()["specs"], [])

    def test_two_spec_run_lists_both_absolute_paths_and_no_spec_key(self):
        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = self.run_json()
        self.assertEqual([os.path.realpath(p) for p in data["specs"]], [
            os.path.realpath(os.path.join(self.d, ALPHA)),
            os.path.realpath(os.path.join(self.d, BETA))])
        self.assertTrue(all(os.path.isabs(p) for p in data["specs"]))
        self.assertNotIn("spec", data)

    # --- resume -----------------------------------------------------------

    def halted_two_spec_run(self, paths=(ALPHA, BETA)):
        """Task 1 passes, task 2 escalates (no ok.flag): a resumable run."""
        plan = self.plan(header_plan(list(paths), tasks=(TASK_ONE_ALPHA, TASK_TWO)))
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        return plan

    def resume_with_header(self, paths):
        plan = self.plan(header_plan(list(paths), tasks=(TASK_ONE_ALPHA, TASK_TWO)))
        self.commit("plan edit")
        return self.run_cli(plan)

    def test_resume_with_header_that_gained_a_spec_exits_1_naming_it(self):
        self.write("docs/forge/specs/gamma.md", SPEC_TEMPLATE.format(
            system="gamma", title="G", heading="Extra", marker="G"))
        self.halted_two_spec_run()
        res = self.resume_with_header([ALPHA, BETA, "docs/forge/specs/gamma.md"])
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("gamma.md", res.stderr)
        self.assertIn("added", res.stderr)

    def test_resume_with_header_that_lost_a_spec_exits_1_naming_it(self):
        self.halted_two_spec_run()
        res = self.resume_with_header([ALPHA])
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("beta.md", res.stderr)
        self.assertIn("removed", res.stderr)
        # the recorded set survives the contract error
        self.assertEqual(len(self.run_json()["specs"]), 2)

    def test_legacy_resume_with_different_spec_exits_1_naming_added_and_removed(self):
        plan = self.plan(LEGACY_PLAN)
        self.write("spec2.md", MINIMAL_SPEC)
        self.commit("plan")
        res = self.run_cli(plan, "--spec", "spec.md")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        res = self.run_cli(plan, "--spec", "spec2.md")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("spec2.md", res.stderr)
        self.assertIn("spec.md", res.stderr)
        self.assertIn("added", res.stderr)
        self.assertIn("removed", res.stderr)

    def test_resume_with_reordered_header_resumes(self):
        self.halted_two_spec_run()
        self.write("ok.flag", "")
        res = self.resume_with_header([BETA, ALPHA])
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_resume_through_a_different_spelling_of_the_same_file_resumes(self):
        plan = self.plan(LEGACY_PLAN)
        self.commit("plan")
        res = self.run_cli(plan, "--spec", "spec.md")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.write("ok.flag", "")
        # same file, spelled through the unresolved temp-dir path (macOS
        # /var is a symlink to /private/var)
        res = self.run_cli(plan, "--spec", os.path.join(self.d, "spec.md"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_first_invocation_contract_error_does_not_stick_the_run(self):
        # --resolve with no halt record is a contract error raised after the
        # run dir exists; the recorded set must be the plan's, so the next
        # correct invocation is not told every spec was "added".
        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("plan")
        res = self.run_cli(plan, "--resolve", "f1=repair")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertTrue(os.path.isdir(self.run_dir))
        self.assertEqual(len(self.run_json().get("specs", [])), 2)
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_run_json_with_non_object_top_level_reads_as_unreadable(self):
        os.makedirs(self.run_dir)
        with open(os.path.join(self.run_dir, "run.json"), "w") as f:
            f.write("[1, 2]")
        self.assertIsNone(forge_receipts._read_specs(self.run_dir))
        self.assertIsNone(forge_status.read_run_state(self.run_dir))
        self.assertEqual(forge_receipts.read_run_specs([1, 2]), [])

    def test_contract_errors_never_exit_2(self):
        plan = self.plan(header_plan([ALPHA, BETA]))
        self.commit("plan")
        self.assertEqual(self.run_cli(plan, "--spec", "spec.md").returncode, 1)
        shutil.rmtree(self.run_dir, ignore_errors=True)
        self.halted_two_spec_run()
        self.assertEqual(self.resume_with_header([ALPHA]).returncode, 1)

    # --- legacy run.json --------------------------------------------------

    def legacy_run_dir(self):
        os.makedirs(self.run_dir)
        spec = os.path.join(self.d, "spec.md")
        with open(os.path.join(self.run_dir, "run.json"), "w") as f:
            json.dump({"plan": os.path.join(self.d, "plan.md"), "spec": spec,
                       "status": "escalated", "base_commit": None, "tasks": []}, f)
        return spec

    def test_legacy_spec_string_reads_as_one_element_set(self):
        spec = self.legacy_run_dir()
        run = self.run_json()
        self.assertEqual(forge_receipts.read_run_specs(run), [spec])
        self.assertEqual(forge_receipts.read_run_specs({"specs": ["a", "b"]}), ["a", "b"])
        self.assertEqual(forge_receipts.read_run_specs({}), [])
        self.assertEqual(forge_status.read_run_state(self.run_dir)["specs"], [spec])

    def test_resume_of_legacy_run_json_compares_against_its_spec_string(self):
        plan = self.plan(LEGACY_PLAN)
        self.write("spec2.md", MINIMAL_SPEC)
        self.commit("plan")
        self.legacy_run_dir()
        res = self.run_cli(plan, "--spec", "spec2.md")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("spec2.md", res.stderr)
        self.assertIn("removed", res.stderr)

    # --- packets and briefs list spec paths (Task 6) -----------------------

    def two_spec_paths(self):
        return [os.path.join(self.d, ALPHA), os.path.join(self.d, BETA)]

    def named_sections(self, plan_text):
        import forge_git
        plan = self.plan(plan_text)
        return forge_git.named_spec_sections(plan, None)

    def test_final_packet_for_two_specs_lists_paths_and_labels_without_body(self):
        import forge_git
        paths = self.two_spec_paths()
        named = self.named_sections(header_plan([ALPHA, BETA]))
        out = forge_git._final_packet(
            paths, "HEAD", "+DIFF-LINE\n", self.run_dir_made(), named_sections=named)
        with open(out) as f:
            packet = f.read()
        for p in paths:
            self.assertIn(p, packet)
        self.assertIn("[alpha] Intro", packet)
        self.assertIn("[beta] Rules", packet)
        self.assertIn("+DIFF-LINE", packet)
        for body in ("ALPHA-BODY", "BETA-BODY", "2026-10-03: created"):
            self.assertNotIn(body, packet)

    def test_final_packet_for_one_spec_has_path_and_no_body(self):
        import forge_git
        out = forge_git._final_packet(
            [os.path.join(self.d, ALPHA)], "HEAD", "+DIFF-LINE\n", self.run_dir_made())
        with open(out) as f:
            packet = f.read()
        self.assertIn(ALPHA, packet)
        self.assertNotIn("ALPHA-BODY", packet)
        self.assertIn("+DIFF-LINE", packet)

    def test_final_packet_for_no_spec_lists_none_and_carries_the_diff(self):
        import forge_git
        out = forge_git._final_packet([], "HEAD", "+DIFF-LINE\n", self.run_dir_made())
        with open(out) as f:
            packet = f.read()
        self.assertIn("This plan has no spec.", packet)
        self.assertNotIn(".md", packet)
        self.assertIn("```diff\n+DIFF-LINE\n```\n", packet)

    def test_doc_sync_brief_for_two_specs_lists_paths_and_no_body(self):
        paths = self.two_spec_paths()
        named = self.named_sections(header_plan([ALPHA, BETA]))
        out = forge_run._doc_sync_brief(
            paths, "+DIFF-LINE\n", self.run_dir_made(), named_sections=named)
        with open(out) as f:
            brief = f.read()
        for p in paths:
            self.assertIn(p, brief)
        self.assertIn("[alpha] Intro", brief)
        self.assertIn("[beta] Rules", brief)
        self.assertIn("+DIFF-LINE", brief)
        for body in ("ALPHA-BODY", "BETA-BODY", "2026-10-03: created"):
            self.assertNotIn(body, brief)

    def test_doc_sync_brief_for_no_spec_carries_instruction_and_diff(self):
        out = forge_run._doc_sync_brief([], "+DIFF-LINE\n", self.run_dir_made())
        with open(out) as f:
            brief = f.read()
        self.assertIn("This plan has no spec.", brief)
        self.assertIn("Reconcile EXISTING documentation", brief)
        self.assertIn("+DIFF-LINE", brief)

    def run_dir_made(self):
        os.makedirs(self.run_dir, exist_ok=True)
        return self.run_dir

    # --- whole runs through the runner (Task 6) ----------------------------

    STANDARD_ONE = TASK_ONE.replace("trivial — test fixture, mechanical", "standard")

    def stage_prompts(self):
        prompts = _log_prompts(self.prompt_log)
        task = [p for p in prompts if "## Spec context" in p]
        final = [p for p in prompts if "## Review kind" in p and "## Specs" in p]
        sync = [p for p in prompts if "Reconcile EXISTING documentation" in p]
        return task, final, sync

    def listing_lines(self, prompt):
        """The spec-listing lines of a stage prompt: the path and section lines
        under its '## Specs' heading, as the listing renders them. Text that
        only the listing produces — the plan file inside the diff and the
        contract checklist repeat the relative paths and labels."""
        lines = prompt.split("## Specs\n", 1)[1].splitlines()
        return [ln for ln in lines
                if ln.startswith("- /") or ln.startswith("  - section: ")]

    def test_two_spec_run_gives_every_stage_both_specs(self):
        plan = self.plan(header_plan([ALPHA, BETA], tasks=(self.STANDARD_ONE,)))
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        task, final, sync = self.stage_prompts()
        self.assertEqual((len(task) >= 1, len(final) >= 1, len(sync)), (True, True, 1))
        # per-task packet: both named sections pasted, labeled by spec id
        for needle in ("ALPHA-BODY", "BETA-BODY", "[alpha] Intro", "[beta] Rules"):
            self.assertIn(needle, task[0])
        # final review and doc-sync: both paths and labels, no spec text
        for prompt in (final[0], sync[0]):
            listed = self.listing_lines(prompt)
            self.assertEqual(listed, [
                "- " + os.path.join(self.d, ALPHA), "  - section: [alpha] Intro",
                "- " + os.path.join(self.d, BETA), "  - section: [beta] Rules",
            ])
            # The final-review checklist quotes each named section's first
            # line (Contract checklist spec), so the spec-only line checked
            # here is one no named section reaches.
            self.assertNotIn("2026-10-03: created", prompt)
        self.assertNotIn("ALPHA-BODY", sync[0])
        self.assertNotIn("BETA-BODY", sync[0])

    def test_no_spec_run_with_a_diff_completes_final_review_and_doc_sync(self):
        no_spec = self.STANDARD_ONE.replace("**Spec:** [alpha] Intro, [beta] Rules\n\n", "")
        plan = self.plan("# Plan\n\n**Goal:** g\n\n" + no_spec)
        self.commit("plan")
        res = self.run_cli(plan)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        task, final, sync = self.stage_prompts()
        self.assertEqual(task, [])
        self.assertEqual((len(final), len(sync)), (1, 1))
        self.assertIn("This plan has no spec.", final[0])
        self.assertIn("CHANGE", final[0])
        self.assertIn("This plan has no spec.", sync[0])
        self.assertIn("CHANGE", sync[0])


if __name__ == "__main__":
    unittest.main()
