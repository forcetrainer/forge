# Phase 11 — Inline finding-process fix — Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Replace inline self-review's silent over-resolution with the Phase 7 disposition canon (fix/defer/halt + convergence + human gate), authored once in `SKILL.md` so both harnesses' inline paths are fixed by construction.
**Architecture:** Prose/skill-only. `skills/planning/SKILL.md` is the shared home — its inline bullet gains the four-quadrant disposition, the convergence rework loop, and the halt-to-user gate; three adjacent lines (rework guardrails, proportional review, final review) are reconciled to it. `skills/planning/codex-execution.md`'s inline branch is repointed to reference that canon rather than restating finding-handling. No `scripts/` changes; the runner's Phase 7 matrix is the dispatch implementation and stays byte-unchanged.
**Tech stack:** Markdown skill files consumed by the orchestrating session at plan time.
**Global Constraints:** No changes under `scripts/`. Runner (dispatch) sections of `codex-execution.md` — everything from "Dispatch (mode = dispatch)" onward — must be byte-unchanged. Inline descriptions must read identically in intent across both files (the symmetry bar). Shared vocabulary is defined in `SKILL.md` (Task 1) and referenced, not duplicated, in `codex-execution.md` (Task 2).

### Task 1: SKILL.md — inline finding-handling canon
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (inline bullet in the Execution section; the "Rework guardrails", "Proportional review", and "Final review" lines)

**Spec:** Inline finding-handling contract (both harnesses), Changes, Decision

**Interface:** the canonical vocabulary this task introduces, which Task 2 must reference verbatim:
- Disposition quadrants: **fix** (in-diff × contract-breaking, the only auto-fix cell), **defer strict** (in-diff × improvement), **halt** (pre-existing × contract-breaking), **defer** (pre-existing × improvement).
- Axis rules: provenance judged against actual diff line ranges; contract-breaking requires a named acceptance criterion else downgrades to a deferral.
- Halt behavior: orchestrator **drafts a disposition** (repair-task sketch or fix/defer rationale), then **surfaces to the user**; nothing in the halt quadrant is auto-applied.
- Convergence rework: self-review labels remaining fix-quadrant findings **resolved / carried / new** vs. the prior attempt; **halt on regression** (a resolved finding reappears, or an acceptance command goes green→red); **halt on stuck** (a fix-quadrant finding carried across two consecutive attempts); **pass** when no fix-quadrant findings remain; **backstop 5** attempts.

**Changes required (what, not how):**
- Inline bullet: replace "an orchestrator self-review before each commit as a lightweight consistency pass" (implies silent over-resolution) with the four-quadrant disposition + halt-to-user gate + convergence loop. Preserve the surrounding "no separate fresh-context reviewer / TDD + acceptance are the objective check / runs on session model / same act on both harnesses" text unchanged in meaning.
- Rework guardrails line: scope the existing "review loops cap at 2 iterations, then escalate" to the **dispatch** path only; state the inline path's stop condition is the convergence model. Keep the per-task cycle-count reporting for both.
- Proportional review line: the inline branch now *disposes* of findings by the matrix rather than resolving them all; trivial-tier still skips review entirely.
- Final review line: the inline final review applies the same disposition gate (halt-quadrant surfaces; fix-quadrant converges).

**Tests:** none (prose change; TDD infrastructure gate not tripped). Verification is the acceptance greps below.

**Acceptance:**
- `grep -n "defer strict" skills/planning/SKILL.md` returns a hit (disposition canon present).
- `grep -n "resolved" skills/planning/SKILL.md` and `grep -n "regression" skills/planning/SKILL.md` both return hits (convergence model present).
- `grep -n "lightweight consistency pass" skills/planning/SKILL.md` returns **nothing** (over-resolution framing removed).
- The rework-guardrails "cap at 2" text is scoped to dispatch — confirmed by reading the edited line (it names the dispatch path).
- `git diff --stat` shows only `skills/planning/SKILL.md` changed by this task.

**Tier:** standard

**Depends on:** nothing.

### Task 2: codex-execution.md — inline branch references the canon
- [ ] Done

**Files:**
- Modify: `skills/planning/codex-execution.md` (the "Inline (mode = inline)" paragraph near the top only)

**Spec:** Changes, Inline finding-handling contract (both harnesses)

**Interface:** consumes the Task 1 vocabulary; introduces no new terms. The inline paragraph must point at the `SKILL.md` inline finding-handling contract as the shared canon for disposition/convergence/gate, not restate a divergent matrix.

**Changes required (what, not how):**
- Inline paragraph: keep the "Codex session executes task-by-task, TDD, commit per task, does not invoke the runner, does not dispatch a separate reviewer" framing; replace/augment the finding-handling wording so it defers to the `SKILL.md` inline finding-handling contract (disposition matrix, convergence, halt-to-user gate) rather than describing self-review as a plain before-commit pass.
- Do not touch anything from "Dispatch (mode = dispatch)" onward.

**Tests:** none (prose change).

**Acceptance:**
- The inline paragraph references the shared `SKILL.md` canon for finding-handling — confirmed by reading it.
- `git diff skills/planning/codex-execution.md` shows changes confined to the inline paragraph; all runner/dispatch sections are byte-unchanged (no hunks below the "Dispatch (mode = dispatch)" line).
- Codex inline and Claude inline describe the same finding-process (symmetry bar) — confirmed by reading both against each other.

**Tier:** standard

**Depends on:** Task 1.
