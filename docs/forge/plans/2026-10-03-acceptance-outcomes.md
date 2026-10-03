# Acceptance Outcomes Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Acceptance clauses carry an explicit outcome the runner checks exactly, prose clauses are never executed, and a repeated first-failing acceptance clause halts `stuck` instead of looping to the backstop.
**Architecture:** `forge_plan` parses each `**Acceptance:**` clause into an `AcceptanceCheck` (command clause) or leaves it prose, raising on a malformed command clause; `forge_lint` reports every such clause and `forge_checklist` keeps only prose clauses. `forge-run.py` checks each command clause's stated outcome against full combined output via `forge_plan.outcome_met`. `forge_dispose` carries the prior attempt's first failing command in `ConvergenceState` and halts `stuck` on a repeat, on both harnesses. The run's `--spec` is `docs/forge/specs/execution.md`; every `**Spec:**` line names its sections, and each task lists the `pipeline.md` / `codex-runner.md` sections it also needs under `**Files:**` as `Read:`.
**Tech stack:** Python 3 stdlib, pytest with the fake `codex` in `tests/_forge_support.py`.
**Global Constraints:**
- Scripts under `scripts/` import the Python 3 standard library only.
- A command clause that does not parse raises or lint-errors naming the task, line and cause; it is never defaulted to `passes` and never read as prose.

### Task 1: Acceptance clause grammar — parse, lint, checklist
- [ ] Done

**Files:**
- Read: `docs/forge/specs/pipeline.md` (section "Plan documents", bullet "Acceptance clause grammar")
- Modify: `scripts/forge_common.py` (`AcceptanceCheck`; `ACCEPTANCE_OUTCOMES`; `Task.acceptance_checks` replaces `Task.acceptance_commands`)
- Modify: `scripts/extract-brief.py` (`parse_field_clause_lines`; `parse_field_clauses` delegates to it, output unchanged)
- Modify: `scripts/forge_plan.py` (`parse_acceptance_clause`; `parse_plan_tasks` builds `acceptance_checks` from clauses; remove `_parse_commands`, and `_field_text` if no caller remains)
- Modify: `scripts/forge_checklist.py` (`t<N>.a<M>` from prose clauses only; remove `_INLINE_CODE_ONLY_RE`)
- Modify: `scripts/forge_lint.py` (one error per malformed command clause, every one reported)
- Modify: `scripts/forge-run.py` (`run_acceptance` iterates `task.acceptance_checks`, running `check.command`; outcome checking is Task 2)
- Test: `tests/test_forge_plan.py`, `tests/test_forge_lint.py`, `tests/test_forge_checklist.py`, `tests/test_extract_brief.py`

**Spec:** Plan lint, Contract checklist

**Interface:**
- `forge_common.AcceptanceCheck(command: str, outcome: str, expected: int | str | None, stated: str)` — `outcome` in `{"passes", "exits", "prints-nothing", "prints"}`; `expected` is the int for `exits`, the literal text for `prints`, else `None`; `stated` is the outcome as written in the plan (e.g. `exits 1`)
- `forge_common.ACCEPTANCE_OUTCOMES: tuple[str, ...]` — the four legal forms as quoted in error messages: `passes`, `exits <N>`, `prints nothing`, `` prints `<text>` ``
- `forge_common.Task.acceptance_checks: list[AcceptanceCheck]`
- `extract_brief.parse_field_clause_lines(block: str, field_name: str) -> list[tuple[int, str]]` — each clause with the 0-based index, within `block`, of its first line; same raises as `parse_field_clauses`
- `forge_plan.parse_acceptance_clause(clause: str) -> AcceptanceCheck | None` — `None` for a prose clause; raises `ValueError` naming the cause for a clause that begins with inline code and does not match the grammar

**Tests:**
- `` `make test` passes `` parses to outcome `passes`, expected `None`
- `` `grep -q x f` exits 1 `` parses to outcome `exits`, expected `1`
- `` `grep -n slug f` prints nothing `` parses to outcome `prints-nothing`
- `` `python3 -V` prints `Python 3` `` parses to outcome `prints`, expected `Python 3`, and its second span is not a command
- a clause with leading whitespace before the command span is still a command clause
- a prose clause containing inline code (a file path, a version string) returns `None`
- a bare `` `make test` `` raises naming the missing outcome
- an unknown outcome (`` `make test` succeeds ``) raises
- a trailing period (`` `make test` passes. ``) raises
- `exits -1` and `exits one` raise
- a second command span (`` `a` `b` passes ``) raises
- trailing text after `` prints `x` `` raises
- `parse_plan_tasks` on a malformed command clause raises naming the task number, the plan line number of the clause, the clause text and all four legal outcomes
- lint reports every malformed command clause across all tasks in one run, each defect naming task, line and clause
- lint accepts a plan whose acceptance clauses are all prose
- the task checklist drops command clauses and keeps prose clauses, including prose that contains inline code, numbered `a1..` over prose clauses only
- `parse_field_clause_lines` returns first-line indices for the single-line form and for multi-line bulleted clauses, and `parse_field_clauses` output is unchanged

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_plan.py tests/test_forge_lint.py tests/test_forge_checklist.py tests/test_extract_brief.py` passes
- `python3 -m pytest -q` passes

**Tier:** standard

**Depends on:** nothing.

### Task 2: The runner checks each stated outcome
- [ ] Done

**Files:**
- Read: `docs/forge/specs/codex-runner.md` (sections "Task loop (per task)", "Receipts and run state")
- Modify: `scripts/forge_common.py` (`TeeResult.output` — full merged output; `AcceptanceResult.outcome`, `AcceptanceResult.passed`)
- Modify: `scripts/forge_plan.py` (`outcome_met`)
- Modify: `scripts/forge-run.py` (`run_acceptance` records outcome and pass; acceptance green from `passed`; the acceptance execution-failure finding names the first failing clause in plan order)
- Test: `tests/test_forge_plan.py`, `tests/test_forge_loop.py`, `tests/test_forge_tee.py`, `tests/test_forge_receipts.py`

**Spec:** Rework loop and convergence

**Interface:**
- `forge_common.TeeResult.output: str` — the full merged stdout+stderr; `tail` unchanged
- `forge_common.AcceptanceResult(command: str, outcome: str, exit_code: int, output_tail: str, passed: bool)` — `outcome` is `AcceptanceCheck.stated`
- `forge_plan.outcome_met(check: AcceptanceCheck, exit_code: int | None, output: str, timed_out: bool) -> bool`

**Tests:**
- `passes` is met by exit 0 and not by exit 1
- `exits 1` is met by exit 1 and not by exit 0
- `prints nothing` is met by empty output at exit 0, 1 or 2, and not by any output
- `` prints `ok` `` is met by output containing `ok` at exit 0, not at exit 1, and not when `ok` is absent
- `` prints `ok` `` is met when `ok` appears only before the tail window of a long output
- a timed-out command meets no outcome
- `run_teed` returns the full merged output alongside the unchanged tail
- a task whose `grep` clause states `prints nothing` and finds nothing (exit 1) passes acceptance
- a prose clause containing an inline-code `touch <path>` is never executed — the path does not exist after acceptance
- with two failing clauses, the rework finding names the first in plan order: its command, stated outcome, exit code and output tail
- each receipt `acceptance_results` entry carries `command`, `outcome`, `exit_code`, `output_tail` and `passed`

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_plan.py tests/test_forge_loop.py tests/test_forge_tee.py tests/test_forge_receipts.py` passes
- `python3 -m pytest -q` passes

**Tier:** standard

**Depends on:** Task 1.

### Task 3: A repeated first-failing acceptance clause halts stuck
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (`ConvergenceState.prev_failed_acceptance`; `convergence_decision` and `advance_state` take `failed_acceptance`; CLI `--failed-acceptance` and its usage errors)
- Modify: `scripts/forge-run.py` (passes the first failing clause's command when acceptance is the attempt's execution failure, `None` otherwise and at final review; `HALT_CAUSE_FOR_WORKER["stuck"]` covers the acceptance cause)
- Test: `tests/test_forge_convergence.py`, `tests/test_forge_dispose.py`, `tests/test_forge_loop.py`

**Spec:** Rework loop and convergence, The shared decision helper

**Interface:**
- `ConvergenceState.prev_failed_acceptance: str | None = None` — serialized in `to_dict`; a dict without the key loads as `None`
- `convergence_decision(findings, state, acceptance_ok, attempt, autofix_mode, backstop=MAX_ATTEMPTS_BACKSTOP, approved_ids=frozenset(), failed_acceptance=None) -> tuple[str, str | None]`
- `advance_state(state, findings, acceptance_ok, failed_acceptance=None) -> None` — every attempt overwrites `prev_failed_acceptance` with `failed_acceptance`
- CLI: `forge_dispose.py ... --failed-acceptance <command>`; `decision.json` `state` gains `prev_failed_acceptance`

**Tests:**
- the same first failing command on two consecutive attempts halts `stuck`
- a different first failing command on the next attempt reworks
- an acceptance failure after a worker-crash or worker-timeout attempt reworks, not `stuck`
- an acceptance failure after a green-acceptance attempt halts `regression`, not `stuck` (precedence unchanged)
- failing commands A, B, B on three attempts halt `stuck` at the third
- a reviewed attempt clears `prev_failed_acceptance` to `None`
- state round-trips `prev_failed_acceptance`, and a state dict without the key loads as `None`
- CLI `--failed-acceptance` without `--execution-failure` exits non-zero naming the conflict
- CLI `--failed-acceptance` with `--acceptance-ok true` exits non-zero naming the conflict
- two CLI invocations chained through `--state` with the same `--failed-acceptance` decide `halt` / `stuck` on the second
- a runner task whose clause can never meet its outcome halts `stuck` at attempt 2, and the halt's outstanding finding names the clause's command, stated outcome and output tail
- the worker halt-cause text for `stuck` names a repeated acceptance failure as one cause

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_convergence.py tests/test_forge_dispose.py tests/test_forge_loop.py` passes
- `python3 -m pytest -q` passes

**Tier:** standard

**Depends on:** Task 2.

### Task 4: Planning skill text — clause grammar, Claude acceptance step, timeout
- [ ] Done

**Files:**
- Read: `docs/forge/specs/pipeline.md` (section "Plan documents", bullet "Acceptance clause grammar"), `docs/forge/specs/codex-runner.md` (sections "Runner", "Session awareness — foreground execution")
- Modify: `skills/planning/SKILL.md` (`**Acceptance:**` description states both clause kinds and the four outcomes; Claude dispatch loop's acceptance step checks each command clause's stated outcome and passes `--failed-acceptance` with `--execution-failure` on an acceptance failure; convergence summaries name the acceptance-stuck halt)
- Modify: `skills/planning/codex-execution.md` (drop `--timeout 900` from the example invocation; replace "recommend ~900" with the 3600 `DEFAULT_TIMEOUT` default; convergence paragraph names the acceptance-stuck halt)

**Spec:** The dispatch loop, Rework loop and convergence

**Tests:** none — prose artifacts; acceptance is mechanical text checks

**Acceptance:**
- `grep -q -- '--timeout 900' skills/planning/codex-execution.md` exits 1
- `grep -q 'recommend ~900' skills/planning/codex-execution.md` exits 1
- `grep -q -- '--failed-acceptance' skills/planning/SKILL.md` passes
- `grep -q 'prints nothing' skills/planning/SKILL.md` passes
- The planning skill's `**Acceptance:**` description states that a clause beginning with inline code must be `` `<command>` <outcome> `` with one of the four outcomes, and that any other clause is prose and never executed
- `python3 -m pytest -q` passes

**Tier:** standard

**Depends on:** Task 3.
