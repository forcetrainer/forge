# Codex exec runner — design

Goal: Codex plan execution moves from in-session subagent dispatch to a deterministic task runner over `codex exec`. One fresh worker process per task; the process boundary eliminates parent-model inheritance and child-thread quota accumulation. Claude Code execution path unchanged.

## Runner

- `scripts/forge-run.py`, Python stdlib only. Reuses `extract-brief.py` and `review-packet.py` (import or subprocess) — plan/spec parsing contracts unchanged, no duplicated parsing.
- Invocation: `forge-run.py <plan.md> --spec <spec.md> [--effort N=LEVEL ...] [--timeout SECONDS] [--notify CMD]`, run by the conversational Codex orchestrator after the execution approval gate. `--effort N=LEVEL` (repeatable; LEVEL in `low`/`medium`/`high`/`xhigh`/`max`) overrides task N's worker reasoning effort only — never the reviewer's; `ultra` and unknown task numbers are rejected loudly. `--timeout SECONDS` (default 3600) bounds every worker and reviewer `codex exec` subprocess call. `--notify`: see Session awareness.
- `--status --run-dir DIR`: read-only mode — print a deterministic run summary (per-task status, attempts, halt reason) from `run.json` + receipts and exit 0. Dispatches nothing (no `codex exec`, no git writes); plan/spec args not required. A missing/empty DIR prints `no run at DIR`, exit 0.
- Runner stdout is a human-readable progress narrative (task started, attempt N, verdict) for `tail -f` on a redirected log. Never load-bearing for session awareness — state lives in receipts.
- Sequential: one worker at a time, `Depends on` order. No pipelining, no worktree isolation.
- Whole-plan scope: runner owns the task loop, review dispatch, rework iterations, receipts, and ledger annotations. The conversational orchestrator only invokes the runner, relays escalations, and holds the human gates.

## Tier mapping

| Tier | model | model_reasoning_effort |
|---|---|---|
| trivial | gpt-5.6-luna | medium |
| standard | gpt-5.6-terra | high |
| complex | gpt-5.6-sol | high |

- Passed per worker as `codex exec -m <model> -c model_reasoning_effort=<effort>` — pinned per process.
- `ultra` effort is prohibited at every tier: it spawns subagents inside the worker, breaking brief isolation and reintroducing child-thread accumulation.
- `max` is never a default; a human may bump a single escalated task to max at the escalation gate.
- Mapping lives in one table in the runner — single place to update on model churn.

## Task loop (per task)

1. Generate brief via `extract-brief.py`; record SHA-256.
2. Dispatch worker: `codex exec` with tier-pinned model/effort; prompt = worker contract preamble + brief. Contract text sourced from the corresponding `agents/*.md` body — single source for both harnesses; `codex/agents/*.toml` retired.
3. Run the task's acceptance commands (runner executes them directly). Failure → rework iteration.
4. Trivial tier: acceptance commands are the whole verification. Standard/complex: generate review packet via `review-packet.py`, dispatch reviewer via `codex exec` (standard → terra/high; complex and final review → sol/high).
5. Reviewer verdict contract: reviewer's final message is JSON, captured via `--output-last-message`:
   ```json
   {"verdict": "pass"}
   {"verdict": "findings", "findings": ["<file:line — issue>", "..."]}
   ```
   Unparseable verdict → loud runner failure naming the cause. Never guessed at, never retried silently.
6. Findings → re-dispatch worker with findings appended to the brief. Rework cap: 2 iterations, enforced by loop counter. Worker crash / timeout / non-zero exit = a failed iteration, same path.
7. Cap hit → status `escalated`: write receipt with outstanding findings, do not start the next task, exit non-zero.
8. After the last task passes: final broad review, one `codex exec` sol/high call against whole-plan diff + spec. Diff base is the persisted `base_commit` (see Commit discipline).

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
- `run.json` is written incrementally so `--status`/the hook can distinguish an in-progress run from a dead one: top-level `status` is set to `running` right after the clean-tree check (carrying `base_commit`, so resume still reads it), then rewritten to the terminal status (`passed` | `escalated` | `escalated-final-review`) at the end. A contract error that occurs after the run dir exists rewrites `status` to `contract-error` with a `contract_error` message field. Contract errors before the run dir exists (dirty tree, unparseable plan) write no `run.json` — surfaced by `--notify` + stderr only.
- Ledger: runner annotates plan checkboxes with outcome (`[x] … — passed, 1 attempt` / `— escalated: <one-liner>`). Plan file remains the durable human-readable record; the annotation rides in the task's commit (git log is the parallel record).

## Resume

- Re-invocation skips tasks whose receipt status is `passed`; resumes at the escalated/incomplete task. Receipts + plan checkboxes are the resume state; no other state store.
- The clean-tree precondition (Commit discipline) holds on resume too: passed tasks are already committed, so a clean tree at resume start is the normal state; the first non-passed task re-runs with base = HEAD = last passed commit. An escalated task's uncommitted attempt must be committed (as a fix) or discarded by the human before resume — the precondition enforces this.

## Halt / escalation

- Two halt classes, distinguished by exit code:
  - **Task escalation (exit 2)** — rework cap hit: receipt written with `outstanding_findings`; orchestrator relays the receipt's contents to the user.
  - **Contract error (exit 1)** — malformed plan, brief/packet generation failure, unparseable reviewer verdict, reviewer process crash, or a dirty working tree at invocation start: fails loudly to stderr naming the cause; no receipt. When the run dir already exists, `run.json` is rewritten `status: contract-error` with the cause (so `--status`/the hook report it); errors before the run dir exists are stderr + `--notify` only. Orchestrator relays the stderr cause.
- Either way the runner stops before the next task and never absorbs work inline. Resolution (amend brief, re-tier, bump to max, defer) is a human decision before re-invocation.

## Session awareness

Codex-only. Purpose: a backgrounded runner is never blind — terminal events are pushed, and live state is pullable on demand and (on Codex) auto-injected. Claude Code handles session awareness natively inside its harness; nothing here targets Claude.

- **`--notify CMD`** — on every terminal event (task escalation, contract error, run completion) the runner fires a one-line summary. `fire_notify(event, summary, cmd)`, events `escalated` | `contract-error` | `completed`, invoked via `subprocess.Popen` fire-and-forget: never blocks the exit path, never changes the exit code; a broken CMD is a stderr warning, not an error. Exactly one notify per terminal event, at three call sites (escalation exit, contract-error exit in `main()`, completion exit). A user CMD receives `event` and `summary` as appended argv. No CMD given: macOS default is a modal `osascript 'display alert'` on all three events (completion included — it is the integration decision gate); non-darwin default writes the summary to stderr and fires nothing.
- **`UserPromptSubmit` hook** — `hooks/user-prompt-submit`, Python stdlib, same output contract as `hooks/session-start`: emits `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<block>"}}` on stdout, or nothing. Behavior: locate `./.forge/runs/` in cwd; no dir / no runs → silent exit 0. Latest run (by dir name) → a block of ≤ 6 lines (run dir, overall state, per-task summary with consecutive same-status tasks range-compressed, halt reason). Terminal runs older than 12h (latest receipt mtime) → silent. File reads only, no subprocess; any malformed-JSON read → silent exit 0 with a stderr note (a broken hook must never block a prompt). Shares its reader/renderer with `--status` via `scripts/forge_status.py` (the hook imports it, never shells out).
- **Codex-only wiring** — registered in the Codex hook surface only (`~/.codex/config.toml` `[[hooks.UserPromptSubmit]]` or a repo `.codex/hooks.json`), **never the shared plugin `hooks/hooks.json`** (both harnesses read that file — an entry there would fire in Claude). Install by documented copy (Codex-installs-by-copy precedent, Phase 3). Live firing on Codex is deferred verification (Codex CLI unavailable in the dev environment; Phase 4 flag-check precedent) — the hook's logic is fully unit-tested locally.
- **codex-execution.md contract** — the runner is always backgrounded to a redirected log **with `--notify`**; on re-entry the orchestrator trusts hook-injected state or runs `--status --run-dir` — never `ps`, never memory.

## Retirements / doc changes

- `codex/agents/*.toml` deleted; README Codex section drops the agent-copy step, gains runner invocation + `.forge/` gitignore note + the clean-tree precondition and per-task commit behavior.
- `skills/planning/codex-execution.md` rewritten around the runner: invocation, halt/resume, orchestrator's reduced role, clean-tree precondition, per-task commits, commit-or-discard-before-resume ergonomic.
- `_snapshot_worktree` removed from the runner (per-task base is the prior commit).
- Planning skill (shared, both harnesses): acceptance commands must treat an environment-gated skip as failure — assert the required infra is present, or make the skip exit non-zero. A skipped check is not a pass. (Also advances Phase 8 Claude parity.)
- Phase 3 spec amended in place (changelog line): in-session dispatch, nickname pools, TOML agents superseded by this spec.
- In-session Codex subagents remain acceptable outside plan execution (exploration, ad-hoc review); no forge machinery uses them.
- Session awareness (Codex-only): `codex-execution.md` gains the never-background-blind contract (background to a log with `--notify`; re-entry trusts hook state or `--status`, never `ps`/memory); README Codex section documents `--notify`/`--status` and the one-time `~/.codex/config.toml` hook install snippet.

## Testing

- pytest, stdlib only. Fake `codex` executable on PATH records argv, plays scripted exits/last-messages.
- Covered: tier→model/effort resolution; dependency ordering; acceptance-failure → rework; findings → rework → cap → halt at 2; unparseable verdict → loud failure; crash/timeout as failed iteration; resume skips `passed` receipts; receipt fields + ledger annotations; ultra never emitted.
- Phase 5 additions (real temp git repo per test): dirty tree at invocation start → contract error (exit 1), first run and resume; clean start → one commit per passed task with `forge: task N — <title>` message; escalation → no commit + halt; per-task review base = prior commit (packet holds only that task's diff); `base_commit` persisted in `run.json` and reused on resume; final review spans the whole plan across a resume; empty stage → commit skipped (no empty commit); `_snapshot_worktree` references removed.
- Phase 6 additions (session awareness): `--status` renders fixture receipts for running/completed/halted/contract-error runs and never spawns codex (fake-codex argv log empty); incremental `run.json` written `running` at start, terminal status at end; `--notify` fired exactly once per terminal event with matching event argv (fake notifier records calls), a broken notifier never changes the exit code; darwin default resolves to `osascript` argv (platform monkeypatched), non-darwin default writes stderr and fires nothing; the hook emits nothing without a run dir, ≤ 6-line valid JSON for a live-run fixture, respects the 12h age cutoff, includes the halt reason when halted, and stays silent (exit 0) on malformed `run.json`.
- Live verification of `codex exec` flag surface (`-m`, `-c model_reasoning_effort=`, `--output-last-message`) is the plan's first task, not a unit test. Live firing of the Codex `UserPromptSubmit` hook is deferred verification on a Codex install (same posture).

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
