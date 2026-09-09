# Field Clause Grammar Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Give `**Tests:**`, `**Acceptance:**` and `**Global Constraints:**` one clause grammar, and make an ambiguous field refuse to lint instead of collapsing.
**Architecture:** One clause parser in `scripts/extract-brief.py` serves all three fields; `scripts/forge_checklist.py` calls it and keeps no separator logic of its own; `scripts/forge_lint.py` owns ambiguity detection. The two ambiguity rules are detectors, never splitters — a line that looks multi-clause is refused, not folded.
**Tech stack:** Python 3 standard library. Existing pytest suite.
**Global Constraints:**
- Python 3 standard library only; no new dependency enters the plugin (constraint: `stdlib-only`).
- Existing tests that assert the old splitting behavior are updated to the new grammar, never worked around or skipped; the behavior change is the point.
- This plan's own fields are authored in bulleted form so they parse under both the pre-task and post-task grammar.

### Task 1: Shared clause parser
- [x] Done

**Files:**
- Modify: `scripts/extract-brief.py` (generalize `parse_test_cases` into a field-agnostic clause parser)
- Modify: `scripts/forge_checklist.py` (call it from `_acceptance_items` and `_global_constraint_items`; delete `_split_acceptance_clauses`, `_split_on_semicolons_outside_inline_code`, `_split_global_constraints`)
- Test: `tests/test_forge_coverage.py`

**Spec:** Plan documents

**Interface:** `parse_field_clauses(block, field_name)` in `scripts/extract-brief.py` returns the field's clauses as a list of strings, and raises on a marker alone followed by neither a bullet nor a value. It accepts a plan header block as well as a task block, since `**Global Constraints:**` lives in the header. `parse_test_cases(task_block)` becomes a thin caller preserving its current signature and its `none — <reason>` zero-clause form. `forge_checklist.py` retains the rule that a clause consisting solely of an inline-code span is dropped from the checklist, now applied per clause.

**Tests:**
- bulleted Acceptance block yields one checklist item per bullet
- single-line Acceptance yields exactly one item
- a semicolon inside a bulleted Acceptance clause does not split it
- a period inside a bulleted Global Constraints clause does not split it
- bulleted Global Constraints block yields one g-item per bullet
- a continuation line not beginning with a dash joins the preceding bullet
- a clause consisting solely of an inline-code command is dropped from the checklist
- a clause mixing an inline-code command with prose is kept
- marker alone followed by neither bullet nor value raises
- Tests field behavior is unchanged, including the `none — <reason>` zero-clause form
- acceptance commands are still extracted from inline-code spans in both forms

**Acceptance:**
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `! grep -q '_split_acceptance_clauses\|_split_on_semicolons_outside_inline_code\|_split_global_constraints' scripts/forge_checklist.py` — the three splitters are gone, not merely unreferenced
- `grep -q 'def parse_field_clauses' scripts/extract-brief.py` — the shared parser exists

**Tier:** standard

**Depends on:** nothing.

### Task 2: Ambiguity detection in lint
- [x] Done

**Files:**
- Modify: `scripts/forge_lint.py` (three field-grammar errors; the period-plus-whitespace regex moves here as a detector)
- Test: `tests/test_forge_lint.py`

**Spec:** Plan documents, Lint

**Interface:** three new lint defects, each an error and a contract error, each naming the offending file and line, all reported in one run alongside existing defects: a marker alone followed by neither a bullet nor a value; a single-line `**Acceptance:**` containing `;` outside an inline-code span; a single-line `**Global Constraints:**` that a period-plus-whitespace split would break into more than one clause. The regex is used only to decide whether to raise, never to produce clauses.

**Tests:**
- single-line Acceptance containing a semicolon outside inline code is an error naming the line
- single-line Acceptance whose only semicolon is inside an inline-code span is legal
- single-line Global Constraints spanning two sentences is an error naming the line
- single-line Global Constraints of one sentence is legal
- a period inside an inline code span such as a filename does not trigger the Global Constraints error
- marker alone followed by neither bullet nor value is an error
- bulleted forms of all three fields are legal
- all field-grammar defects in one plan are reported in a single run, not just the first
- a plan with no Global Constraints block at all remains legal

**Acceptance:**
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `python3 scripts/forge_lint.py docs/forge/plans/2026-09-09-field-clause-grammar.md --spec docs/forge/specs/pipeline.md` exits 0 — this plan is legal under its own new rules
- `python3 scripts/forge_lint.py --specs` exits 0

**Tier:** standard

**Depends on:** Task 1.

### Task 3: Document the grammar
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (state the shared grammar once; `**Acceptance:**` and `**Global Constraints:**` adopt it; the `**Tests:**` paragraph defers to it rather than restating the form)

**Spec:** Plan documents

**Interface:** the skill states the two legal forms, the continuation rule, that `;` and `.` are literal below the bullet level, the `**Tests:** none — <reason>` zero-clause exception, and the three lint errors. `**Spec:**`'s single-line comma-separated list is named as the documented exception. No worked examples beyond the minimum needed to show each form.

**Tests:** none — prose edit to a skill file; verification is the mechanical acceptance below.

**Acceptance:**
- `grep -qi 'field clause grammar' skills/planning/SKILL.md` — the shared rule is named
- `! grep -q 'a; b; c' skills/planning/SKILL.md` — no stale example of a form that is now an error, outside the rejection notice
- `python3 -m pytest tests/ -q` passes
- `python3 scripts/forge_lint.py --specs` exits 0

**Tier:** standard

**Depends on:** Task 2.
