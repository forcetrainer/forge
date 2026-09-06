# Phase 1 — Pipeline & document-contract skill edits

Phase 1 of the 2026-07 upgrade cycle ([ideation doc](../ideas/2026-07-02-upgrade-cycle.md)). Scope: text edits to three skills + README + release. No new files except this spec; no scripts; no Codex; planning-skill Execution section untouched (phase 2).

## 1. skills/brainstorming/SKILL.md

### 1.1 Gear routing — new section before Flow

- Routing test: change creates new architecture → gear 3 (full flow below); operates within existing architecture → gear 2. Size is secondary: 10-line change adding a dependency → gear 3; 100-line change filling spec-implied behavior → gear 2.
- Gear 1 (trivial/mechanical/content) = existing frontmatter don't-trigger list; unchanged.
- Gear 2 path:
  1. Name the owning spec in `docs/forge/specs/`.
  2. Present the design in conversation, one paragraph max.
  3. One approval gate.
  4. Hand off directly to the tdd skill — no spec file, no plan file, planning skill skipped.
  5. After execution: amend the owning spec in place; changelog line (§1.4); commit amendment with the change. DECISIONS entry only if something was genuinely decided.
- Tripwires, both mandatory:
  - Can't name the owning spec → gear 3.
  - Design stops fitting in a paragraph mid-conversation → escalate to gear 3; never stretch the conversational gate.

### 1.2 Ideation handoff — step 1 addition

- Docs in `docs/forge/ideas/` (or a path handed at kickoff) = pre-answered clarification.
- Protocol: read; confirm understanding; flag DECISIONS conflicts; skip answered questions; go to approaches.
- Applies only when an idea graduates to "building this"; free-form ideation stays unprocessed.

### 1.3 Flow edits

- Step 3: batch 2–3 independent clarifying questions per turn; single-question only when the answer forks the design.
- Step 5 check-ins: decision digests — what was chosen, what it forecloses, what's assumed.
- Step 7 (self-review): unchanged.
- Step 8 demoted: close-out message = "spec written to `<path>` and committed — flag changes, otherwise proceeding to planning." No mandatory file re-review; the sectioned walkthrough is the gate.

### 1.4 Living specs + style contract — step 6 additions

- Specs are living per-system documents, not frozen snapshots. Any change that alters what a spec asserts → amend that spec in place.
- Changelog: `## Changelog` section at spec end, created on first amendment; one dated line per amendment: `2026-07-02: sort by division, not date (commit abc123)`.
- Spec style: telegraphic — bullets, contracts, constraints. Sentence test: carries a requirement, contract, or decision, else cut. No narrative preamble, no restated codebase context, no justification prose (the why lives in DECISIONS).
- Guard: trim toward decision-relevant, not short. Substance rules unchanged — edge-case naming, interfaces, acceptance criteria stay.

## 2. skills/planning/SKILL.md

- Plan header template gains a `**Global Constraints:**` block: version floors, dependency limits, naming rules. Complements per-task Interface blocks. Omitted entirely when the plan has none — no empty block.
- Plan prose adopts the §1.4 style contract (same sentence test).
- End-of-plan summary: lead with failures, deviations, deferrals — not achievements.
- Execution section: no other changes (phase 2).

## 3. skills/tdd/SKILL.md

- Target ≤650 words (`wc -w`), from ~1,690.
- Keep, substance intact:
  - Frontmatter unchanged; trigger/floor line.
  - Iron Law.
  - Test-infrastructure gate (logged decision 2026-06-10 — all three branches preserved).
  - RED → verify-RED → GREEN → verify-GREEN → REFACTOR, with both verifications mandatory and their failure rules: test passes immediately → testing existing behavior, fix test; test errors → fix until it fails correctly; on GREEN, other tests failing → fix now; fix code, not test.
  - Good-test qualities: minimal (one behavior), clear name, shows intent, real code over mocks.
  - Bug fix = failing repro test first.
  - Final verification checklist; final rule; exceptions require human partner permission.
  - On-demand pointer to `testing-anti-patterns.md` when adding mocks/test utilities.
- Cut: dot digraph, all code-example blocks, "Why Order Matters," "Common Rationalizations," "Red Flags," worked bug-fix example, "When Stuck," "When to Use" list (folded into trigger line).

## 4. Unchanged by this phase

- `hooks/session-start` — continuity-only per logged decision; gear routing lives in skills.
- `skills/project-memory/` — `ideas/` is brainstorm input, not a memory surface.
- `agents/` — no tier changes.

## 5. Release

- README: flow description updated for gears, living specs, `ideas/` input.
- `.claude-plugin/plugin.json` version bump; `claude plugin update forge@forge`; session restart to apply.
