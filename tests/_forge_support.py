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
# --output-last-message path, and exits with the scripted code.
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
exit_code = 0
msg = ""
sleep_s = 0
out = ""
err = ""
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
if sleep_s:
    time.sleep(sleep_s)
if out:
    sys.stdout.write(out)
    sys.stdout.flush()
if err:
    sys.stderr.write(err)
    sys.stderr.flush()
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

**Tier:** trivial

**Depends on:** nothing
"""

# Task 2 listed before Task 1 in the file; Task 2 depends on Task 1. A correct
# runner dispatches Task 1 first regardless of file order.
PLAN_DEPS = """# Fixture Plan

**Goal:** Do the thing.

### Task 2: Second task
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial

**Depends on:** Task 1

### Task 1: First task
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial

**Depends on:** nothing
"""

PLAN_ACC_FAIL = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `false`

**Tier:** trivial

**Depends on:** nothing
"""

PLAN_BAD_HEADING = """# Fixture Plan

**Goal:** Do the thing.

## Task 1: Wrong level
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial

**Depends on:** nothing
"""

PLAN_DUP = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial

**Depends on:** nothing

### Task 1: Second
- [ ] Done

**Acceptance:** `true`

**Tier:** trivial

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

**Tier:** trivial

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


def _pass_msg():
    return '{"verdict": "pass"}'


def _findings_msg(*items):
    return json.dumps({"verdict": "findings", "findings": list(items)})


# --- Phase 5: commit discipline fixtures -----------------------------------

# One trivial task whose acceptance command mutates a tracked file, so a passed
# task has something to commit.
PLAN_COMMIT_ONE = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt`

**Tier:** trivial

**Depends on:** nothing
"""

# Two trivial tasks, each mutating its own tracked file; task 2 depends on task 1.
PLAN_COMMIT_TWO = """# Fixture Plan

**Goal:** Do the thing.

### Task 1: First task
- [ ] Done

**Acceptance:** `echo ONEMARK >> f1.txt`

**Tier:** trivial

**Depends on:** nothing

### Task 2: Second task
- [ ] Done

**Acceptance:** `echo TWOMARK >> f2.txt`

**Tier:** trivial

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

**Tier:** trivial

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

**Tier:** trivial

**Depends on:** nothing
"""


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
    "PLAN_TWO_TRIVIAL",
    "PLAN_TWO_STD",
    "PLAN_COMMIT_ONE",
    "PLAN_COMMIT_TWO",
    "PLAN_COMMIT_STD",
    "PLAN_COMMIT_ONE_THEN_STD",
    "PLAN_COMMIT_NOOP",
    "_pass_msg",
    "_findings_msg",
    "_log_argvs",
    "_find_dispatch",
]
