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
- Self-review of the spec is fixed inline, no re-review. Self-review is **not** the
  gate — an author checking their own document is the control Spec review replaces.
- Spec review (below) runs after self-review and before close-out. Planning does not
  begin until it passes.
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

### Spec review

A **cold** reviewer validates the spec against the codebase before planning begins.
Every other control examines work produced *from* the spec; this one examines the spec.

**Two halves.** The mechanical half enumerates and never decides; the judgment half
decides and is never the only check.

**Reference table** (mechanical). Every backticked span is classified path-shaped
(contains `/`, or an extension carried by some file in `git ls-files` — a derived
set, never a hardcoded list), symbol-shaped (identifier, dotted name, or
`name()`), or neither — flags, enum values and constraint ids are neither and are
dropped, so the table is signal rather than noise. Each path/symbol reference resolves
against the repo or does not. An unresolved reference is **legal**: a spec for a system
not yet built names files that do not exist. The table is therefore a **checklist, not a
rule** — it is not a sixth lint rule, and Lint's five stand.

**The reviewer owes a disposition on every unresolved reference** — `intended-new`,
`wrong`, or `unverifiable`, each with evidence. A missing disposition invalidates the
verdict (schema: `execution` spec). This is what makes the mechanical half enforced
rather than advisory: no claim the spec makes about the codebase can go unexamined.

**Hunting list.** The reviewer's guidance is `skills/brainstorming/design-anti-patterns.md`,
loaded by the `@design-anti-patterns.md` on-demand reference form, never inlined.
**To change what the reviewer looks for, change that file.** It is the single tuning
surface; no other file restates a Gate. Entries use the Trigger / Gate / Instead format
of `testing-anti-patterns.md`. An entry enters on **two independent observations** — one
incident does not mint a permanent rule. An entry backed by a required verdict field is
marked as such: deleting a marked entry does not remove the field, and the two are
checked to agree.

**The author's requirements are stated separately** (Spec style, below) and are not the
reviewer's hunting list. A reviewer hunting the author's checklist re-performs a presence
check against a document written to satisfy it — the failure this gate exists to replace.

**Coldness.** Discovery is a fresh reviewer (constraint: `discovery-review-is-cold`); a
re-review after an amendment is a verification lap and may resume.

**Amendments re-enter, and the review is always whole-document** — never scoped to the
changed sections. Scoping was specified first and dropped: "changed sections" never said
changed *relative to what*, and three incompatible baselines each satisfied the words
while covering different things. Every one of them fails the same way, by running a gate
that covers less than it claims — a last-commit baseline reviews only the final slice of
a multi-commit amendment, and an author-declared scope lets the party under review set
the reviewer's scope. Scoping also bought little: the whole-document contradiction
question has to be asked regardless, since an amendment can contradict a section it did
not touch, so the reviewer reads the whole document either way. A stateful
last-passing-review baseline remains addable later if full review proves costly; its
fallback when no state exists is this rule.

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

Five rules in `scripts/forge_lint.py`, applied to a living spec. No sixth. Spec
review's reference table is not a rule and does not become one — an unresolved
reference is legal and is adjudicated by the reviewer, not by lint.

1. The filename carries no `YYYY-MM-DD` prefix.
2. Frontmatter parses, and `system` equals the filename stem.
3. Every `supersedes` path resolves to a file that exists.
4. `## Changelog` is present, and every entry matches `YYYY-MM-DD: <text>`.
5. Every bracketed id in a cross-spec `amended by` entry names a system that exists.

Applied to each spec a plan declares at run start, plus a corpus mode —
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
- **`**Spec files:**`** is an optional header field listing the spec files the plan
  implements, one repo-relative path per clause, by the field clause grammar below. It
  is the single source of the plan's specs: every tool reads it from the plan, and no
  caller passes a spec path. A path is relative to the repository root and is written
  plain or inside one pair of backticks; both forms are legal and mean the same file. A
  path that resolves outside the repository root — an absolute path elsewhere, a `..`
  climb, a symlink — is an error naming the path, raised before the file is opened. A
  path naming no file, and a file that is not a living spec — one that fails any rule
  under Lint above — are lint errors naming the path. A file's **spec id** is its
  frontmatter `system` value; two declared files with one id are a lint error.
  A plan carrying the field is run without `--spec`; passing both is a Plan lint error
  (`execution` spec).
  A plan without the field is a **legacy plan**: `--spec <file>` supplies its one spec,
  and with neither it has no spec. Completed plans are not migrated.
- **Task heading** is `### Task N:` — three `#`, colon. The extraction scripts require
  it.
- **`**Spec:**`** is an optional line after `**Files:**` naming the spec sections this
  task's worker needs, by heading text (unique prefix acceptable, matched
  case-insensitively at extraction). In a plan written from a spec, every task names
  the sections it implements; a task omits the line only when it implements no spec
  section (setup, a pure refactor). The planning skill's self-review checks this. A
  single line of comma-separated entries — no parentheticals, no `;`, no wrapping.
  Wrapped or parenthetical `**Spec:**`/`**Goal:**` lines fail brief generation.

  An entry is `[<spec id>] <heading name>`, or a bare `<heading name>`. The bracketed
  id names one of the plan's declared spec files and says which file the section is in:
  `**Spec:** [execution] Plan lint, [pipeline] Plan review`. It is **required** on every
  entry when the plan declares more than one spec file, optional when it declares one,
  and never present in a legacy plan. An id that names no declared spec, and a bare
  entry in a plan declaring more than one, are errors listing the declared ids — a name
  is never searched for across files. The id is matched **exactly and
  case-sensitively** against the declared spec ids, with no whitespace inside the
  brackets; the entry splits at its first `]`, and what follows, with surrounding
  whitespace trimmed, is the heading name.

  Within its file a name resolves in this order: a heading whose text equals the name
  is the match, even when other headings begin with it; otherwise a unique prefix;
  otherwise an error naming the candidates. Both comparisons are case-insensitive, with
  whitespace collapsed on both sides and leading numbering (`2.`, `2.3`) stripped from
  the **heading only** — never from the name, which may legitimately begin with a digit.
  Two headings with identical text are ambiguous by either rule.
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
- **`**Acceptance:**`** is what must hold when the task is done: command clauses the
  runner checks and prose clauses the reviewer verdicts on (Acceptance clause grammar,
  below). An environment-gated skip is not a pass — the command asserts required
  infrastructure is present, or makes the skip exit non-zero. A grep for text is not a command: acceptance executes the
  behavior and asserts on what changed, not on words describing it. Prose artifacts —
  skills, docs, migrations — are the stated exception: nothing is executable, so
  mechanical text checks are the correct form (`testing-anti-patterns.md`). That
  exception carries its own bar: **a mechanical text check must be demonstrated to fail
  when the thing it guards is violated** — plant the violation, confirm red, restore.
  A check nobody has seen fail is not known to check anything, and a passing suite says
  nothing about it. Write the check against the requirement's **meaning, not its
  spelling**: a grep for a chosen string is defeated by any violation spelled
  differently, and fires on any innocent line spelled the same. Observed repeatedly —
  a needle that missed a line-wrapped copy, a count that an empty marker satisfied, a
  denylist of two literal names, and an acceptance command that could never pass because
  its word was the file's own vocabulary.
- **Field clause grammar** governs every machine-read multi-clause field —
  `**Tests:**`, `**Acceptance:**`, `**Global Constraints:**`, `**Spec files:**`. Exactly two forms are legal:
  the **marker alone** on its line followed by one `-` bullet per clause, the block ending
  at the first blank line, next `**Field:**`, or any heading line (`#` through `######` at
  column 0), whichever comes first; or the **marker with a value** on the same
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

- **Acceptance clause grammar.** Each `**Acceptance:**` clause is exactly one of two
  kinds, decided by its first character after trimming:
  - **Command clause** — begins with an inline-code span. Its whole text must be
    `` `<command>` <outcome> ``: one single-backtick span holding the command, one space,
    then exactly one outcome from the table below, nothing after it (no trailing period).
    The command span is the clause's first span; the `` `<text>` `` span of a
    `` prints `<text>` `` outcome is part of the outcome, never a command. A command
    cannot contain a backtick — one that needs it goes in a script the clause runs.
  - **Prose clause** — begins with anything else. Never executed; inline code inside it
    is literal text (a path, a version string), never a command. It becomes a `t<N>.a<M>`
    checklist item for the reviewer.

  | outcome | the clause passes when |
  |---|---|
  | `passes` | exit code 0 |
  | `exits <N>` | exit code exactly `<N>` (a non-negative decimal integer) |
  | `prints nothing` | stdout and stderr both empty; exit code not checked |
  | `` prints `<text>` `` | the full output (stdout and stderr combined) contains `<text>` literally and exit code 0 |

  A command that times out fails whatever its outcome. A clause beginning with inline
  code that does not match this shape exactly — a bare `` `<command>` ``, an unknown or
  misspelled outcome, a second command span, trailing text — is a lint **error** naming
  the task, the line and the clause, and listing the four legal outcomes. It is never
  read as prose and never defaulted to `passes`: a clause the runner cannot read is
  refused, not guessed (constraint: `parsers-fail-loud`). The runner checks exactly the
  stated outcome; nothing else in a clause is checked by machine.

  Command clauses are dropped from the checklist — the runner checks them
  deterministically, so they would be dead checklist weight. Prose clauses are the
  reviewer's.

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

### Plan review

A **cold** reviewer validates the plan against its spec after the planning skill's
self-review and before execution is offered. Self-review stays and is **not** the gate.
Every other control downstream checks work against the plan's promises; this one checks
the promises against the spec.

Applies to every plan written from a spec — one declaring `**Spec files:**`, or a
legacy plan executed with `--spec`. It covers every declared spec in one review. A plan with no
spec has nothing to validate against and skips it. A plan written from a spec whose
tasks name no spec section is an **error** at packet build: an empty section table is
never a pass, and there is no skip. The error names the cause and the fix — add
`**Spec:**` lines naming the sections the tasks implement, then re-run. It is raised
before any reviewer is dispatched.

**Three checks, and three is the whole set:**

1. **No plan element contradicts the spec** — reviewer.
2. **Every changed or named spec section is covered** — the reviewer, for the
   requirements inside each section some task's `**Spec:**` line names; Plan lint's
   changed-section rule (`execution` spec: Plan lint), for a changed section no task
   names. An unchanged section no task names is not examined: in a living spec it is
   already built.
3. **Plan lint**, with no rule added for plan review — before the packet is built, since the packet is
   built by parsing the plan, and again on the plan as amended.

**Covered means promised.** A requirement is covered only by a plan promise: a
`**Tests:**` case, an `**Acceptance:**` clause of either kind, or a
`**Global Constraints:**` clause. A task naming the section, or declaring an
`**Interface:**`, covers nothing — per-task review enforces promises only, so anything
else is unenforced downstream. A requirement the plan deliberately does not build is
marked `n/a` with a reason and needs no promise — the same escape Plan lint's
changed-section rule relies on.

**Packet** — `scripts/forge_docreview.py --plan <plan> [--spec <spec>] [--out <path>]`.
Carries the plan path and every spec path, never their pasted content; a **section
table**; a **promise table**; the two reviewer questions; the required verdict fields.
`--spec` is given only for a legacy plan. `--plan` on a plan with no spec — no
`**Spec files:**` and no `--spec` — is a usage error.

- **Section table** — each named spec section and the tasks naming it. A section is
  identified by its resolved heading text, whitespace-collapsed, prefixed `[<spec id>] `
  when the plan declares more than one spec file: the text of its `spec:` id. When a section and one of its subsections are both named, each is
  its own entry and the subsection's requirements are listed under the subsection only.
- **Promise table** — every promise id with its text, built by
  `scripts/forge_checklist.py` (ids: `execution` spec, Contract checklist).

**The reviewer owes a coverage entry on every section in the section table**, listing
the section's requirements and the promise ids covering each. A missing section
invalidates the verdict (schema: `execution` spec, Document review contract). Promise
ids are copied verbatim from the promise table, never derived.

**Verdict** — `scripts/forge_docreview.py --plan <plan> [--spec <spec>] --verdict <file>`
validates and disposes. Non-zero exit is an invalid verdict.

**Coldness.** Discovery is a fresh reviewer (constraint: `discovery-review-is-cold`); a
re-review after a plan amendment is a verification lap in that constraint's sense and
resumes the reviewer; a failed resume falls back to a fresh reviewer given the full
packet. Unlike a task review's verification it is always whole-plan, for the reason
Spec review is whole-document, and its verdict carries the full `coverage` array. A spec
amended in response to a `spec-defect` finding re-enters Spec review; plan lint then
re-runs against the amended spec, and plan review restarts cold.

**An undeclared changed spec is settled before the offer.** Plan lint warns when a spec
changed on the branch and the plan does not declare it (`execution` spec: Plan lint). A
warning does not fail lint, so the skill gives it an owner: the plan's author either
declares the spec or records why it is not this plan's, and the execution offer lists
every such warning with its reason.

**The gate is skill text.** `skills/planning/SKILL.md` states that execution is not
offered until plan review passes. No runner checks for a review receipt.

**Out of scope:** a hunting list for the plan reviewer; feeding the coverage entries
into per-task review; constructing a conforming-but-wrong implementation against a
task's acceptance criteria.

## TDD skill contract

`skills/tdd/SKILL.md` is budgeted at ≤650 words (`wc -w`). Substance kept intact:
frontmatter and the trigger/floor line; the Iron Law; the test-infrastructure gate,
all three branches; RED → verify-RED → GREEN → verify-GREEN → REFACTOR with both
verifications mandatory and their failure rules (a test that passes immediately is
testing existing behavior — fix the test; a test that errors is fixed until it fails
correctly; other tests failing on GREEN are fixed now; fix code, not test); good-test
qualities (minimal, one behavior, clear name, shows intent, real code over mocks, lowest
level that can prove it — a higher-level test proves something a lower one cannot); bug
fix begins with a failing repro test; the final verification checklist, the final rule,
and exceptions requiring human-partner permission; the on-demand pointer to
`testing-anti-patterns.md`, fired on three named moments — reaching for a mock or
fixture, testing something that cannot be executed, asserting on text. Excluded:
diagrams, code-example blocks, rationale sections, worked examples, and any "when to
use" list the trigger line already covers.

Test scope, stated in the skill and binding on every repo:

- **Run scope** — verify-GREEN runs the test files the cycle touched, never the whole
  suite. The whole suite runs once at completion; for a plan task, completion is its
  acceptance commands and the whole suite runs at plan close-out.
- **Unit of coverage** — a test per new behavior reachable through a public interface,
  not per function or method; helpers are covered through their callers.
- Repo-specific testing policy — which levels exist, which command covers which
  subsystem, how a change type is verified — is not skill content. It lives in the
  repo's own constraints.

`skills/tdd/testing-anti-patterns.md` is budgeted at ≤600 words (`wc -w`) and loads only
on that pointer. Organizing principle: a test that cannot fail for the reason stated is
not a test. Five entries, each `trigger → gate → instead`:

- asserting on what the test itself set up
- substituting away the behavior the assertion depends on
- doubles shaped by assumption rather than an observed instance
- testing text instead of running it — prose artifacts take mechanical acceptance
  (grep, file-absent, exit code)
- asserting on descriptions instead of effects — including a stand-in for an effect the
  harness cannot observe, which takes no test there and is verified where it is
  observable

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
  and the spec sections named on the task's `**Spec:**` line, each taken from the file
  its entry resolves in (resolution: Plan documents) and, when the plan declares more
  than one spec file, labeled `[<spec id>] <heading>`. The
  specs come from the plan's `**Spec files:**`; `--spec` is for a legacy plan only.
- Missing task number, a task declaring `**Spec:**` in a plan with no spec, or an
  unmatched or ambiguous section name → nonzero exit, message on stderr. Never emit a
  silently thin brief.

### `scripts/review-packet.py`

```
review-packet.py <plan.md> <task-number> --base <git-ref> [--spec <spec.md>] [--out <dir>]
```

- Output `<out>/task-<N>-review.md`; prints the path to stdout.
- The specs come from the plan's `**Spec files:**`; `--spec` is for a legacy plan only.
- Contents: the Task N block (interface, tests, acceptance); the spec sections the
  task's `**Spec:**` line names, pasted as context and labeled `[<spec id>] <heading>`
  when the plan declares more than one spec file; and `git diff <base>` in a
  fenced `diff` block; fence length exceeds the longest backtick run in the diff body,
  minimum 3.
- Missing task number, a task declaring `**Spec:**` in a plan with no spec, an
  unmatched or ambiguous section name, a spec set that fails to load (`--spec` given
  alongside `**Spec files:**`, a declared path naming no file or resolving outside the
  repository root, an undeclared `[<spec id>]`), or a failed git invocation → nonzero
  exit, message on stderr.

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
  runner pre-assembles the reviewer's input with `review-packet.py`, so the diff is
  computed once against the right base. A per-task packet carries that diff and the
  task's named spec sections as pasted context; the final-review packet and the doc-sync
  brief list spec paths instead, and the reviewer reads those files itself
  (`codex-runner` spec: Runner). On Claude
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

2026-10-03: a plan declares its spec files in a `**Spec files:**` header and its tasks name sections as `[<spec id>] <heading>`, so one plan implements any number of specs and every tool reads them from the plan; `--spec` remains for legacy plans only. An exact heading match now wins over a prefix match. Until now a run took one `--spec`, and a plan amending two specs got coverage checking on one and silence on the other (#62)
2026-10-03: plan review — a cold reviewer validates a plan against its spec before execution is offered: no plan element contradicts the spec, every requirement in a named section is covered by a plan promise, and plan lint runs before the packet and on the amended plan. Until now the only checks were the author's self-review and a lint rule satisfied by naming a section, so a spec obligation dropped at planning surfaced only at the final review (#97, #113)
2026-10-03: TDD test scope — verify-GREEN runs the touched test files, the whole suite once at completion (plan close-out for a plan task); coverage is per behavior through a public interface, not per function; lowest level that proves it; the description-asserting anti-pattern covers a stand-in for an effect the harness cannot observe. Replaces "run the full suite" on every cycle and "every new function/method has a test", which drove repeated heavy-suite runs and tests coupled to internals in two downstream repos
2026-10-02: acceptance clauses are command clauses (`` `<command>` <outcome> ``, outcome from a closed set: `passes`, `exits <N>`, `prints nothing`, `` prints `<text>` `` over combined output) or prose clauses, never executed; a clause beginning with inline code that does not parse is a lint error. Replaces "every inline-code span is a command that must exit 0", which executed version strings and file paths and scored a passing `prints nothing` grep as a failure (#112)
2026-09-09: amendment re-review is whole-document, never scoped — "scoped to the changed sections" never defined the baseline, and three incompatible readings each satisfied it while covering different things; scoping saved little because the whole-document contradiction question forces a full read regardless (#96 final review)

2026-09-09: the prose exception to execute-don't-substring gains a bar — a mechanical text check must be demonstrated to fail when the thing it guards is violated, and is written against the requirement's meaning rather than its spelling; five checks in the #96 run were green while guarding nothing, including one whose requirement, acceptance command and eponymous test all passed with the violation in place (#98)

2026-09-09: spec review — a cold reviewer validates a spec against the codebase before planning, with a mechanical reference table it owes a disposition on and `design-anti-patterns.md` as its single tuning surface; self-review stays but is no longer the gate; amendments re-enter scoped to changed sections (#96)

2026-09-09: field clause grammar's clause-block termination widens from `#`–`###` to any heading level (`#` through `######` at column 0), agreeing with `forge_plan._field_text`'s existing boundary — closes the gap where an h4+ heading and the prose beneath it were absorbed into the last clause (#87)

2026-09-09: field clause grammar's block termination set gains a heading line (`#` through `###` at column 0), alongside the blank line and next `**Field:**` — closes the gap where a heading was absorbed into the last clause (#87)

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
