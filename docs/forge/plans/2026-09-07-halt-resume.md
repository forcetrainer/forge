# Halted-run resume Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** A `scope-decision` halt freezes the paused task instead of leaving the tree dirty, so a resumed run continues that task against the human's fix rather than restarting into the same halt.
**Architecture:** Three additions to the Codex runner. `forge_git` gains freeze/restore primitives that park an in-progress attempt off the mainline and replay it later. `forge_receipts` gains a `halt` record in `run.json`, read back on resume alongside `deferrals`/`seeded_findings`. `forge-run` writes that record at the halt, and on resume restores the freeze, hands the worker the resolution delta, and applies `--resolve` decisions so an answered finding cannot halt again. `forge_dispose` gains only an approved-id exemption; its precedence ladder is otherwise untouched.
**Tech stack:** Python 3 standard library, `git` plumbing via `subprocess`, `pytest`.
**Global Constraints:** Standard library only, no third-party dependency (`stdlib-only`). Git helpers raise `RuntimeError` naming the cause rather than returning a default (`parsers-fail-loud`). No test-harness changes (`test-harness-is-plan-work`). Autonomous repair dispatch is out of scope by design — the runner applies no fix of its own.

### Task 1: Freeze and restore primitives
- [x] Done — passed, 2 attempt(s)

**Files:**
- Modify: `scripts/forge_git.py` (add `freeze_attempt`, `restore_freeze`, `freeze_diff`, `freeze_ref_name`; no change to existing helpers)
- Test: `tests/test_forge_commit.py`

**Spec:** Halt resolution, Commit discipline

**Interface:**
```
def freeze_ref_name(run_id, task_number) -> str
def freeze_attempt(cwd, ref_name) -> str | None
def restore_freeze(cwd, freeze_sha) -> bool
def freeze_diff(cwd, freeze_sha) -> str
```
`freeze_ref_name` returns `refs/forge/freeze/<run_id>/task-<N>`. `freeze_attempt` captures tracked **and untracked, non-ignored** changes as a commit whose parent is HEAD, writes it to `ref_name`, returns the sha, and leaves the working tree and index matching HEAD; returns `None` when there is nothing to freeze (tree already equals HEAD). The branch ref never moves — the freeze is reachable only through `ref_name`. `restore_freeze` replays that commit's change onto the current HEAD, returning `True` when it applies cleanly (working tree now carries the frozen change) and `False` on conflict, leaving the working tree clean at HEAD in the conflict case. `freeze_diff` returns the frozen change as patch text. `restore_freeze` and `freeze_diff` raise `RuntimeError` naming the sha when it is not a resolvable object. `freeze_attempt` additionally raises `RuntimeError` naming the path when an untracked nested git repo is present: such a tree cannot be captured (git records only a gitlink to a commit the outer store lacks) and `clean -ff` would destroy the human's work, so refusing is the only outcome that neither corrupts the freeze nor discards work. Callers in Task 4 must treat a freeze failure as a fail-loud halt, not a None.

**Tests:**
- freezes a tracked modification, leaves the working tree clean at HEAD, and the ref resolves to the captured commit
- freezes an untracked new file and restores it (the `git add -A` / `stash create` blind spot this exists to avoid)
- leaves a `.gitignore`d path out of the freeze
- leaves the index unstaged after a freeze — a following `git add -A && git commit` sweeps nothing extra
- returns `None` and writes no ref when the tree already equals HEAD
- does not move `HEAD` or the current branch ref
- restores cleanly onto a HEAD that advanced by an unrelated commit
- returns `False` and leaves the tree clean when the replay conflicts with a commit touching the same lines
- `freeze_diff` returns patch text containing the frozen change, including for an untracked-file freeze
- `restore_freeze` raises naming the sha when the object is missing
- `freeze_diff` raises naming the sha when the object is missing
- outside a git repo, `freeze_attempt` returns `None`

**Acceptance:** `python3 -m pytest tests/test_forge_commit.py -q` passes with no skips.

**Tier:** complex — the freeze must capture untracked files without leaving them staged, a mistake with a recorded prior occurrence in this file (`snapshot_tree`'s docstring: a `git add -A` fallback that a later commit swept into the task slice), and must park the commit off the branch so the resumed task's review base stays the checkpoint.

**Depends on:** nothing.

### Task 2: The halt record in run.json
- [x] Done — passed, 1 attempt(s)

**Files:**
- Modify: `scripts/forge_receipts.py` (`write_run_json` gains `halt=`; add `_read_halt`)
- Test: `tests/test_forge_receipts.py`

**Spec:** Receipts and run state

**Interface:**
```
def write_run_json(..., seeded_findings=None, halt=None)
def _read_halt(run_dir) -> dict | None
```
`halt` is omitted from the written JSON when `None`, matching the other optional fields. Its shape:
```
{"task": int, "attempt": int, "freeze_commit": str | None,
 "freeze_base": str, "convergence_state": {...}, "halt_reason": str,
 "findings": [ ... ], "repair_task": {...} | None, "approved": {"<id>": "repair"|"defer"}}
```
`freeze_base` is the commit the freeze was taken against — the base for the resolution delta. `convergence_state` is `ConvergenceState.to_dict()`. `approved` accumulates across resumes.

**Tests:**
- `halt=None` writes no `halt` key; an existing run.json shape stays valid
- a written halt record round-trips through `_read_halt` field for field
- `_read_halt` returns `None` for a run dir with no run.json, and for one whose run.json carries no `halt` key
- `_read_halt` raises naming the file when run.json is malformed JSON. This DIVERGES from every sibling reader in the module, which catches `ValueError` and returns `None`; `parsers-fail-loud` governs here because reading a corrupt run.json as "no halt record" would resume a fresh task run against a frozen tree. The divergence is deliberate and must be documented at the function
- a later `write_run_json` call passing `halt=None` clears a previously written record rather than preserving it

**Acceptance:** `python3 -m pytest tests/test_forge_receipts.py -q` passes with no skips.

**Tier:** standard

**Depends on:** nothing.

### Task 3: Approved-finding exemption in convergence
- [x] Done — passed, 1 attempt(s)

**Files:**
- Modify: `scripts/forge_dispose.py` (`convergence_decision` gains `approved_ids`; CLI gains `--approved`)
- Test: `tests/test_forge_convergence.py`

**Spec:** Rework loop and convergence, The disposition matrix, Halt resolution

**Interface:**
```
def convergence_decision(findings, state, acceptance_ok, attempt, autofix_mode,
                         backstop=MAX_ATTEMPTS_BACKSTOP, approved_ids=frozenset())
```
`approved_ids` holds canonical finding ids (`carried_from` else `id`) the human has resolved. Step 2 of the precedence ladder — any halt-disposition finding → `halt`/`scope-decision` — ignores findings whose canonical id is in `approved_ids`. Every other step is unchanged, including regression (3), which still sees those findings. The return type is unchanged: `(action, halt_reason)` with action in `{"pass", "rework", "halt"}`. The CLI gains `--approved <id>` (repeatable), threaded into the same parameter.

**Tests:**
- an approved halt-disposition finding no longer triggers `scope-decision`; the decision falls through to the remaining rules
- a non-approved halt-disposition finding still halts when another finding is approved
- an approved finding still trips regression when its id is in the runner's resolved set and it reappears
- approval matches on the canonical id — a finding re-issued under a new id with `carried_from` pointing at an approved id is also exempt
- `gate` mode still halts on an approved finding (gate precedes step 2)
- an empty `approved_ids` reproduces existing behavior across the existing decision cases
- the CLI accepts repeated `--approved` and reflects them in the emitted decision

**Acceptance:** `python3 -m pytest tests/test_forge_convergence.py tests/test_forge_dispose.py -q` passes with no skips.

**Tier:** standard

**Depends on:** nothing.

### Task 4: Freeze at halt, restore and reconcile on resume
- [x] Done — passed, 3 attempt(s)

**Files:**
- Modify: `scripts/forge-run.py` (`execute_task` reconciliation brief and freeze-on-halt; `run_plan` halt-record read/write and resume restore; `main` gains `--resolve`)
- Modify: `scripts/forge_status.py` (render the halt record's resolvable state)
- Test: `tests/test_forge_resume.py`
- Test: `tests/test_forge_loop.py`

**Spec:** Halt resolution, Receipts and run state, Commit discipline

**Interface:**
```
def _resolution_delta(cwd, freeze_base) -> str
def _reconcile_brief(task, run_dir, restored, resolution_delta, frozen_diff, finding) -> str
def run_plan(..., autofix_mode="auto", resolve=None)
```
`resolve` is `{finding_id: "repair"|"defer"}` from repeatable `--resolve <id>=repair|defer`. On a `scope-decision` halt `run_plan` calls `freeze_attempt`, writes the halt record (Task 2 shape) with `freeze_base` = HEAD at freeze time, and exits 2 as today. On re-invocation, when a halt record names the resumed task: the recorded `approved` map plus this invocation's `--resolve` entries thread into `convergence_decision(approved_ids=...)`; `restore_freeze` runs before the task dispatches; and the task's first worker prompt is `_reconcile_brief` rather than the plain brief. `restored=True` states the frozen work is in the tree; `restored=False` supplies `frozen_diff` as reference text. Both carry `resolution_delta`. `convergence_state` is restored from the record. A `--resolve` id absent from the record raises naming it; a recorded `freeze_commit` that no longer resolves raises naming the sha. `defer`-resolved findings are staged through the existing `stage_deferrals` path.

**Tests:**
- a `scope-decision` halt writes a halt record naming the task, attempt, freeze sha and freeze base, and leaves the working tree clean
- after that halt, a re-invocation is not refused by the clean-tree precondition
- an unrelated dirty tree at resume is still refused
- resume restores the frozen work and dispatches the reconciliation brief, not the plain brief
- the reconciliation brief carries the resolution delta and names the resolved finding
- when the replay conflicts, the tree is left at the checkpoint and the brief carries the frozen diff as reference instead
- resume without `--resolve` halts again on the same finding — the exemption is opt-in
- `--resolve <id>=repair` lets the same finding pass without halting
- `--resolve <id>=defer` stages a deferral and does not halt
- restored convergence state carries the prior attempt count, so the backstop is not reset by a halt
- `--resolve` naming an id absent from the halt record raises naming the id
- a missing `freeze_commit` object at resume raises naming the sha
- a halt with nothing to freeze records `freeze_commit: null` and resumes by starting the task from the checkpoint
- a passed task after a resumed halt commits its slice with the frozen work included
- `--status` reports a halted run as resumable and names the outstanding finding ids

**Acceptance:** `python3 -m pytest tests/test_forge_resume.py tests/test_forge_loop.py tests/test_forge_status.py -q` passes with no skips; `python3 -m pytest tests -q` passes with no new failures.

**Tier:** complex — the resume path must decide restore-versus-reference from the replay result, and rebuild convergence state from the record so a halt does not silently reset the attempt counter or the resolved-id set the regression rule depends on.

**Depends on:** Task 1, Task 2, Task 3.

### Task 5: Reconcile the Codex orchestrator instructions
- [ ] Done

**Files:**
- Modify: `skills/planning/codex-execution.md` (invocation form, clean-tree precondition, commit discipline, resume, halt-resolution options)

**Spec:** Halt resolution, Commit discipline

**Interface:** no code interface. The claims that must change, each currently stated and made false by Tasks 1–4:
- "Escalated tasks commit nothing — the rejected attempt stays uncommitted for the human to resolve" — a `scope-decision` halt now freezes the attempt under a forge-owned ref and leaves the tree clean.
- The clean-tree precondition paragraph's "the human must commit or discard those changes before re-invoking" — no longer the halted-task path, which resumes with no human git work at all.
- The resume paragraph's account of an escalated task "attempted but not passed" — must state that the frozen attempt is restored and the worker receives the resolution delta.
- The invocation form must carry `--resolve <finding-id>=repair|defer` with its meaning: the human fixed it, or file it for later; the runner applies no fix of its own.
The `--autofix`, disposition-matrix, convergence and session-continuity paragraphs are unchanged; session continuity's "a halted run's re-invocation always spawns cold" stays true and must not be edited to suggest otherwise.

**Tests:** none — prose. Verified by the acceptance greps below, not by unit tests.

**Acceptance:**
- `grep -c 'resolve' skills/planning/codex-execution.md` is non-zero and the invocation block contains `--resolve`
- `grep -F 'the rejected attempt stays uncommitted' skills/planning/codex-execution.md` exits non-zero (the false claim is gone)
- `grep -F 'always spawns cold' skills/planning/codex-execution.md` exits zero (the still-true continuity rule is intact)
- `python3 -m pytest tests/test_manifests.py -q` passes with no skips

**Tier:** standard

**Depends on:** Task 4.
