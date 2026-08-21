# Review Continuity & Contract Coverage — design

Cross-harness. Two independent defects make review loops expensive without making them better: reviews are not **exhaustive** (a review returns when it has found *something*, so findings arrive one per lap), and every lap **rebuilds context from zero** (a fresh worker patches code it did not write; a fresh reviewer re-reads the full packet). Fix: a machine-checked **contract-coverage** requirement on the discovery review, and **session continuity** for the worker and for the verification reviewer.

Convergence rules, the disposition matrix, the 5-lap backstop, and `--autofix auto` are **unchanged** — this reduces the work each lap re-does and the number of laps needed, not the loop's authority to run.

## Scope

- In: contract checklist generation (`scripts/forge_checklist.py`, new, shared); `coverage` array added to the reviewer verdict contract with runner-side completeness validation + one retry; worker session continuity across rework laps; reviewer session continuity for **verification** laps only; delta-scoped verification packets; final-review fixer continuity and de-pasted brief; Codex `--json` dispatch with `thread.started` capture and event→live-log rendering; Claude adapter via `Agent`/`SendMessage`.
- Out: convergence-rule changes (backstop stays 5; "resolved one, surfaced one → rework" stays). Token/call/monetary budgets and monitor cost telemetry. Doc-sync tier or brief changes (belongs to the deferred doc revamp, DEFERRALS 2026-07-17). `--output-schema` verdict forcing (Codex-only capability; would fork the shared verdict contract). Auto model/effort escalation.

## Mechanism being corrected

1. Nothing defines *done* for a review → single-finding returns → each finding costs a full lap.
2. Rework dispatches a **new** worker with findings + a pasted diff and no memory of intent → narrow patch, next facet surfaces next lap. The halt's `repair_task` succeeds on human approval because five laps of receipts assembled the diagnosis the first fixer lacked.
3. Final review pays this at whole-plan scale: ~2 full-plan cold context loads per lap (reviewer + fixer), both with spec and whole-plan diff pasted into the prompt.

## Contract checklist

New shared module `scripts/forge_checklist.py` — pure functions + CLI, mirroring the `forge_dispose.py` pattern (one implementation, two harness callers). Codex's `review-packet.py` imports it; the Claude orchestrator invokes the CLI.

Checklist items are derived **mechanically** from existing plan/spec grammar — no new authoring burden, no new plan fields:

| id form | source |
|---|---|
| `spec:<slug>` | each spec section named on a `**Spec:**` line (the task's own for a task review; the union across all tasks for final review), resolved via `extract-brief.py`'s `find_spec_sections` |
| `g<N>` | each clause of the plan header's `**Global Constraints:**` |
| `t<N>.a<M>` | each `;`-separated clause of task N's `**Acceptance:**` line **whose content is not solely an inline-code command** — those are already executed deterministically by the acceptance runner and would be dead checklist weight |
| `t<N>` | final review only: task N's title, as an integration item |

CLI: `forge_checklist.py <plan.md> --spec <spec.md> [--task N | --final] [--out <path>]` → JSON `[{"id", "source", "text"}]` and a rendered `## Contract checklist` markdown section. Fail-loud on an unresolvable `**Spec:**` name (reuses `find_spec_sections`' existing raise) — never a silently thin checklist, matching the packet contract.

An empty checklist is a **library-level** contract error: `forge_checklist.py` invoked directly (CLI or import) raises naming the absent source, because an author who explicitly asks for a checklist and gets nothing has a defect to see.

At the **runner/orchestrator** layer it is not an error. A task with no `**Spec:**` line, no `**Global Constraints:**`, and an `**Acceptance:**` of nothing but inline-code commands is a *legal* plan — the skill makes both fields optional — and such a task has no contract material for a reviewer to cover. Forcing an error there would make legal plans unexecutable. Instead the runner **skips the coverage step for that review** (no checklist section in the packet, no `coverage` validation, no retry) and records `coverage_skipped: true` on the receipt, surfaced in the end-of-plan summary. Skipped, visibly — never silently. This is the "checklist quality is plan quality" risk made observable rather than fatal: a plan that earns no coverage enforcement says so on its receipts.

Trivial-tier tasks are unaffected — they skip reviewer dispatch entirely, so no checklist is generated for them.

## Reviewer verdict contract — `coverage`

`REVIEW_VERDICT_INSTRUCTION` (shared verbatim by the Codex runner's reviewer prompt and the Claude reviewer subagent) gains a **required** `coverage` array on every verdict, `pass` included:

```json
{
  "verdict": "pass" | "findings",
  "coverage": [
    {"id": "spec:Convergence stop", "status": "satisfied" | "violated" | "n/a", "evidence": "file:line, hunk, or why n/a"}
  ],
  "findings": [ ... ]
}
```

Validation (shared, in `forge_dispose.py` alongside the existing verdict parse — the reviewer proposes, the runner decides):

- every checklist id appears exactly once; missing or unknown ids → invalid;
- every `violated` id is named by the `contract_ref` of at least one finding; a `violated` with no finding → invalid;
- `evidence` non-empty on every entry.

On invalid: **one retry**, re-dispatching with the specific defect named (missing ids listed; unbacked `violated` ids listed). A second invalid verdict is a contract error (exit 1), consistent with the existing unparseable-verdict behavior.

Verification laps (below) carry the **reduced** checklist — only the items referenced by the outstanding findings — not the full one.

`n/a` requires a reason in `evidence`; it is the honest escape for a checklist item the diff cannot touch, and it is what keeps the requirement from degrading into rubber-stamping.

## Session continuity

| dispatch | today | after |
|---|---|---|
| task worker, lap 1 | cold | cold |
| task worker, rework lap | cold | **resume** — prompt is the findings alone |
| task reviewer, discovery | cold | **cold — unchanged, deliberately** |
| task reviewer, verification | cold, full packet | **resume** — prompt is the repair delta + outstanding findings |
| final reviewer, discovery | cold | **cold — unchanged, deliberately** |
| final reviewer, verification | cold, whole-plan diff re-pasted | **resume** — repair delta only |
| final-review fixer | cold every attempt, spec + whole-plan diff pasted | **cold once, then resume**; brief carries findings + affected paths only |

**Discovery review stays cold.** An independent first read is the entire justification for a separate reviewer (DECISIONS 2026-07-16, 2026-07-17); resuming it for discovery would hand the review to an agent that already holds the worker's reasoning. Verification is a narrower ask — "are f1–f4 resolved, did the repair break what it touched" — where the residual bias risk is under-flagging *new* issues, which is precisely what the coverage requirement on the discovery pass guards. This is a deliberate qualification of the fresh-context rule, not an exception to it: **fresh for discovery, resumed for verification.**

### Codex mechanics

- Every `codex exec` dispatch runs with `--json`. The runner parses the first `{"type":"thread.started","thread_id":...}` event and persists `thread_id` in run state, keyed by role (`task-N-worker`, `task-N-reviewer`, `final-reviewer`, `final-fixer`). `~/.codex/sessions` is never inspected; `--last` is never used (wrong-session hazard).
- Resume form: `codex exec resume --json --output-last-message <path> -m <model> -c 'model_reasoning_effort="<effort>"' <thread_id> <prompt>`. Tier pinning and last-message verdict capture are preserved on resume.
- `--ephemeral` must never be passed — it defeats persistence.

### Live-log rendering (required, not incidental)

`--json` replaces the human-readable stream `run_teed` currently tees to `task-N-live.log`, which `forge-monitor.py` renders. The runner must translate the event stream into readable lines in the live log so the monitor is unaffected. Raw JSONL is retained alongside for thread capture and debugging. **A monitor that shows JSON noise is a failed implementation of this spec.**

The bar is **parity with today's live view, not improvement** — the existing output already reads like a debug trace, and redesigning it is explicitly deferred until the new loop has run a few cycles (DEFERRALS 2026-08-21). No task in this phase may spend effort on live-log readability beyond preserving what is there.

### Continuity scope and failure

- Resume is scoped to **one runner invocation**. After a halt the human may hand-edit code; the persisted session's context is then stale and misleading, so re-invocation spawns cold. Thread ids are not carried across invocations.
- A failed resume (session missing, context overflow, non-zero exit before any event) **falls back to a cold spawn with the full packet** and records the fallback on the receipt. Degraded, never fatal — continuity is an optimization, and the loop's correctness must not depend on it.

### Claude adapter

Same contract, different mechanism: `Agent` for cold spawns, `SendMessage` to the named agent for resume. `SKILL.md`'s dispatch loop states the same discovery-cold / verification-resumed rule and the same fallback (a failed `SendMessage` → new `Agent` with the full context). No thread-id plumbing — agent names are the handle.

## Delta-scoped verification packets

- Verification packet = outstanding findings + the **repair delta** + the reduced checklist. Not the whole-plan diff, not the full spec — the resumed reviewer already holds both in session.
- Repair delta = `git diff <pre-repair tree>`, where the pre-repair tree is snapshotted with `git stash create` (or `git write-tree`) before the repair dispatch — no working-tree mutation, no interference with the single `fix: final-review` commit discipline.
- Final-review fixer brief = findings + affected paths + referenced spec sections. The whole-plan diff is not pasted; the fixer reads the repo.

## Inline path

Inline execution gets the **coverage half only** — an orchestrator self-review is equally capable of surfacing one finding at a time, so the checklist and the `coverage` discipline apply. The continuity half is moot: inline never discarded its context.

## Receipts & state

- `run.json` gains a `threads` map (role → thread_id), cleared at invocation start.
- Receipts gain the verdict's `coverage` array, a `coverage_retry` flag when the retry fired, and a `resume_fallback` flag when continuity degraded.
- `--status` and the end-of-plan summary report per-task lap counts as today; no cost fields (out of scope).

## Testing

- Checklist generation: id forms per source; `**Spec:**` name resolution and its fail-loud path; union-across-tasks for `--final`; empty checklist → error.
- Coverage validation: complete → valid; missing id → invalid naming it; unknown id → invalid; `violated` without a backing `contract_ref` → invalid; empty `evidence` → invalid; retry fires once then contract-errors.
- Thread capture: `thread.started` parsed from a fixture event stream; resume argv shape (model, effort, `--output-last-message`, id, prompt order); `--ephemeral` never emitted.
- Live-log rendering: an event-stream fixture produces readable lines; the monitor's existing render tests stay green.
- Fallback: resume failure → cold spawn with full packet, receipt flag set, loop continues to a normal verdict.
- Delta scoping: verification packet excludes the whole-plan diff and the full spec; contains the outstanding findings and the reduced checklist.
- Convergence suites (`test_forge_convergence.py`, `test_forge_classify.py`) pass **unchanged** — the proof that loop authority was not altered.

## Acceptance criteria

- A discovery review returns every contract violation it can see in one verdict, evidenced per checklist item, or the runner rejects the verdict.
- A rework lap dispatches no full re-read: the worker resumes, and the verification reviewer resumes against the repair delta.
- The final-review fixer never receives a pasted whole-plan diff.
- The monitor's live view is unchanged for an operator.
- Backstop, disposition matrix, and `--autofix` semantics are byte-for-byte the same decisions as before.

## Claims discipline

This design claims: fewer laps, better repair continuity, less repeated discovery and tool work, and *potential* cached-input savings. It does **not** claim lower total input tokens or lower cost. Resumed transcripts remain model input and are re-billed each turn; prompt caching applies to eligible exact prefixes on a best-effort basis and is not guaranteed. Any cost claim requires measurement against a comparable run.

## Risks

- **Coverage theatre** — a reviewer marks every item `satisfied` with thin evidence. Mitigated by requiring non-empty `evidence` and by `violated`↔finding cross-checking; not fully eliminable. Watch the first runs.
- **Checklist quality is now plan quality** — a plan with vague `**Acceptance:**` prose or a thin `**Spec:**` list yields a weak checklist. This makes an existing weakness visible rather than introducing one.
- **Resumed-transcript growth** — a 5-lap resumed reviewer holds the whole-plan diff plus every verification round. The context-overflow path is covered by the cold-spawn fallback, but long runs may hit it; if that becomes common, revisit scoping the discovery packet itself.
- **Verification bias** — a resumed reviewer confirming its own findings were addressed. Bounded by keeping discovery cold; accepted deliberately, logged in DECISIONS.

## Touch points

`scripts/forge_checklist.py` (new), `scripts/forge_dispose.py` (coverage in the verdict schema + validation), `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION`), `scripts/forge-run.py` (`--json` dispatch, thread capture/persistence, resume dispatch, event rendering, delta packets, de-pasted fixer brief), `scripts/review-packet.py` (checklist section, verification packet mode), `scripts/forge-monitor.py` (render-path verification only), `skills/planning/SKILL.md` (cross-harness canon: coverage, discovery-cold/verification-resumed, Claude adapter), `skills/planning/codex-execution.md` (runner specifics), changelog pointers in `2026-07-16-phase7-scope-autonomy-design.md` and `2026-07-17-phase12b-claude-dispatch-parity-design.md`.

## Changelog

2026-08-21: empty checklist is a library-level error but a runner-level skip with `coverage_skipped` on the receipt — the original rule made legal plans (no Spec/Global Constraints/prose acceptance) unexecutable. Found during Task 4 execution.
