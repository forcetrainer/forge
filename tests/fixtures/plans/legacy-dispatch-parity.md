# Claude Dispatch Parity (fixture) Implementation Plan

> Trimmed fixture derived from
> `docs/forge/plans/2026-07-17-phase12b-claude-dispatch-parity.md`, owned by
> the test suite so a plan-grammar change elsewhere never breaks these lint
> tests for reasons unrelated to lint itself.

**Goal:** Bring the Phase 7 disposition matrix + convergence to the Claude dispatch path via a shared, tested `forge_dispose` helper.
**Global Constraints:** No behavior change to the Codex runner; one `Finding` class identity.

### Task 1: Extract decision logic into forge_dispose.py
- [x] Done

**Files:**
- Create: `scripts/forge_dispose.py`
- Modify: `scripts/forge-run.py`

**Spec:** The shared decision helper

**Interface:** `scripts/forge_dispose.py` exposes the decision functions moved verbatim from `forge-run.py`; `forge-run.py` imports and re-exports each into its own namespace.

**Tests:**
- the existing decision-logic suites pass unchanged, proving the move is behavior-preserving
- any required edit to a decision-logic assertion means the move altered behavior and must be corrected

**Acceptance:** `python3 -m pytest -q`

**Tier:** `standard`

**Depends on:** nothing.

### Task 2: Sequential orchestrator loop
- [x] Done

**Files:**
- Modify: `skills/planning/SKILL.md`

**Spec:** Claude execution model

**Interface:** Per task: dispatch implementer, run acceptance, dispatch reviewer, decide via `forge_dispose`, act on the decision.

**Tests:**
- a converging Claude dispatch task runs past two attempts to a clean pass
- a churning one halts at the churn
- a pre-existing contract-breaking finding halts with a drafted repair task

**Acceptance:** `grep -n "forge_dispose" skills/planning/SKILL.md`

**Tier:** `standard`

**Depends on:** Task 1.

### Task 3: Bookkeeping — changelog pointers, roadmap, version bump
- [x] Done

**Files:**
- Modify: `docs/forge/ROADMAP.md`
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`

**Spec:** Retirements

**Interface:** Mechanical edits: changelog pointers, roadmap status flip, lockstep version bump.

**Tests:**
- none — mechanical edits

**Acceptance:** `grep -H '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json`

**Tier:** `trivial` — mechanical doc edits and a version bump, no design content.

**Depends on:** Task 1, Task 2.
