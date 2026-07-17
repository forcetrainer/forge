# Claude Dispatch Parity (Phase 7 canon on Claude) — design

Phase 12b (Claude path). Brings the Phase 7 **disposition matrix + convergence** to the Claude *dispatch* path — the lone remaining cell without it (inline has it via Phase 11; the Codex runner has it via Phase 7). Reaches **full cross-harness logic parity**; ships as the **0.8.0** milestone. The approach (DECISIONS 2026-07-17, option C): extract the runner's pure decision logic into a **shared, tested `scripts/forge_dispose.py`** with a CLI, driven by the Codex runner in-process and by the Claude orchestrator via the CLI — one tested implementation of the decision, two actors carrying it out.

Its own spec per the no-mixed-implementations constraint. Resolves DEFERRALS 2026-07-13 (mechanical rework-cap enforcement on Claude was unenforceable prose — after 12b the Claude decision *is* the same tested script).

## Scope

- **In:** extract classification + convergence from `forge-run.py` into shared `scripts/forge_dispose.py` (pure functions + a CLI); a **sequential orchestrator-driven** Claude dispatch loop that calls the CLI; the reviewer as a Claude subagent emitting the Phase 7 verdict schema; `--autofix auto|gate` at the Claude execution offer; the final-review loop reusing the CLI; DEFERRALS write-back at completion; the `forge-run.py` refactor to import the shared module (behavior + all tests preserved); README + SKILL.md documentation of the **serial-by-design** stance.
- **Out:** the **terminal doc-sync stage** on the Claude path — intentionally deferred into the upcoming forge-wide doc revamp (DEFERRALS 2026-07-17). This is the one known Claude/Codex divergence 12b leaves: a terminal reconciliation *stage*, not finding-handling *logic* — the steering-matrix + convergence core still reaches full parity, so 0.8.0 is "logic parity" with doc-sync as a logged follow-up. Any parallel implementation of substantive tasks on Claude (rejected by design — see Serial by design). Auto model/effort escalation (unchanged: a human on-halt action). New Codex-runner behavior (12b is a *refactor* of the runner's internals, not a behavior change). `review-packet.py` on the Claude path (dropped — see Reviewer).

## The shared decision helper — `scripts/forge_dispose.py`

**Extraction (behavior-preserving).** Move the pure decision logic out of `forge-run.py` into `forge_dispose.py`:

- Classification: `diff_line_ranges`, `_parse_lines`, `verify_provenance`, `derive_disposition`, `classify_findings`.
- Verdict parse: `_finding_from_obj`, `_verdict_from_obj`, `parse_verdict`.
- Convergence: `ConvergenceState`, `_canon`, `_real_fix_canons`, `_is_execution_failure`, `convergence_decision`, `advance_state`.

`forge_dispose.py` imports `Finding`/`Verdict`/`HALT_REASONS`/`MAX_ATTEMPTS_BACKSTOP` from `forge_common` **as a plain module** (`sys.path` insert, one `sys.modules` instance) so there is exactly one `Finding` class identity — the duplicate-dataclass `__eq__` hazard the 2026-07-14 decomposition documented. `forge-run.py` imports these names from `forge_dispose` and **re-exports** them into its own namespace, so `tests/test_forge_convergence.py`, `tests/test_forge_classify.py`, and every `forge_run.<name>` reference keep working untouched. No behavior changes; the full suite is the safety net.

**CLI.** `forge_dispose.py` gains a `main()`:

```
python3 forge_dispose.py \
  --verdict <verdict.json> \      # the reviewer's proposed verdict (schema below)
  --base <sha> \                  # diff base: prior commit (per-task) | run-start HEAD (final)
  --state <state.json> \          # prior ConvergenceState (empty/absent on attempt 1)
  --attempt <N> \
  --acceptance-ok <true|false> \
  --autofix <auto|gate>
```

The CLI computes the authoritative diff itself (`git diff <base>` via subprocess), runs `classify_findings` → `convergence_decision` → `advance_state`, and writes **`decision.json`** to stdout:

```json
{
  "action": "pass" | "rework" | "halt",
  "halt_reason": "scope-decision" | "regression" | "stuck" | "backstop" | "gate" | null,
  "findings": {
    "fix":   [ { "id": "...", "summary": "...", "file": "...", "lines": "..." } ],
    "defer": [ { ... , "why_harmless": "reviewer improvement rationale" } ],
    "halt":  [ { ... , "repair_task": { ... } } ]
  },
  "state": { "resolved_ids": [...], "carried_ids": [...], "prev_acceptance_ok": true }
}
```

The pure functions stay diff-text-in (unit-testable); only the CLI wrapper shells to git. `ConvergenceState` gains `to_dict`/`from_dict` (sets ↔ JSON lists) for the `--state` round-trip. The orchestrator acts purely off `decision.json` — it never re-reads the diff, keeping it thin.

**Authority note.** `forge_dispose` computes the diff it verifies against; the reviewer's own diff view is advisory (its provenance claims are overridden regardless). So a reviewer that ran a slightly different `git diff` cannot corrupt the decision — provenance is always the helper's recomputation against `--base`.

## Claude execution model — sequential orchestrator loop

The Claude dispatch path becomes an **orchestrator-driven sequential loop**, identical in shape to `forge-run.py` (the orchestrator plays the runner's role; Agent-tool subagents play the `codex exec` workers). Per task, in order:

1. **Precondition** (run start): clean working tree or halt (Half A, already landed). Record `base_commit` = run-start HEAD.
2. **Dispatch implementer** — Agent at the task's tier agent, prompt = brief-file path (`extract-brief.py`) + relevant DECISIONS + TDD discipline + deferral rule. Trivial tasks batch into one `forge-light` dispatch (serial within), skip steps 4–6.
3. **Acceptance** — run the task's acceptance commands; capture pass/fail.
4. **Dispatch reviewer** — Agent at the task's tier, given the review base (`<prior commit>`), the spec path, and the **Phase 7 verdict schema** (StructuredOutput). The reviewer **self-serves**: runs `git diff <base>` in its own context, reads the spec, emits `verdict.json`. On a rework re-review the prior attempt's findings (ids + summaries — small) ride in the reviewer's prompt so it labels `resolved/carried/new`.
5. **Decide** — `forge_dispose --base <prior commit> --verdict verdict.json --state state.json --attempt N --acceptance-ok … --autofix …` → `decision.json`. Persist `decision.state` to `state.json` for the next attempt.
6. **Act** on `decision.action`:
   - `rework` → re-dispatch the implementer with `decision.findings.fix` appended to the brief; loop to 3.
   - `halt` → stop; surface `decision.findings.halt` (+ drafted `repair_task`) and any outstanding `fix` findings to the user; do not start the next task.
   - `pass` → collect `decision.findings.defer`; **commit the task** (`git add -A && git commit -m "forge: task N — <title>"`); next task.

State (`state.json`), the reviewer verdict (`verdict.json`), and `decision.json` live in a scratch dir, never in orchestrator context. The diff never enters orchestrator context — the reviewer holds it in its context; `forge_dispose` holds it in its process.

**Reviewer.** No `review-packet.py` on the Claude path. The packet exists to pre-assemble input for a `codex exec` **subprocess**, which cannot gather its own context; a Claude **Agent** reviewer self-serves the diff and spec, so the packet is machinery Claude doesn't need. Thin-orchestrator is preserved by subagent self-service + `forge_dispose` self-computing the diff, not by the packet. `review-packet.py` stays the Codex mechanism only.

## Serial by design (documented stance)

Substantive implementation on Claude runs **serially, on purpose** — not because Workflow parallelism is unavailable, but because it is the wrong tool for mutating code:

- Parallelism buys **only wall-clock** — no correctness, quality, or logic gain.
- forge's discipline has a **serial spine**: the per-task review base is `git diff <prior commit>`, meaningful only on a **linear** history of clean vertical slices (Phase 5). Parallel writers break the linear history and force worktree isolation + ordered merge-back, whose conflicts reintroduce the integration mess vertical slices exist to prevent.
- Fan-out is safe for **read-only** work (research, review lenses) — no shared mutable state. Coding writes; racing writes is the sketchy part. The genuinely-independent coding case (large mechanical migration) tiers **trivial**, which skips review and was never in the loop.

Documented in **README.md** (a "why forge runs implementation serially" note) and reaffirmed in **SKILL.md** (the dispatch branch states the serial stance + the linear-history reason). Recorded in DECISIONS 2026-07-17.

## Autonomy flag

`--autofix auto|gate` offered at the Claude execution gate (the same offer that discloses tier routing), default `auto`, passed through to `forge_dispose` each call. `auto` = the matrix. `gate` = any real finding halts (conservative escape hatch). Parity with the runner's flag; same semantics because it is the same code deciding.

## Final review

After every task passes, the orchestrator runs the whole-plan review through the **same loop**: reviewer subagent (diff base = `base_commit` = run-start HEAD; tier = plan's highest task tier) → `forge_dispose --base <base_commit>` → act. The final-review "worker" on rework is a fix dispatch scoped to `decision.findings.fix` against the whole-plan diff, committed as a single `fix: final-review` commit on pass. Halt payload identical (drafted repair task).

## Deferral write-back

Defer-quadrant findings are collected across the run (from each `decision.findings.defer`). Once final review passes, the orchestrator appends them to `docs/forge/DEFERRALS.md` (project-memory format) as a reviewed batch — parity with the runner, where the conversational orchestrator already does this write-back (the decision layer never touches curated docs). (The runner's terminal doc-sync stage is **not** ported here — deferred to the doc revamp, DEFERRALS 2026-07-17.)

## Commit discipline

Unchanged from Half A (12a): clean-tree precondition at run start; commit per passed task (establishing the per-task review base); final-review fixes → one `fix: final-review` commit. The orchestrator commits on the Claude path; the runner commits on Codex. (No `docs: sync` commit — doc-sync deferred.)

## Runner refactor

Behavior-preserving. `forge-run.py` deletes the moved function bodies, imports them from `forge_dispose`, re-exports them. Its per-task loop (currently lines ~712–719) and final-review loop (~938–950) keep calling `classify_findings` / `convergence_decision` / `advance_state` by the same names. `MAX_ATTEMPTS_BACKSTOP` stays in `forge_common`. The full suite (263 pass / 2 skip) must stay green with no test edits beyond, at most, an added import line — if a test needs rewriting, the extraction changed behavior and is wrong.

## Retirements / doc changes

- `review-packet.py`: unchanged, but documented as **Codex-path-only** (SKILL.md dispatch branch, its docstring).
- SKILL.md dispatch branch: replace the cap-2 + raw "finding→rework→re-review" prose with the sequential-loop + `forge_dispose` canon; state the serial-by-design reason; mark `review-packet.py` Codex-only; note `--autofix` at the offer.
- README.md: serial-by-design note; `forge_dispose` as the shared decision helper.
- `codex-execution.md`: note the decision logic now lives in shared `forge_dispose.py` (the runner imports it); no behavior change.
- `2026-07-13-codex-exec-runner-design.md` + `2026-07-16-phase7-scope-autonomy-design.md`: changelog pointers (living-spec rule) noting the decision logic was extracted to the shared module in 12b.
- DEFERRALS 2026-07-13: mark resolved (Claude decision is now the shared tested script).

## Testing

- **Extraction (regression):** the full existing suite green with no logic edits — the proof the move is behavior-preserving. One `Finding` class identity (import-path test if warranted).
- **CLI:** `decision.json` shape per quadrant fixture (fix / defer / halt / pass); provenance override (reviewer `in-diff`, lines outside diff → `pre-existing`); null `contract_ref` downgrade → defer; `--state` round-trip (sets ↔ lists) across a multi-attempt sequence; `--autofix gate` halts on any finding; the synthesized execution-failure path (no verdict → implicit fix-retry) through the CLI.
- **Convergence via CLI:** progress → rework, regression (resolved id reappears / green→red) → halt, stuck (id carried ×2) → halt, backstop (5) → halt, clean → pass — same sequences as the in-process tests, exercised through the CLI boundary.
- The Claude loop itself is orchestrator discipline (prose), like the current dispatch path; its *decision* is now the tested CLI, so the untested surface shrinks to the orchestration glue.

## Acceptance criteria

- `forge_dispose.py` exists; `forge-run.py` imports it; the full suite is green with no decision-logic test rewritten.
- The CLI reproduces every in-process decision (same quadrant → same `decision.json`; same convergence sequence → same `action`).
- A converging Claude dispatch task runs past 2 attempts to a clean pass; a churning one halts at the churn; a `pre-existing`×`contract-breaking` finding halts with a drafted repair task; improvements defer and the run continues.
- The Claude final review fixes its own `fix` findings in-loop and halts on the same conditions; a surfaced deferral batch closes a clean run.
- `--autofix gate` halts on any finding on the Claude path.
- README + SKILL.md state the serial-by-design stance and the linear-history reason.
- Version bumped to **0.8.0** (lockstep, both manifests); ROADMAP Phase 12b `done`.

## Risks / constraints

- **Extraction identity bug** — two `Finding` classes → silent dataclass `__eq__` failure. Mitigation: plain-module import of `forge_common` (one `sys.modules` instance), exactly as the 2026-07-14 decomposition; the suite catches a regression.
- **Reviewer/helper diff skew** — the reviewer's self-served diff and the helper's recomputed diff could differ if the tree mutates between. Mitigation: the loop mutates nothing between reviewer dispatch and `forge_dispose`; the helper's diff is authoritative regardless (provenance is always its recomputation).
- **Orchestrator drift** — the Claude loop is prose the session must follow. Mitigation: the decision is a tested CLI (no longer prose), so the drift surface is only the glue; SKILL.md states the loop steps explicitly.
- **Wall-clock** on large all-independent plans (the serial cost). Accepted by design; hybrid parallelism is a logged future optimization, not 12b.
</content>
</invoke>
