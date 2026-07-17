# Codex exec runner — design

Goal: Codex plan execution moves from in-session subagent dispatch to a deterministic task runner over `codex exec`. One fresh worker process per task; the process boundary eliminates parent-model inheritance and child-thread quota accumulation. Claude Code execution path unchanged.

## Runner

- `scripts/forge-run.py`, Python stdlib only. Reuses `extract-brief.py` and `review-packet.py` (import or subprocess) — plan/spec parsing contracts unchanged, no duplicated parsing.
- Invocation: `forge-run.py <plan.md> --spec <spec.md> [--effort N=LEVEL ...] [--timeout SECONDS]`, run in the foreground by the conversational Codex orchestrator after the execution approval gate. `--effort N=LEVEL` (repeatable; LEVEL in `low`/`medium`/`high`/`xhigh`/`max`) overrides task N's worker reasoning effort only — never the reviewer's; `ultra` and unknown task numbers are rejected loudly. `--timeout SECONDS` (default 3600; recommend ~900) bounds every worker and reviewer `codex exec` subprocess call. See Session awareness.
- `--status --run-dir DIR`: read-only mode — print a deterministic run summary (per-task status, attempts, halt reason) from `run.json` + receipts and exit 0. Dispatches nothing (no `codex exec`, no git writes); plan/spec args not required. A missing/empty DIR prints `no run at DIR`, exit 0.
- Runner stdout is a human-readable progress narrative (task started, attempt N, verdict) for `tail -f` on a redirected log. Never load-bearing for session awareness — state lives in receipts.
- Sequential: one worker at a time, `Depends on` order. No pipelining, no worktree isolation.
- Whole-plan scope: runner owns the task loop, review dispatch, rework iterations, receipts, and ledger annotations. The conversational orchestrator only invokes the runner, relays escalations, and holds the human gates.

## Tier mapping

| Tier | model | model_reasoning_effort |
|---|---|---|
| trivial | gpt-5.6-luna | low |
| standard | gpt-5.6-terra | medium |
| complex | gpt-5.6-sol | medium |

- Passed per worker as `codex exec -m <model> -c model_reasoning_effort=<effort>` — pinned per process.
- `ultra` effort is prohibited at every tier: it spawns subagents inside the worker, breaking brief isolation and reintroducing child-thread accumulation.
- `max` is never a default; a human may bump a single escalated task to max at the escalation gate.
- Mapping lives in one table in the runner — single place to update on model churn.

## Task loop (per task)

1. Generate brief via `extract-brief.py`; record SHA-256.
2. Dispatch worker: `codex exec` with tier-pinned model/effort; prompt = worker contract preamble + brief. Contract text sourced from the corresponding `agents/*.md` body — single source for both harnesses; `codex/agents/*.toml` retired.
3. Run the task's acceptance commands (runner executes them directly). Failure → rework iteration.
4. Trivial tier: acceptance commands are the whole verification. Standard/complex: generate review packet via `review-packet.py`, dispatch reviewer via `codex exec` routed at the task's own tier via `TIER_MAP` (standard → terra/medium, complex → sol/medium) — fresh context, no strength premium. Per-tier model/effort superseded by `2026-07-16-tier-policy-recalibration-design.md`.
5. Reviewer verdict contract: reviewer's final message is JSON, captured via `--output-last-message`:
   ```json
   {"verdict": "pass"}
   {"verdict": "findings", "findings": ["<file:line — issue>", "..."]}
   ```
   Unparseable verdict → loud runner failure naming the cause. Never guessed at, never retried silently.
6. Findings → re-dispatch worker with findings appended to the brief. Rework cap: 2 iterations, enforced by loop counter. Worker crash / timeout / non-zero exit = a failed iteration, same path.
7. Cap hit → status `escalated`: write receipt with outstanding findings, do not start the next task, exit non-zero.
8. After the last task passes: final broad review, one `codex exec` call at the plan's **highest task tier** (`TIER_MAP`) against whole-plan diff + spec — fresh context. Diff base is the persisted `base_commit` (see Commit discipline).

## Commit discipline

- Precondition: every invocation (first run and resume) requires a clean working tree — `git status --porcelain` empty, the self-ignored `.forge/` excluded. Dirty → contract error (exit 1) naming the dirty paths; the human commits or discards before re-invoking. The runner never resets or stashes user work.
- Per passed task: after the task reaches `passed` and its ledger checkbox is annotated, the runner stages all changes and commits — `git add -A && git commit -m "forge: task <N> — <title>"`. Nothing staged (e.g. a human pre-fixed on resume) → commit skipped, no empty commits. `.forge/` is ignored, never staged; the ledger annotation rides in the commit. Escalated tasks commit nothing — the rejected attempt stays uncommitted for the human to resolve.
- Clean tree at task start means `git add -A` captures exactly that task's own work; HEAD is therefore a clean checkpoint after every passed task — the invariant the per-task base and resume rely on.
- Per-task review base = HEAD at task start (the prior task's commit; the run-start commit for task 1). Replaces the stash snapshot — `_snapshot_worktree` retires.
- Final-review base = `base_commit`: HEAD captured before any task commits, persisted in `run.json` on first invocation and read (never recaptured) on resume — so the final diff spans the whole plan across invocations. Empty diff → final review skipped (unchanged).

## Receipts

- Run dir `.forge/runs/<timestamp>/`, uncommitted — on first creation the runner writes a `.gitignore` containing `*` into `.forge/` (self-ignoring, no target-repo setup); README notes the behavior. One receipt per task attempt: `task-<N>-attempt-<i>.json`; plus `run.json` summary.
- Receipt fields: task number, title, tier, model + effort requested, brief path + SHA-256, worker exit code, acceptance results (command, exit code, output tail), review verdict, attempt number, status (`passed` | `rework` | `escalated`), outstanding_findings.
- `run.json` gains top-level `base_commit` (the whole-plan final-review diff base); each task summary gains `commit` — the SHA of that task's commit, or null when the commit was skipped (empty stage).
- `run.json` is written incrementally so `--status` can distinguish an in-progress run from a dead one: top-level `status` is set to `running` right after the clean-tree check (carrying `base_commit`, so resume still reads it), then rewritten to the terminal status (`passed` | `escalated` | `escalated-final-review`) at the end. A contract error that occurs after the run dir exists rewrites `status` to `contract-error` with a `contract_error` message field. Contract errors before the run dir exists (dirty tree, unparseable plan) write no `run.json` — surfaced by stderr only.
- Ledger: runner annotates plan checkboxes with outcome (`[x] … — passed, 1 attempt` / `— escalated: <one-liner>`). Plan file remains the durable human-readable record; the annotation rides in the task's commit (git log is the parallel record).

## Resume

- Re-invocation skips tasks whose receipt status is `passed`; resumes at the escalated/incomplete task. Receipts + plan checkboxes are the resume state; no other state store.
- The clean-tree precondition (Commit discipline) holds on resume too: passed tasks are already committed, so a clean tree at resume start is the normal state; the first non-passed task re-runs with base = HEAD = last passed commit. An escalated task's uncommitted attempt must be committed (as a fix) or discarded by the human before resume — the precondition enforces this.

## Halt / escalation

- Two halt classes, distinguished by exit code:
  - **Task escalation (exit 2)** — rework cap hit: receipt written with `outstanding_findings`; orchestrator relays the receipt's contents to the user.
  - **Contract error (exit 1)** — malformed plan, brief/packet generation failure, unparseable reviewer verdict, reviewer process crash, or a dirty working tree at invocation start: fails loudly to stderr naming the cause; no receipt. When the run dir already exists, `run.json` is rewritten `status: contract-error` with the cause (so `--status` reports it); errors before the run dir exists are stderr only. Orchestrator relays the stderr cause.
- Either way the runner stops before the next task and never absorbs work inline. Resolution (amend brief, re-tier, bump to max, defer) is a human decision before re-invocation.

## Session awareness

Codex-only. Purpose: when the runner halts and needs human input, the human is told — without babysitting. The mechanism is **foreground execution**, not pushed notifications: run the runner in the foreground, and a halt surfaces in the conversation on its own. Claude Code handles session awareness natively inside its harness; nothing here targets Claude.

- **Foreground, orchestrator-relayed halts.** The orchestrator runs the runner in the foreground and blocks on it. On a non-zero exit (escalation exit 2, contract error exit 1) control returns to the orchestrator, which reads the receipt/stderr and relays the halt to the human in the conversation. A halt that hands control straight back to a waiting orchestrator cannot go silent — that is the whole mechanism. The runner never pushes notifications; there is no notify flag and no hook. (The silent-halt failure mode is a property of *backgrounding* — the orchestrator fires `… &`, moves on, and nobody holds the exit code; foreground removes it by construction.)
- **`--timeout SECONDS` is the hang backstop** (recommend ~900). Every worker/reviewer `codex exec` call is bounded; a genuinely stuck task is killed at the timeout, counts as a failed iteration, and escalates — so even a hang becomes a relayed halt rather than silence. It is a dead-man's switch, not a performance tuner: set it well above any real task and well below "all day."
- **Runner stdout is a live progress narrative** (`task N: <title> — starting` / `task N: passed`) streamed in the Codex TUI. Never load-bearing for correctness — state lives in receipts — but a stalled stream on the last "starting" line shows where a run is parked.
- **`--status --run-dir DIR`** — read-only on-demand state from `run.json` + receipts (`RUNNING` | `COMPLETED` | `HALTED — <reason>` | `CONTRACT-ERROR — <cause>` + one line per task); dispatches nothing, exits 0. Backed by incremental `run.json` (`running` → terminal) and `scripts/forge_status.py`.

## Retirements / doc changes

- `codex/agents/*.toml` deleted; README Codex section drops the agent-copy step, gains runner invocation + `.forge/` gitignore note + the clean-tree precondition and per-task commit behavior.
- `skills/planning/codex-execution.md` rewritten around the runner: invocation, halt/resume, orchestrator's reduced role, clean-tree precondition, per-task commits, commit-or-discard-before-resume ergonomic.
- `_snapshot_worktree` removed from the runner (per-task base is the prior commit).
- Planning skill (shared, both harnesses): acceptance commands must treat an environment-gated skip as failure — assert the required infra is present, or make the skip exit non-zero. A skipped check is not a pass. (Also advances Phase 8 Claude parity.)
- Phase 3 spec amended in place (changelog line): in-session dispatch, nickname pools, TOML agents superseded by this spec.
- In-session Codex subagents remain acceptable outside plan execution (exploration, ad-hoc review); no forge machinery uses them.
- Session awareness (Codex-only): `codex-execution.md` and the README Codex section document the foreground-execution model — run the runner in the foreground so a halt is relayed into the conversation on exit; `--timeout ~900` as the hang backstop; `--status --run-dir` for an on-demand peek.

## Testing

- pytest, stdlib only. Fake `codex` executable on PATH records argv, plays scripted exits/last-messages.
- Covered: tier→model/effort resolution; dependency ordering; acceptance-failure → rework; findings → rework → cap → halt at 2; unparseable verdict → loud failure; crash/timeout as failed iteration; resume skips `passed` receipts; receipt fields + ledger annotations; ultra never emitted.
- Phase 5 additions (real temp git repo per test): dirty tree at invocation start → contract error (exit 1), first run and resume; clean start → one commit per passed task with `forge: task N — <title>` message; escalation → no commit + halt; per-task review base = prior commit (packet holds only that task's diff); `base_commit` persisted in `run.json` and reused on resume; final review spans the whole plan across a resume; empty stage → commit skipped (no empty commit); `_snapshot_worktree` references removed.
- Phase 6 additions (session awareness): `--status` renders fixture receipts for running/completed/halted/contract-error runs and never spawns codex (fake-codex argv log empty); incremental `run.json` written `running` at start, terminal status at end; a contract error after the run dir exists persists a `contract-error` marker, before it exists writes no `run.json`.
- Live verification of `codex exec` flag surface (`-m`, `-c model_reasoning_effort=`, `--output-last-message`) is the plan's first task, not a unit test.

## Acceptance criteria

- Existing pytest suite passes; new runner tests pass.
- Claude Code behavior unchanged.
- On Codex: runner executes a multi-task plan end-to-end with pinned models per receipt; forced reviewer findings drive rework then mechanical halt at cap; re-invocation resumes past passed tasks.

## Risks / constraints

- `codex exec` flag surface may churn (effort values, output flags) — verified live as first plan task; mapping table is the single update point.
- Reviewer JSON discipline: models may wrap JSON in prose; extraction rule (last fenced/parseable JSON object in the message) must be specified in the reviewer contract, and still fail loud when absent.

## Changelog

2026-07-13: `.forge/` ignore is runner-written (self-ignoring `.gitignore`), not target-repo setup — requirement was unowned by any plan task (Task 2 escalation).
2026-07-13: receipts gain outstanding_findings on escalation (Task 3).
2026-07-14: Halt section split into two classes — exit 2 task escalation (receipt) vs exit 1 contract error (stderr, no receipt); docs follow code (Task 5 escalation).
2026-07-14: CLI gains --effort N=LEVEL (per-task worker override; ultra rejected) and --timeout SECONDS (default 3600; worker timeout = failed iteration, reviewer timeout = contract error) — final-review findings.
2026-07-14 (phase 5): Commit discipline — clean-tree precondition every invocation (dirty → contract error exit 1); commit per passed task (`forge: task N — <title>`, empty stage skipped, ledger annotation rides in the commit); per-task review base = prior commit (`_snapshot_worktree` retired); final-review base = persisted `base_commit` (whole-plan diff across resume). Fixes final-review over-scoping when the working tree started dirty (`git diff HEAD` swept the whole cross-phase diff into every review). Env-gated-skip authoring rule added to the shared planning skill (skip ≠ pass).
2026-07-14 (phase 6): Session awareness (Codex-only) — `--notify` modal on all terminal events (escalation, contract error, completion; `osascript` default, `--notify CMD` override, fire-and-forget); `--status --run-dir` read-only run summary; `run.json` written incrementally (`running` → terminal, `contract-error` marker when the run dir exists); runner stdout demoted to human narrative; `UserPromptSubmit` hook (`hooks/user-prompt-submit`, shared reader `scripts/forge_status.py`) wired Codex-only via `~/.codex/config.toml`, never the shared `hooks/hooks.json`; never-background-blind contract in `codex-execution.md`. Scope widening split out to a later phase; stall watchdog dropped (Claude-path concern, not the Codex runner).
2026-07-15 (phase 6): `--notify` gains a per-task `task-passed` event (modal per task, alongside `escalated`/`contract-error`/`completed`) so a backgrounded run pings on each task completion, not only at the end — operator preference for hands-free progress.
2026-07-15 (phase 6): UserPromptSubmit hook wiring moved from a manual `~/.codex` install to the shared `hooks/hooks.json` (auto-installed on both harnesses like `session-start`, resolves to the running install's own copy — no stale-path drift). Kept Codex-only-in-effect by a best-effort in-hook harness gate: silent under Claude (input carries `transcript_path`), fires under Codex (`turn_id`), fires when ambiguous (harmless — already silent without an active run). Eliminates the manual install step.
2026-07-15 (phase 6): **Push machinery removed** — `--notify`/`fire_notify` and the `UserPromptSubmit` hook (script + shared-`hooks.json` wiring + `forge_status.render_hook_block`) deleted. Live Codex testing showed the push path was the wrong model: `osascript` is blocked inside the Codex sandbox (modals never fired), and the hook errored (`exit 127`) on Codex. Session awareness is now **foreground execution** — the orchestrator runs the runner in the foreground and relays the halt into the conversation on non-zero exit; `--timeout` is the hang backstop; `--status` + incremental `run.json` remain for on-demand peeks. Simpler, and it closes the actual need (a halt is visible in the session window) without fighting the sandbox.
2026-07-16: Tier-mapping recalibrated — trivial/standard/complex efforts lowered from medium/high/high to low/medium/medium respectively, collapsing reviewer routing to task-tier fresh-context; see `2026-07-16-tier-policy-recalibration-design.md` for rationale and implementation.
2026-07-16 (phase 7): Review/rework machinery superseded — the raw `MAX_ATTEMPTS = 2` rework cap and the single-shot final-review human gate (Task loop §6-8, Halt / escalation, Final review) are replaced by a runner-verified fix/defer/halt disposition matrix, convergence-based rework (backstop `MAX_ATTEMPTS_BACKSTOP = 5`), a final-review fix loop, an `--autofix auto|gate` autonomy knob, and a terminal doc-sync stage. This spec is not rewritten (living-spec rule: superseding sections, not amended in place); see `2026-07-16-phase7-scope-autonomy-design.md` for the full design and `skills/planning/codex-execution.md` for the current operational description.
