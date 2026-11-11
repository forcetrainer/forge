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
5. After execution: amend the owning spec in place, changelog line (step 6 below) that states the new rule and never quotes the retired one — the changelog is matchable prose; commit the amendment with the change.

**Gear 3** — full flow below.

Tripwires, both mandatory:
- Can't name the owning spec → gear 3.
- Design stops fitting in a paragraph mid-conversation → escalate to gear 3; never stretch the conversational gate.

## Flow

Input is unconstrained: conversation, pasted documents, answers to questions. Spec authoring (steps 1, 3, 5, 7, 8) covers a new spec and an amendment written ahead of a build. Outside it: a gear 2 change, and an amendment made in answer to a review finding (spec review finding, plan review `spec-defect`) — that amendment owes no draft, checker run or digest, keeps the dependency record current, and re-enters spec review with it. The conditions a spec must meet are stated once, in `@design-anti-patterns.md`; this skill restates none.

1. **Explore context** — current files, docs, recent commits, and the open GitHub issue list (plus the owning epic, when the work belongs to a program). Constraints (`docs/forge/constraints.md`) are supplied at session start — flag conflicts with them to the user, don't design around them silently. If the idea comes from `docs/forge/ideas/` or a path handed at kickoff, and it's graduating to a build (not free-form ideation): read it, confirm your understanding, flag constraint conflicts, skip questions it already answers, go straight to approaches. Read the existing code the design will rely on before designing against it; when it depends on more than one part of the code, run read-only scouts in parallel, each returning facts with file and line — their number is your call. Every piece of existing code the design relies on gets a dependency record entry (step 5).
2. **Scope check** — if the request holds pieces that each work and ship on their own, decompose before refining details: identify the pieces, how they relate, what order to build them. Record the decomposition through `add-program` + `add-phase` (formats: project-memory skill). Then brainstorm the first sub-project; each gets its own design → plan → implementation cycle. Touching several specs is not decomposition: a feature that amends the semantic layer, API and UI specs together is one sub-project, amending each owning spec in place, with one plan across them.
3. **Clarify** — work the material toward the conditions. Ask the user only where a condition is unmet and reading the code cannot meet it; each question carries a proposed default. No fixed batch size; single-question only when the answer forks the design. Focus on purpose, constraints, and success criteria. A behaviour the user leaves to the implementer is written where it lives as `<behaviour>: not constrained`.
4. **Propose 2–3 approaches** with trade-offs. Lead with your recommendation and why. Skip what the user has already decided.
5. **Present the design** — before approval, write what the spec will say (for an amendment, the amended sections as they will read) to a draft file outside the repository's tracked files, and the dependency record beside it. Nothing is written to a spec before approval. Record shape, kept with the draft, never in the spec, never committed:

   ```
   {"dependencies": [{"symbol", "file", "behavior"}], "waiver": "<reason>" | null}
   ```

   Empty `dependencies` is legal only with a non-null `waiver`. Run the checker (below), then approve the design from one **decision digest** derived from the draft — what was chosen, what it forecloses, what is assumed. Sections with a check-in after each replace the single digest when the design spans several specs, is too large to judge in one read, or the user asks. Cover architecture, components, data flow, error handling, testing. After approval the draft's text goes into the spec (step 6).

   **Checker.** A separate agent, never the author, tests the draft against the conditions. Material: one file the author assembles, holding the draft, the dependency record, every document the user supplied, and the path of the conditions file. It reads the code and returns, per unmet condition, the condition, the place in the draft and the evidence, or states that none is unmet; the author reads the return, no script validates it. It runs at least once, in the foreground, before the design is presented, on both harnesses; it may also run in the background where the harness supports it. An unmet condition that is a fact about the code sends the author to read; one that is a judgment becomes a question to the user. A condition still unmet at presentation is stated in the digest. The checker is not the gate — spec review is.

   Start it as the spec reviewer starts (step 8), with its own prompt: on Claude a spawned agent at the standard tier whose prompt is the material file's path; on Codex the document review command under Document reviews on Codex in the planning skill's `codex-execution.md`, used as written except for its prompt (the checker prompt given beside it). When the checker cannot be started, stop and tell the user — never substitute your own check, never retry with a different model, never present the design without the run.
6. **Write the spec.** Specs are living per-system documents, not dated snapshots: a change that alters what a spec asserts amends the owning spec in place, never a new file — gear 3 reaches this step only when no owning spec exists yet, i.e. a genuinely new system. In that case, create `docs/forge/specs/<system>.md` (kebab id, equal to the filename stem) and commit it. (User/project preferences for spec location override this default.) Code appears in a spec only as **contract, never solution**: interface signatures (no bodies), data/wire-format examples, algorithms that are themselves the requirement, desired call-site ergonomics. If deleting a code block would lose only typing time — not a decision — cut it. On first amendment, add a `## Changelog` section at the spec's end; one dated line per amendment, e.g. `2026-07-02: sort by division, not date (commit abc123)`. Spec style is telegraphic — bullets, contracts, constraints. Sentence test: carries a requirement, contract, or decision, else cut. No narrative preamble, no restated codebase context, no justification prose — the why lives in the PR body. Guard: trim toward decision-relevant, not short; edge-case naming, interfaces, and acceptance criteria stay.
7. **Self-review the spec** with fresh eyes: placeholders ("TBD", vague requirements), internal contradictions, scope (focused enough for one plan?), ambiguity (any requirement readable two ways? pick one, make it explicit). Fix inline, no re-review. Self-review is not the gate.
8. **Spec review** — a cold reviewer validates the spec against the codebase. Generate the packet: `scripts/forge_docreview.py --spec <path> --record <path> --out <path>`; the record is the dependency record from step 5, and no packet is built without one. It first lints that spec (frontmatter `system` equal to the filename stem, `## Changelog` present) and validates the record (each `file` tracked, each `symbol` present in it); a lint or record defect is the author's, not the reviewer's — fix it and regenerate. The reviewer is a **fresh** agent whose prompt is the packet file's path, and validates every record entry against the code. Capture its verdict JSON to a file, then run `scripts/forge_docreview.py --spec <path> --record <path> --verdict <file>` to validate and dispose it. A non-zero exit is an invalid verdict: one retry naming the specific defect, re-invoked by the caller, then a contract error. Only that retry resumes the reviewer that emitted the invalid verdict (`SendMessage` to it, prompt is the defect list alone); a failed resume falls back to a fresh reviewer given the packet plus the defects. Read the decision the verdict command writes: `pass` true ends the review; `amend` and `surface` findings are amended (surfaced ones with the user's call); `plan` findings are plan-scale — never amend them into the spec, append them to a carried-cases file kept beside the dependency record. A review-driven amendment keeps the record current and re-enters here with it. **Rounds:** every re-review runs on a fresh reviewer. Rounds 1 and 2 are whole-document. From round 3 add `--prior-packet <the previous round's packet>` to both commands: the script scopes the round to the sections amended since that packet. **Amending between rounds:** before a re-review, find every statement of the amended rule in every spec and change them together; a section that drew findings in two consecutive rounds is rewritten whole, as a state table where it describes states, never patched a third time. **Round stop:** a spec not passed after round 3 stops — show the user the findings per round and what is open, and let them choose another round, handing the open findings to planning as carried cases, or marking them not constrained. Never run a fourth round unasked; every later round stops the same way. On Codex, start the reviewer as the planning skill's `codex-execution.md` says, under Document reviews on Codex. On either harness, if a cold reviewer cannot be started, stop and tell the user — never substitute a self-review. Hunting list: `@design-anti-patterns.md`, never inlined here. Planning does not begin until spec review passes or the user ends it at the round stop.
9. **Close out** — present a three-way reconciliation against the open issue list and take approval on it as a set: **addresses** (issues this spec covers, closed by planning at completion), **obsoletes** (issues the design makes moot — the user closes them, or declines), **discovers** (scope found while designing — filed only if the user accepts it). Propose; never close an issue unattended — a wrongly closed issue is invisible afterward, so the gate is cheaper than the recovery. Show the carried cases once, as a list: the user may promote any to an amendment or mark it not constrained; the rest go to planning. Then tell the user: "spec written to `<path>` and committed — flag changes, otherwise proceeding to planning." The decision digest approval in step 5 was the design approval gate; no mandatory re-review of the file.
10. **Hand off to the planning skill**, naming the carried-cases file when there is one. That is the only next step — no implementation skills.

## Design principles

- **YAGNI ruthlessly** — strip unnecessary features from every design.
- Break the system into units with one clear purpose, well-defined interfaces, and independent testability. For each unit you should be able to say what it does, how it's used, and what it depends on — without reading its internals.
- In existing codebases: explore the structure first and follow established patterns. Include targeted improvements where existing problems affect the work (an overgrown file, tangled boundaries); skip unrelated refactoring.

## Visual companion

When a question is genuinely visual — mockups, wireframes, layout comparisons, architecture diagrams — show it in the browser companion rather than describing it (standing user preference: use it without asking). Decide per question: content the user understands better by *seeing* goes to the browser; requirements, trade-off lists, and conceptual choices stay in the terminal. When the user picks a direction, don't start building — ask whether they want to refine it further or it's good enough to fold into the design; their answer decides. Before first use in a session, read `visual-companion.md` in this skill directory.
