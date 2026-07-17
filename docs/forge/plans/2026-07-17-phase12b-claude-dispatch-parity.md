# Claude Dispatch Parity (Phase 12b) Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Bring the Phase 7 disposition matrix + convergence to the Claude dispatch path via a shared, tested `forge_dispose` helper, reaching cross-harness logic parity (0.8.0).
**Architecture:** Extract the runner's pure decision logic into `scripts/forge_dispose.py` (pure functions + CLI); `forge-run.py` imports and re-exports it (behavior + tests preserved). The Claude dispatch path becomes a sequential orchestrator loop that calls the CLI. Documentation states the serial-by-design stance.
**Tech stack:** Python 3 stdlib (argparse, json, subprocess, dataclasses); pytest.
**Global Constraints:** No behavior change to the Codex runner (12b is an internal refactor + a new Claude path). One `Finding` class identity — `forge_dispose` imports `forge_common` as a plain module (single `sys.modules` instance), per the 2026-07-14 decomposition hazard. The full suite (263 pass / 2 skip) is the extraction safety net; a decision-logic test that needs rewriting means the extraction changed behavior and is wrong.

## File structure

- `scripts/forge_dispose.py` (create) — the extracted pure decision logic (classification, verdict parse, convergence, `ConvergenceState`) + a CLI emitting `decision.json`. Single responsibility: decide, never act.
- `scripts/forge-run.py` (modify) — delete the moved function bodies; import from `forge_dispose`; re-export the names so the runner loop and its tests are unchanged.
- `scripts/forge_common.py` (unchanged) — already holds `Finding`/`Verdict`/`HALT_REASONS`/`MAX_ATTEMPTS_BACKSTOP`.
- `tests/test_forge_dispose.py` (create) — CLI + state-round-trip + convergence-through-the-CLI tests.
- `tests/test_forge_convergence.py`, `tests/test_forge_classify.py` (unchanged) — keep testing via `forge_run` re-exports (proves the re-export path).
- `skills/planning/SKILL.md` (modify) — dispatch-branch rewrite: sequential-loop canon, `--autofix` at the offer, `review-packet.py` marked Codex-only, serial-by-design + linear-history reason.
- `README.md` (modify) — serial-by-design note + `forge_dispose` as the shared decision helper.
- `skills/planning/codex-execution.md`, `docs/forge/specs/2026-07-13-codex-exec-runner-design.md`, `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md` (modify) — changelog pointers: decision logic extracted to shared `forge_dispose.py`.
- `docs/forge/DEFERRALS.md`, `docs/forge/ROADMAP.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (modify) — resolve DEFERRALS 2026-07-13, mark Phase 12b done, lockstep 0.8.0 bump.

### Task 1: Extract decision logic into forge_dispose.py
- [x] Done

**Files:**
- Create: `scripts/forge_dispose.py`
- Modify: `scripts/forge-run.py` (delete moved bodies; import + re-export from `forge_dispose`)
- Test: `tests/test_forge_convergence.py`, `tests/test_forge_classify.py` (unchanged — must stay green via re-export)

**Spec:** The shared decision helper, Runner refactor

**Interface:** `scripts/forge_dispose.py` exposes (moved verbatim from `forge-run.py`, bodies unchanged): `diff_line_ranges`, `_parse_lines`, `verify_provenance`, `derive_disposition`, `classify_findings`, `_finding_from_obj`, `_verdict_from_obj`, `parse_verdict`, `ConvergenceState`, `_canon`, `_real_fix_canons`, `_is_execution_failure`, `convergence_decision`, `advance_state`. It imports `Finding`, `Verdict`, `HALT_REASONS`, `MAX_ATTEMPTS_BACKSTOP` from `forge_common` as a plain module (`sys.path.insert(0, SCRIPTS_DIR)` then `import forge_common`). `forge-run.py` imports these names from `forge_dispose` and re-exports each into its own namespace (module-level assignment), so `forge_run.<name>` continues to resolve for its loop code and tests.

**Tests:** No new tests. The existing decision-logic suites pass **unchanged** — this is the behavior-preserving proof. Any required edit to a `test_forge_convergence`/`test_forge_classify` assertion means the move altered behavior and must be corrected, not the test.

**Acceptance:** `python3 -m pytest -q` → 263 passed, 2 skipped (unchanged from pre-task); `git diff` shows no assertion changes in `tests/test_forge_convergence.py` or `tests/test_forge_classify.py`.

**Tier:** `standard` — a well-specified move with the full suite as the objective check; the single-`Finding`-identity constraint is an established pattern (2026-07-14), not novel design.

**Depends on:** nothing.

### Task 2: forge_dispose CLI + ConvergenceState JSON round-trip
- [x] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (add `ConvergenceState.to_dict`/`from_dict`, `main()`/argparse, diff computation, `decision.json` assembly)
- Test: `tests/test_forge_dispose.py`

**Spec:** The shared decision helper, Autonomy flag

**Interface:**
- `ConvergenceState.to_dict() -> dict` (`resolved_ids`/`carried_ids` as sorted lists, `prev_acceptance_ok` passthrough) and `ConvergenceState.from_dict(d) -> ConvergenceState` (lists → sets; absent/empty → fresh state).
- CLI: `forge_dispose.py --verdict <path> --base <sha> [--state <path>] --attempt <N> --acceptance-ok <true|false> --autofix <auto|gate> [--execution-failure]`. Computes the diff via `git diff <sha>` (subprocess, cwd = repo). `--execution-failure` synthesizes the implicit fix-retry finding (no reviewer verdict) and skips `--verdict`. Runs `classify_findings` → `convergence_decision` → `advance_state`.
- Writes `decision.json` to stdout: `{"action": "pass|rework|halt", "halt_reason": <one of HALT_REASONS|null>, "findings": {"fix": [...], "defer": [{..., "why_harmless"}], "halt": [{..., "repair_task"}]}, "state": <to_dict>}`. Each finding entry carries `id`, `summary`, `file`, `lines`.

**Tests:** `decision.json` shape per quadrant (fix / defer / halt / clean-pass) from a verdict fixture; provenance override (reviewer says `in-diff`, `location.lines` outside the diff → `pre-existing` → not fixed); null `contract_ref` downgrade → defer; `--state` round-trip across a 3-attempt sequence (state persists resolved/carried ids); `--autofix gate` → any real finding halts; `--execution-failure` → implicit fix-retry, subject to regression/backstop only; convergence sequences through the CLI boundary — progress→rework, regression (resolved id reappears / green→red)→halt, stuck (id carried ×2)→halt, backstop(5)→halt, clean→pass.

**Acceptance:** `python3 -m pytest -q tests/test_forge_dispose.py` all pass; a manual `forge_dispose.py --verdict … --base HEAD --attempt 1 --acceptance-ok true --autofix auto` emits well-formed `decision.json`.

**Tier:** `standard` — CLI glue over already-tested pure functions, clear test path.

**Depends on:** Task 1.

### Task 3: SKILL.md dispatch-branch rewrite
- [x] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (Execution section — Claude dispatch sub-bullet, rework-guardrails, proportional-review, final-review dispatch prose)

**Spec:** Claude execution model, Serial by design, Autonomy flag

**Interface:** none (prose). Replace the Claude dispatch sub-bullet's cap-2 + raw "finding→rework→re-review" with the sequential orchestrator loop (implement → acceptance → reviewer self-serves diff + emits the verdict schema → `forge_dispose` CLI → act → commit); state `--autofix auto|gate` is chosen at the execution offer; mark `review-packet.py` **Codex-path-only**; state the serial-by-design stance with the linear-history reason. Update the "Rework guardrails (dispatch path)" cap-2 line to reference the convergence model on both paths. Keep the inline canon unchanged (Phase 11).

**Tests:** none (documentation).

**Acceptance:** `grep -n "review-packet" skills/planning/SKILL.md` shows it marked Codex-only; the dispatch branch names `forge_dispose` and the sequential loop; no "cap at 2 iterations" remains as the dispatch stop condition; a read-through confirms consistency with the Phase 11 inline canon.

**Tier:** `standard` — authoring that must accurately express the new canon; no mechanical shortcut.

**Depends on:** Task 2.

### Task 4: README serial-by-design note
- [x] Done

**Files:**
- Modify: `README.md`

**Spec:** Serial by design

**Interface:** none (prose). Add a short "why forge runs implementation serially" note (parallelism buys only wall-clock; the per-task review base needs a linear history of clean vertical slices; fan-out is safe only for read-only work) and a one-line mention of `scripts/forge_dispose.py` as the shared cross-harness decision helper.

**Tests:** none (documentation).

**Acceptance:** `grep -n "serial\|forge_dispose" README.md` shows both additions; the note's reasoning matches DECISIONS 2026-07-17.

**Tier:** `standard` — explanatory authoring, low-stakes but not mechanical.

**Depends on:** Task 3.

### Task 5: Bookkeeping — changelog pointers, resolve deferral, roadmap, 0.8.0 bump
- [x] Done

**Files:**
- Modify: `skills/planning/codex-execution.md` (changelog pointer: decision logic now in shared `forge_dispose.py`)
- Modify: `docs/forge/specs/2026-07-13-codex-exec-runner-design.md`, `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md` (changelog pointers)
- Modify: `docs/forge/DEFERRALS.md` (mark 2026-07-13 mechanical-rework-cap deferral resolved)
- Modify: `docs/forge/ROADMAP.md` (Phase 12b → done)
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (0.8.0)

**Spec:** Retirements / doc changes

**Interface:** none. Mechanical edits: append dated changelog lines to the two specs + `codex-execution.md` noting the extraction; annotate DEFERRALS 2026-07-13 as RESOLVED (Claude decision is now the shared tested script); flip the ROADMAP Phase 12b line to `done`; set both `plugin.json` versions to `0.8.0`.

**Tests:** none.

**Acceptance:** `grep -H '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json` both show `0.8.0`; ROADMAP shows Phase 12b `done`; DEFERRALS 2026-07-13 shows RESOLVED; the three changelog pointers are present.

**Tier:** `trivial — mechanical doc edits + version bump, no design content`.

**Depends on:** Task 1, Task 2, Task 3, Task 4.

## Notes

- Full-suite close-out after all tasks: `python3 -m pytest -q` green, then the branch-finishing preferences (CLAUDE.md).
- This plan touches the forge plugin itself — after merge, `claude plugin update forge@forge` + restart to load 0.8.0.
- Terminal doc-sync on the Claude path is intentionally **not** in this plan (DEFERRALS 2026-07-17 — folded into the coming doc revamp).
</content>
