# Halt Precision — design

Cross-harness. Phase 13 made reviews exhaustive and laps cheap. This phase fixes the other half of the same complaint: **forge halts on things that are not scope decisions.** Every halt in this class arrives with an obvious fix attached, costs a human round-trip and a re-invocation, and teaches the operator to distrust halts generally — which is worse than the individual stops.

Four distinct mechanisms produce it, plus one review-load defect found while exercising Phase 13. None is a convergence-rule change: the disposition matrix's existing cells, the 5-lap backstop, and `--autofix auto|gate` are untouched. This phase changes **what reaches** the matrix, not what the matrix does.

## Scope

- In: plan/spec lint at run start (`scripts/forge_lint.py`, new, shared); honoring the `convergence: "resolved"` label; multi-range location parsing and fail-loud on an unparseable location for a contract-breaking finding; a third runner-computed provenance value `in-run` with a new `seed` disposition; `coverage` required on discovery reviews only.
- Out: convergence rules, the backstop, `--autofix` semantics, the four existing matrix cells. Token/call budgets. Doc-sync. Live-watcher readability (DEFERRALS 2026-08-21). Auto-editing a plan or spec mid-run — a document is the contract, and a worker rewriting its own contract is self-dealing.

## Mechanisms being corrected

1. **Document defects** — a defect in the plan or spec can never be `in-diff`, because a task's diff contains code, not the document specifying it. Every such finding lands `pre-existing × contract-breaking` → halt, regardless of triviality. Observed twice in the Phase 13 run: a tier-parsing bug that made 11 of 13 plans unparseable, and a `**Spec:**` line violating documented grammar.
2. **Ignored convergence label** — `convergence` is parsed into `Finding` and then read by nothing. A finding labelled `resolved` still classifies as `fix` and drives `rework`, so a reviewer following the contract literally ("resolved findings may be listed") causes the runner to re-dispatch a repair for something already repaired, indefinitely, until the backstop.
3. **Silent location degradation** — `_parse_lines` accepts only `"12"` or `"12-20"`. Anything else returns `None`, provenance falls through to `pre-existing`, and a contract-breaking finding routes to halt. A reviewer naming five call sites is punished for precision.
4. **Cross-task defects** — a finding against code *this run* wrote, but outside *this task's* diff, is indistinguishable from genuinely pre-existing code by a diff-based check. It halts, though it is our own new work.
5. **Coverage output load** — `coverage` on every verdict adds output-token load to precisely the largest reviews. Two reviewers died mid-verdict on whole-plan reviews during the Phase 13 run.

## Plan lint

New shared module `scripts/forge_lint.py` — pure functions + CLI, mirroring `forge_dispose.py` / `forge_checklist.py` (one implementation, two harness callers).

Runs at **run start**: after the clean-tree precondition, before any dispatch. Codex: `forge-run.py` calls it in-process. Claude: the orchestrator invokes the CLI before its first task.

Checks, all against **documented grammar only** — never taste, never style:

| check | failure |
|---|---|
| every `### Task N:` heading at level 3, numbers unique | names the offending heading and line |
| `**Tier:**` present, valid after normalization, justification present for complex/trivial | names the task and value |
| `**Goal:**` present, single non-empty line | names the defect |
| `**Spec:**` single line, no parenthetical or `;`, every name resolving uniquely in the spec | names the unresolvable or ambiguous heading |
| `**Depends on:**` references existing task numbers, no cycles | names the missing task or the cycle |
| `**Acceptance:**` present per task | names the task |
| checklist generates for every task and for `--final` | names the task; an empty checklist is reported as a **warning**, not an error (it is a legal plan — Phase 13 spec) |

**Reports every defect in one run**, never the first only — the same anti-one-per-lap principle this work exists to install. Exit 1 with the full list on any error; warnings do not fail.

Lint must never reject a legal plan. `**Spec:**`, `**Global Constraints:**`, and prose acceptance are all optional per the planning skill; absence is never an error.

## Convergence label honored

A finding carrying `convergence: "resolved"` is dropped before disposition, making "listed as resolved" behave identically to "omitted" — which the Phase 7 spec already promises ("`resolved` findings may be listed (informational) or omitted").

**Guard:** the label is honored only when the finding's canonical id (`carried_from` or `id`) is in the prior attempt's carried-fix set. A `resolved` label on an id the runner never tracked is meaningless and is ignored — the finding is dispositioned normally. A false claim is still caught: the id reappearing later trips the existing regression rule against the runner's authoritative resolved-id set.

This is a **bug fix, not a rule change**. `convergence_decision` is not modified; the finding never reaches it.

## Location parsing

- `_parse_lines` accepts a comma-separated list of ranges (`"12-20,45,60-62"`) in addition to the existing forms. A finding is `in-diff` when **any** range intersects the diff's changed lines.
- An absent or unparseable location on a finding claiming `impact: "contract-breaking"` is a **verdict validation defect** — re-dispatched once naming the defect, then a contract error, reusing Phase 13's coverage-retry mechanism rather than a new one. It no longer degrades silently into `pre-existing`.
- An improvement finding may still carry no location; it defers regardless of provenance, unchanged.

## in-run provenance and the seed disposition

The runner computes three-way provenance, all verified against real diffs, never trusted from the reviewer:

| value | test |
|---|---|
| `in-diff` | intersects this review's diff (per-task: review base = prior commit) |
| `in-run` | intersects `git diff <run_base>` but not this task's diff |
| `pre-existing` | neither |

Matrix gains one cell; the existing four are unchanged:

| | contract-breaking | improvement |
|---|---|---|
| `in-diff` | **fix** | defer |
| `in-run` | **seed** | defer |
| `pre-existing` | **halt** | defer |

**`seed`**: logged, the run **continues**, and the finding is carried into the **final review's discovery packet** as a pre-seeded finding. Cross-task defects are integration defects, and integration review is where they can actually be judged. A task's rework loop editing another task's committed work would break the linear vertical-slice history the per-task review base depends on (Phase 5, Phase 12b).

Seeded findings persist in `run.json` under `seeded_findings`, survive a resume, and are surfaced in the end-of-plan summary whether or not the final review confirms them.

In the final review, `run_base` **is** the diff base, so `in-run` and `in-diff` coincide and `seed` is unreachable — no special case required.

## Coverage on discovery only

`coverage` is required on **discovery** verdicts and omitted on **verification** verdicts; validation skips it there. `REVIEW_VERDICT_INSTRUCTION` states this explicitly, and every packet marks which kind of review it is so the reviewer knows which contract applies.

Verification's scope is the prior findings plus the repair delta. A full contract sweep there is redundant — the discovery pass's coverage is what carries the exhaustiveness guarantee — and the redundant load is what killed two reviewers mid-verdict during the Phase 13 run.

## Receipts & state

- `run.json` gains `seeded_findings` (a list of `finding_to_dict()` entries), cleared at invocation start like `threads`, then appended across tasks.
- Receipts gain `seeded` alongside the existing disposition groups.
- Lint failures are a **contract error** (exit 1) before any receipt exists, consistent with a malformed plan today.

## Testing

- Lint: each check's failure case named precisely; all defects reported in one run, not the first; a legal minimal plan (no `**Spec:**`, no `**Global Constraints:**`, command-only acceptance) passes clean; empty checklist warns without failing; the real Phase 13 plan and the phase12b plan both pass.
- Convergence label: a listed `resolved` finding whose id was carried → dropped, loop converges to pass; a `resolved` label on an untracked id → ignored, dispositioned normally; a falsely-resolved finding reappearing next attempt → regression halt still fires.
- Location: comma-separated ranges parse, any-intersection wins; contract-breaking with absent/unparseable location → validation defect → one retry → contract error; improvement with no location still defers.
- `in-run`: a finding against an earlier task's committed work classifies `in-run` → `seed`, run continues, finding lands in `run.json` and in the final review's discovery packet; same finding in the final review classifies `in-diff`.
- Coverage: discovery verdict without coverage → defect; verification verdict without coverage → accepted.
- `test_forge_convergence.py` and `test_forge_classify.py`: classify tests will legitimately change (provenance is now three-way); **convergence tests must pass unchanged** — that file remains the proof that loop authority is untouched.

## Acceptance criteria

- A plan with a mechanical grammar defect fails in seconds at run start, naming every defect, before any dispatch.
- A reviewer that lists its resolved findings does not cause a repair loop.
- A reviewer that names several locations for one finding is classified on the diff, not routed to halt.
- A defect in an earlier task's work does not stop the run; it reaches the final review.
- A verification reviewer is not asked for a contract sweep.
- Halts that remain are scope decisions: genuinely pre-existing, genuinely contract-breaking.

## Risks

- **Seeded findings arriving in bulk** at the final review, making its discovery pass heavy. Bounded: they arrive pre-classified with contract refs, as prior findings rather than raw diff. Watch the first multi-task run.
- **Lint rejecting a legal plan** — the exact mistake Phase 13's empty-checklist rule made. Mitigated by checking only documented grammar and by warning rather than failing on the empty-checklist case; the legal-minimal-plan test is the guard.
- **Honoring `resolved` increases trust in the reviewer.** Bounded by the carried-set guard and by the regression rule, which is unchanged and still authoritative.
- **Three-way provenance touches `classify_findings`**, the most load-bearing pure function in the system. Its own tests change; the convergence tests must not.

## Touch points

`scripts/forge_lint.py` (new, shared), `scripts/forge_dispose.py` (three-way provenance, `seed` disposition, resolved-label filter, `_parse_lines`, location validation), `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION`), `scripts/forge-run.py` (lint at run start, seeded-findings state, final-review seeding, discovery/verification coverage gating), `scripts/forge_receipts.py` (`seeded`), `scripts/review-packet.py` (review-kind marker, seeded findings in the final discovery packet), `skills/planning/SKILL.md` and `skills/planning/codex-execution.md` (lint step, `seed` cell, coverage-on-discovery-only), changelog pointers in `2026-07-16-phase7-scope-autonomy-design.md` and `2026-08-21-review-continuity-design.md`, `tests/`.
