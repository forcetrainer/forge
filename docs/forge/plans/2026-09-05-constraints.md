# Constraints Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Retire docs/forge/DECISIONS.md — binding rules move to a budgeted, CLI-managed constraints.md holding only what is currently true.
**Architecture:** Phase 1's record engine already provides the CLI, budgets, the PreToolUse deny, the lint check and canonical re-render. This phase adjusts the constraint schema, adds an update operation, rewrites what the session hook supplies, deletes decision-logging from the skills, migrates the surviving rules, and repoints in-code citations.
**Tech stack:** Python 3 stdlib only; bash for the hook; pytest; markdown for skills and docs.
**Global Constraints:** No third-party dependencies. Constraint records are composed from typed CLI arguments — no free-form body argument on any subcommand. Creation and update require user approval; no forge stage writes a constraint unattended. Tasks 3 and 4 are prose and migration: mechanical acceptance only, no test files.

### Task 1: Constraint schema and update operation
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (SCHEMA, `update-constraint`, `_EXPECTED_DESTS`, soft-cap notice)
- Test: `tests/test_forge_memory.py`

**Spec:** Record, CRUD, Soft cap

**Interface:**
```
forge_memory.py add-constraint    --id <slug> --rule <text> --because <text>
                                  [--scope <glob>] --source <ref>
forge_memory.py update-constraint --id <slug> [--rule <text>] [--because <text>]
                                  [--scope <glob>] [--source <ref>]
```
SCHEMA["constraint"] becomes: `id` (≤40, kebab, unique), `rule` (≤200), `scope` (≤80,
required, CLI default `repo`), `because` (≤300), `source` (≤120, required). The `added`
field is removed. `id` is the key and cannot be changed by update.

**Tests:**
- `added` is absent from the schema; a record carrying an `**Added:**` line fails to parse.
- `source` is required: omitting it exits non-zero naming the field.
- `scope` omitted defaults to `repo`; the rendered record never carries an empty scope.
- `scope` over 80 chars and `source` over 120 are rejected naming field and limit.
- `update-constraint` changes only the named fields; unnamed fields survive byte-identical.
- `update-constraint` with no field flags exits non-zero — an update that changes nothing is a mistake.
- `update-constraint` on an unknown id exits non-zero naming the id; the file is unchanged.
- `update-constraint` rejects `--id` as a settable field.
- A budget overrun during update leaves the file byte-identical — validation precedes the write.
- Adding a 13th constraint succeeds and prints a notice naming the count; it never refuses.
- The notice does not appear at 12 or fewer.
- `constraints.md` is created on first add; no empty file is scaffolded before then.
- `_EXPECTED_DESTS` updated deliberately; the allow-list still fails on an unlisted flag.
- Round-trip idempotence holds for the five-field record.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; full suite passes.

**Tier:** standard

**Depends on:** nothing.

### Task 2: Session-start hook supplies constraints
- [ ] Done

**Files:**
- Modify: `hooks/session-start` (constraint injection; remove the decisions claim)
- Test: `tests/test_forge_memory.py` (hook invoked as a subprocess)

**Spec:** Session-start hook, What a constraint is

**Interface:** the hook's `additionalContext` names the forge flow and, when
`docs/forge/constraints.md` exists and is non-empty, includes its constraints. It makes
no claim that logged decisions are constraints and never mentions `DECISIONS.md`.

**Tests:**
- A repo with no `constraints.md` emits the flow context and no constraint text.
- A repo with constraints includes each constraint's rule in the emitted context.
- The emitted context never contains the string `DECISIONS`.
- An unparsable `constraints.md` does not break the hook: it still emits flow context and exits 0.
- A repo with no forge signal still emits nothing and exits 0.
- The legacy `docs/theforge/` path behaves the same as `docs/forge/`.
- Output remains valid JSON with the constraints embedded, including when a rule contains a quote or a backslash.
- The hook makes no `gh` call and no network call — process-level, PATH-stubbed.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `hooks/session-start` output parses as JSON in both the constraints-present and constraints-absent cases; full suite passes.

**Tier:** standard

**Depends on:** Task 1.

### Task 3: Delete decision-logging from skills and docs
- [ ] Done

**Files:**
- Modify: `skills/brainstorming/SKILL.md` (remove the log-the-decision step; renumber the flow)
- Modify: `skills/planning/SKILL.md` (remove decision-logging from self-review and close-out)
- Modify: `skills/project-memory/SKILL.md` (remove the DECISIONS section and its frontmatter mention)
- Modify: `README.md`, `CONTRIBUTING.md` (project memory no longer includes a decision log)

**Spec:** Rationale after the freeze, What a constraint is

**Interface:** none — prose only. Required content: rationale lives in PR bodies and spec
changelogs; constraints hold only what is currently true and are user-approved; a
decision is not a constraint.

**Tests:** none — agent-facing prose with no assertable behavior.

**Acceptance:**
- `grep -rn "DECISIONS" skills/ README.md CONTRIBUTING.md` returns no hit instructing anyone to log a decision or read a decision file.
- The three required content points appear in `skills/project-memory/SKILL.md`.
- `skills/brainstorming/SKILL.md`'s flow steps are contiguously numbered after the deletion.
- Full suite passes with no test changes.

**Tier:** standard

**Depends on:** nothing.

### Task 4: Migrate the surviving constraints and freeze DECISIONS.md
- [ ] Done

**Files:**
- Create: `docs/forge/constraints.md` (via the CLI only)
- Delete: `docs/forge/DECISIONS.md` (via `git mv`)
- Create: `docs/forge/archive/DECISIONS.md`

**Spec:** Migration, Authoring tests

**Interface:** none.

**Tests:** none — a guided content migration with no assertable behavior.

**Acceptance:**
- Every one of the 37 entries is judged against the six authoring tests; rejects are grouped by the test they failed.
- Survivors are authored through `add-constraint`, never by editing the file.
- `docs/forge/DECISIONS.md` does not exist.
- `docs/forge/archive/DECISIONS.md` exists and opens with a header stating it is historical, not authoritative, and takes no new entries.
- `forge_memory.py fmt --check` passes on `constraints.md`.
- `forge_lint.py` reports no memory defects.
- Full suite passes.

**Tier:** standard

**Depends on:** Task 1.

**Execution note:** NOT DISPATCHABLE. Which rules still bind is the user's judgment, and each survivor must be re-authored within budget. Runs inline with the user after Tasks 1-3 are committed.

### Task 5: Repoint in-code citations
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py`, `scripts/forge_common.py`, `scripts/forge_dispose.py`, `scripts/forge_lint.py`, `scripts/forge_memory.py`, `scripts/forge_plan.py`, `scripts/forge_checklist.py` (comment citations)
- Modify: `skills/planning/SKILL.md`, `skills/planning/codex-execution.md`, `skills/brainstorming/SKILL.md`, `skills/project-memory/SKILL.md` (prose citations)
- Modify: `README.md`, `CONTRIBUTING.md`

**Spec:** Citations

**Interface:** none. Each `(DECISIONS <date>)` citation is replaced by exactly one of: a
constraint id, the owning spec path, a PR reference, or nothing when no durable target
exists.

**Tests:** none — comment and prose edits with no assertable behavior.

**Acceptance:**
- `grep -rn "DECISIONS [0-9]" scripts/ skills/ hooks/ README.md CONTRIBUTING.md` returns no hit — no dated citation survives. Prose explaining that a decision is not a constraint may name the word.
- Every citation naming a constraint id matches an id present in `constraints.md`.
- Every citation naming a spec path resolves to a file that exists.
- Every citation naming a PR names one that exists.
- No comment is left asserting a rule with no explanation where it previously carried one.
- Full suite passes.

**Tier:** standard

**Depends on:** Task 4.
