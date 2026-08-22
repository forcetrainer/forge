# Halt Precision (Phase 14) Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Stop routing non-scope-decisions into the halt quadrant — by catching document defects before dispatch, honoring the resolved label, parsing locations properly, giving cross-task defects a home, and cutting redundant coverage load.
**Architecture:** A new shared `scripts/forge_lint.py` validates plan/spec grammar at run start on both harnesses. `scripts/forge_dispose.py` gains a third provenance value (`in-run`), a fifth disposition (`seed`), a resolved-label filter applied before disposition, and stricter location parsing. `scripts/forge-run.py` wires lint into run start, persists seeded findings in `run.json`, and carries them into the final review's discovery packet. Coverage becomes discovery-only.
**Tech stack:** Python 3 stdlib (argparse, json, subprocess, dataclasses, re); pytest.
**Global Constraints:** `tests/test_forge_convergence.py` must pass **unchanged** — it alone remains the proof that loop authority is untouched. **`tests/test_forge_classify.py` legitimately changes in Task 4**, because provenance becomes three-way; this inverts the Phase 13 rule where both files were untouched, and a worker must not weaken a convergence test to accommodate a classification change. The disposition matrix's four existing cells, the 5-lap backstop, and `--autofix auto|gate` semantics are untouched — this phase changes what *reaches* the matrix, not what the matrix does. No live `codex` binary is available: every dispatch test is fixture-based, argv asserted and event streams replayed, never executed. New parameters take defaults so existing call sites and tests stay untouched. `forge_lint.py` imports `forge_common` as a plain module, preserving one `Finding`/`Verdict` identity (2026-07-14 decomposition hazard). Baseline suite: 404 passed, 2 skipped. **Test-run discipline:** the full suite takes ~47s and a targeted file takes ~0.2s. Run targeted tests during TDD; run the full suite EXACTLY ONCE per task, at the end, to confirm no regression. Re-running the full suite through a TDD cycle was measured as roughly a third of a task's elapsed time in Phase 13 and buys nothing a targeted run plus one final check does not.

## File structure

- `scripts/forge_lint.py` (create) — plan/spec grammar validation + CLI. Single responsibility: report defects; never fixes, never dispatches.
- `scripts/forge_dispose.py` (modify) — three-way provenance, `seed` disposition, resolved-label filter, `_parse_lines` multi-range, location validation. Still decides only.
- `scripts/forge_common.py` (modify) — `REVIEW_VERDICT_INSTRUCTION`: discovery-only coverage, multi-range locations, location required for contract-breaking.
- `scripts/forge-run.py` (modify) — lint at run start, `seeded_findings` state, final-review seeding, discovery/verification coverage gating.
- `scripts/forge_receipts.py` (modify) — `seeded` disposition group on receipts and `seeded_findings` in `run.json`.
- `scripts/review-packet.py` (modify) — review-kind marker; seeded findings in the final discovery packet.
- `skills/planning/SKILL.md`, `skills/planning/codex-execution.md` (modify) — lint step, `seed` cell, coverage-on-discovery-only.
- `tests/test_forge_lint.py`, `tests/test_forge_seed.py` (create); `tests/test_forge_classify.py`, `tests/test_forge_coverage.py` (extend).
- `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md`, `docs/forge/specs/2026-08-21-review-continuity-design.md`, `docs/forge/ROADMAP.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (modify) — changelog pointers, phase status, lockstep 0.10.0.

### Task 1: Plan/spec lint
- [x] Done

**Files:**
- Create: `scripts/forge_lint.py`
- Test: `tests/test_forge_lint.py`

**Spec:** Plan lint

**Interface:**
- `LintDefect` dataclass: `severity: str` (`error` | `warning`), `where: str` (file and line or task number), `message: str`.
- `lint_plan(plan_path, spec_path=None) -> list[LintDefect]` — returns **every** defect found, never short-circuits on the first.
- `main(argv)` CLI: `forge_lint.py <plan.md> [--spec <spec.md>]` → prints one line per defect, exit 1 if any `error`, exit 0 if only warnings or clean.
- Checks: task headings at level 3 with unique numbers; `**Tier:**` present and valid after normalization with justification where required; `**Goal:**` present and a single non-empty line; `**Spec:**` single-line, free of parentheticals and `;`, every name resolving uniquely against the spec; `**Depends on:**` referencing existing task numbers with no cycles; `**Acceptance:**` present per task; a checklist generating for every task and for `--final`.
- An empty checklist is a **warning**, not an error — a legal plan may have no contract material (Phase 13 spec).
- Reuses `forge_plan.parse_plan_tasks`, `forge_checklist`, and `extract-brief.py`'s section resolution rather than reimplementing any grammar.

**Tests:** each check's error case reported with its own message naming the offending task or heading; multiple simultaneous defects all reported in one call, not just the first; a **legal minimal plan** (no `**Spec:**`, no `**Global Constraints:**`, command-only `**Acceptance:**`) lints clean; empty checklist produces a warning and exit 0; dependency cycle detected and named; dependency on a nonexistent task named; duplicate task number named; wrong heading level named; the real `2026-08-21-phase14-halt-precision.md` and `2026-07-17-phase12b-claude-dispatch-parity.md` plans both lint clean.

**Acceptance:** `python3 -m pytest -q tests/test_forge_lint.py` all pass; `python3 scripts/forge_lint.py docs/forge/plans/2026-08-21-phase14-halt-precision.md --spec docs/forge/specs/2026-08-21-halt-precision-design.md` exits 0.

**Tier:** standard

**Depends on:** nothing.

### Task 2: Honor the convergence resolved label
- [x] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (resolved-label filter ahead of disposition)
- Test: `tests/test_forge_classify.py` (extend)

**Spec:** Convergence label honored

**Interface:**
- `classify_findings` drops a finding carrying `convergence == "resolved"` **before** disposition, so a listed resolved finding behaves identically to an omitted one.
- **Guard:** the label is honored only when the finding's canonical id (`carried_from` or `id`) is present in the prior attempt's carried-fix set. Signature gains `carried_ids=None`; when absent or when the id is not a member, the label is ignored and the finding is dispositioned normally.
- `convergence_decision` is **not** modified — dropped findings never reach it.

**Tests:** a listed `resolved` finding whose canonical id was carried → dropped, no fix disposition, loop converges to pass; a `resolved` label on an id absent from the carried set → ignored, dispositioned normally; `resolved` echoing `carried_from` matched against the carried set, not the raw id; a falsely-resolved finding reappearing on a later attempt still trips the regression rule against the runner's authoritative resolved-id set; `carried_ids=None` preserves today's behavior exactly.

**Acceptance:** `python3 -m pytest -q tests/test_forge_classify.py tests/test_forge_dispose.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py` passes **unchanged**.

**Tier:** standard

**Depends on:** nothing.

### Task 3: Location parsing and validation
- [x] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (`_parse_lines`, location validation)
- Modify: `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION` location wording)
- Test: `tests/test_forge_classify.py` (extend), `tests/test_forge_coverage.py` (extend)

**Spec:** Location parsing

**Interface:**
- `_parse_lines` returns a **list** of `(lo, hi)` ranges, accepting `"12"`, `"12-20"`, and comma-separated combinations. Provenance is `in-diff` when **any** range intersects the diff's changed lines.
- `validate_locations(verdict) -> list[str]` — a finding claiming `impact: "contract-breaking"` with an absent or unparseable `location.lines` is a verdict validation defect, reported in the same shape as coverage defects and re-dispatched through the **existing** Phase 13 retry rather than a new mechanism.
- An improvement finding may carry no location and still defers, unchanged.
- `REVIEW_VERDICT_INSTRUCTION` states that `lines` accepts a single range or a comma-separated list, and that a contract-breaking finding must carry a parseable location.

**Tests:** single number, single range, comma-separated list, and mixed forms all parse; any-range intersection yields `in-diff` while no intersection yields `pre-existing`; whitespace tolerated; a genuinely malformed string is reported as a defect rather than silently returning empty; contract-breaking with absent location → defect; contract-breaking with unparseable location → defect; improvement with no location → no defect, defers; the retry path fires once then contract-errors, reusing Phase 13's mechanism (assert no second mechanism was added).

**Acceptance:** `python3 -m pytest -q tests/test_forge_classify.py tests/test_forge_coverage.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py` passes unchanged.

**Tier:** standard

**Depends on:** Task 2.

### Task 4: Three-way provenance and the seed disposition
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (`verify_provenance`, `derive_disposition`, `classify_findings`)
- Modify: `scripts/forge_receipts.py` (`seeded` disposition group)
- Test: `tests/test_forge_classify.py` (extend — **this file legitimately changes**)

**Spec:** in-run provenance and the seed disposition

**Interface:**
- `verify_provenance(finding, ranges, run_ranges=None)` returns `in-diff` | `in-run` | `pre-existing`. `in-run` = intersects `run_ranges` (the run's cumulative diff from run-start HEAD) but not this review's `ranges`. With `run_ranges=None` the function is two-way exactly as today.
- `derive_disposition` gains one cell: `in-run` × contract-breaking → `seed`. The existing four are unchanged: `in-diff` × contract-breaking → `fix`; `pre-existing` × contract-breaking → `halt`; every improvement → `defer`.
- `classify_findings(verdict, diff, run_diff=None, carried_ids=None)` — `run_diff` absent reproduces today's two-way behavior.
- `decision.json` and receipts gain a `seeded` group alongside `fix`/`defer`/`halt`.
- In a final review the run base **is** the review base, so `in-run` is unreachable there; no special case is written for it.

**Tests:** a finding intersecting the run diff but not the task diff → `in-run` → `seed`; intersecting the task diff → `in-diff` → `fix` (unchanged); intersecting neither → `pre-existing` → `halt` (unchanged); an `in-run` improvement → `defer`; `run_diff=None` reproduces two-way classification exactly (the back-compat proof); `seeded` group present in `decision.json`; a seeded finding does not appear in the fix set and so cannot drive rework; convergence outcomes for the existing four cells are byte-identical to before.

**Acceptance:** `python3 -m pytest -q tests/test_forge_classify.py tests/test_forge_dispose.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py` passes **unchanged** — classification changed, loop authority did not.

**Tier:** standard

**Depends on:** Task 3.

### Task 5: Runner wiring — lint, seeded state, final-review seeding
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (lint at run start, `seeded_findings` state, final-review discovery seeding)
- Modify: `scripts/review-packet.py` (seeded findings in the final discovery packet)
- Modify: `scripts/forge_receipts.py` (`seeded_findings` in `run.json`)
- Test: `tests/test_forge_seed.py`

**Spec:** Plan lint, in-run provenance and the seed disposition

**Interface:**
- `run_plan` calls `forge_lint.lint_plan` after the clean-tree precondition and **before any dispatch**. Any `error` defect is a contract error (exit 1) printing every defect; warnings print and continue.
- **Both** `classify_findings` call sites (`execute_task` and `run_final_review_loop`) pass `run_diff` so `in-run` is computed, **and pass `carried_ids=state.carried_ids`** (read before `advance_state` runs) so Task 2's resolved-label filter is actually live. Without this wiring Task 2's fix is dead code — correct, tested, and never invoked. `execute_task` appends `seed`-disposition findings to a `seeded_findings` list persisted in `run.json` — cleared at invocation start like `threads`, appended across tasks, surviving a resume.
- `run_final_review_loop`'s **discovery** packet carries the seeded findings as pre-seeded prior findings; verification packets are unchanged.
- Seeded findings appear in the end-of-plan summary whether or not the final review confirms them.

**Tests:** a plan with a grammar error fails at run start naming every defect, with no dispatch occurring (assert the fake codex was never invoked); a warning-only plan proceeds; an end-to-end rework lap where the reviewer LISTS a prior finding as `convergence: "resolved"` converges to pass instead of reworking — the proof Task 2's filter is wired, not merely present; a finding against an earlier task's committed work classifies `in-run`, is seeded, does not halt the run, and the run continues to the next task; `seeded_findings` written to `run.json` and cleared on a fresh invocation; seeded findings appear in the final review's discovery packet and not in verification packets; the end-of-plan summary lists them.

**Acceptance:** `python3 -m pytest -q tests/test_forge_seed.py` all pass; `python3 -m pytest -q` shows no regression against the 404 passed / 2 skipped baseline.

**Tier:** standard

**Depends on:** Task 1, Task 4.

### Task 6: Coverage on discovery only
- [ ] Done

**Files:**
- Modify: `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION` coverage wording)
- Modify: `scripts/forge_dispose.py` (`validate_coverage` skipped for verification)
- Modify: `scripts/forge-run.py` (`_review_with_coverage` gating by review kind)
- Modify: `scripts/review-packet.py` (review-kind marker in the packet)
- Test: `tests/test_forge_coverage.py` (extend)

**Spec:** Coverage on discovery only

**Interface:**
- Packets carry an explicit review-kind marker (`discovery` | `verification`) so the reviewer knows which contract applies.
- `REVIEW_VERDICT_INSTRUCTION` states that `coverage` is required on discovery verdicts and omitted on verification verdicts.
- `_review_with_coverage` validates coverage only on discovery; a verification verdict without `coverage` is accepted without defect or retry.

**Tests:** discovery verdict lacking coverage → defect → one retry → contract error (unchanged); verification verdict lacking coverage → accepted, no retry; verification verdict *carrying* coverage → accepted, not rejected for carrying extra; the review-kind marker present and correct in both packet kinds; a discovery lap after a verification lap still demands coverage.

**Acceptance:** `python3 -m pytest -q tests/test_forge_coverage.py` all pass; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 3.

### Task 7: Cross-harness canon
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (lint step at run start; the `seed` cell in the dispatch finding-handling matrix; coverage-on-discovery-only; multi-range locations)
- Modify: `skills/planning/codex-execution.md` (runner specifics for all four)

**Spec:** Plan lint, in-run provenance and the seed disposition, Coverage on discovery only

**Interface:** Prose only, no code. Must state: lint runs at run start on both harnesses and reports every defect before any dispatch; the matrix's fifth cell (`in-run` × contract-breaking → `seed`, carried to final review, run continues); that the four existing cells are unchanged; coverage required on discovery and omitted on verification; `lines` accepting a comma-separated list with a location required for contract-breaking findings. Convergence, backstop, and `--autofix` prose must remain untouched.

**Tests:** None (documentation). Verified by the acceptance greps and Task 8's changelog consistency.

**Acceptance:** `grep -c 'forge_lint' skills/planning/SKILL.md` ≥ 1; `grep -c 'seed' skills/planning/SKILL.md` ≥ 1; `grep -c 'lint' skills/planning/codex-execution.md` ≥ 1; `grep -n 'backstop of \*\*5\*\*' skills/planning/SKILL.md` still matches; `python3 -m pytest -q` unchanged.

**Tier:** standard

**Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6.

### Task 8: Changelog pointers, roadmap, lockstep version bump
- [ ] Done

**Files:**
- Modify: `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md`, `docs/forge/specs/2026-08-21-review-continuity-design.md` (changelog lines)
- Modify: `docs/forge/ROADMAP.md` (Phase 14 `[spec'd]` → `[done]`, plan link)
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (0.9.0 → 0.10.0)

**Spec:** Touch points

**Interface:** Changelog lines follow the existing dated form: `2026-08-21 (phase 14): <what changed> (commit <sha>)`. Both manifests carry the identical version string. 0.10.0 rather than 0.9.1 — lint and the `seed` disposition are new capability, not only fixes.

**Tests:** None (mechanical edits).

**Acceptance:** `grep -h '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json` shows `0.10.0` twice and nothing else; both amended specs carry a `2026-08-21 (phase 14)` changelog line; ROADMAP Phase 14 reads `[done]` with a plan link; `python3 -m pytest -q` shows no regression.

**Tier:** trivial — version strings, two changelog lines, one status word; no logic, no design content, one call site each.

**Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7.
