# Phase 11 — Fix the inline finding-process — Design

**Status:** approved (2026-07-17)
**Roadmap:** Phase 11 (cross-harness). Decomposed from Phase 8 — see [DECISIONS 2026-07-17](../DECISIONS.md) (Phase 8 decomposition; inline self-review).
**Constraint:** own spec per the no-mixed-implementations rule — never amend the Codex-runner spec or the Phase 7 scope-autonomy spec.
**Builds on:** [Phase 10 spec](2026-07-17-phase10-codex-inline-design.md) (established the symmetric-but-flawed inline pair) and the [Phase 7 scope-autonomy spec](2026-07-16-phase7-scope-autonomy-design.md) (the disposition canon this phase imports).

## Problem

After Phase 10, Claude inline and Codex inline are **symmetric** — and symmetrically **flawed**. Both run the orchestrator's self-review as a "lightweight consistency pass" that **silently resolves every finding it raises**, including decision-grade ones (`SKILL.md` inline bullet; `codex-execution.md` inline branch). This is over-resolution: the autonomous-fixer failure mode Phase 7 named — over-fixing is diff over-scoping wearing a new hat. On the dispatch path the Phase 7 disposition matrix already stops this on the runner; the inline path has no such gate, so a finding about **pre-existing, contract-breaking** code gets swept into the fix silently, with no human ever seeing the scope decision.

The inline path is where this is *least* backstopped: inline has no fresh-context reviewer (by design — [DECISIONS 2026-07-17](../DECISIONS.md)), so the self-review is the only check on rationalized judgment calls, and right now it rationalizes them all away.

## Decision

Correct the finding-handling **once in the shared home** — `SKILL.md`'s inline bullet — so the fix reaches both harnesses by construction (Phase 10 made them symmetric; this improves the symmetric pair once). Replace inline over-resolution with the **Phase 7 disposition canon**: classify every self-review finding on the two axes, act by quadrant, and **surface the halt-grade quadrant to the user** instead of silently fixing it. Inline runs in-session, so the "human gate" is the conversation itself — no notify machinery.

**Inline stays self-reviewed.** This phase does **not** add a fresh-context reviewer — that is a dispatch-only concern ([DECISIONS 2026-07-17](../DECISIONS.md)). TDD + acceptance commands remain the objective load-bearing check; what changes is *how the self-review disposes of what it finds*.

Prose/skill-only phase: **no script changes.** The runner's Phase 7 matrix and convergence loop are the dispatch implementation and are untouched; inline adopts the same *canon* as authored prose the session follows.

## Inline finding-handling contract (both harnesses)

The orchestrator's self-review no longer resolves findings uniformly. It classifies each finding on two axes — **provenance** (in-diff = this plan's new code, vs. pre-existing) × **contract impact** (contract-breaking vs. improvement) — and acts by quadrant:

| | contract-breaking | improvement |
|---|---|---|
| **in-diff** | **fix** — self-fix, then re-review (the only auto-fix cell; drives the rework loop) | **defer strict** — no gold-plating our own new code; log to DEFERRALS |
| **pre-existing** | **halt** — draft a disposition, then surface to the user | **defer** — harmless; log to DEFERRALS |

Axis rules carried from the Phase 7 canon:
- **Provenance** is judged against the actual diff line ranges (this plan's changes), not assumed. A finding is in-diff only if it lands on code this plan wrote or changed.
- **Contract-breaking requires a named acceptance criterion.** Absent one, the finding downgrades to a deferral (the tier-policy "named evidence" rule). This keeps "I'd have done it differently" out of the fix and the halt.
- **The halt quadrant (pre-existing × contract-breaking) is the one that must never be silently fixed *or* silently deferred.** The orchestrator drafts a disposition — a repair-task sketch or a fix/defer rationale — and **halts to the user** with it; the user makes the call. Nothing in this quadrant is auto-applied.

### Rework loop — convergence, not a raw cap

Inline adopts the Phase 7 **convergence** model for its fix-quadrant rework, replacing the current cap-at-2 on the inline path. Because the self-reviewing session retains the prior attempt in context, the convergence labels come for free — no re-review packet is needed (that was a dispatch artifact):

- After each fix attempt, the self-review labels its remaining fix-quadrant findings **resolved / carried / new** relative to the prior attempt.
- **Halt on regression** — a previously-resolved finding reappears, or an acceptance command goes green → red (shuffling one bad state into another).
- **Halt on stuck** — a fix-quadrant finding is carried across two consecutive attempts (no progress on it).
- **Pass** when no fix-quadrant findings remain.
- **Backstop of 5** attempts — a seatbelt against slow non-convergence, not the primary stop. Net progress each round is not required (the self-review may surface new findings incrementally); only regression or stuck halts early.

A halt (regression, stuck, backstop, or a halt-quadrant finding) surfaces to the user with the outstanding findings and a drafted disposition — the same conversational gate.

**Scope boundary:** convergence here governs **inline** rework on both harnesses. The **Codex dispatch** path already has convergence (Phase 7, in the runner). The **Claude dispatch** path keeps its existing cap-at-2 rework guardrail *not by decision but because no phase currently owns bringing it the Phase 7 canon* — that gap fell out of the Phase 8→10–12 decomposition and is logged in [DEFERRALS 2026-07-17](../DEFERRALS.md) (Claude dispatch never got the Phase 7 canon), with a follow-up to rethink Phase 12 as the overall "Claude dispatch → Codex-runner parity." Phase 11 stays inline-only on purpose; it does **not** touch either dispatch path.

### Final review (multi-task inline plans)

The inline final review (Phase 10 deliberately preserved its over-resolution) gets the **same** disposition gate: its findings are classified on the same matrix, fix-quadrant findings drive the same convergence loop, and a halt-quadrant finding surfaces to the user rather than being silently resolved.

## Changes

### 1. `skills/planning/SKILL.md` — the shared canon (primary change)

- **Inline bullet:** replace "an orchestrator self-review before each commit as a lightweight consistency pass" (which implies silent over-resolution) with the inline finding-handling contract above — the four-quadrant disposition, the named-criterion downgrade rule, the halt-to-user gate with a drafted disposition, and the convergence rework loop. Keep the existing "no separate fresh-context reviewer / TDD + acceptance are the objective check / session model / same act on both harnesses" text — this phase gates the self-review's *output*, it does not change what the self-review *is*.
- **Rework guardrails line:** scope the "review loops cap at 2 iterations, then escalate" statement to the **dispatch** path (it currently reads as universal). The inline path's stop condition is the convergence model above. The end-of-plan cycle-count reporting stays for both.
- **Proportional review line:** the inline branch ("the orchestrator's own self-review, no separate reviewer") stays true; add that the self-review now *disposes* of findings by the matrix rather than resolving them all. Trivial-tier still skips review entirely.
- **Final review line:** note the inline final review applies the same disposition gate (halt-quadrant surfaces; fix-quadrant converges).

### 2. `skills/planning/codex-execution.md` — inline branch references the canon

The inline branch (top of file) currently restates self-review-before-commit. Point it at the SKILL.md inline finding-handling contract as the shared canon rather than duplicating the matrix — the Codex inline session follows the same disposition/convergence/gate. Do **not** alter the runner (dispatch) sections, exit codes, receipts, `--status`, the monitor, or the runner's own Phase 7 matrix text.

### 3. No script changes

Inline is session-driven prose. `scripts/forge-run.py` (and the whole runner surface) is the dispatch implementation of the same canon and is byte-unchanged. No new tests (prose-only; TDD infrastructure gate not tripped — the acceptance below is grep-level presence/absence over the skill files).

## Non-goals

- **A fresh-context reviewer on inline** — explicitly rejected ([DECISIONS 2026-07-17](../DECISIONS.md)); this gates the self-review, it does not add a reviewer.
- **The whole Phase 7 canon (matrix + convergence) on the Claude dispatch path** — deferred; no phase owns it yet ([DEFERRALS 2026-07-17](../DEFERRALS.md)), candidate for a rethought Phase 12 (Claude dispatch → Codex-runner parity).
- **Claude dispatch commit-per-task + clean-tree precondition** — Phase 12 (Half A).
- **Tier-recalibration effort drift** (agent frontmatter) and stale "escalation reviewer" prose — Phase 12.
- **Any change to the runner's Phase 7 matrix, convergence loop, or `--autofix` semantics** — that implementation is done and correct.

## Acceptance

- `SKILL.md`'s inline description states the four-quadrant disposition, the named-criterion downgrade, and the halt-to-user gate with a drafted disposition — and no longer describes self-review as silently resolving all findings.
- `SKILL.md` describes the inline convergence rework loop (resolved/carried/new; halt on regression/stuck; backstop 5) and scopes the cap-at-2 guardrail to the dispatch path.
- `SKILL.md` inline final review applies the same disposition gate.
- `codex-execution.md` inline branch references the SKILL.md canon for finding-handling and does not restate a divergent matrix; runner sections are byte-unchanged.
- Codex inline and Claude inline describe the **same** finding-process (symmetry preserved — now the fixed process).
- No changes under `scripts/`; no new tests.

## Changelog

- 2026-07-17 — initial spec. Decomposed from Phase 8; imports the Phase 7 disposition canon into the inline path per DECISIONS 2026-07-17. Inline rework adopts Phase 7 convergence (operator choice); halt-quadrant drafts a disposition before surfacing (operator choice); delivered as spec + plan (operator choice).
- 2026-07-17 — corrected the Claude-dispatch scope language: the draft asserted "Claude dispatch stays cap-2" as a boundary, but that's an unlogged gap from the Phase 8→10–12 decomposition, not a decision. Reframed as inline-only-on-purpose + a DEFERRALS entry (Claude dispatch never got the Phase 7 canon) with a Phase 12 rethink. Phase 11 scope unchanged (inline only).
