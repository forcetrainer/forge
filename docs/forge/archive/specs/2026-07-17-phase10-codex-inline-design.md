# Phase 10 — Codex inline execution branch — Design

**Status:** approved (2026-07-17)
**Roadmap:** Phase 10 (Codex path). Decomposed from Phase 8 — see [DECISIONS 2026-07-17](../archive/DECISIONS.md) (Phase 8 decomposition; inline self-review).
**Constraint:** own spec per the no-mixed-implementations rule — never amend the Codex-runner spec.

## Problem

Codex plan execution is **runner-only**. `SKILL.md` routes "Workflow tool unavailable → `codex-execution.md`," and that doc assumes every plan goes through `scripts/forge-run.py`. So the inline/dispatch choice — which is a **task-shape** decision (accumulated context is an asset / the change is simple) — is currently entangled with **harness capability**: Claude can go inline, Codex cannot. The runner was built to fix in-session *dispatch* (spawn friction, quota accumulation, 2026-07-13); making it Codex's *only* path swept in the low end (simple edits, doc updates, mechanical changes, small plans) that never needed dispatch machinery. That low end has nowhere lightweight to run on Codex.

## Decision

The execution **mode** (inline vs. dispatch) is chosen **first**, by the shared context-is-an-asset criterion, harness-independent. The **harness** only determines *how the dispatch branch runs* (Claude → Workflow subagents; Codex → the runner). **Inline is the same act on both harnesses** — the session does the work itself. This phase gives Codex the inline vehicle it lacks and reaches **behavioral symmetry** with Claude's current inline path — including its current finding-handling flaw, which Phase 11 fixes for both at once (build symmetry first, then fix the symmetric pair once).

Prose/skill-only phase: **no script changes**. Inline is the session following instructions; the runner (`forge-run.py`) is the dispatch branch and is untouched.

## Inline execution contract (both harnesses)

Inline = the orchestrating session executes the plan task-by-task in-session, keeping accumulated context:

- **TDD per task** — test first, then implementation, per the tdd skill.
- **Self-review before commit** — the orchestrator reviews its own work as a lightweight consistency pass. Inline does **not** dispatch a separate fresh-context reviewer: fresh-context review is a dispatch-only concern (its bias-removal value scales with design content, which is low at the simple end where inline operates), and **TDD + acceptance commands are the objective, unbiased check** ([DECISIONS 2026-07-17](../archive/DECISIONS.md), inline self-review). Trivial-tier precedent: acceptance commands are verification enough.
- **Commit per task** — a clean checkpoint after each passed task (`git add -A && git commit`), so the working tree is clean between tasks.
- **Clean-tree expectation** — inline assumes a clean tree between tasks, established by the per-task commit.
- **Finding-handling — mirrors Claude's current model (the flaw):** the orchestrator resolves review/self-review findings itself, including decision-grade ones (over-resolution). This is **carried deliberately** and fixed in Phase 11 (gate the self-review; surface decision-grade findings). Phase 10 does not change it.
- **Final review** for multi-task plans stays as specified in `SKILL.md` — the flawed over-resolution of its findings likewise persists to Phase 11.

## Changes

### 1. `skills/planning/SKILL.md` — mode-before-harness routing

The Execution section's offer currently lists three peer bullets that conflate harness with mode: "Workflow tool unavailable → `codex-execution.md`," "Inline when…," "Dispatch otherwise." Restructure so the **mode decision is made first and is harness-independent**, and the harness branch lives *inside* dispatch:

- **Mode selection** (shared): inline when accumulated context is an asset — few tasks, later tasks build on earlier output, the change is simple enough that dispatch machinery is overkill; dispatch otherwise.
- **Inline** (shared, both harnesses): the session executes task-by-task per the inline execution contract above (TDD, self-review, commit per task). One description, not per-harness.
- **Dispatch** → harness picks the mechanism:
  - Claude → a Workflow script spawns one worker per task at its tier agent (existing text).
  - Codex → the runner (`codex-execution.md`).

The current "inline work runs on the session model; say so in the offer" disclosure is retained under Inline. No behavior change for Claude — this is a reorganization; Claude inline keeps its current self-review + over-resolution until Phase 11.

### 2. `skills/planning/codex-execution.md` — add the inline branch

Add a short leading section establishing that the runner is the **dispatch** branch, not the whole of Codex execution:

- When the mode is **inline** (per `SKILL.md`), the Codex session executes directly per the inline execution contract — TDD, self-review, commit per task, clean tree — and does **not** invoke the runner.
- When the mode is **dispatch**, execution runs through `scripts/forge-run.py` as documented in the rest of the file (unchanged).

Frame the existing runner content as the dispatch branch; do not alter runner mechanics, exit codes, receipts, `--status`, or the monitor.

### 3. Claude path — unchanged behavior

The `SKILL.md` restructure reorganizes routing presentation; Claude inline behavior (self-review, over-resolution) is **not** changed here. Preserving the flaw on both paths is what makes Phase 11's shared fix provable against a symmetric pair.

## Non-goals

- **Finding-handling fix** (gate the self-review; surface decision-grade findings) — Phase 11.
- **Claude dispatch commit-per-task + clean-tree precondition** — Phase 12 (Half A).
- **Tier-recalibration effort drift** (agent frontmatter xhigh→high, high→medium) and stale "escalation reviewer" prose — Phase 12.
- **New scripts, receipts, or hooks for inline** — inline is session-driven prose; no machinery.

## Acceptance

- `SKILL.md` Execution offers the mode decision (inline vs. dispatch) **before** any harness branch, and the harness branch (Workflow vs. runner) appears only inside dispatch.
- `SKILL.md` describes inline once, applying to both harnesses, matching the inline execution contract (TDD, self-review, commit per task).
- `codex-execution.md` states inline runs in-session (not the runner) and frames the runner as the dispatch branch; runner mechanics are byte-unchanged.
- Codex inline and Claude inline describe the **same** process, including the over-resolving finding-handling (symmetry bar).
- No changes under `scripts/`; no new tests (prose-only change; TDD infrastructure gate not tripped).

## Changelog

- 2026-07-17 — initial spec. Decomposed from Phase 8; inline self-review model per DECISIONS 2026-07-17.
