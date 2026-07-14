# Codex exec runner — design

Goal: Codex plan execution moves from in-session subagent dispatch to a deterministic task runner over `codex exec`. One fresh worker process per task; the process boundary eliminates parent-model inheritance and child-thread quota accumulation. Claude Code execution path unchanged.

## Runner

- `scripts/forge-run.py`, Python stdlib only. Reuses `extract-brief.py` and `review-packet.py` (import or subprocess) — plan/spec parsing contracts unchanged, no duplicated parsing.
- Invocation: `forge-run.py <plan.md> --spec <spec.md> [--effort N=LEVEL ...] [--timeout SECONDS]`, run by the conversational Codex orchestrator after the execution approval gate. `--effort N=LEVEL` (repeatable; LEVEL in `low`/`medium`/`high`/`xhigh`/`max`) overrides task N's worker reasoning effort only — never the reviewer's; `ultra` and unknown task numbers are rejected loudly. `--timeout SECONDS` (default 3600) bounds every worker and reviewer `codex exec` subprocess call.
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
8. After the last task passes: final broad review, one `codex exec` sol/high call against whole-plan diff + spec.

## Receipts

- Run dir `.forge/runs/<timestamp>/`, uncommitted — on first creation the runner writes a `.gitignore` containing `*` into `.forge/` (self-ignoring, no target-repo setup); README notes the behavior. One receipt per task attempt: `task-<N>-attempt-<i>.json`; plus `run.json` summary.
- Receipt fields: task number, title, tier, model + effort requested, brief path + SHA-256, worker exit code, acceptance results (command, exit code, output tail), review verdict, attempt number, status (`passed` | `rework` | `escalated`), outstanding_findings.
- Ledger: runner annotates plan checkboxes with outcome (`[x] … — passed, 1 attempt` / `— escalated: <one-liner>`). Plan file remains the durable human-readable record.

## Resume

- Re-invocation skips tasks whose receipt status is `passed`; resumes at the escalated/incomplete task. Receipts + plan checkboxes are the resume state; no other state store.

## Halt / escalation

- Two halt classes, distinguished by exit code:
  - **Task escalation (exit 2)** — rework cap hit: receipt written with `outstanding_findings`; orchestrator relays the receipt's contents to the user.
  - **Contract error (exit 1)** — malformed plan, brief/packet generation failure, unparseable reviewer verdict, reviewer process crash: fails loudly to stderr naming the cause, before meaningful task state exists; no receipt. Orchestrator relays the stderr cause.
- Either way the runner stops before the next task and never absorbs work inline. Resolution (amend brief, re-tier, bump to max, defer) is a human decision before re-invocation.

## Retirements / doc changes

- `codex/agents/*.toml` deleted; README Codex section drops the agent-copy step, gains runner invocation + `.forge/` gitignore note.
- `skills/planning/codex-execution.md` rewritten around the runner: invocation, halt/resume, orchestrator's reduced role.
- Phase 3 spec amended in place (changelog line): in-session dispatch, nickname pools, TOML agents superseded by this spec.
- In-session Codex subagents remain acceptable outside plan execution (exploration, ad-hoc review); no forge machinery uses them.

## Testing

- pytest, stdlib only. Fake `codex` executable on PATH records argv, plays scripted exits/last-messages.
- Covered: tier→model/effort resolution; dependency ordering; acceptance-failure → rework; findings → rework → cap → halt at 2; unparseable verdict → loud failure; crash/timeout as failed iteration; resume skips `passed` receipts; receipt fields + ledger annotations; ultra never emitted.
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
