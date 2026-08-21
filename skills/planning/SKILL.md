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

**Tier:** standard (no justification) | complex — <named decision> | trivial — <mechanical rationale>

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

Choose the **execution mode first** — inline vs. dispatch is a task-shape decision, independent of harness; the harness only determines *how the dispatch branch runs*. **Dispatch is the default; the burden of proof is on choosing inline, not dispatch** — the question is "is there a compelling reason to do this inline," never "should this be considered for dispatch." Wall-clock is never that reason: dispatch's per-task fresh-context spawn (and, on Claude, a second fresh-context reviewer per task) is often *slower* than inline now that the per-task loop is serial by design (Phase 12b) — an accepted tradeoff, not a defect, since speed was never why dispatch exists. Dispatch stands on two independent reasons, either sufficient alone:
1. **Context economy** — worker context is born, used, and discarded; inline context compounds forever across a session.
2. **Error correction against the orchestrator's own drift** — a long-running orchestrator accumulates a narrative of its own prior conclusions (designs it proposed, specs it wrote); writing a worker's brief from memory of that narrative instead of the source document risks silently propagating a misremembered or paraphrased version of a decision the orchestrator itself got right the first time. A fresh-context worker has no such narrative — pointed at the actual source (spec, plan, DECISIONS.md) rather than a restatement of it, it is structurally forced to ground itself in the authoritative text and surfaces a contradiction instead of silently inheriting one.

- **Inline, only when both hold:** (a) a later task genuinely benefits from having seen an earlier task's in-context output, **and** (b) the change is genuinely simple/small — never "dispatch machinery feels like overkill" as a standalone excuse. (b) isn't a cost-tradeoff call; it's the same gate Phase 11 built inline's no-independent-reviewer safety case on (the missing bias-check is only safe to skip because inline is confined to low-stakes work) — loosening it here would quietly undercut that decision, not just this one. The orchestrating session executes task-by-task itself: the **tdd** skill (test first, then implementation), an orchestrator **self-review** before each commit that **disposes of findings by the inline finding-handling canon below** (it does not silently resolve them), and a commit per task on a clean working tree. Inline does **not** dispatch a separate fresh-context reviewer — that is a dispatch-only concern; TDD + acceptance commands are the objective check (trivial-tier already trusts acceptance alone). Inline work runs on the session model; say so in the offer. Inline is the same act on both harnesses.
- **Dispatch, otherwise:** Dispatch shares inline's commit discipline: a **clean working tree is required at the start of a run** (a dirty tree halts before dispatch — never reset or stash a user's uncommitted work), and **each passed task is committed** before the next dispatches, so HEAD advances to a clean checkpoint after every task. That per-task commit is what makes the task's review base the prior commit (`git diff <prior commit>` is exactly this task's work) and keeps the whole-plan final-review diff from sweeping in unrelated pre-existing changes. On the Claude path the orchestrator commits each task after its review passes; on Codex the runner does it. The harness then picks the mechanism:
  - **Claude (Workflow tool available):** the orchestrator runs a **sequential**, per-task loop — implement (`agentType` at the task's tier agent, prompt carries the brief-file path from `scripts/extract-brief.py`, relevant DECISIONS.md content, the deferral rule below, and TDD discipline) → acceptance → review (a fresh subagent at the same tier as the task, covering spec compliance and code quality together — it self-serves its own `git diff <prior commit>` and the spec, then emits the reviewer verdict schema, below) → `scripts/forge_dispose.py` (the shared decision helper — see Dispatch finding-handling) → act on the decision (rework / halt / pass) → commit. All trivial-tier tasks batch into a single `forge-light` dispatch (serial within, no review — acceptance alone is verification), respecting `Depends on`. Task review is a **cold** `Agent` spawn on discovery — the first review of a task — and a `SendMessage` **resume** to that same named agent on every verification lap (rework re-review, or a repeat review of the same task), carrying only the repair delta and outstanding findings rather than the full packet; a failed `SendMessage` falls back to a fresh `Agent` with the full context, recorded as a resume fallback. Discovery stays cold on every task, deliberately — independence is the entire justification for a separate reviewer (DECISIONS 2026-07-16, 2026-07-17), and this is a qualification of the fresh-context rule, not an exception to it. The worker is cold on lap 1 and resumed the same way (`SendMessage` to its named agent, same fallback) on rework laps, with the findings-only prompt as its brief. Agent names are the handle on this path — no thread-id plumbing. Continuity is scoped to **one invocation**: after a halt, re-invocation always spawns cold, since a human may have hand-edited code in between and the persisted session's context would then be stale and misleading. **Serial by design, not a harness limitation:** substantive tasks run one at a time even though Workflow could fan first-pass implementers out in parallel. Parallelism would buy only wall-clock — no correctness, quality, or logic gain — and the per-task review base (`git diff <prior commit>`) is meaningful only on a **linear** history of clean vertical slices (commit discipline above); parallel writers break that and force worktree isolation plus ordered merge-back, whose conflicts reintroduce exactly the integration mess vertical slices exist to prevent. Fan-out stays for **read-only** work (research, review lenses, no shared mutable state) — coding writes, so it stays serial. See README.md for the fuller rationale.
  - **Codex (no Workflow tool):** read `codex-execution.md` in this skill directory — the runner is the dispatch branch, driven by the same `scripts/forge_dispose.py` decision helper called in-process.

**Reviewer verdict contract (both harnesses' dispatch reviewers):** one JSON object — `{"verdict": "pass", "coverage": […]}` or `{"verdict": "findings", "coverage": […], "findings": [{"id", "summary", "location": {"file", "lines"}, "provenance": "in-diff"|"pre-existing", "impact": "contract-breaking"|"improvement", "contract_ref": "…"|null, "convergence": "resolved"|"carried"|"new"|null, "carried_from": "…"|null, "repair_task": {…}|null}]}` — the full instruction text lives in `forge_common.REVIEW_VERDICT_INSTRUCTION` and is shared verbatim by both the Codex runner's reviewer prompt and the Claude reviewer subagent's prompt, so the two harnesses' reviewers are held to the identical contract. `coverage` is **required on every verdict, pass included**: one entry per checklist id supplied in the packet, `{"id", "status": "satisfied"|"violated"|"n/a", "evidence"}`, evidence non-empty on every entry, `n/a` requiring a reason in evidence rather than a rubber-stamped `satisfied`.

**Coverage checklist generation:** before each review, `scripts/forge_checklist.py` derives the checklist the reviewer's `coverage` array must satisfy — a task checklist for a task review, a final checklist for the final review — and it is included in the review packet. Verification laps carry the **reduced** checklist, only the items referenced by the outstanding findings' `contract_ref`, not the full one. An empty checklist is a **runner/orchestrator-level SKIP**, not an error: the review dispatches with no coverage requirement and the receipt records `coverage_skipped`; `forge_checklist.py` invoked directly still raises on an empty checklist — the tolerance belongs to the dispatch loop, not the generator. `scripts/forge_dispose.py` validates the `coverage` array (missing/unknown checklist ids, an unbacked `violated` id, empty evidence) alongside the existing verdict parse — the reviewer proposes, the runner decides. An invalid coverage array gets **one retry**, re-dispatching with the specific defect named; that retry never advances the attempt counter or convergence state — it is not a rework lap. A second invalid verdict is a contract error, consistent with the existing unparseable-verdict behavior.

`scripts/extract-brief.py` and `scripts/review-packet.py` live at the plugin root (`../../scripts/` from this skill's base directory — see "Base directory for this skill" in the loading message), not in this skill's directory.

**File-referenced briefs:** worker prompts carry a brief-file path plus the exact file paths the worker needs — never pasted plan or spec content. Generate the brief with `scripts/extract-brief.py`; its instructions bound the worker's reading explicitly: "read these N files and spec §X, nothing else."

**Thin orchestrator:** workers report back in one paragraph, not a transcript. On **Codex**, diffs and review packets travel reviewer-to-file via `scripts/review-packet.py` (Codex-path-only: a `codex exec` reviewer is a subprocess and cannot gather its own context, so the runner pre-assembles its input). On **Claude**, the reviewer subagent self-serves its own diff and spec directly (it can run `git diff` itself), and `scripts/forge_dispose.py` computes its own diff independently for verification — `review-packet.py` is not used on the Claude path, and the orchestrator never sees diff text either way, only file paths and `decision.json`. The orchestrator never pre-rates finding severity when handing review to a reviewer.

**Dispatch finding-handling (both harnesses):** the reviewer *proposes* a verdict (findings with its own provenance/impact claims, per the Reviewer verdict contract above); `scripts/forge_dispose.py` *decides* — it recomputes provenance against the actual diff (never trusting the reviewer's claim) and derives disposition by the same fix/defer/halt matrix as the inline canon below (in-diff × contract-breaking → **fix**; pre-existing × contract-breaking → **halt**; every improvement → **defer**), then applies the same convergence rule across attempts: resolved/carried id tracking, halt on **regression** (a resolved finding reappears, or acceptance goes green→red) or **stuck** (a fix finding carried two consecutive attempts), pass when no fix findings remain, backstop of **5** attempts as a seatbelt. `--autofix auto|gate` is chosen at the execution offer alongside tier routing (default `auto` = the matrix; `gate` = any finding halts — the conservative escape hatch). On **Codex** the runner calls these functions in-process; on **Claude** the orchestrator calls the identical logic via the CLI (`forge_dispose.py --verdict --base --state --attempt --acceptance-ok --autofix` → `decision.json`) after each reviewer verdict, persisting `decision.state` for the next attempt. One tested implementation of the decision, two callers — this is what makes the dispatch rework loop enforcement real rather than prose on both harnesses. The end-of-plan summary reports review-cycle counts per task on either path.

**Inline finding-handling (both harnesses):** the orchestrator's self-review classifies each finding on two axes — **provenance** (in-diff = this plan's changed lines, vs. pre-existing) × **contract impact** (contract-breaking vs. improvement) — and acts by quadrant: in-diff × contract-breaking → **fix** (self-fix, then re-review — the only auto-fix cell); in-diff × improvement → **defer strict** (no gold-plating our own new code; DEFERRALS entry); pre-existing × improvement → **defer** (harmless, logged); pre-existing × contract-breaking → **halt** — draft a disposition (a repair-task sketch or a fix/defer rationale) and surface it to the user for the call, nothing in this quadrant auto-applied. Provenance is judged against the actual diff line ranges, not assumed. Contract-breaking requires a **named acceptance criterion**; absent one the finding downgrades to a deferral, keeping taste out of both the fix and the halt. Fix-quadrant rework **converges** rather than counting: after each attempt the self-review labels its remaining fix findings **resolved/carried/new** against the prior attempt (the session holds the prior attempt in context — no re-review packet needed), and halts on a **regression** (a resolved finding reappears, or an acceptance command goes green→red) or **stuck** (a fix finding carried across two consecutive attempts); it **passes** when no fix findings remain, with a **backstop of 5** attempts as a seatbelt. Any halt — regression, stuck, backstop, or a halt-quadrant finding — surfaces to the user with the outstanding findings and the drafted disposition. Inline stays self-reviewed; this gates *how* the self-review disposes of findings, it does not add a reviewer. The Codex inline path follows this canon identically (`codex-execution.md`). Inline adopts the **coverage half** of the reviewer verdict contract only — the self-review generates the checklist via `scripts/forge_checklist.py` and states `coverage` by the same discipline as a dispatch reviewer, since an orchestrator self-review is equally capable of surfacing one finding at a time. The continuity half (resume) is moot for inline: it never discarded its context to begin with.

**Proportional review:** trivial-tier tasks skip subagent review entirely; passing their acceptance commands is verification enough. Standard and complex tasks get the combined review — on the **dispatch** path the fresh-context subagent above, whose verdict `forge_dispose` disposes of; on the **inline** path the orchestrator's own self-review (no separate reviewer), which *disposes* of findings by the inline finding-handling canon rather than resolving them all.

**Final review:** once every task in a multi-task plan passes, run one broad review against the whole-plan diff and spec — integration issues a per-task review can't see. On the **dispatch** path it runs with fresh context, at the tier agent matching the **plan's highest task tier** (an all-standard plan gets a standard-tier final review, not a pinned `forge:forge-deep`), and reuses the **same** `forge_dispose` loop as a per-task review — diff base = run-start HEAD, and a rework fix that passes lands as a single `fix: final-review` commit. On the **inline** path it is the orchestrator's own broad self-review, disposing of its findings by the inline finding-handling canon (halt-quadrant findings surface to the user; fix-quadrant findings converge) rather than silently resolving them. This runs alongside the full-test-suite close-out below, not instead of it. (Codex's runner additionally runs a terminal doc-sync stage after final review passes; the Claude path does not yet — folded into the coming doc revamp, DEFERRALS 2026-07-17.)

**Deferral rule:** implementers may skip **non-spec scope** (nice-to-haves, refactors, edge polish) with a `docs/forge/DEFERRALS.md` entry explaining why (formats: project-memory skill). Spec'd requirements are never silently deferred — flag them at the review gate. The **defer**-disposition findings collected across a run (both inline and dispatch, either harness) are appended to `docs/forge/DEFERRALS.md` as a reviewed batch once the run completes clean, alongside author-initiated deferrals. List all new deferrals in the end-of-plan summary.

Plans written before this fork (embedded implementation code, subagent-driven-development headers) execute fine under this section: treat embedded code as a suggestion to re-derive via TDD, not text to paste.

When all tasks are done: run the full test suite, mark the roadmap phase `done` if a roadmap exists, then follow the branch-finishing preferences in CLAUDE.md. The end-of-plan summary leads with failures, deviations, deferrals — not achievements. Recurring "wouldn't have done it that way" review calls become written conventions — CLAUDE.md, or DECISIONS.md when architectural.
