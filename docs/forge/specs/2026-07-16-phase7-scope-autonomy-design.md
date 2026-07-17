# Runner Scope Autonomy — design

Phase 7 (Codex runner). Replaces the runner's raw 2-iteration rework cap and single-shot final-review human gate with a **disposition matrix** (fix / defer / halt) driven by two per-finding axes, a **convergence-based** stop condition, an auto-fix-by-default autonomy knob, and a **terminal doc-sync stage**. Goal: let the runner rework as far as it is *converging*, fix only what threatens the phase's contract, defer what is harmless, and halt only on a genuine human decision.

Codex-only (`scripts/forge-run.py` and its reviewer verdict contract). The Claude path is Phase 8 (DEFERRALS 2026-07-13); no mixed implementation. Consumes the tier-policy floor as the escalation *target*, but does **not** auto-escalate model/effort — plain progress-based rework only (deliberate; the effort bump stays a human on-halt action).

## Scope

- In: the fix/defer/halt disposition matrix; per-finding provenance + contract-impact classification with runner-side verification; convergence-based rework stop (resolved/carried/new labels → deterministic runner decision) replacing the count cap; final (whole-plan) review gains the same rework loop; `--autofix auto|gate` chosen at the execution offer (default `auto`); terminal doc-sync stage after final review passes; deferral collection surfaced at completion; backstop rework ceiling raised from 2 to 5.
- Out: the Claude dispatch/inline path (Phase 8). Auto model/effort escalation. Auto-appending drafted repair tasks to the plan (the never-shipped `--widen auto` behavior) — halts instead carry a drafted repair task for the human. Doc-sync authoring *new* documentation (reconciliation of existing docs only).

## Disposition matrix

Every review finding (per-task and final) is classified on two independent axes and dispatched by quadrant. Only the top-left quadrant is ever auto-fixed.

| | **contract-breaking** | **improvement-only** |
|---|---|---|
| **in-diff** (touched by this review's diff) | **fix** — rework in-loop | **defer** — log, never fix (no gold-plating our own new code) |
| **pre-existing** (outside the diff) | **halt** — human scope decision; carry drafted repair task | **defer** — harmless; log |

- **Provenance** (`in-diff` | `pre-existing`) — runner-verified against the actual diff, not trusted from the reviewer. A finding whose `location.lines` intersect the diff's changed line ranges is `in-diff`; otherwise `pre-existing`, **overriding** any optimistic reviewer claim. Per-task diff base = task's review base (prior commit); final-review diff base = run-start HEAD.
- **Contract impact** (`contract-breaking` | `improvement`) — `contract-breaking` requires a non-null `contract_ref` naming an acceptance criterion or spec section the finding violates ("named evidence", mirroring the tier-policy floor rule). A finding with null `contract_ref` is downgraded to `improvement` (→ defer) by the runner regardless of the reviewer's label.
- The runner **derives** disposition from (verified provenance × verified impact); the reviewer proposes but does not decide.

## Reviewer verdict contract

Reviewer (`codex exec`) emits one parseable JSON object; last such object in the stream is authoritative (unchanged parse rule). Schema per finding:

```json
{
  "verdict": "pass" | "findings",
  "findings": [
    {
      "id": "f1",
      "summary": "one line",
      "location": {"file": "path", "lines": "12-20"},
      "provenance": "in-diff" | "pre-existing",
      "impact": "contract-breaking" | "improvement",
      "contract_ref": "acceptance criterion / spec §" | null,
      "convergence": "resolved" | "carried" | "new" | null,
      "carried_from": "f1" | null,
      "repair_task": { "title": "…", "files": ["…"], "spec": "…", "tests": ["…"], "acceptance": ["…"], "tier": "standard" } | null
    }
  ]
}
```

- `convergence` set only on rework re-reviews (attempt ≥ 2); the re-review packet carries the **prior attempt's findings with ids** so the reviewer labels each current finding against them. `carried` findings echo the prior id in `carried_from`. `resolved` findings may be listed (informational) or omitted — the runner treats "prior fixable id absent from current findings" as resolved.
- `repair_task` (plan-task form) required only on findings the runner will `halt` — it is the payload of the human gate, never auto-applied. Optional elsewhere.
- Unparseable verdict, or a `contract-breaking` finding missing `location` → loud runner failure naming the cause (no silent retry). This is a contract error (exit 1), distinct from a task halt (exit 2).

## Rework loop & convergence

Per task (standard/complex; trivial = acceptance only, no reviewer). Each attempt: worker → acceptance → reviewer → classify. An **execution failure** (worker crash, worker timeout, acceptance non-zero) preempts the reviewer as today and is treated as an implicit `fix`-retry finding with no provenance/impact — it never defers and never scope-halts, but it is subject to the regression and backstop rules below. Then the runner picks one deterministically, in this precedence:

1. Any review finding with disposition **halt** present → **HALT** (receipt with `outstanding_findings` + drafted `repair_task`; do not start next task; exit 2). Halt reason = `scope-decision`.
2. **Regression** — a finding the runner previously recorded `resolved` reappears (matched by id / `carried_from`), **or** acceptance went green→red since the prior attempt → **HALT** (reason `regression`). This is the "shuffling one bad state to another" case: a fix undid an earlier fix, or broke acceptance. A *newly-surfaced* finding never seen before is **not** a regression — incremental reviewer discovery is allowed.
3. **Stuck** — a `fix` finding `carried` across two consecutive attempts (worker cannot resolve it) → **HALT** (reason `stuck`).
4. No `fix` findings remain (only defers, or clean) and acceptance green → **PASS**. Defers collected (below).
5. **Backstop** — attempt count reaches `MAX_ATTEMPTS_BACKSTOP` (5) → **HALT** (reason `backstop`).
6. Otherwise → **rework**: re-dispatch the worker with the outstanding `fix` findings appended to the brief (unchanged mechanism); carry the finding set + the runner's resolved-id set into the next re-review packet.

Net progress each round is **not** required (a round may resolve one finding and surface another); the backstop (5) is the honest bound on slow non-convergence. The runner owns the authoritative **resolved-id set** across attempts — reappearance in (2) is judged against it, not against the reviewer's self-labeling, so a reviewer mislabeling a reappearance as `new` is still caught. Convergence is the primary control; the count in (5) is only a seatbelt. `--gate` mode (below) short-circuits: any finding at all → HALT before step 1 (reason `gate`).

## Final review

Whole-plan review after every task passes now runs the **same loop** (previously single-shot). Diff base = run-start HEAD; tier = plan's highest task tier (unchanged). Same disposition matrix, same convergence stop, same backstop. The final-review "worker" is a `codex exec` fix dispatch scoped to the outstanding `fix` findings against the whole-plan diff. Halt payload identical (drafted repair task). On PASS, proceed to doc-sync.

## Deferral handling

- Defer-quadrant findings (both right-column cells, plus the never-fixed pre-existing set) are **collected**, not fixed and not halted: recorded structurally in the task/final receipt and aggregated into `run.json` under `deferrals`.
- The runner does **not** write `DEFERRALS.md` mid-loop (keeps memory-file writes out of the hot path and the runner off the target repo's curated docs). At clean completion it emits the deferral list in the end-of-run summary and the completion notification; the conversational orchestrator appends them to `docs/forge/DEFERRALS.md` (project-memory format) as a reviewed batch.
- Each deferral carries: summary, location, provenance, and why-harmless (the reviewer's `impact=improvement` rationale).

## Autonomy flag

- `--autofix auto|gate`, chosen at the execution offer (same gate that discloses tier routing), default **`auto`**.
- `auto`: run the disposition matrix — fix `in-diff`+`contract-breaking`, defer the right column, halt only `pre-existing`+`contract-breaking`.
- `gate`: conservative — **any** finding halts (receipt + drafted repair task), no auto-fix. The pre-Phase-7 behavior, retained as an opt-in escape hatch.
- The runner never auto-fixes without a mode chosen by a human at the offer. Supersedes the never-shipped `--widen gate|auto` (4b); no back-compat (4b was backed out).

## Terminal doc-sync stage

Runs once, after final review PASSES (never masks a code defect as drift — everything else is green first). A `codex exec` dispatch reconciles **existing** documentation against the shipped whole-plan diff: stale references, changed signatures/behavior, spec changelog, ROADMAP status. Bounded — updates docs the diff affects or that reference changed surfaces; does **not** author new docs (that would be the gold-plating the matrix forbids). Commits as a `docs: sync` commit (vertical-slice discipline). If it surfaces a doc/contract contradiction it cannot mechanically reconcile, it **halts** with the contradiction named; otherwise the run completes green. Reported in the completion summary/notification.

## Commit discipline

- Per-task fix rework: within the existing per-task commit (task commits after passing, rework included). Unchanged.
- Final-review fixes: a single `fix: final-review` commit once final review passes.
- Doc-sync: `docs: sync` commit after final review.
- Clean-tree precondition and per-task review base (prior commit) unchanged from Phase 5.

## Receipts / run.json

- Receipt gains per-finding classification (`provenance`, `impact`, `contract_ref`, `disposition`, `convergence`), the derived disposition, and (on halt) the drafted `repair_task`. `status` adds nothing new — `passed` | `rework` | `escalated` (halt) — but escalated receipts distinguish halt reason (scope-decision | regression | stuck | backstop | gate).
- `run.json` gains `deferrals` (aggregated), `autofix_mode`, and a `doc_sync` terminal record. `--status` output lists deferrals and the halt reason class.

## Retirements / doc changes

- `MAX_ATTEMPTS = 2` → `MAX_ATTEMPTS_BACKSTOP = 5` (`forge_common.py`); resolves the tier-policy spec's "revisit the cap in Phase 7" note.
- `review-packet.py` gains a `--prior-findings` input (prior attempt's findings JSON) for convergence labeling on re-reviews.
- `skills/planning/codex-execution.md`: document `--autofix` mode at the execution offer, the disposition matrix, convergence stop, doc-sync stage, and the orchestrator's DEFERRALS write-back at completion.
- `2026-07-13-codex-exec-runner-design.md`: changelog pointer noting the rework/final-review machinery is superseded here (living-spec rule; the runner spec is not rewritten).

## Testing

- pytest fixtures over reviewer verdicts, one per matrix quadrant → correct derived disposition; provenance override (reviewer says `in-diff`, `location.lines` outside diff → `pre-existing`); null `contract_ref` downgrade → defer.
- Convergence sequences → correct stop: progress (resolved, fixable remain → rework), regression (new `fix` finding → halt), stuck (same id carried ×2 → halt), backstop (5 attempts → halt), clean (no fixable → pass).
- Final-review loop parity: findings → fix → re-review → pass; halt payload.
- Deferral aggregation into `run.json`; runner does not touch `DEFERRALS.md`.
- `--autofix gate`: any finding halts, no fix dispatch. `--autofix auto`: matrix applied.
- Doc-sync: stale-doc fixture reconciled + committed; unresolved contract contradiction → halt.
- Codex integration: forced multi-quadrant findings drive fix-then-pass on `auto`, halt on the pre-existing/contract-breaking quadrant, deferrals surfaced, doc-sync commit present.

## Acceptance criteria

- A converging task (each attempt resolves a `fix` finding) runs past the old cap of 2 to a clean pass without human intervention.
- A churning task (fix introduces a new contract-breaking finding, or breaks acceptance) halts at the churn, not at an arbitrary count.
- A `pre-existing`+`contract-breaking` finding halts with a drafted repair task; a `pre-existing`+`improvement` or any `in-diff`+`improvement` finding defers and the run continues.
- Final review fixes its own `fix` findings in-loop and only halts on the same conditions as a task.
- `--autofix gate` reproduces pre-Phase-7 halt-on-any-finding behavior.
- Run completes with a `docs: sync` commit and a deferral list surfaced at completion.

## Risks / constraints

- Reviewer misclassification of contract impact → over- or under-fixing. Mitigation: provenance is runner-verified (not trusted); `contract-breaking` requires named `contract_ref` or is downgraded to defer. Residual risk is a reviewer citing a spurious `contract_ref` — bounded to the in-diff surface (the fix cannot leave the diff), so blast radius is the phase's own code.
- Convergence labels depend on the reviewer tracking finding identity across attempts. Mitigation: prior findings + ids are supplied in the re-review packet; `carried_from` echoes the id. A mislabeled `resolved` that is actually unresolved re-appears as `new`/`carried` next attempt and is caught by the regression/stuck rules.
- Backstop of 5 is a starting value; tune on the halt-mix data Phase 5's receipts produce.
- Doc-sync auto-edits docs. Bounded to reconciliation of existing docs, gated behind an all-green run, committed separately so a bad sync is trivially revertible.

## Changelog

2026-07-17 (phase 12b): The disposition-matrix and convergence functions specified here (`diff_line_ranges`, `verify_provenance`, `derive_disposition`, `classify_findings`, `ConvergenceState`, `convergence_decision`, `advance_state`) were extracted from `forge-run.py` into shared `scripts/forge_dispose.py`, with a CLI so the Claude dispatch path can call the identical decision logic. Behavior unchanged on Codex; the Claude path gains the same disposition matrix + convergence this spec designed, reaching cross-harness parity. See `2026-07-17-phase12b-claude-dispatch-parity-design.md`.
