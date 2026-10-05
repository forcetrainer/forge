"""Shared fixtures and helpers for the forge-run test suite.

Loads scripts/forge-run.py once (its filename is hyphenated, so importlib), and
holds the fake ``codex`` binary, the plan/spec fixtures, and the verdict/argv
helpers every split test file uses. Named with a leading underscore so pytest
does not collect it as a test module. Import with ``from _forge_support import *``.
"""
import importlib.util
import contextlib
import io
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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "forge-run.py"

_spec = importlib.util.spec_from_file_location("forge_run", SCRIPT_PATH)
forge_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forge_run)


# A fake `codex` binary: appends its argv (JSON) to FORGE_FAKE_LOG, reads a
# per-call response from FORGE_FAKE_RESPONSES ([{"exit":int,"msg":str}, ...],
# index = prior log line count, clamped to last), writes msg to the
# --output-last-message path, optionally simulates a real-worker file edit via
# ``append_file``/``append_text`` (an absolute path; a real `codex exec` worker
# edits repo files directly, which this fake cannot do — used to exercise commit
# discipline around a fix dispatch), and exits with the scripted code.
#
# Coverage stubbing (Phase 13 Task 4): REVIEW_VERDICT_INSTRUCTION now requires
# a `coverage` array on every reviewer verdict. A canned `_pass_msg()`/
# `_findings_msg()`-style fixture response predates that and carries none —
# obeying it verbatim would make every fixture fail the runner's own coverage
# validation, asserting runner behavior against a reviewer that couldn't
# exist. So: when the dispatched prompt (the final argv element) contains a
# '## Contract checklist' section AND the canned message parses as a JSON
# verdict object with no `coverage` key already, the fake parses the
# checklist ids out of the prompt (the same '- <id> — <text>' lines
# build_checklist_section renders) and injects one `coverage` entry per id —
# "status": "satisfied", non-empty "evidence" — before writing the message.
# A canned message that already sets its own `coverage` (a test deliberately
# exercising the coverage/retry path) is never touched. No checklist section
# in the prompt (a worker dispatch, or a reviewer packet with no checklist —
# the empty-checklist skip case), or a message that isn't a JSON verdict
# object: passed through untouched.
FAKE_CODEX_SRC = '''#!/usr/bin/env python3
import json, os, subprocess, sys, time
argv = sys.argv[1:]
log = os.environ.get("FORGE_FAKE_LOG")
idx = 0
if log:
    if os.path.exists(log):
        with open(log) as f:
            idx = sum(1 for _ in f)
    with open(log, "a") as f:
        f.write(json.dumps(argv) + "\\n")
# The real `codex exec` reads the prompt from stdin when no PROMPT argument is
# given (`codex exec --help`: "If not provided as an argument (or if `-` is
# used), instructions are read from stdin"). The runner relies on that to get
# past ARG_MAX, so the fake must read it the same way. FORGE_FAKE_PROMPT_LOG,
# when set, records what actually arrived so a test can assert the child got
# the whole thing rather than merely that the spawn succeeded.
prompt = "" if sys.stdin.isatty() else sys.stdin.read()
plog = os.environ.get("FORGE_FAKE_PROMPT_LOG")
if plog:
    with open(plog, "a") as f:
        f.write(json.dumps(prompt) + "\\n")
exit_code = 0
msg = ""
sleep_s = 0
out = ""
err = ""
append_file = None
append_text = ""
file_ops = []
ops_first = False
resp = os.environ.get("FORGE_FAKE_RESPONSES")
if resp and os.path.exists(resp):
    with open(resp) as f:
        responses = json.load(f)
    if responses:
        r = responses[idx] if idx < len(responses) else responses[-1]
        exit_code = r.get("exit", 0)
        msg = r.get("msg", "")
        sleep_s = r.get("sleep", 0)
        out = r.get("stdout", "")
        err = r.get("stderr", "")
        append_file = r.get("append_file")
        append_text = r.get("append_text", "")
        file_ops = r.get("file_ops", [])
        ops_first = r.get("ops_first", False)
if msg:
    if "## Contract checklist" in prompt:
        try:
            obj = json.loads(msg)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "verdict" in obj and "coverage" not in obj:
            ids = []
            in_section = False
            for line in prompt.splitlines():
                if line.strip() == "## Contract checklist":
                    in_section = True
                    continue
                if in_section:
                    if line.startswith("## "):
                        break
                    if line.startswith("- "):
                        cid = line[2:].split(" \\u2014 ", 1)[0].strip()
                        if cid:
                            ids.append(cid)
            if ids:
                obj["coverage"] = [
                    {"id": cid, "status": "satisfied",
                     "evidence": "stub: " + cid}
                    for cid in ids
                ]
                msg = json.dumps(obj)
def run_ops():
    # {"op": "write"|"append"|"delete", "path": abs path, "text": str} or
    # {"op": "git", "args": [...]} (run in the child's cwd, the repository).
    for op in file_ops:
        if op["op"] == "delete":
            os.remove(op["path"])
        elif op["op"] == "git":
            r = subprocess.run(["git"] + op["args"], capture_output=True, text=True)
            if r.returncode != 0:
                sys.stderr.write("fake git op failed: %s\\n" % r.stderr)
                sys.exit(97)
        else:
            with open(op["path"], "w" if op["op"] == "write" else "a") as f:
                f.write(op.get("text", ""))
if ops_first:
    run_ops()
if sleep_s:
    time.sleep(sleep_s)
if out:
    sys.stdout.write(out)
    sys.stdout.flush()
if err:
    sys.stderr.write(err)
    sys.stderr.flush()
if append_file:
    with open(append_file, "a") as f:
        f.write(append_text)
if not ops_first:
    run_ops()
if "--output-last-message" in argv:
    p = argv[argv.index("--output-last-message") + 1]
    with open(p, "w") as f:
        f.write(msg)
sys.exit(exit_code)
'''


def write_fake_codex(dirpath):
    path = os.path.join(dirpath, "fake_codex.py")
    with open(path, "w") as f:
        f.write(FAKE_CODEX_SRC)
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


PLAN_PASS = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Files:**
- Modify: `foo.txt`

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

# Task 2 listed before Task 1 in the file; Task 2 depends on Task 1. A correct
# runner dispatches Task 1 first regardless of file order.
PLAN_DEPS = """# Fixture Plan

**Goal:** Do the thing.

### Task 2: Second task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1

### Task 1: First task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_ACC_FAIL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `false` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_BAD_HEADING = """# Fixture Plan

**Goal:** Do the thing.

## Task 1: Wrong level
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_DUP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 1: Second
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

MINIMAL_SPEC = "# Spec\n\nNothing referenced.\n"

# A single standard-tier task: acceptance passes, so a reviewer is dispatched.
PLAN_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** standard

**Depends on:** nothing
"""

# Standard task 1 (reviewed) followed by a trivial task 2 that depends on it —
# used to prove a halt at task 1 never dispatches task 2.
PLAN_STD_THEN_TRIVIAL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** standard

**Depends on:** nothing

### Task 2: Trivial follow-up
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1
"""

# Two trivial tasks, task 2 depends on task 1 — used by the resume test where the
# escalation is driven by a worker crash (no reviewer, so no git repo required).
PLAN_TWO_TRIVIAL = PLAN_DEPS

# Two standard (reviewed) tasks, each appending a distinct marker to its OWN
# tracked file via its acceptance command. Proves per-task review packets are
# isolated to that task's own diff: task 1 commits when it passes, so task 2's
# base is that commit and its packet carries only task 2's change.
PLAN_TWO_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First standard
- [ ] Done

**Acceptance:** `echo TASK1MARK >> f1.txt` passes

**Tier:** standard

**Depends on:** nothing

### Task 2: Second standard
- [ ] Done

**Acceptance:** `echo TASK2MARK >> f2.txt` passes

**Tier:** standard

**Depends on:** Task 1
"""


# A standard (reviewed) task whose acceptance appends to an ALREADY-TRACKED file,
# so `git diff <base>` is non-empty and a finding on the appended line is
# runner-verified in-diff (disposition "fix"). Callers commit f1.txt in repo init.
PLAN_STD_TRACKED = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `echo NEEDFIX >> f1.txt` passes

**Tier:** standard

**Depends on:** nothing
"""

# PLAN_STD_TRACKED's reviewed task 1 followed by a trivial task 2 (depends on it) —
# proves a per-task halt at task 1 never dispatches task 2.
PLAN_STD_TRACKED_THEN_TRIVIAL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `echo NEEDFIX >> f1.txt` passes

**Tier:** standard

**Depends on:** nothing

### Task 2: Trivial follow-up
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1
"""


def _pass_msg():
    return '{"verdict": "pass"}'


def _findings_msg(*items):
    """Build a ``findings`` verdict in the per-finding schema (Phase 7 Reviewer
    verdict contract) from summary strings. Each item becomes one finding object
    with the string as its ``summary``; findings are ``improvement`` with no
    location so they parse without the contract-breaking location requirement —
    enough for the loop's ``kind == "findings"`` rework/escalation behavior, which
    is all these fixtures assert."""
    findings = [
        {
            "id": "f{}".format(i),
            "summary": item,
            "location": None,
            "provenance": "in-diff",
            "impact": "improvement",
            "contract_ref": None,
            "convergence": None,
            "carried_from": None,
            "repair_task": None,
        }
        for i, item in enumerate(items, 1)
    ]
    return json.dumps({"verdict": "findings", "findings": findings})


def _fix_findings_msg(file, lines, summary, id="f1",
                      contract_ref="Acceptance: `true`", carried_from=None,
                      repair_task=None):
    """Build a `findings` verdict (Phase 7 schema) with one contract-breaking
    finding located at ``file:lines``. When the reviewed diff touches those lines
    the runner verifies it in-diff -> disposition ``fix`` (rework); outside the
    diff it is pre-existing -> ``halt`` (scope decision). Drives the disposition-
    aware convergence loop through the fake reviewer. ``carried_from`` marks a
    re-issued (``carried``) finding for stuck/regression matching. ``repair_task``
    (a plan-task-shaped dict) is the drafted repair payload a halt-disposition
    finding carries — pass it when the fixture is meant to land outside the diff."""
    finding = {
        "id": id,
        "summary": summary,
        "location": {"file": file, "lines": lines},
        "provenance": "in-diff",
        "impact": "contract-breaking",
        "contract_ref": contract_ref,
        "convergence": "carried" if carried_from else None,
        "carried_from": carried_from,
        "repair_task": repair_task,
    }
    return json.dumps({"verdict": "findings", "findings": [finding]})


# --- Phase 5: commit discipline fixtures -----------------------------------

# One trivial task whose acceptance command mutates a tracked file, so a passed
# task has something to commit.
PLAN_COMMIT_ONE = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

# Two trivial tasks, each mutating its own tracked file; task 2 depends on task 1.
PLAN_COMMIT_TWO = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TWOMARK >> f2.txt` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1
"""

# One standard (reviewed) task mutating a tracked file — used to force an
# escalation (two findings verdicts) and assert no commit is created.
PLAN_COMMIT_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `echo STDMARK >> f1.txt` passes

**Tier:** standard

**Depends on:** nothing
"""

# Task 1 trivial (commits on run 1), task 2 standard (reviewed) so it can be
# forced to escalate via findings — used to prove the final-review base survives
# a resume as the persisted base_commit.
PLAN_COMMIT_ONE_THEN_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TWOMARK >> f2.txt` passes

**Tier:** standard

**Depends on:** Task 1
"""

# A passed task that changes no tracked file (acceptance is a no-op) — commit
# must be skipped rather than creating an empty commit.
PLAN_COMMIT_NOOP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: No-op task
- [ ] Done

**Acceptance:** `true` passes

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""


def scratch_repo(testcase, prefix="forge-scratch-repo-"):
    """A throwaway git repository for tests that call a reviewer dispatch
    directly: the unchanged-repository check fingerprints ``cwd``, so a
    dispatch needs a repository of its own that no harness file lands in."""
    d = tempfile.mkdtemp(prefix=prefix)
    testcase.addCleanup(shutil.rmtree, d, ignore_errors=True)
    with open(os.path.join(d, "f1.txt"), "w") as f:
        f.write("base\n")
    for args in (["init", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "Test"], ["add", "-A"],
                 ["commit", "-m", "base"]):
        subprocess.run(["git", *args], cwd=d, check=True, capture_output=True,
                       text=True)
    return d


def thread_stream(thread_id):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


# A contract-breaking finding whose location has no line range: a location
# defect, so the verdict is invalid and drives the one validation retry (which
# resumes the reviewer's recorded thread).
INVALID_LOCATION_MSG = json.dumps({
    "verdict": "findings",
    "findings": [{
        "id": "f1", "summary": "BADLOC", "location": {"file": "f1.txt"},
        "provenance": "in-diff", "impact": "contract-breaking",
        "contract_ref": "Acceptance: `true`", "convergence": None,
        "carried_from": None, "repair_task": None,
    }],
})


class ReviewerWroteCase(unittest.TestCase):
    """Shared harness for the reviewer-write-discipline runner tests: a git
    repository at ``self.repo`` holding one tracked file, with the fake codex,
    plan, spec, run dir and logs OUTSIDE it so only a reviewer's own write can
    change the fingerprint. ``run_cli`` drives the shipped CLI in a
    subprocess; ``responses`` is the fake codex script."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-rw-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.fake = write_fake_codex(self.root)
        self.run_dir = os.path.join(self.root, "run")
        self.log = os.path.join(self.root, "fakelog")
        self.spec = os.path.join(self.root, "spec.md")
        with open(self.spec, "w") as f:
            f.write(MINIMAL_SPEC)
        self.plan = os.path.join(self.root, "plan.md")
        with open(self.plan, "w") as f:
            f.write(PLAN_STD_TRACKED)
        with open(os.path.join(self.repo, "f1.txt"), "w") as f:
            f.write("base\n")
        with open(os.path.join(self.repo, ".gitignore"), "w") as f:
            f.write(".forge/\n")
        self.git("init", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-m", "base")
        self.base_sha = self.git("rev-parse", "HEAD").strip()
        self.stray = os.path.join(self.repo, "stray.txt")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True,
            text=True,
        ).stdout

    def porcelain(self):
        return self.git("status", "--porcelain").strip()

    def write_responses(self, responses):
        for p in (self.log, self.log + ".prompts"):
            if os.path.exists(p):
                os.remove(p)
        path = os.path.join(self.root, "responses.json")
        with open(path, "w") as f:
            json.dump(responses, f)
        return path

    def run_cli(self, responses, extra_args=()):
        env = os.environ.copy()
        env["FORGE_FAKE_LOG"] = self.log
        env["FORGE_FAKE_PROMPT_LOG"] = self.log + ".prompts"
        env["FORGE_FAKE_RESPONSES"] = self.write_responses(responses)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), self.plan, "--spec", self.spec,
             "--run-dir", self.run_dir, "--codex-bin", self.fake, *extra_args],
            cwd=self.repo, capture_output=True, text=True, env=env,
        )

    def dispatches(self, marker):
        """argvs whose --output-last-message path contains ``marker``."""
        out = []
        for a in _log_argvs(self.log):
            if "--output-last-message" in a:
                if marker in a[a.index("--output-last-message") + 1]:
                    out.append(a)
        return out

    def run_json(self):
        with open(os.path.join(self.run_dir, "run.json")) as f:
            return json.load(f)

    def halt_record(self):
        return self.run_json().get("halt")

    def commit_count(self):
        return int(self.git("rev-list", "--count", "HEAD").strip())

    def in_commit(self, sha, path):
        """True when ``path`` exists in commit ``sha``'s tree."""
        return subprocess.run(
            ["git", "cat-file", "-e", "{}:{}".format(sha, path)], cwd=self.repo,
            capture_output=True,
        ).returncode == 0

    def main_failing_fingerprint(self, responses, fail_on):
        """Run ``forge_run.main`` in-process with ``repo_fingerprint`` raising
        ``FingerprintError`` on its ``fail_on``-th call; returns (rc, stderr)."""
        from unittest import mock
        import forge_git
        real = forge_git.repo_fingerprint
        calls = {"n": 0}

        def flaky(cwd):
            calls["n"] += 1
            if calls["n"] == fail_on:
                raise forge_git.FingerprintError(
                    "fingerprint (git rev-parse HEAD) failed in {}: boom".format(cwd))
            return real(cwd)

        env = {
            "FORGE_FAKE_LOG": self.log,
            "FORGE_FAKE_PROMPT_LOG": self.log + ".prompts",
            "FORGE_FAKE_RESPONSES": self.write_responses(responses),
        }
        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(forge_git, "repo_fingerprint", flaky), \
                contextlib.redirect_stderr(err):
            rc = forge_run.main([
                self.plan, "--spec", self.spec, "--run-dir", self.run_dir,
                "--codex-bin", self.fake,
            ])
        return rc, err.getvalue()

    def stray_op(self, text="stray\n"):
        return {"op": "write", "path": self.stray, "text": text}


class UnverifiedCase(ReviewerWroteCase):
    """Harness for the final review's unverified close-out halt. The plan is
    ``PLAN_STD_TRACKED`` (its worker's acceptance appends NEEDFIX to f1.txt),
    so a passed task one leaves line 2 of f1.txt in the whole-plan diff, and
    the final checklist is the single integration item ``t1``. ``first_call``
    is the call sequence of a first invocation up to the final reviewer."""

    WORKER = {"exit": 0, "msg": ""}
    TASK_PASS = {"exit": 0, "msg": '{"verdict": "pass"}'}
    DOC_SYNC_CLEAN = {"exit": 0, "msg": '{"doc_sync": "clean"}'}

    @staticmethod
    def seed_finding(fid, reason):
        return {
            "id": fid, "summary": reason,
            "location": {"file": "f1.txt", "lines": "2"},
            "provenance": "in-diff", "impact": "unverifiable",
            "contract_ref": None, "convergence": None,
            "carried_from": None, "repair_task": None,
        }

    def seed_msg(self, *pairs):
        return json.dumps({
            "verdict": "findings",
            "findings": [self.seed_finding(i, r) for i, r in pairs],
        })

    @staticmethod
    def coverage_msg(reason, cid="t1"):
        return json.dumps({
            "verdict": "pass",
            "coverage": [{"id": cid, "status": "unverifiable",
                          "evidence": reason}],
        })

    def final(self, msg):
        return {"exit": 0, "msg": msg}

    def first_call(self, final_msg):
        return [self.WORKER, self.TASK_PASS, self.final(final_msg)]

    def unverified(self):
        return self.run_json().get("unverified")

    def entry(self, eid):
        return next(e for e in self.unverified() if e["id"] == eid)

    def run_main(self, responses, extra_args=()):
        """``forge_run.main`` in-process (so a helper can be spied on);
        returns (rc, stdout, stderr)."""
        from unittest import mock
        env = {
            "FORGE_FAKE_LOG": self.log,
            "FORGE_FAKE_PROMPT_LOG": self.log + ".prompts",
            "FORGE_FAKE_RESPONSES": self.write_responses(responses),
        }
        old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old_cwd)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = forge_run.main([
                self.plan, "--spec", self.spec, "--run-dir", self.run_dir,
                "--codex-bin", self.fake, *extra_args,
            ])
        return rc, out.getvalue(), err.getvalue()


def _log_prompts(log_path):
    """Prompts received by the fake codex, one per dispatch, in call order.
    Set FORGE_FAKE_PROMPT_LOG to that path first. The prompt no longer rides in
    argv (ARG_MAX), so this is how a test inspects what a child was sent."""
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _log_argvs(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _find_dispatch(argvs, marker):
    """Return the first argv (list) whose --output-last-message path contains
    ``marker`` — distinguishes worker vs reviewer vs final-review calls."""
    for a in argvs:
        if "--output-last-message" in a:
            path = a[a.index("--output-last-message") + 1]
            if marker in path:
                return a
    return None


__all__ = [
    "REPO_ROOT",
    "SCRIPT_PATH",
    "forge_run",
    "FAKE_CODEX_SRC",
    "write_fake_codex",
    "PLAN_PASS",
    "PLAN_DEPS",
    "PLAN_ACC_FAIL",
    "PLAN_BAD_HEADING",
    "PLAN_DUP",
    "MINIMAL_SPEC",
    "PLAN_STD",
    "PLAN_STD_THEN_TRIVIAL",
    "PLAN_STD_TRACKED",
    "PLAN_STD_TRACKED_THEN_TRIVIAL",
    "PLAN_TWO_TRIVIAL",
    "PLAN_TWO_STD",
    "PLAN_COMMIT_ONE",
    "PLAN_COMMIT_TWO",
    "PLAN_COMMIT_STD",
    "PLAN_COMMIT_ONE_THEN_STD",
    "PLAN_COMMIT_NOOP",
    "_pass_msg",
    "_findings_msg",
    "_fix_findings_msg",
    "_log_argvs",
    "_log_prompts",
    "_find_dispatch",
    "scratch_repo",
    "thread_stream",
    "INVALID_LOCATION_MSG",
    "ReviewerWroteCase",
    "UnverifiedCase",
]
