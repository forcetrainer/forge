# GPT-6 Codex Routing Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Route Codex tiers to GPT-6 — trivial gpt-6-luna·low, standard gpt-6-sol·medium, complex gpt-6-sol·high.
**Architecture:** `forge_common.TIER_MAP` is the single update point for Codex model churn; worker, reviewer and final-review routing all read it. Standard and complex now share one model, so every routing assertion that tells them apart must assert effort, not the model id alone. Claude routing is untouched — `agents/forge-deep.md` pins the `opus` alias, which already resolves to Opus 5.5.
**Tech stack:** Python 3 standard library. Existing pytest suite.
**Global Constraints:**
- Python 3 standard library only; no new dependency enters the plugin (constraint: `stdlib-only`).
- No `gpt-5.6-*` model id remains in `scripts/` or `tests/`.

### Task 1: Remap TIER_MAP to GPT-6
- [x] Done

**Files:**
- Modify: `scripts/forge_common.py` (`TIER_MAP` values only; keys, `TIER_ORDER` and `CONTRACT_AGENT` unchanged)
- Modify: `tests/test_forge_review.py` (tier-routing assertions; rename `test_standard_reviewer_maps_terra_medium` and `test_complex_reviewer_maps_sol_medium` to name the new model·effort)
- Modify: `tests/test_forge_final_review.py` (expected argv model id)
- Modify: `tests/test_forge_resume.py` (expected argv model id)
- Modify: `tests/test_forge_loop.py` (expected receipt model)
- Modify: `tests/test_forge_dispatch.py` (literal model id passed to the dispatch helper)
- Read only: `docs/forge/specs/codex-runner.md` sections Tier mapping, Live logs, Monitor — already amended to the same table

**Spec:** Routing — model and effort per tier

**Interface:** `TIER_MAP = {"trivial": ("gpt-6-luna", "low"), "standard": ("gpt-6-sol", "medium"), "complex": ("gpt-6-sol", "high")}`. No other name changes.

**Tests:**
- a standard task's reviewer dispatches with gpt-6-sol and effort medium
- a complex task's reviewer dispatches with gpt-6-sol and effort high
- the final review of an all-trivial plan routes to gpt-6-luna at effort low
- the final review of an all-standard plan routes to gpt-6-sol at effort medium
- the final review of a plan containing a complex task routes to gpt-6-sol at effort high
- a trivial worker's receipt records model gpt-6-luna
- no tier's effort is ultra, and max is never a default

**Acceptance:**
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `! git grep -n 'gpt-5\.6' -- scripts tests` — no stale model id remains in code or tests
- `python3 -c "import sys; sys.path.insert(0,'scripts'); import forge_common as f; assert f.TIER_MAP == {'trivial': ('gpt-6-luna','low'), 'standard': ('gpt-6-sol','medium'), 'complex': ('gpt-6-sol','high')}"` exits 0

**Tier:** standard

**Depends on:** nothing.
