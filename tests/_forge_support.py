"""Shared fixtures and helpers for the forge-run test suite.

Loads scripts/forge-run.py once (its filename is hyphenated, so importlib), and
holds the fake ``codex`` binary, the plan/spec fixtures, and the verdict/argv
helpers every split test file uses. Named with a leading underscore so pytest
does not collect it as a test module. Import with ``from _forge_support import *``.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import types

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
import json, os, sys, time
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

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

# Task 2 listed before Task 1 in the file; Task 2 depends on Task 1. A correct
# runner dispatches Task 1 first regardless of file order.
PLAN_DEPS = """# Fixture Plan

**Goal:** Do the thing.

### Task 2: Second task
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1

### Task 1: First task
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_ACC_FAIL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `false`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_BAD_HEADING = """# Fixture Plan

**Goal:** Do the thing.

## Task 1: Wrong level
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

PLAN_DUP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 1: Second
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

MINIMAL_SPEC = "# Spec\n\nNothing referenced.\n"

# A single standard-tier task: acceptance passes, so a reviewer is dispatched.
PLAN_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true`

**Tier:** standard

**Depends on:** nothing
"""

# Standard task 1 (reviewed) followed by a trivial task 2 that depends on it —
# used to prove a halt at task 1 never dispatches task 2.
PLAN_STD_THEN_TRIVIAL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `true`

**Tier:** standard

**Depends on:** nothing

### Task 2: Trivial follow-up
- [ ] Done

**Acceptance:** `true`

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

**Acceptance:** `echo TASK1MARK >> f1.txt`

**Tier:** standard

**Depends on:** nothing

### Task 2: Second standard
- [ ] Done

**Acceptance:** `echo TASK2MARK >> f2.txt`

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

**Acceptance:** `echo NEEDFIX >> f1.txt`

**Tier:** standard

**Depends on:** nothing
"""

# PLAN_STD_TRACKED's reviewed task 1 followed by a trivial task 2 (depends on it) —
# proves a per-task halt at task 1 never dispatches task 2.
PLAN_STD_TRACKED_THEN_TRIVIAL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `echo NEEDFIX >> f1.txt`

**Tier:** standard

**Depends on:** nothing

### Task 2: Trivial follow-up
- [ ] Done

**Acceptance:** `true`

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

**Acceptance:** `echo ONEMARK >> f1.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""

# Two trivial tasks, each mutating its own tracked file; task 2 depends on task 1.
PLAN_COMMIT_TWO = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TWOMARK >> f2.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** Task 1
"""

# One standard (reviewed) task mutating a tracked file — used to force an
# escalation (two findings verdicts) and assert no commit is created.
PLAN_COMMIT_STD = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: Standard task
- [ ] Done

**Acceptance:** `echo STDMARK >> f1.txt`

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

**Acceptance:** `echo ONEMARK >> f1.txt`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TWOMARK >> f2.txt`

**Tier:** standard

**Depends on:** Task 1
"""

# A passed task that changes no tracked file (acceptance is a no-op) — commit
# must be skipped rather than creating an empty commit.
PLAN_COMMIT_NOOP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: No-op task
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial — test fixture, mechanical

**Depends on:** nothing
"""


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
]
