---
system: pipeline
supersedes:
  - archive/specs/2026-07-02-phase1-pipeline-skill-edits-design.md
  - archive/specs/2026-07-02-phase2-execution-efficiency-design.md
  - archive/specs/2026-09-05-living-specs-design.md
---

# Pipeline

The brainstorm → plan → implement pipeline: its skill contracts, the spec and plan
document grammars, and the two scripts that assemble worker and reviewer input.

## Gear routing

Routing test, in `skills/brainstorming/SKILL.md`: a change that creates new
architecture → gear 3; a change that operates within existing architecture → gear 2.
Size is secondary — a 10-line change adding a dependency is gear 3; a 100-line change
filling spec-implied behavior is gear 2.

- **Gear 1** — trivial/mechanical/content. The skill frontmatter's don't-trigger list.
- **Gear 2** — delta to an already-spec'd system: name the owning spec in
  `docs/forge/specs/`; present the design in conversation, one paragraph max; one
  approval gate; hand off directly to the tdd skill — no spec file, no plan file,
  planning skill skipped; after execution amend the owning spec in place with a
  changelog line, committed with the change.
- **Gear 3** — the full brainstorming flow.

Tripwires, both mandatory: cannot name the owning spec → gear 3; the design stops
fitting in a paragraph mid-conversation → escalate to gear 3, never stretch the
conversational gate.

## Ideation handoff

Documents in `docs/forge/ideas/` — or a path handed at kickoff — are pre-answered
clarification. Protocol: read; confirm understanding; flag constraint conflicts; skip
questions already answered; go straight to approaches. Applies only when an idea
graduates to a build; free-form ideation stays unprocessed.

## Brainstorming flow contracts

- Clarify step: batch 2–3 independent questions per turn, multiple choice preferred;
  single-question only when the answer forks the design.
- Design presentation: sections scaled to their complexity, a check-in after each with
  a decision digest — what was chosen, what it forecloses, what is assumed.
- Self-review of the spec is fixed inline, no re-review.
- Close-out is a message, not a gate: "spec written to `<path>` and committed — flag
  changes, otherwise proceeding to planning." The sectioned walkthrough was the
  approval gate.

## Spec documents

One document per system, named `docs/forge/specs/<system>.md`. No date in the
filename: a document named for the day it was written invites a second document the
next time. The `-design` suffix is dropped — redundant inside `specs/`.

Four systems, and four is the whole set. A fifth document requires a genuinely new
system, not a new change to an existing one.

| system | subject |
|---|---|
| `execution` | the plan-execution loop, review contract, dispositions |
| `project-memory` | constraints, deferrals, memory tooling |
| `codex-runner` | the Codex harness and its runner scripts |
| `pipeline` | this document: skill contracts and document grammars |

A change that alters what a spec asserts amends that spec in place; it never adds a
superseding document (constraint: `specs-amend-in-place`). `skills/brainstorming/SKILL.md`
instructs amendment in place and the `<system>.md` form for a genuinely new system;
`skills/planning/SKILL.md` and `skills/project-memory/SKILL.md` follow where they name
spec paths. This convention binds every repo running forge, not only this one.

### Frontmatter

YAML at the top of every living spec, exactly two keys:

```yaml
---
system: execution
supersedes:
  - archive/specs/2026-07-16-phase7-scope-autonomy-design.md
  - archive/specs/2026-08-21-halt-precision-design.md
---
```

- `system` — kebab id, equal to the filename stem. The equality is what makes the id
  unforgeable: there is no second place to declare identity, so it cannot drift.
- `supersedes` — paths of the dated specs this document answers for, relative to
  `docs/forge/`. Provenance for a merge, and the list a reader audits it against.
  Absent — not an empty list — on a spec that supersedes nothing.

No date field. A document recording when it was last touched grows a second history
competing with git and the changelog.

### Spec style

Telegraphic — bullets, contracts, constraints. Sentence test: a sentence carries a
requirement, contract, or decision, else it is cut. No narrative preamble, no restated
codebase context, no justification prose; the why lives in the PR body. Guard: trim
toward decision-relevant, not short — edge-case naming, interfaces, and acceptance
criteria stay. Code appears as contract, never solution: interface signatures without
bodies, data and wire-format examples, algorithms that are themselves the requirement.

### Spec changelog

Every living spec ends with `## Changelog`. One line per amendment:

```
2026-09-05: <what changed> (<issue, PR, or commit>)
```

A cross-spec amendment — a change to system A that alters what B asserts — is recorded
in B, naming the changing system in brackets after `amended by`:

```
2026-09-05: amended by [pipeline] — spec filenames drop their date prefix (#47)
```

The bracketed id must name a system that exists. This is what makes a cross-system
change visible from the amended document, not only from the changing one.

### Merge method — anchor and fold

When dated specs are consolidated into a system document: the newest source is the
**anchor** — each later document was written as a delta against its predecessors, so
the newest already describes current state — and the older sources contribute only
claims still true. A claim contradicted by a later source is dropped; a claim a later
source restates is kept once, in the anchor's words. The merged document describes
**what is true now**: no phase numbers, no "X changed to Y", no narrative of how the
system arrived. That history is in the archive, the changelog, and git.

**Section accounting.** The merge is lossy and nothing mechanical catches loss, so
accounting is manual and required. `grep '^## ' <source specs>` is the inventory.
Every section in it is either **present** — its content lives in the merged document,
under any heading — or **dropped** — named in the merged document's changelog with the
reason it is no longer true. A section that is neither is a defect. This is the
acceptance criterion for a merge; it is a list a reviewer walks, not a judgment call.

### Lint

Five rules in `scripts/forge_lint.py`, applied to a living spec. No sixth.

1. The filename carries no `YYYY-MM-DD` prefix.
2. Frontmatter parses, and `system` equals the filename stem.
3. Every `supersedes` path resolves to a file that exists.
4. `## Changelog` is present, and every entry matches `YYYY-MM-DD: <text>`.
5. Every bracketed id in a cross-spec `amended by` entry names a system that exists.

Applied to the spec named by a plan at run start, plus a corpus mode —
`forge_lint.py --specs` — that lints every file in `docs/forge/specs/`. Frontmatter is
parsed without a YAML library: the two keys are a fixed grammar, and `stdlib-only`
binds. A dated spec under `archive/` is never linted — the archive is frozen and its
documents are not living specs.

### Citations

Live references to a spec point at the living document. `plans/` and `archive/` are
**not** repointed — both are frozen records, and rewriting a record to match a later
reorganization is the drift this convention exists to remove. `docs/forge/constraints.md`
is hook-protected: a `Source:` field moves through `scripts/forge_memory.py
update-constraint`, and a denied direct edit is the mechanism working, not an obstacle.

## Plan documents

- **Header** carries `**Goal:**` (a single non-empty line) and a
  `**Global Constraints:**` block — version floors, dependency limits, naming rules —
  omitted entirely when the plan has none. No empty block. Its clauses follow the field
  clause grammar below; each becomes a `g<N>` checklist item.
- **Task heading** is `### Task N:` — three `#`, colon. The extraction scripts require
  it.
- **`**Spec:**`** is an optional line after `**Files:**` naming the spec sections this
  task's worker needs, by heading text (unique prefix acceptable, matched
  case-insensitively at extraction). Omitted when the task needs no spec context. A
  single line of bare comma-separated heading names — no parentheticals, no `;`, no
  wrapping — and one spec file per task, since `--spec` takes one. Wrapped or
  parenthetical `**Spec:**`/`**Goal:**` lines fail brief generation.
- **`**Tests:**`** lists the task's test cases by behavior, descriptions not code
  ("rejects empty email", "retries 3 times then throws"). It follows the field clause
  grammar below; `**Tests:** none — <reason>` on a single line is the legal empty form.
  The inline joined form (`**Tests:** a; b; c`) is **rejected**, not
  silently accepted: a test description is prose and may itself contain a `;`, so
  splitting on one guesses whether "rejects empty email; rejects a malformed domain" is
  one case or two — the guess `parsers-fail-loud` exists to forbid. It is **machine-read**:
  each case becomes a `t<N>.t<M>` item on that task's contract checklist (`execution`
  spec), so it is contract text, not commentary, and plan lint checks the grammar. A
  malformed block raises at extraction rather than yielding silently zero items.

  This field drifted for as long as nothing read it — four plans in this repo use both
  forms *within one document*. Every other plan field is lint-checked and none of them
  drifted; a field authored like a contract and validated like prose is the whole
  explanation. Historical plans are not migrated: a completed plan is never re-executed,
  and lint runs on the plan about to run.
- **`**Acceptance:**`** is the commands to run and what must pass. An environment-gated
  skip is not a pass — the command asserts required infrastructure is present, or makes
  the skip exit non-zero. A grep for text is not a command: acceptance executes the
  behavior and asserts on what changed, not on words describing it. Prose artifacts —
  skills, docs, migrations — are the stated exception: nothing is executable, so
  mechanical text checks are the correct form (`testing-anti-patterns.md`).
- **Field clause grammar** governs every machine-read multi-clause field —
  `**Tests:**`, `**Acceptance:**`, `**Global Constraints:**`. Exactly two forms are legal:
  the **marker alone** on its line followed by one `-` bullet per clause, the block ending
  at the first blank line or next `**Field:**`; or the **marker with a value** on the same
  line, which is exactly **one** clause — except `**Tests:** none — <reason>`, that field's
  documented **zero**-clause form. A line inside a bulleted block not beginning with
  `-` continues the preceding bullet, joined with a space — leading `-` starts a clause,
  anything else continues one, so no line is ambiguous. Below the bullet level `;` and `.`
  are literal: no separator has meaning inside a clause. `**Spec:**`'s single-line
  comma-separated list is the documented exception and is unaffected.

  Three lint **errors**, each a contract error: a marker alone followed by neither a bullet
  nor a value; a single-line `**Acceptance:**` containing `;` outside inline code; a
  single-line `**Global Constraints:**` that a period-plus-whitespace split would break
  into more than one. The last two are **detectors, never splitters** — a line that looks
  multi-clause is refused, never silently folded into one. The alternative, accepting it
  quietly, is the defect this grammar replaces: a bulleted `**Acceptance:**` block used to
  collapse into a single checklist item, so a reviewer owed one `coverage` verdict for
  every command in it (#87).

  An acceptance clause that is **solely** an inline-code command is dropped from the
  checklist — the acceptance runner executes it deterministically, so it is dead checklist
  weight. The rule is per clause, and commands are read from inline-code spans regardless
  of form, so it is independent of clause structure.

  Historical plans are not migrated, per the same rule the `**Tests:**` drift set: a
  completed plan is never re-executed, and lint runs on the plan about to run.
- **Decomposition** minimizes dependency chains: wall-clock is the critical path, not
  task count — prefer decompositions that share interfaces over ones that impose
  sequence.
- **Plan prose** adopts the spec style contract above, same sentence test. Plans
  specify what and where — files, interfaces, test cases, acceptance — never
  implementation code (constraint: `plans-carry-contracts`).
- **End-of-plan summary** leads with failures, deviations, and deferrals, not
  achievements, and reports review-cycle counts per task. Recurring "wouldn't have
  done it that way" review calls become written conventions.

## TDD skill contract

`skills/tdd/SKILL.md` is budgeted at ≤650 words (`wc -w`). Substance kept intact:
frontmatter and the trigger/floor line; the Iron Law; the test-infrastructure gate,
all three branches; RED → verify-RED → GREEN → verify-GREEN → REFACTOR with both
verifications mandatory and their failure rules (a test that passes immediately is
testing existing behavior — fix the test; a test that errors is fixed until it fails
correctly; other tests failing on GREEN are fixed now; fix code, not test); good-test
qualities (minimal, one behavior, clear name, shows intent, real code over mocks); bug
fix begins with a failing repro test; the final verification checklist, the final rule,
and exceptions requiring human-partner permission; the on-demand pointer to
`testing-anti-patterns.md`, fired on three named moments — reaching for a mock or
fixture, testing something that cannot be executed, asserting on text. Excluded:
diagrams, code-example blocks, rationale sections, worked examples, and any "when to
use" list the trigger line already covers.

`skills/tdd/testing-anti-patterns.md` is budgeted at ≤600 words (`wc -w`) and loads only
on that pointer. Organizing principle: a test that cannot fail for the reason stated is
not a test. Five entries, each `trigger → gate → instead`:

- asserting on what the test itself set up
- substituting away the behavior the assertion depends on
- doubles shaped by assumption rather than an observed instance
- testing text instead of running it — prose artifacts take mechanical acceptance
  (grep, file-absent, exit code)
- asserting on descriptions instead of effects

No code blocks, no language or framework names. The prose-artifact entry governs the
document case: mechanical text checks on prose are not an instance of the
description-asserting entry, and both entries are worded to make that explicit.

## Pipeline scripts

Inclusion test: a script must eliminate model *reading*, not typing. No memory-file
CRUD tooling. A third script proposal is a stop-and-re-justify.

### `scripts/extract-brief.py`

```
extract-brief.py <plan.md> <task-number> [--spec <spec.md>] [--out <dir>]
```

- Output `<out>/task-<N>-brief.md`; prints the path to stdout.
- Contents: plan header contracts (Goal, Global Constraints), the full Task N block,
  and the spec sections named on the task's `**Spec:**` line (case-insensitive
  heading-prefix match against `--spec` headings; an ambiguous prefix is an error).
- Missing task number, missing `--spec` when the task declares `**Spec:**`, or an
  unmatched section heading → nonzero exit, message on stderr. Never emit a silently
  thin brief.

### `scripts/review-packet.py`

```
review-packet.py <plan.md> <task-number> --base <git-ref> [--out <dir>]
```

- Output `<out>/task-<N>-review.md`; prints the path to stdout.
- Contents: the Task N block (interface, tests, acceptance) plus `git diff <base>` in a
  fenced `diff` block; fence length exceeds the longest backtick run in the diff body,
  minimum 3.
- Missing task number or a failed git invocation → nonzero exit, message on stderr.

### Shared behavior

- `--out` defaults to the system temp dir; the orchestrator passes a session scratchpad
  in practice. Output is never committed.
- Task-block parse anchor: the `### Task <N>:` heading through the next `###`/`##`
  heading or EOF.
- Worker prompts carry the brief-file path and exact file paths, never pasted plan or
  spec content. The brief bounds the worker's reading: "read these N files and spec §X,
  nothing else."
- Diff text never passes through orchestrating context: the orchestrator hands file
  paths, and a worker reports back in one paragraph, not a transcript. On Codex, the
  runner pre-assembles the reviewer's input with `review-packet.py`, since a
  `codex exec` reviewer is a subprocess and cannot gather its own context; on Claude
  the reviewer subagent self-serves its own diff and spec, and `review-packet.py` is
  not used.

## Agent files

Reviewer-facing conduct lives only in `agents/` — review is read-only and never
modifies files; "can't verify from diff" is a valid verdict, reported as such;
implementer rationales never suppress a finding. `forge-deep.md` and
`forge-standard.md` each carry the final-integration-reviewer role for a plan whose
highest tier is theirs; `forge-light.md` never reviews. Orchestrator-facing rules —
including that the orchestrator never pre-rates finding severity — live only in
`skills/planning/SKILL.md`. Reviewer rules are never relayed through orchestrator
prompts.

## Release

A change to skills, agents, or scripts ships as a release: README updated where the
flow description changed, both plugin manifests (`.claude-plugin/plugin.json`,
`.codex-plugin/plugin.json`) bumped in lockstep, `claude plugin update forge@forge`,
and a session restart to apply.

## Changelog

2026-09-09: one field clause grammar for `**Tests:**`, `**Acceptance:**` and `**Global Constraints:**` — marker-alone-plus-bullets or a single-line one-clause value, `;` and `.` literal below the bullet; three lint errors, the two ambiguity checks being detectors rather than splitters; no migration (#87)

2026-09-09: `testing-anti-patterns.md` gets its own contract — ≤600 words, falsifiability principle, five trigger/gate/instead entries, no code or framework names; the TDD pointer fires on three named moments; `**Acceptance:**` gains a Plan documents bullet carrying the environment-gated-skip rule (previously unspec'd) and execute-don't-substring (#85)

2026-09-06: `**Tests:**` is bulleted-form only; the inline `;`-joined form is rejected and lint-checked (#60)
2026-09-06: amended by [execution] — `**Tests:**` is machine-read plan grammar, one `t<N>.t<M>` checklist item per case (#60)

2026-09-05: consolidated from three dated specs — phase1 pipeline-skill-edits, phase2 execution-efficiency, living-specs (#47)
2026-09-05: deliberate exception to newest-is-anchor — living-specs (2026-09-05) is the newest source but covers only the spec-document convention, so phase2 execution-efficiency (2026-07-02) supplies the spine and living-specs contributes the Spec documents section; its Self-migration section is dropped, having been performed by this merge (#47)
2026-09-05: dropped phase1 "Unchanged by this phase" — a phase-scoped delta statement, and its claims no longer hold: `hooks/session-start` now supplies constraints rather than being continuity-only (#47)
2026-09-05: dropped phase1 §1.1's gear-2 "DECISIONS entry if something was genuinely decided" and §1.4's "the why lives in DECISIONS" — the decision log is frozen; rationale lives in the PR body and durable rules in `docs/forge/constraints.md` (#45)
2026-09-05: dropped phase2 "Acceptance" and "Out of scope" — one-time phase gates, satisfied and expired (#47)
2026-09-05: dropped from phase2's Execution section rewrite the context-lifetime inline rule, the trivial-batch and rework-guardrail wording, and the proportional-review, final-review and deferral rules — all superseded, and now owned by the execution spec; what survives here is the document-and-script side: file-referenced briefs, the thin-orchestrator contract (one-paragraph worker reports, diffs travelling reviewer-to-file rather than through orchestrating context), and the end-of-plan summary's content (#47)
2026-09-05: dropped phase2 §1's tier-down preference (fully enumerated interfaces and test cases → prefer the lower tier) — reversed by tier-policy-recalibration, which makes standard the floor and requires demonstrated mechanicalness to move down; tier policy is the execution spec's (#47)
2026-09-05: dropped living-specs "Motivating defect", "Testing", "Acceptance", "Out of scope" — narrative of how the convention arrived plus one-time phase gates; the lint rules they tested are stated under Lint (#47)
2026-09-05: dropped living-specs' enumerated citation-migration list — a one-time worklist; the standing rule is kept under Citations (#47)
2026-07-03: review-packet fence length adapts to diff content; error-path tests pin relayed stderr text
