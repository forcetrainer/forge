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

Task headings are exactly `### Task N:` — three `#`, the number, a colon. The extraction scripts (`extract-brief.py`, `review-packet.py`) key on this literal form; a `## Task N:` at the wrong level fails brief generation.

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

**Acceptance:** the commands to run and what must pass.

**Tier:** trivial | standard | complex.

**Depends on:** Task M, or nothing.
```

No placeholders at this level: never "TBD", "handle edge cases", or "add validation" — *name* the edge cases and the validation rules. The line is: name **what** to handle; don't write **how**.

**Spec:** is optional — the spec sections this task's worker needs, named by heading text (unique prefix acceptable; matched case-insensitively at extraction time). Omit when the task needs no spec context. It drives mechanical brief extraction at execution (`scripts/extract-brief.py`).

**Tier** is judged by what the task demands, not its category: a mechanical edit with no design content is trivial; a well-specified change with a clear test path is standard; novel design, cross-file impact, or ambiguous spec territory is complex. A "code implementation" task that's really one field threaded through one call site is trivial, whatever its name. The tier drives model routing and review depth at execution. When interfaces and test cases are fully enumerated, prefer the lower tier — the worker has less to decide, not less to verify.

## Self-review

Check the finished plan against the spec with fresh eyes: every task heading is `### Task N:` (three `#`, colon — the extraction scripts require it, and one count of `### Task N:` per task should equal the task count); every spec requirement maps to a task (add tasks for gaps); nothing contradicts DECISIONS.md; names and signatures are consistent across tasks; dependency order is sound; no placeholder language; each tier holds in both directions — would a smaller model handle this cleanly, and does any "trivial" task hide a design decision? Fix inline and move on. Log any design decisions the plan locked in to DECISIONS.md.

## Execution

Each task's tier routes to a shipped worker agent. Routing is absolute — the session's model and effort settings never apply to subagent execution:

| Tier | Agent | Profile |
|---|---|---|
| trivial | `forge:forge-light` | haiku |
| standard | `forge:forge-standard` | sonnet · high effort |
| complex | `forge:forge-deep` | opus · xhigh effort |

Offer the user the choice, with a recommendation by size, and **disclose the resolved routing** in the offer (e.g. "4 standard → forge-standard, 1 complex → forge-deep") so tiers can be overridden before anything runs:

- **Workflow tool unavailable:** read `codex-execution.md` in this skill directory.
- **Inline when accumulated context is an asset:** few tasks, and later tasks build on seeing earlier work's output. Execute task-by-task in this session using the **tdd** skill — inline work runs on the session model; say so in the offer. Check off steps, commit per task.
- **Dispatch otherwise — even for serial phases:** worker context is born, used, and discarded; inline context compounds forever. A Workflow script spawns one worker per task as the task's tier agent via `agentType` — its prompt carries the brief-file path (from `scripts/extract-brief.py`), relevant DECISIONS.md content, the deferral rule below, and TDD discipline — pipelined so independent tasks overlap and `Depends on` is respected. All trivial-tier tasks batch into a single `forge-light` dispatch, respecting `Depends on` among them. After each standard or complex task, **one combined review** by `forge:forge-standard` covering spec compliance and code quality together; dispatch a second reviewer on `forge:forge-deep` only if the first finds substantive issues, and loop the implementer until clean.

`scripts/extract-brief.py` and `scripts/review-packet.py` live at the plugin root (`../../scripts/` from this skill's base directory — see "Base directory for this skill" in the loading message), not in this skill's directory.

**File-referenced briefs:** worker prompts carry a brief-file path plus the exact file paths the worker needs — never pasted plan or spec content. Generate the brief with `scripts/extract-brief.py`; its instructions bound the worker's reading explicitly: "read these N files and spec §X, nothing else."

**Thin orchestrator:** workers report back in one paragraph, not a transcript. Diffs and review packets travel reviewer-to-file via `scripts/review-packet.py`, never through orchestrating context. The orchestrator never pre-rates finding severity when handing a diff to a reviewer.

**Rework guardrails:** review loops cap at 2 iterations, then escalate to the user with the outstanding findings rather than looping further. The end-of-plan summary reports review-cycle counts per task.

**Proportional review:** trivial-tier tasks skip subagent review entirely; passing their acceptance commands is verification enough. Standard and complex tasks get the combined review.

**Final review:** once every task in a multi-task plan passes, run one broad review on `forge:forge-deep` against the whole-plan diff and spec — integration issues a per-task review can't see. This runs alongside the full-test-suite close-out below, not instead of it.

**Deferral rule:** implementers may skip **non-spec scope** (nice-to-haves, refactors, edge polish) with a `docs/forge/DEFERRALS.md` entry explaining why (formats: project-memory skill). Spec'd requirements are never silently deferred — flag them at the review gate. List all new deferrals in the end-of-plan summary.

Plans written before this fork (embedded implementation code, subagent-driven-development headers) execute fine under this section: treat embedded code as a suggestion to re-derive via TDD, not text to paste.

When all tasks are done: run the full test suite, mark the roadmap phase `done` if a roadmap exists, then follow the branch-finishing preferences in CLAUDE.md. The end-of-plan summary leads with failures, deviations, deferrals — not achievements. Recurring "wouldn't have done it that way" review calls become written conventions — CLAUDE.md, or DECISIONS.md when architectural.
