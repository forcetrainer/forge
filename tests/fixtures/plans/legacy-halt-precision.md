# Halt Precision (fixture) Implementation Plan

> Trimmed fixture derived from
> `docs/forge/plans/2026-08-21-phase14-halt-precision.md`, owned by the test
> suite so a plan-grammar change elsewhere never breaks these lint tests for
> reasons unrelated to lint itself.

**Goal:** Stop routing non-scope-decisions into the halt quadrant.
**Global Constraints:** `tests/test_forge_convergence.py` must pass unchanged; no live `codex` binary is available.

### Task 1: Plan/spec lint
- [x] Done

**Files:**
- Create: `scripts/forge_lint.py`
- Test: `tests/test_forge_lint.py`

**Spec:** Plan lint

**Interface:**
- `LintDefect` dataclass: `severity`, `where`, `message`.
- `lint_plan(plan_path, spec_path=None) -> list[LintDefect]` — returns every defect found, never short-circuits.

**Tests:**
- each check's error case is reported with its own message naming the offending task or heading
- multiple simultaneous defects all reported in one call, not just the first
- a legal minimal plan lints clean
- an empty checklist produces a warning and exit 0

**Acceptance:** `python3 -m pytest -q tests/test_forge_lint.py`

**Tier:** `standard`

**Depends on:** nothing.

### Task 2: Honor the convergence resolved label
- [x] Done

**Files:**
- Modify: `scripts/forge_dispose.py`
- Test: `tests/test_forge_classify.py`

**Spec:** Convergence label honored

**Interface:**
- `classify_findings` drops a finding carrying `convergence == "resolved"` before disposition.
- The label is honored only when the finding's canonical id is present in the prior attempt's carried-fix set.

**Tests:**
- a listed resolved finding whose canonical id was carried is dropped, with no fix disposition
- a resolved label on an id absent from the carried set is ignored and dispositioned normally
- a falsely-resolved finding reappearing later still trips the regression rule

**Acceptance:** `python3 -m pytest -q tests/test_forge_classify.py`

**Tier:** `standard`

**Depends on:** Task 1.

### Task 3: Touch points bookkeeping
- [x] Done

**Files:**
- Modify: `docs/forge/ROADMAP.md`
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`

**Spec:** Touch points

**Interface:** Changelog lines follow the existing dated form; both manifests carry the identical version string.

**Tests:**
- none — mechanical edits

**Acceptance:** `grep -h '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json` shows the same version twice

**Tier:** `trivial` — version strings and one status word; no logic, no design content.

**Depends on:** Task 1, Task 2.
