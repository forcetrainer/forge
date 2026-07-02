# Phase 1 — Pipeline & Document-Contract Skill Edits Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Apply the phase-1 spec — three-gear routing, ideation handoff, flow edits, living specs + style contract, Global Constraints header, TDD trim — as text edits to three skills plus README/release.
**Architecture:** No structural change. Each task rewrites one skill file per its spec section; README/release last.
**Tech stack:** Markdown skill files; `wc`/`grep` for acceptance.
**Global Constraints:**
- Spec: `docs/theforge/specs/2026-07-02-phase1-pipeline-skill-edits-design.md` (section refs below).
- Frontmatter (`name`, `description`) of all three skills: byte-identical to current.
- All added prose follows the spec §1.4 style contract (sentence test applies to the skill text itself).
- No new files. No edits outside the file(s) each task names. Planning-skill Execution section untouched.
- Docs-only repo, no test harness; per the TDD infrastructure gate, verification = each task's acceptance commands.

### Task 1: Brainstorming skill — gears, ideation handoff, flow edits, living specs
- [ ] Done

**Files:**
- Modify: `skills/brainstorming/SKILL.md` (spec §1.1–§1.4)

**Interface:** the section added before Flow is titled `## Gear check`; spec-amendment changelog heading is `## Changelog`. Later skill text (planning, future phases) refers to these names.

**Content requirements (spec §):**
- §1.1 `## Gear check` before Flow: architectural routing test with both size counter-examples; gear-2 five-step path (owning spec → one-paragraph design → one gate → tdd skill direct, no spec/plan file → in-place spec amendment + changelog line, DECISIONS only if genuinely decided); both tripwires verbatim in force (no owning spec → gear 3; design outgrows a paragraph → escalate).
- §1.2 step 1 addition: `docs/theforge/ideas/` or kickoff-handed path = pre-answered clarification; read → confirm → flag DECISIONS conflicts → skip answered → approaches; only for ideas graduating to build.
- §1.3: step 3 batching (2–3 independent; single only on design forks); step 5 decision digests (chosen/forecloses/assumed); step 8 close-out wording, no mandatory re-review.
- §1.4 step 6 additions: living-spec amendment rule; `## Changelog` format with dated one-line example; telegraphic style contract + sentence test + guard.

**Tests:** none (docs-only; acceptance commands verify).

**Acceptance:**
- `grep -c '## Gear check' skills/brainstorming/SKILL.md` → 1; grep confirms presence of: `owning spec`, `## Changelog`, `2–3 independent`, `decision digests`, `flag changes, otherwise proceeding to planning`.
- Frontmatter block byte-identical: `git diff skills/brainstorming/SKILL.md | grep -E '^[-+](name|description):'` → empty.
- Human-readable pass: steps 1–10 still coherent after edits (no orphaned numbering).

**Tier:** standard

**Depends on:** nothing.

### Task 2: Planning skill — Global Constraints block, style contract, failure-first summary
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (spec §2)

**Interface:** header-template line is `**Global Constraints:**` — same name Task 1's gear-2 text and future phases assume.

**Content requirements (spec §):**
- Header template gains `**Global Constraints:**` (version floors, dependency limits, naming rules), noted as omitted entirely when none — no empty block.
- Plan-prose style contract: same sentence test as specs (one or two sentences, may reference the brainstorming skill's contract rather than restate it).
- End-of-plan summary rule: lead with failures, deviations, deferrals — not achievements.
- Execution section: no other edits.

**Tests:** none (docs-only; acceptance commands verify).

**Acceptance:**
- `grep -c 'Global Constraints' skills/planning/SKILL.md` ≥ 1; grep confirms `failures, deviations, deferrals`.
- `git diff skills/planning/SKILL.md` shows no hunks inside the `## Execution` section beyond the summary-rule line.
- Frontmatter unchanged (same check as Task 1).

**Tier:** standard

**Depends on:** nothing.

### Task 3: TDD skill — cut to operational core
- [x] Done

**Files:**
- Modify: `skills/tdd/SKILL.md` (spec §3; rewrite, not patch)

**Interface:** section names kept for external references: `The Iron Law`, `Test Infrastructure Gate`.

**Content requirements (spec §):**
- Keep-list, substance intact: trigger/floor line; Iron Law; infrastructure gate with all three branches (existing suite → use it, never parallel; approved plan with test setup → build it; ad-hoc no-harness → stop, ask one question) plus the where-not-whether closing rule; RED→verify-RED→GREEN→verify-GREEN→REFACTOR with both verifications mandatory and failure rules (passes immediately → fix test; errors → fix until failing correctly; other tests fail on GREEN → fix now; fix code, not test); good-test qualities (one behavior, clear name, shows intent, real code over mocks); bug fix = failing repro test first; final checklist; final rule; exceptions need human partner permission; pointer to `testing-anti-patterns.md` when adding mocks/test utilities.
- Cut-list: dot digraph, all code blocks, Why Order Matters, Common Rationalizations, Red Flags, worked example, When Stuck, When to Use.
- Frontmatter unchanged.

**Tests:** none (docs-only; acceptance commands verify).

**Acceptance:**
- `wc -w skills/tdd/SKILL.md` ≤ 650.
- Grep confirms all of: `Iron Law`, `Test Infrastructure Gate`, `testing-anti-patterns.md`, `fail`, and no ` ```typescript`/` ```dot` fences remain (`grep -c '^```' ` → 0 or only the Iron Law banner if kept as a fence — banner may be plain text instead).
- Frontmatter unchanged (same check as Task 1).

**Tier:** standard

**Depends on:** nothing.

### Task 4: README refresh + release
- [ ] Done

**Files:**
- Modify: `README.md` (flow paragraph + skills table: gear routing, living specs, `ideas/` input)
- Modify: `.claude-plugin/plugin.json` (version 0.2.2 → 0.3.0)

**Content requirements:** README's "The flow" section mentions the three gears and living specs in ≤3 added/changed sentences; skills table descriptions stay one line each. After commit: `claude plugin update theforge@theforge` + session restart note to the user.

**Tests:** none (docs-only; acceptance commands verify).

**Acceptance:**
- `grep -ci 'gear' README.md` ≥ 1; `grep '"version": "0.3.0"' .claude-plugin/plugin.json` → match.

**Tier:** trivial

**Depends on:** Task 1, Task 2, Task 3.
