# Runner Worker Isolation Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Disable subagents and memories in every runner `codex exec` dispatch and make reviewers read-only.
**Architecture:** Two argument-group constants in `forge_common` are spliced into every dispatch argv in `forge-run.py`. The Codex orchestrator skill text and the Codex docs describe the flags, and a hand-run script verifies them against a real Codex.
**Tech stack:** Python 3 stdlib, pytest with the fake `codex` in `tests/_forge_support.py`, POSIX sh for the live check.
**Global Constraints:**
- Scripts under `scripts/` import the Python 3 standard library only.

### Task 1: Isolation flags on every dispatch
- [x] Done — passed, 1 attempt

**Files:**
- Modify: `scripts/forge_common.py` (two argument-group constants)
- Modify: `scripts/forge-run.py` (`dispatch_worker`, `_dispatch_review_call`, `dispatch_final_review_fix`, `dispatch_doc_sync` — cold and resume argvs)
- Modify: `tests/test_forge_resume.py` (exact-argv expectations include the flags)
- Modify: `tests/test_forge_final_review.py` (exact-argv expectations include the flags)
- Test: `tests/test_forge_isolation_flags.py`

**Spec:** Worker isolation, Worker dispatch mechanics, Testing

**Interface:**
- `forge_common.CODEX_ISOLATION_ARGS: tuple[str, ...]` — `("--disable", "multi_agent", "--disable", "multi_agent_v2", "--disable", "memories")`
- `forge_common.CODEX_REVIEWER_SANDBOX_ARGS: tuple[str, ...]` — `("-c", 'sandbox_mode="read-only"')`

**Tests:**
- task worker cold and resume argvs carry all three `--disable` pairs and no sandbox override
- task reviewer cold and resume argvs carry all three `--disable` pairs and `sandbox_mode="read-only"`
- final reviewer cold and resume argvs carry all three `--disable` pairs and `sandbox_mode="read-only"`
- final-review fixer cold and resume argvs carry all three `--disable` pairs and no sandbox override
- doc-sync cold argv carries all three `--disable` pairs and no sandbox override
- every dispatch site builds its flags from the two constants (changing a constant changes every recorded argv)
- `ultra` is still never emitted

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_isolation_flags.py` passes
- `python3 -m pytest -q tests/test_forge_dispatch.py tests/test_forge_threads.py tests/test_forge_final_review.py tests/test_forge_docsync.py tests/test_forge_resume.py` passes

**Tier:** standard

**Depends on:** nothing.

### Task 2: Codex docs describe worker isolation
- [ ] Done

**Files:**
- Modify: `skills/planning/codex-execution.md` (dispatch description names the isolation flags and the read-only reviewer)
- Modify: `docs/forge/running-on-codex.md` (Known Codex caveats: current subagent behavior per the spec's Risks / constraints, in-worker subagents disabled; drop the v0.137.0 regression claim)

**Spec:** Worker isolation, Risks / constraints

**Tests:** none — prose artifacts take mechanical acceptance

**Acceptance:**
- `grep -q -- '--disable multi_agent' skills/planning/codex-execution.md` exits 0
- `grep -q 'read-only' skills/planning/codex-execution.md` exits 0
- `grep -q 'multi_agent_v2' docs/forge/running-on-codex.md` exits 0
- `grep -q 'v0.137.0' docs/forge/running-on-codex.md` exits 1
- `python3 -m pytest -q tests/test_forge_docs.py` passes

**Tier:** standard

**Depends on:** Task 1

### Task 3: Live isolation check on a real Codex
- [ ] Done

**Files:**
- Create: `tests/live/check_codex_isolation.sh` (runs the spec's paired prompt with and without the isolation flags, then flagged with `multi_agent_v2` enabled; exits non-zero when `codex` is absent or older than 0.154.0)

**Spec:** Acceptance

**Tests:** none — a live check against a real model, run by hand, not part of the pytest suite

**Acceptance:**
- `sh tests/live/check_codex_isolation.sh` exits 0, having shown `spawn_agent` in the unflagged run's events or final message and in neither flagged run

**Tier:** standard

**Depends on:** Task 1
