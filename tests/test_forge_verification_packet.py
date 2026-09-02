"""Delta-scoped verification packets (Phase 13 Task 6).

Three layers:
  - `forge_git.snapshot_tree`: a pre-repair tree snapshot for a later
    `git diff <ref>` — dirty-tree `git stash create` path, clean-tree
    (including a clean TRACKED tree with an untracked file present) `HEAD`
    fallback, `None` outside a repo, and never mutating the working tree or
    the index (`git status --porcelain` byte-identical before/after).
  - `rp.build_verification_packet`: outstanding findings + the repair delta +
    the reduced checklist, excluding the task block/full spec/whole-plan
    diff, with build_packet's fence-safety preserved.
  - `execute_task`-level wiring: a verification lap (review_attempts > 0)
    builds its packet from the pre-repair snapshot's delta rather than the
    task's whole accumulated diff, and the reduced checklist contains only
    ids named by the outstanding findings' contract_ref.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from _forge_support import *  # noqa: F401,F403

import forge_checklist
import forge_git

rp = forge_run.rp


# A standard task whose Acceptance carries one prose clause (checklist
# material) alongside the inline-code command that actually mutates the
# tracked file — `t1.a1` is the resulting checklist id, matched by the
# outstanding finding's contract_ref in these tests.
PLAN_STD_CHECKLIST = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** the output file must contain the marker; `echo NEEDFIX >> f1.txt`

**Tier:** standard

**Depends on:** nothing
"""

# Two prose Acceptance clauses -> a MULTI-item checklist (t1.a1, t1.a2). The
# rework/verification fixtures below reference only t1.a2 in the outstanding
# finding, so the reduced checklist is a strict subset of the full one —
# exercising the case a single-item checklist can't (reduced == full there).
PLAN_STD_CHECKLIST_MULTI = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** the output must contain marker one; the output must contain marker two; `echo NEEDFIX >> f1.txt`

**Tier:** standard

**Depends on:** nothing
"""


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


class SnapshotTreeTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-snapshot-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def _init_repo(self):
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
            f.write("base\n")
        _git(self.d, "init")
        _git(self.d, "config", "user.email", "t@example.com")
        _git(self.d, "config", "user.name", "Test")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-m", "base")

    def _status(self):
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.d,
            capture_output=True, text=True, check=True,
        )
        return proc.stdout

    def test_none_outside_a_git_repo(self):
        self.assertIsNone(forge_git.snapshot_tree(self.d))

    def test_dirty_tree_returns_usable_ref_and_leaves_status_unchanged(self):
        self._init_repo()
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("dirty change\n")
        with open(os.path.join(self.d, "untracked.txt"), "w") as f:
            f.write("new\n")
        before = self._status()
        ref = forge_git.snapshot_tree(self.d)
        after = self._status()
        self.assertIsNotNone(ref)
        self.assertTrue(ref)
        self.assertEqual(before, after)

    def test_clean_tree_fallback_returns_usable_ref_and_leaves_status_unchanged(self):
        self._init_repo()
        before = self._status()
        self.assertEqual(before, "")
        ref = forge_git.snapshot_tree(self.d)
        after = self._status()
        self.assertIsNotNone(ref)
        self.assertTrue(ref)
        self.assertEqual(before, after)

    def test_clean_tracked_tree_with_untracked_file_leaves_status_unchanged(self):
        # `git stash create` ignores untracked files (no -u), so a tree whose
        # only content is untracked also reports nothing to stash. The
        # fallback must resolve to HEAD directly — never `git add -A`, which
        # would stage the untracked file and silently change its status from
        # `??` to `A `, corrupting the single-commit discipline.
        self._init_repo()
        with open(os.path.join(self.d, "untracked.txt"), "w") as f:
            f.write("not tracked\n")
        before = self._status()
        self.assertEqual(before.strip(), "?? untracked.txt")
        ref = forge_git.snapshot_tree(self.d)
        after = self._status()
        self.assertIsNotNone(ref)
        self.assertTrue(ref)
        self.assertEqual(before, after)

    def test_ref_is_usable_with_git_diff_for_changes_made_after(self):
        self._init_repo()
        ref = forge_git.snapshot_tree(self.d)
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("AFTERSNAPSHOT\n")
        diff = subprocess.run(
            ["git", "diff", ref], cwd=self.d, capture_output=True, text=True,
            check=True,
        ).stdout
        self.assertIn("AFTERSNAPSHOT", diff)

    def test_dirty_tree_ref_diff_excludes_pre_snapshot_changes(self):
        # A dirty-tree snapshot must capture the tree AS OF the snapshot —
        # `git diff <ref>` against it shows nothing until something changes
        # again after the snapshot.
        self._init_repo()
        with open(os.path.join(self.d, "f1.txt"), "a") as f:
            f.write("already-dirty\n")
        ref = forge_git.snapshot_tree(self.d)
        diff = subprocess.run(
            ["git", "diff", ref], cwd=self.d, capture_output=True, text=True,
            check=True,
        ).stdout
        self.assertEqual(diff.strip(), "")


class BuildVerificationPacketTests(unittest.TestCase):
    def _findings(self, contract_ref="t1.a1"):
        return [{
            "id": "f1",
            "summary": "still broken",
            "location": {"file": "f1.txt", "lines": "2"},
            "provenance": "in-diff",
            "impact": "contract-breaking",
            "contract_ref": contract_ref,
            "convergence": None,
            "carried_from": None,
            "repair_task": None,
        }]

    def _checklist(self):
        return [
            forge_checklist.ChecklistItem(
                id="t1.a1", source="acceptance", text="the output must contain the marker"
            ),
            forge_checklist.ChecklistItem(
                id="g1", source="global", text="an unrelated global constraint"
            ),
        ]

    def test_contains_findings_delta_and_checklist(self):
        findings = self._findings()
        delta = "diff --git a/f1.txt b/f1.txt\n+REPAIRED\n"
        packet = rp.build_verification_packet(findings, delta, self._checklist()[:1])
        self.assertIn("still broken", packet)
        self.assertIn("REPAIRED", packet)
        self.assertIn("t1.a1", packet)
        self.assertIn("the output must contain the marker", packet)

    def test_excludes_task_block_full_spec_and_whole_plan_diff(self):
        findings = self._findings()
        delta = "diff --git a/f1.txt b/f1.txt\n+REPAIRED\n"
        packet = rp.build_verification_packet(findings, delta, self._checklist()[:1])
        self.assertNotIn("### Task 1:", packet)
        self.assertNotIn("SPECMARKERUNIQUE", packet)
        # A whole-plan diff would carry an unrelated file's hunk header —
        # only the delta's own file appears.
        self.assertNotIn("b/unrelated-plan-file.py", packet)

    def test_checklist_none_omits_section(self):
        packet = rp.build_verification_packet(
            self._findings(), "diff --git a/f1.txt b/f1.txt\n+x\n", None
        )
        self.assertNotIn("## Contract checklist", packet)

    def test_reduced_checklist_only_contains_referenced_ids(self):
        items = self._checklist()  # t1.a1 + g1
        findings = self._findings(contract_ref="t1.a1")
        reduced = forge_checklist.reduce_checklist(items, findings)
        packet = rp.build_verification_packet(findings, "diff --git a/f1.txt b/f1.txt\n", reduced)
        self.assertIn("t1.a1", packet)
        self.assertNotIn("g1 —", packet)
        self.assertNotIn("an unrelated global constraint", packet)

    def test_fence_safety_delta_containing_backtick_fence(self):
        findings = self._findings()
        delta = "diff --git a/README.md b/README.md\n+ ```\n+some code\n+ ```\n"
        packet = rp.build_verification_packet(findings, delta, self._checklist()[:1])
        # The delta's own fence must appear intact inside the packet, not
        # truncate the block early.
        self.assertIn("+some code", packet)
        self.assertIn("t1.a1", packet)  # checklist section still present after the delta

        # Prove the fence actually outran the delta's own backtick run,
        # rather than merely surviving by luck: the wrapping fence must be
        # longer than 3 backticks (the delta's own run), and it must appear
        # exactly twice — once opening, once closing — never split early by
        # one of the delta's embedded ``` lines (the exact bug build_packet's
        # dynamic fence length was written to prevent).
        fence_match = re.search(r"## Repair delta\n\n(`{3,})diff\n", packet)
        self.assertIsNotNone(fence_match, packet)
        fence = fence_match.group(1)
        self.assertGreater(len(fence), 3)
        self.assertEqual(packet.count(fence), 2)

    def test_fence_outruns_a_longer_backtick_run_in_delta(self):
        # A delta whose own fence is itself longer than 3 backticks (e.g. a
        # diffed markdown file nesting a fenced example inside a fence) must
        # still be outrun — the wrapping fence is sized past the LONGEST
        # backtick run in the delta, not a fixed length.
        findings = self._findings()
        delta = (
            "diff --git a/README.md b/README.md\n"
            "+`````\n"
            "+kept intact end to end\n"
            "+`````\n"
        )
        packet = rp.build_verification_packet(findings, delta, self._checklist()[:1])
        self.assertIn("+kept intact end to end", packet)
        fence_match = re.search(r"## Repair delta\n\n(`{3,})diff\n", packet)
        self.assertIsNotNone(fence_match, packet)
        fence = fence_match.group(1)
        self.assertGreater(len(fence), 5)
        self.assertEqual(packet.count(fence), 2)

    def test_smaller_than_equivalent_full_packet(self):
        findings = self._findings()
        delta = "diff --git a/f1.txt b/f1.txt\n+REPAIRED\n"
        checklist = self._checklist()[:1]
        verification_packet = rp.build_verification_packet(findings, delta, checklist)

        full_task_block = (
            "### Task 1: Standard task\n- [ ] Done\n\n"
            "**Acceptance:** the output file must contain the marker; "
            "`echo NEEDFIX >> f1.txt`\n\n**Tier:** standard\n\n"
            "**Depends on:** nothing\n"
        )
        full_diff = (
            "diff --git a/f1.txt b/f1.txt\n+NEEDFIX\n+REPAIRED\n"
            "diff --git a/unrelated.py b/unrelated.py\n+import os\n"
        )
        full_packet = rp.build_packet(
            full_task_block, "abc123", full_diff, checklist=checklist,
            prior_findings=findings,
        )
        self.assertLess(len(verification_packet), len(full_packet))


class ExecuteTaskVerificationPacketTests(unittest.TestCase):
    """Integration: execute_task builds a delta-scoped packet on the
    verification lap (review_attempts > 0), scoped to the pre-repair
    snapshot's delta and the reduced checklist."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="forge-vpacket-exec-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.fake = write_fake_codex(self.d)
        self.spec = os.path.join(self.d, "spec.md")
        with open(self.spec, "w") as f:
            f.write("# Spec\n\nSPECMARKERUNIQUE\n")
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

    def _git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.d, check=True, capture_output=True, text=True
        )

    def _init_repo(self):
        with open(os.path.join(self.d, ".gitignore"), "w") as f:
            f.write("fakelog*\nresponses.json\nrun/\n.forge/\n")
        with open(os.path.join(self.d, "f1.txt"), "w") as f:
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

    def _set_responses(self, responses):
        path = os.path.join(self.d, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        os.environ["FORGE_FAKE_RESPONSES"] = path
        os.environ["FORGE_FAKE_LOG"] = self.log

    def _task1(self, plan_path):
        tasks = forge_run.parse_plan_tasks(plan_path)
        return forge_run.order_tasks(tasks)[0]

    def test_verification_lap_packet_is_delta_scoped_not_whole_task_diff(self):
        plan = self._plan(PLAN_STD_CHECKLIST)
        self._init_repo()
        f1 = os.path.join(self.d, "f1.txt")
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream_local("th-w1")},
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "still missing the marker", contract_ref="t1.a1",
            ), "stdout": _worker_event_stream_local("th-r1")},   # a1 review (discovery)
            {  # a2 worker (resume): appends the repair, distinct from the
               # acceptance command's own NEEDFIX line already in the diff
               "exit": 0, "msg": "",
               "append_file": f1, "append_text": "REPAIRTOKEN\n",
            },
            {"exit": 0, "msg": _pass_msg()},                     # a2 review (verification)
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")

        packet_path = os.path.join(self.run_dir, "task-1-review.md")
        with open(packet_path) as f:
            packet = f.read()

        # Delta-scoped: the repair's own change (REPAIRTOKEN) is in the
        # packet's diff as an addition. Acceptance re-runs every attempt, so
        # attempt 1's NEEDFIX line is already present at snapshot time (a
        # context line, not an addition) and attempt 2's own NEEDFIX append
        # shows as exactly one new addition — never attempt 1's, which the
        # whole-task diff would have included as a second "+NEEDFIX" line.
        self.assertIn("+REPAIRTOKEN", packet)
        self.assertEqual(packet.count("+NEEDFIX"), 1)
        # Not the task block, not the full spec.
        self.assertNotIn("### Task 1:", packet)
        self.assertNotIn("SPECMARKERUNIQUE", packet)
        # The reduced checklist (referenced by contract_ref="t1.a1") is present.
        self.assertIn("t1.a1", packet)
        # Outstanding finding text carried into the packet.
        self.assertIn("still missing the marker", packet)

    def test_discovery_packet_still_full_task_and_diff(self):
        # Sanity: attempt 1 (discovery) is unaffected — full task block +
        # whole diff, exactly as before this task.
        plan = self._plan(PLAN_STD_CHECKLIST)
        self._init_repo()
        self._set_responses([
            {"exit": 0, "msg": ""},
            {"exit": 0, "msg": _pass_msg()},
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")
        packet_path = os.path.join(self.run_dir, "task-1-review.md")
        with open(packet_path) as f:
            packet = f.read()
        self.assertIn("### Task 1:", packet)

    def test_verification_lap_coverage_validated_against_reduced_checklist(self):
        # Regression guard: a MULTI-item checklist (t1.a1, t1.a2) where the
        # outstanding finding references only t1.a2. Validating the
        # verification lap's coverage against the FULL checklist (t1.a1 +
        # t1.a2) while the packet shown to the reviewer only names t1.a2
        # forces a spurious "missing coverage for t1.a1" retry-then-error on
        # every real multi-item-checklist plan. The reduced checklist must be
        # what's validated, so this must simply pass.
        plan = self._plan(PLAN_STD_CHECKLIST_MULTI)
        self._init_repo()
        f1 = os.path.join(self.d, "f1.txt")
        self._set_responses([
            {"exit": 0, "msg": "", "stdout": _worker_event_stream_local("th-w1")},
            {"exit": 0, "msg": _fix_findings_msg(
                "f1.txt", "2", "marker two missing", contract_ref="t1.a2",
            ), "stdout": _worker_event_stream_local("th-r1")},   # a1 review (discovery)
            {
                "exit": 0, "msg": "",
                "append_file": f1, "append_text": "REPAIRTOKEN\n",
            },
            {"exit": 0, "msg": _pass_msg()},                     # a2 review (verification)
        ])
        task = self._task1(plan)
        threads = {}
        outcome = forge_run.execute_task(
            task, plan, self.spec, self.run_dir, self.fake, self.d, threads,
        )
        self.assertEqual(outcome.status, "passed")

        packet_path = os.path.join(self.run_dir, "task-1-review.md")
        with open(packet_path) as f:
            packet = f.read()
        self.assertIn("t1.a2", packet)
        self.assertNotIn("t1.a1", packet)

        with open(os.path.join(self.run_dir, "task-1-attempt-2.json")) as f:
            receipt = json.load(f)
        self.assertFalse(receipt["coverage_retry"])


def _worker_event_stream_local(thread_id, text="ok"):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"item_type": "agent_message", "text": text}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


if __name__ == "__main__":
    unittest.main()
