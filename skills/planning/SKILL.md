---
name: planning
description: Use after a spec is approved, or for any multi-step implementation task, to write an implementation plan — and when executing an existing plan from docs/forge/plans/.
---

# Planning

**Trigger:** a spec was just approved (handoff from brainstorming); the user asks to plan a multi-step task; or you're executing an existing plan from `docs/forge/plans/`. **Don't trigger:** non-trivial creative work with no spec yet — brainstorm first.

A plan specifies **what and where — never implementation code**. It locks in design decisions: files touched, interfaces and signatures, the test cases to write, acceptance criteria, ordering. The code itself is written once, during execution, with compiler and test feedback. Writing it earlier in the plan means writing it twice and getting it wrong the first time.

Plan prose is agent-consumed, not narrative: apply the brainstorming skill's sentence test — every sentence carries a requirement, contract, or decision, else cut.

**Save to:** `docs/forge/plans/YYYY-MM-DD-<feature>.md` and commit. (User/project preferences for plan location override this default.)

**Before writing:** read `docs/forge/DECISIONS.md` if present — the plan must not contradict logged decisions; surface conflicts to the user. Code in the plan follows the same contract rule as specs: signatures, schemas, and wire formats are decisions and belong; bodies, test code, and boilerplate are solutions and don't.

## Scope

One plan per subsystem. Each plan must produce working, testable software on its own. If the spec covers multiple independent subsystems, split into separate plans.

## File structure first

Before defining tasks, map which files will be created or modified and what each is responsible for — one clear responsibility per file, smaller focused files over large ones, split by responsibility not technical layer. In existing codebases follow established patterns. This map drives the task decomposition. Minimize dependency chains: wall-clock time is the critical path, not the task count — prefer decompositions that share interfaces over ones that impose sequence.

## Plan header

```markdown
# [Feature] Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** [one sentence]
**Architecture:** [2–3 sentences]
**Tech stack:** [key technologies/libraries]
**Global Constraints:** [version floors, dependency limits, naming rules]
```

Omit `**Global Constraints:**` entirely when the plan has none — never an empty block.

## Task structure

Task headings are exactly `### Task N:` — three `#`, the number, a colon; numbers are unique. The extraction scripts (`extract-brief.py`, `review-packet.py`) key on this literal form; a `## Task N:` at the wrong level or a duplicated number fails brief generation. A task block runs to the next h1–h3 heading (h4+ is intra-task structure); `**Goal:**`/`**Global Constraints:**` are header fields and live before the first task heading.

```markdown
### Task N: [Component]
- [ ] Done

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py` (what changes)
- Test: `tests/exact/path/to/test.py`

**Spec:** [section heading], [section heading]

**Interface:** signatures, types, and names this task introduces or changes —
declarations only, no bodies. Later tasks must use these exact names.

**Tests:** the list of test cases, by behavior — "rejects empty email",
"retries 3 times then throws". Descriptions, not code.

**Acceptance:** the commands to run and what must pass. An environment-gated skip is not a pass — an acceptance command must assert required infrastructure is present, or make the skip exit non-zero.

**Tier:** `standard` (no justification) | `complex — <named decision>` | `trivial — <mechanical rationale>`

**Depends on:** Task M, or nothing.
```

No placeholders at this level: never "TBD", "handle edge cases", or "add validation" — *name* the edge cases and the validation rules. The line is: name **what** to handle; don't write **how**.

**Spec:** is optional — the spec sections this task's worker needs, named by heading text (unique prefix acceptable; matched case-insensitively at extraction time). Omit when the task needs no spec context. It drives mechanical brief extraction at execution (`scripts/extract-brief.py`). Keep it to a **single line of bare, comma-separated heading names** — no parentheticals, no `;`, no wrapping onto a second line, and one spec file per task (`--spec` takes one). The plan's `**Goal:**` is likewise a single non-empty line and is required. Wrapped or parenthetical `**Spec:**`/`**Goal:**` lines fail brief generation.

**Standard is the default floor** — a task sits there unless evidence moves it, and the burden of proof is on *leaving* standard in either direction. Moving **up** to complex requires a **named** design decision or cross-cutting invariant that standard demonstrably cannot resolve — file count, task category ("code implementation"), "touches core", and "feels complex" name a shape, not a decision, and are **not** evidence. Moving **down** to trivial requires demonstrated mechanicalness — no design content (rename, single config value, one field through one call site); if mechanicalness can't be shown, it is not trivial. A task may touch many files and carry a real test path and still be standard — that alone justifies nothing either way. The tier drives model routing and review depth at execution.

The `Tier:` field carries justification off the floor:

```
Tier: standard                          # floor — no justification
Tier: complex — <the named decision>    # e.g. "reconciles two conflicting retry semantics no single call site owns"
Tier: trivial — <mechanical rationale>  # e.g. "single enum value, one call site, no logic"
```

`standard` takes no justification (trailing text is ignored); `complex` and `trivial` require a non-empty justification after `— `. The justification's *presence* is mechanically checkable; its *quality* (a real decision vs. a rejected shape) is enforced at authoring by the self-review below, never by a runner script.

## Self-review

Check the finished plan against the spec with fresh eyes: every task heading is `### Task N:` (three `#`, colon — the extraction scripts require it, and one count of `### Task N:` per task should equal the task count); every spec requirement maps to a task (add tasks for gaps); nothing contradicts DECISIONS.md; names and signatures are consistent across tasks; dependency order is sound; no placeholder language; every non-standard tier carries a valid, non-categorical justification — a named decision for complex, a demonstrated mechanical rationale for trivial — else push it back to standard. Fix inline and move on. Log any design decisions the plan locked in to DECISIONS.md.

## Execution

Each task's tier routes to a shipped worker agent. Routing is absolute — the session's model and effort settings never apply to subagent execution:

| Tier | Agent | Profile |
|---|---|---|
| trivial | `forge:forge-light` | haiku |
| standard | `forge:forge-standard` | sonnet · medium |
| complex | `forge:forge-deep` | opus · high |

These are each provider's stated default, not escalation targets: the stronger settings removed as defaults (xhigh/max) aren't deleted — they remain what a human may bump a halted task to (see rework guardrails), never a default.

Offer the user the choice, with a recommendation by size, and **disclose the resolved routing** in the offer (e.g. "4 standard → forge-standard, 1 complex → forge-deep") plus **each non-standard task's justification** from its `Tier:` line, so an up-tier is visible and overridable before anything runs:

Choose the **execution mode first** — inline vs. dispatch is a task-shape decision, independent of harness; the harness only determines *how the dispatch branch runs*.

- **Inline when accumulated context is an asset:** few tasks, later tasks build on seeing earlier work's output, and the change is simple enough that dispatch machinery is overkill. The orchestrating session executes task-by-task itself: the **tdd** skill (test first, then implementation), an orchestrator **self-review** before each commit that **disposes of findings by the inline finding-handling canon below** (it does not silently resolve them), and a commit per task on a clean working tree. Inline does **not** dispatch a separate fresh-context reviewer — that is a dispatch-only concern; TDD + acceptance commands are the objective check (trivial-tier already trusts acceptance alone). Inline work runs on the session model; say so in the offer. Inline is the same act on both harnesses.
- **Dispatch otherwise — even for serial phases:** worker context is born, used, and discarded; inline context compounds forever. The harness then picks the mechanism:
  - **Claude (Workflow tool available):** a Workflow script spawns one worker per task as the task's tier agent via `agentType` — its prompt carries the brief-file path (from `scripts/extract-brief.py`), relevant DECISIONS.md content, the deferral rule below, and TDD discipline — pipelined so independent tasks overlap and `Depends on` is respected. All trivial-tier tasks batch into a single `forge-light` dispatch, respecting `Depends on` among them. After each standard or complex task, **one combined review** at the task's own tier — a fresh subagent dispatched at the same tier agent as the task, covering spec compliance and code quality together. The reviewer's value is fresh context (an independent pass that hasn't rationalized the work's own defects), not a stronger model: there is no escalation to a stronger reviewer when the first pass finds issues. A finding is not a failure — it triggers same-tier rework by the implementer and re-review at the same tier with fresh context, looping until clean or the rework cap is hit.
  - **Codex (no Workflow tool):** read `codex-execution.md` in this skill directory — the runner is the dispatch branch.

`scripts/extract-brief.py` and `scripts/review-packet.py` live at the plugin root (`../../scripts/` from this skill's base directory — see "Base directory for this skill" in the loading message), not in this skill's directory.

**File-referenced briefs:** worker prompts carry a brief-file path plus the exact file paths the worker needs — never pasted plan or spec content. Generate the brief with `scripts/extract-brief.py`; its instructions bound the worker's reading explicitly: "read these N files and spec §X, nothing else."

**Thin orchestrator:** workers report back in one paragraph, not a transcript. Diffs and review packets travel reviewer-to-file via `scripts/review-packet.py`, never through orchestrating context. The orchestrator never pre-rates finding severity when handing a diff to a reviewer.

**Rework guardrails (dispatch path):** dispatch review loops cap at 2 iterations, then escalate to the user with the outstanding findings rather than looping further. (Inline's stop condition is the convergence model in the canon below, not this cap.) The end-of-plan summary reports review-cycle counts per task on either path.

**Inline finding-handling (both harnesses):** the orchestrator's self-review classifies each finding on two axes — **provenance** (in-diff = this plan's changed lines, vs. pre-existing) × **contract impact** (contract-breaking vs. improvement) — and acts by quadrant: in-diff × contract-breaking → **fix** (self-fix, then re-review — the only auto-fix cell); in-diff × improvement → **defer strict** (no gold-plating our own new code; DEFERRALS entry); pre-existing × improvement → **defer** (harmless, logged); pre-existing × contract-breaking → **halt** — draft a disposition (a repair-task sketch or a fix/defer rationale) and surface it to the user for the call, nothing in this quadrant auto-applied. Provenance is judged against the actual diff line ranges, not assumed. Contract-breaking requires a **named acceptance criterion**; absent one the finding downgrades to a deferral, keeping taste out of both the fix and the halt. Fix-quadrant rework **converges** rather than counting: after each attempt the self-review labels its remaining fix findings **resolved/carried/new** against the prior attempt (the session holds the prior attempt in context — no re-review packet needed), and halts on a **regression** (a resolved finding reappears, or an acceptance command goes green→red) or **stuck** (a fix finding carried across two consecutive attempts); it **passes** when no fix findings remain, with a **backstop of 5** attempts as a seatbelt. Any halt — regression, stuck, backstop, or a halt-quadrant finding — surfaces to the user with the outstanding findings and the drafted disposition. Inline stays self-reviewed; this gates *how* the self-review disposes of findings, it does not add a reviewer. The Codex inline path follows this canon identically (`codex-execution.md`).

**Proportional review:** trivial-tier tasks skip subagent review entirely; passing their acceptance commands is verification enough. Standard and complex tasks get the combined review — on the **dispatch** path the fresh-context subagent above; on the **inline** path the orchestrator's own self-review (no separate reviewer), which *disposes* of findings by the inline finding-handling canon rather than resolving them all.

**Final review:** once every task in a multi-task plan passes, run one broad review against the whole-plan diff and spec — integration issues a per-task review can't see. On the **dispatch** path it runs with fresh context, at the tier agent matching the **plan's highest task tier** (an all-standard plan gets a standard-tier final review, not a pinned `forge:forge-deep`). On the **inline** path it is the orchestrator's own broad self-review, disposing of its findings by the inline finding-handling canon (halt-quadrant findings surface to the user; fix-quadrant findings converge) rather than silently resolving them. This runs alongside the full-test-suite close-out below, not instead of it.

**Deferral rule:** implementers may skip **non-spec scope** (nice-to-haves, refactors, edge polish) with a `docs/forge/DEFERRALS.md` entry explaining why (formats: project-memory skill). Spec'd requirements are never silently deferred — flag them at the review gate. List all new deferrals in the end-of-plan summary.

Plans written before this fork (embedded implementation code, subagent-driven-development headers) execute fine under this section: treat embedded code as a suggestion to re-derive via TDD, not text to paste.

When all tasks are done: run the full test suite, mark the roadmap phase `done` if a roadmap exists, then follow the branch-finishing preferences in CLAUDE.md. The end-of-plan summary leads with failures, deviations, deferrals — not achievements. Recurring "wouldn't have done it that way" review calls become written conventions — CLAUDE.md, or DECISIONS.md when architectural.
