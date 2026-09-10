---
name: brainstorming
description: Use before creative work — new features, new components, behavior changes. Not for trivial mechanical edits, bug fixes with a clear repro, or questions. Turns an idea into a validated design spec through collaborative dialogue, before any planning or implementation.
---

# Brainstorming

**Trigger:** creative work without an approved spec — new features, components, behavior changes. **Don't trigger:** bug fixes with a clear repro, questions, trivial mechanical edits.

Turn an idea into a validated design through dialogue, then a written spec the user approves before planning begins.

Don't write code, scaffold a project, or take any implementation action until the user has approved a design. "Simple" projects still get one — the design can be a few sentences, but it gets presented and approved. Simple is where unexamined assumptions waste the most work.

## Gear check

Routing test: creates new architecture → gear 3 (full flow below); operates within existing architecture → gear 2. Size is secondary — a 10-line change adding a dependency is gear 3; a 100-line change filling spec-implied behavior is gear 2.

**Gear 1** — trivial/mechanical/content: frontmatter don't-trigger list above, unchanged.

**Gear 2** — delta to an already-spec'd system:
1. Name the owning spec in `docs/forge/specs/`.
2. Present the design in conversation, one paragraph max.
3. One approval gate.
4. Hand off directly to the tdd skill — no spec file, no plan file, planning skill skipped.
5. After execution: amend the owning spec in place, changelog line (step 6 below); commit the amendment with the change.

**Gear 3** — full flow below.

Tripwires, both mandatory:
- Can't name the owning spec → gear 3.
- Design stops fitting in a paragraph mid-conversation → escalate to gear 3; never stretch the conversational gate.

## Flow

1. **Explore context** — current files, docs, recent commits, and the open GitHub issue list (plus the owning epic, when the work belongs to a program). Constraints (`docs/forge/constraints.md`) are supplied at session start — flag conflicts with them to the user, don't design around them silently. If the idea comes from `docs/forge/ideas/` or a path handed at kickoff, and it's graduating to a build (not free-form ideation): read it, confirm your understanding, flag constraint conflicts, skip questions it already answers, go straight to approaches.
2. **Scope check** — if the request spans multiple independent subsystems, decompose before refining details: identify the pieces, how they relate, what order to build them. Record the decomposition through `add-program` + `add-phase` (formats: project-memory skill). Then brainstorm the first sub-project; each gets its own spec → plan → implementation cycle.
3. **Clarify** — batch 2–3 independent questions per turn, multiple choice preferred; single-question only when the answer forks the design. Focus on purpose, constraints, and success criteria.
4. **Propose 2–3 approaches** with trade-offs. Lead with your recommendation and why.
5. **Present the design** in sections scaled to their complexity (a few sentences when straightforward, ~200–300 words when nuanced); check in after each section with decision digests — what was chosen, what it forecloses, what's assumed. Cover architecture, components, data flow, error handling, testing.
6. **Write the spec.** Specs are living per-system documents, not dated snapshots: a change that alters what a spec asserts amends the owning spec in place, never a new file — gear 3 reaches this step only when no owning spec exists yet, i.e. a genuinely new system. In that case, create `docs/forge/specs/<system>.md` (kebab id, equal to the filename stem) and commit it. (User/project preferences for spec location override this default.) Code appears in a spec only as **contract, never solution**: interface signatures (no bodies), data/wire-format examples, algorithms that are themselves the requirement, desired call-site ergonomics. If deleting a code block would lose only typing time — not a decision — cut it. On first amendment, add a `## Changelog` section at the spec's end; one dated line per amendment, e.g. `2026-07-02: sort by division, not date (commit abc123)`. Spec style is telegraphic — bullets, contracts, constraints. Sentence test: carries a requirement, contract, or decision, else cut. No narrative preamble, no restated codebase context, no justification prose — the why lives in the PR body. Guard: trim toward decision-relevant, not short; edge-case naming, interfaces, and acceptance criteria stay.
7. **Self-review the spec** with fresh eyes: placeholders ("TBD", vague requirements), internal contradictions, scope (focused enough for one plan?), ambiguity (any requirement readable two ways? pick one, make it explicit). Fix inline, no re-review.
8. **Spec review** — a cold reviewer validates the spec against the codebase. Generate the packet: `scripts/forge_docreview.py --spec <path> --out <path>`. The reviewer is a **fresh** agent — discovery is cold; a re-review after an amendment is a verification lap and may resume — whose prompt is the packet file's path. Capture its verdict JSON to a file, then run `scripts/forge_docreview.py --spec <path> --verdict <file>` to validate and dispose it. A non-zero exit is an invalid verdict: one retry naming the specific defect, re-invoked by the caller, then a contract error. An amendment re-enters at the whole document, not a narrower slice of it. Hunting list: `@design-anti-patterns.md`, never inlined here. Planning does not begin until spec review passes.
9. **Close out** — present a three-way reconciliation against the open issue list and take approval on it as a set: **addresses** (issues this spec covers, closed by planning at completion), **obsoletes** (issues the design makes moot — the user closes them, or declines), **discovers** (scope found while designing — filed only if the user accepts it). Propose; never close an issue unattended — a wrongly closed issue is invisible afterward, so the gate is cheaper than the recovery. Then tell the user: "spec written to `<path>` and committed — flag changes, otherwise proceeding to planning." The sectioned walkthrough in step 5 was the design approval gate; no mandatory re-review of the file.
10. **Hand off to the planning skill.** That is the only next step — no implementation skills.

## Design principles

- **YAGNI ruthlessly** — strip unnecessary features from every design.
- Break the system into units with one clear purpose, well-defined interfaces, and independent testability. For each unit you should be able to say what it does, how it's used, and what it depends on — without reading its internals.
- In existing codebases: explore the structure first and follow established patterns. Include targeted improvements where existing problems affect the work (an overgrown file, tangled boundaries); skip unrelated refactoring.

## Visual companion

When a question is genuinely visual — mockups, wireframes, layout comparisons, architecture diagrams — show it in the browser companion rather than describing it (standing user preference: use it without asking). Decide per question: content the user understands better by *seeing* goes to the browser; requirements, trade-off lists, and conceptual choices stay in the terminal. When the user picks a direction, don't start building — ask whether they want to refine it further or it's good enough to fold into the design; their answer decides. Before first use in a session, read `visual-companion.md` in this skill directory.
