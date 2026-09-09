# Testing Anti-Patterns Rewrite Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Replace the verbatim-upstream `testing-anti-patterns.md` with a stack-neutral falsifiability reference, and fire it from the moments that actually need it.
**Architecture:** One on-demand reference under `skills/tdd/`, loaded by a pointer in `skills/tdd/SKILL.md`. Its fifth entry has a second authoring site — `skills/planning/SKILL.md`'s `**Acceptance:**` definition — which gets the rule inline rather than a second document pointer.
**Tech stack:** Markdown only. No scripts, no dependencies.

### Task 1: Reference document
- [x] Done

**Files:**
- Modify: `skills/tdd/testing-anti-patterns.md` (full rewrite — replace all 299 lines)

**Spec:** TDD skill contract

**Interface:** the document states the organizing principle once, then five entries. Each entry is three bold-labelled beats in this order and no others: `**Trigger:**`, `**Gate:**`, `**Instead:**`. Entry order is the spec's order: setup-assertion, substitution, assumed-shape doubles, text-instead-of-running, descriptions-instead-of-effects.

**Tests:** none — prose artifact with nothing executable; verification is the mechanical acceptance below, per the document's own fourth entry.

**Acceptance:**
- `[ "$(wc -w < skills/tdd/testing-anti-patterns.md)" -le 600 ]`
- `! grep -q '^```' skills/tdd/testing-anti-patterns.md` — no code fences
- `! grep -qiE 'typescript|javascript|react|vitest|jest|vi\.mock|afterEach|\.tsx?\b' skills/tdd/testing-anti-patterns.md` — no language or framework names
- `[ "$(grep -c '^\*\*Gate:\*\*' skills/tdd/testing-anti-patterns.md)" -eq 5 ]`
- `[ "$(grep -c '^\*\*Trigger:\*\*' skills/tdd/testing-anti-patterns.md)" -eq 5 ]`
- `[ "$(grep -c '^\*\*Instead:\*\*' skills/tdd/testing-anti-patterns.md)" -eq 5 ]`
- `! grep -q 'your human partner' skills/tdd/testing-anti-patterns.md` — upstream idiom gone

**Tier:** standard

**Depends on:** nothing.

### Task 2: Trigger sites
- [x] Done

**Files:**
- Modify: `skills/tdd/SKILL.md` (replace the `## Testing Anti-Patterns` pointer line with the three-moment trigger)
- Modify: `skills/planning/SKILL.md` (extend the `**Acceptance:**` definition line with the execute-don't-substring rule and its prose-artifact exception)

**Spec:** TDD skill contract, Plan documents

**Interface:** the TDD pointer names three moments — reaching for a mock or fixture, testing something that cannot be executed, asserting on text — and keeps the `@testing-anti-patterns.md` reference form that makes it an on-demand load. The planning addition is a clause on the existing `**Acceptance:**` line, matching the register of the environment-gated-skip rule already there; it does not add a document pointer.

**Tests:** none — prose edits to two skill files; verification is the mechanical acceptance below.

**Acceptance:**
- `[ "$(wc -w < skills/tdd/SKILL.md)" -le 650 ]` — budget from the spec still met
- `grep -q '@testing-anti-patterns.md' skills/tdd/SKILL.md`
- `grep -qi 'mock or fixture' skills/tdd/SKILL.md`
- `grep -qi 'cannot be executed\|can.t execute' skills/tdd/SKILL.md`
- `grep -qi 'asserting on text\|assert on text' skills/tdd/SKILL.md`
- `! grep -q 'when adding mocks or test utilities' skills/tdd/SKILL.md` — old narrow trigger gone
- `grep -qi 'not a command' skills/planning/SKILL.md`
- `grep -qi 'environment-gated skip is not a pass' skills/planning/SKILL.md` — existing rule preserved
- `python3 scripts/forge_lint.py --specs` exits 0
- `python3 -m pytest -q` passes

**Tier:** standard

**Depends on:** Task 1.
