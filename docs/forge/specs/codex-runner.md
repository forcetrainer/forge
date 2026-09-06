---
system: codex-runner
supersedes:
  - specs/2026-07-03-phase3-codex-dual-harness-design.md
  - specs/2026-07-13-codex-exec-runner-design.md
  - specs/2026-07-15-forge-run-monitor-design.md
---

# Codex runner

forge's Codex CLI surface: the plugin packaging that makes it installable alongside
Claude Code, the deterministic `codex exec` plan runner that replaces in-session
dispatch, and the read-only live monitor that watches a run.

Claude Code executes a plan natively. Codex CLI has no in-session tier dispatch, no
native session awareness, no automatic per-task commits — so on Codex forge supplies
the execution substrate itself. The flow, gates, tiers, and finding dispositions are
identical across harnesses; only the substrate differs. The
review/classify/fix/defer/halt model both harnesses implement is the `execution`
spec's, not this one's.

## Packaging

Skills, scripts, and hooks are shared verbatim between harnesses. Divergence is
confined to the manifests.

- `.codex-plugin/plugin.json` — required `name` (kebab-case), `version` (semver),
  `description`. Name and description match `.claude-plugin/plugin.json`; the version
  is **kept in lockstep** — every bump touches both manifests. Plugin root convention:
  `skills/`, `hooks/hooks.json`, optional `.mcp.json`.
- `.agents/plugins/marketplace.json` at repo root — `name`, `interface.displayName`,
  `plugins[]` with `source: {source: "local", path: "./"}`. Exposes this repo as the
  plugin root.
- Hooks: one shared `hooks/hooks.json`. The Codex and Claude schemas coexist —
  `SessionStart` output contract is identical (`hookSpecificOutput.additionalContext`
  JSON on stdout) and Codex sets `CLAUDE_PLUGIN_ROOT`, so no Codex-specific hook file
  and no manual `config.toml` wiring. A hook fires only in a repo carrying the forge
  signal directory (constraint: `hooks-inert-without-signal`).
- Install is `codex plugin marketplace add <path>` + `codex plugin install forge@forge`.
  No agent-copy step: `codex exec` takes model and effort as flags, and the worker
  contract text is sourced from `agents/*.md`.
- Invocation model: no Workflow tool, no auto-delegation — Codex subagents spawn only
  when named explicitly in a prompt. This is why plan execution is a runner, not
  dispatch.
- Harness branch: `skills/planning/SKILL.md`'s execution step branches on Workflow-tool
  availability — Claude runs the in-session per-task loop; Codex (no Workflow tool)
  reads `codex-execution.md` in the skill directory, where the runner is the dispatch
  branch. That one line is what routes a Codex session into this system. Both branches
  drive the same `scripts/forge_dispose.py` decision helper.
- Test: manifest JSON validity plus version equality across both plugin manifests
  (pytest, stdlib only).

In-session Codex subagents stay acceptable *outside* plan execution — exploration,
ad-hoc review. No forge machinery uses them.

## Runner

`scripts/forge-run.py`, Python stdlib only. Reuses `extract-brief.py` and
`review-packet.py` — plan/spec parsing contracts unchanged, no duplicated parsing.

```
forge-run.py <plan.md> --spec <spec.md> [--effort N=LEVEL ...] [--timeout SECONDS]
             [--autofix auto|gate] [--run-dir DIR] [--codex-bin PATH]
forge-run.py --status --run-dir DIR
```

- Run in the **foreground** by the conversational Codex orchestrator, after the
  execution approval gate. See Session awareness.
- `--effort N=LEVEL` (repeatable; LEVEL in `low`/`medium`/`high`/`xhigh`/`max`)
  overrides task N's worker reasoning effort only — never the model, never the
  reviewer's. `ultra` and unknown task numbers are rejected loudly.
- `--timeout SECONDS` (default 3600; recommend ~900) bounds every worker and reviewer
  `codex exec` subprocess.
- `--autofix auto|gate` (default `auto`) is the finding-autonomy knob; the disposition
  matrix it selects is the `execution` spec's.
- `--status --run-dir DIR` — read-only: prints a deterministic run summary (per-task
  status, attempts, halt reason) from `run.json` + receipts and exits 0. Dispatches
  nothing — no `codex exec`, no git writes; plan/`--spec` not required. A missing or
  empty DIR prints `no run at DIR`.
- Sequential: one worker at a time, `Depends on` order. No pipelining, no worktree
  isolation.
- Whole-plan scope: the runner owns the task loop, brief generation, review dispatch,
  rework iterations, receipts, and ledger annotations. The conversational orchestrator
  only invokes the runner, relays escalations, and holds the human gates — it cannot
  absorb work inline because it is not in the loop.
- Lint runs in-process after the clean-tree precondition and before the first task, so
  a plan/spec grammar defect fails in seconds rather than surfacing mid-run as a halt.

## Tier mapping

| Tier | model | model_reasoning_effort |
|---|---|---|
| trivial | gpt-5.6-luna | low |
| standard | gpt-5.6-terra | medium |
| complex | gpt-5.6-sol | medium |

- Passed per process as `codex exec -m <model> -c model_reasoning_effort=<effort>` —
  pinned, never inherited.
- Reviewer routing reads the same table at the task's own tier: fresh context, no
  strength premium. One table, so two tier tables cannot drift on a model-churn edit.
- `ultra` is prohibited at every tier: it spawns subagents inside the worker, breaking
  brief isolation and reintroducing child-thread accumulation.
- `max` is never a default; a human may bump a single escalated task to `max` at the
  escalation gate.
- The whole-plan final review is one `codex exec` call routed at the plan's **highest**
  task tier.
- The table lives in one place, `forge_common.TIER_MAP` — the single update point on
  model churn.

## Task loop (per task)

1. Generate the brief via `extract-brief.py`; record its SHA-256.
2. Dispatch the worker: one `codex exec` process, tier-pinned model/effort. Cold spawn
   prompt = worker contract preamble + brief; the preamble is the corresponding
   `agents/*.md` body — single source shared with the Claude harness.
3. Run the task's acceptance commands directly. Failure → rework iteration.
4. Trivial tier: acceptance commands are the whole verification. Standard/complex:
   assemble the reviewer's input with `review-packet.py` and dispatch the reviewer via
   `codex exec` at the task's own tier. The packet exists because a `codex exec`
   reviewer is a subprocess and cannot gather its own context.
5. Reviewer verdict contract — the reviewer's final message is JSON, captured via
   `--output-last-message`:
   ```json
   {"verdict": "pass"}
   {"verdict": "findings", "findings": ["<file:line — issue>", "..."]}
   ```
   An unparseable verdict is a loud runner failure naming the cause. Never guessed at,
   never retried silently.
6. Findings are classified, then fixed, deferred, or halted on, per the `execution`
   spec's disposition matrix and convergence rules. The runner drives them through
   `scripts/forge_dispose.py`, the same module the Claude path calls.

### Worker dispatch mechanics

- Cold argv: `codex exec --json -m <model> -c model_reasoning_effort=<effort>
  --output-last-message <path>`; the prompt is written to the child's **stdin**, not
  argv — a review packet passed as an argument fails the spawn with `E2BIG` against
  `ARG_MAX`, far below the model's usable context.
- Resume argv: `codex exec resume --json --output-last-message <path> -m <model>
  -c 'model_reasoning_effort="<effort>"' <thread_id>` — tier pinning and last-message
  capture preserved exactly as on a cold spawn. A resumed worker already holds the
  contract and its original brief, so the prompt is findings-only.
- Always `--json`, never `--ephemeral`: the thread id from the first `thread.started`
  event is what makes resume possible.
- A hung process is killed at `--timeout` (whole process group) and reported as
  `timed_out` — the caller treats it exactly like a failed iteration. A hung
  `codex exec` never hangs the run.

## Commit discipline

- Precondition: every invocation — first run and resume — requires a clean working
  tree, `git status --porcelain` empty with the self-ignored `.forge/` excluded. Dirty
  → contract error (exit 1) naming the dirty paths; the human commits or discards
  before re-invoking. The runner never resets or stashes user work.
- Per passed task: after the task reaches `passed` and its ledger checkbox is
  annotated, the runner stages all changes and commits —
  `git add -A && git commit -m "forge: task <N> — <title>"`. Nothing staged → commit
  skipped, no empty commits. `.forge/` is never staged; the ledger annotation rides in
  the commit. Escalated tasks commit nothing — the rejected attempt stays uncommitted
  for the human to resolve.
- A clean tree at task start means `git add -A` captures exactly that task's own work,
  so HEAD is a clean checkpoint after every passed task — the invariant the per-task
  base and resume rely on.
- Per-task review base = HEAD at task start (the prior task's commit; the run-start
  commit for task 1). No stash snapshot.
- Final-review base = `base_commit`: HEAD captured before any task commits, persisted
  in `run.json` on first invocation and read — never recaptured — on resume, so the
  final diff spans the whole plan across invocations. Empty diff → final review
  skipped.

## Receipts and run state

Run dir `.forge/runs/<timestamp>/`, uncommitted. On first creation the runner writes a
`.gitignore` containing `*` into `.forge/` — self-ignoring, so there is no target-repo
setup.

- One receipt per task attempt, `task-<N>-attempt-<i>.json`: task number, title, tier,
  model + effort requested, brief path + SHA-256, worker exit code, acceptance results
  (command, exit code, output tail), review verdict, attempt number, status
  (`passed` | `rework` | `escalated`), outstanding findings.
- `run.json` is the run summary and the monitor's contract:

```
{
  "status": "running",              // running | passed | escalated
                                    // | escalated-final-review | contract-error
  "base_commit": "9f0aa21",         // whole-plan final-review diff base
  "plan": "...", "spec": "...",
  "started_at": "2026-07-15T09:11:42Z",   // run start (UTC ISO-8601)
  "updated_at": "2026-07-15T09:17:03Z",   // heartbeat, rewritten every phase transition
  "pid": 48213,                            // runner pid (liveness hint, same host)
  "current_task": 4,                       // in-flight task; null between/at terminal
  "current_phase": "worker",               // worker|acceptance|review|final-review; null at terminal
  "tasks": [
    { "number": 1, "title": "...", "tier": "standard", "status": "passed",
      "attempts": 1, "commit": "abc1234",
      "started_at": "...", "ended_at": "..." }
  ]
}
```

- Per-task `commit` is that task's commit SHA, or null when the commit was skipped.
- `current_task`/`current_phase` are set at the start of each phase and cleared to
  `null` at any terminal status.
- Written **incrementally** — `status: running` right after the clean-tree check
  (carrying `base_commit`, so resume still reads it), rewritten to the terminal status
  at the end. This is what lets `--status` and the monitor tell an in-progress run from
  a dead one.
- A contract error after the run dir exists rewrites `status: contract-error` with the
  cause in a `contract_error` field, preserving `base_commit`, tasks, and `started_at`
  so resume and elapsed time are unaffected. Contract errors *before* the run dir
  exists — dirty tree, unparseable plan — write no `run.json`; stderr is the only
  signal.
- **Heartbeat / stale-run detection:** a run whose `status` is `running` but whose
  `updated_at` (or newest live-log mtime) is older than the cutoff is reported
  *likely-dead*; a present `pid` on the same host is confirmed with `os.kill(pid, 0)`.
  Bidirectional — a fresh heartbeat or a live pid clears staleness, so a phase that
  reasons silently for minutes is not mistaken for a corpse. Display-only: never a
  terminal state and never an exit condition.
- Ledger: the runner annotates plan checkboxes with the outcome
  (`[x] … — passed, 1 attempt` / `— escalated: <one-liner>`). Idempotent — replaces any
  prior annotation; only a passed outcome checks the box. The plan file stays the
  durable human-readable record and the annotation rides in the task's commit.

## Resume

- Re-invocation skips tasks whose receipt status is `passed` and resumes at the
  escalated/incomplete task. Receipts plus plan checkboxes are the resume state; there
  is no other state store.
- The clean-tree precondition holds on resume too. Passed tasks are already committed,
  so a clean tree is the normal state; the first non-passed task re-runs with
  base = HEAD = last passed commit. An escalated task's uncommitted attempt must be
  committed as a fix or discarded by the human before resume — the precondition
  enforces this.

## Halt / escalation

Two halt classes, distinguished by exit code:

- **Task escalation (exit 2)** — the loop stops on a task: receipt written with
  outstanding findings; the orchestrator relays the receipt's contents to the user.
  Which conditions escalate is the `execution` spec's halt taxonomy.
- **Contract error (exit 1)** — malformed plan, brief/packet generation failure,
  unparseable reviewer verdict, reviewer process crash, or a dirty working tree at
  invocation start. Fails loudly to stderr naming the cause; no receipt. `run.json` is
  marked `contract-error` when the run dir exists, stderr-only when it does not.

Either way the runner stops before the next task and never absorbs work inline.
Resolution — amend the brief, re-tier, bump to `max`, defer — is a human decision
before re-invocation.

## Session awareness — foreground execution

Codex-only. Claude Code handles session awareness natively inside its harness; nothing
here targets Claude. The mechanism is foreground execution, **not** pushed
notifications.

- **Foreground, orchestrator-relayed halts.** The orchestrator runs the runner in the
  foreground and blocks on it. On a non-zero exit control returns to the orchestrator,
  which reads the receipt or stderr and relays the halt into the conversation. A halt
  that hands control straight back to a waiting orchestrator cannot go silent — that is
  the whole mechanism. The silent-halt failure mode is a property of *backgrounding*:
  fire `… &`, move on, and nobody holds the exit code. Never background and walk away.
- **The runner pushes nothing.** No notify flag, no `UserPromptSubmit` hook. Push
  machinery does not exist and is not to be reintroduced: `osascript` is blocked by the
  Codex sandbox, and a prompt hook is the wrong layer for a state a blocked
  orchestrator already holds.
- **`--timeout` is the hang backstop** (recommend ~900). A dead-man's switch, not a
  performance tuner: set it well above any real task and well below "all day." A stuck
  task is killed, counts as a failed iteration, and escalates — so even a hang becomes
  a relayed halt rather than silence.
- **Runner stdout is a human progress narrative** (`task N: <title> — starting` /
  `task N: passed`), streamed in the Codex TUI. Never load-bearing for correctness —
  state lives in receipts — but a stream stalled on the last "starting" line shows
  where a run is parked. At start the runner also prints the monitor launch line.
- **`--status --run-dir DIR`** is the on-demand, zero-dependency peek.

## Live logs

One log per task, `run_dir/task-<N>-live.log`; final review, `run_dir/final-review-live.log`.
Appended across phases with a header rule per phase:

```
── worker · codex exec · gpt-5.6-sol · medium ──
<streamed worker output, verbatim>
── acceptance ──
$ pytest -q
<streamed acceptance output>
── review · codex exec · gpt-5.6-terra · medium ──
<streamed reviewer output>
```

On a rework attempt the loop appends fresh phase headers — the file is the full attempt
history, and the monitor shows the tail.

Two tee helpers in `forge_common`, both spawning through shared plumbing that kills a
timed-out child's whole process group:

```
run_teed(argv, *, cwd=None, shell=False, timeout, live_path, header, stdin_text=None)
    -> TeeResult(exit_code: int|None, timed_out: bool, tail: str)
run_json_teed(argv, *, cwd=None, timeout, live_path, events_path, header,
              render_line, stdin_text=None)
    -> JsonTeeResult(exit_code, timed_out, tail, thread_id: str|None)
```

- Both append `header`, then stream the child's merged stdout+stderr, flushed per line
  so the monitor tails it live, and return `tail` = the last `_ACC_TAIL_CHARS` of the
  merged stream. Behavior-preserving replacements for
  `subprocess.run(capture_output=True)`.
- `run_json_teed` drives `codex exec --json`: raw JSONL goes to `events_path` for
  thread capture and debugging, and each event is translated by `render_line` into a
  plain-text live-log line — `None` emits nothing, so control and progress events
  produce no noise. The live log is human-readable text, never raw JSON. A merged-stream
  line that fails to parse as JSON is teed verbatim and not recorded as an event —
  fail-open. `thread_id` comes from the first `thread.started` event and stays `None`
  without raising when absent.
- **Fail-loud diagnostics are preserved:** a reviewer-crash `RuntimeError` quotes the
  tee result's `tail`. Teeing must not lose the stderr tail used in the crash message.
- Acceptance output is tee'd to the same task log so the monitor sees it scroll; unit
  calls pass no live path and the output goes to `os.devnull`.
- Worker last-message capture (the `--output-last-message` file) is independent of the
  tee: the verdict and the worker's final report come from that file, never from the
  live log.
- Tee failure must never fail the run — degrade to no-tee for that phase and keep
  executing. Observability is best-effort; execution is not.

## Monitor

`scripts/forge-monitor.py` — an attach-from-outside TUI showing where a run is in its
plan and streaming the in-flight task's `codex exec` output. Read-only observer: it
never dispatches, never touches git, and never imports the runner's dispatch code. The
run dir is the only contract between the two processes; data flows one way,
runner → (`run.json` + `task-N-live.log`) → monitor.

```
forge-monitor.py (--run-dir DIR | --latest | --follow) [--poll SECONDS]
```

- The three modes are mutually exclusive and one is required. `--run-dir` watches one
  run; `--latest` watches the newest dir under `.forge/runs/`, then exits; `--follow`
  is the standing monitor — it re-picks the newest run each tick, so a finished run's
  final frame stays up until a newer run appears and then flips to it automatically.
  `--poll` defaults to 0.1s.
- The runner writes `.forge/watch`, a one-line launcher for `--follow` with the
  monitor's absolute path baked in, and prints `sh .forge/watch` at start — a short
  copyable command instead of a long plugin path that line-wraps. Idempotent, and
  `.forge/` is gitignored so it never dirties the tree.
- Reuses `forge_status.read_run_state` by import — never shells out to `--status`. One
  reader backs both.
- `rich` is the monitor's one dependency, and the only exception to `stdlib-only`; it
  is read-only rendering. A missing `rich` exits 1 with an install hint, never a
  traceback. Textual only if interactivity is ever wanted — it is not.
- No interactivity: no scrollback, no filtering, no keyboard nav. No push, remote, or
  away notification.

Layout — two panels plus a terminal-state banner:

```
┌ FORGE RUN ─────────────────────────────────── ● running ┐
  plan  2026-07-15-forge-run-monitor.md
  run   .forge/runs/20260715T091142
  3/7 tasks · elapsed 04:12 · 1 rework
  ✓  1  Parse plan tasks            standard  passed  0:41
  ✓  2  run.json incremental        standard  passed  1:03
  ✓  3  --status reader             trivial   passed  0:12
  ⠙  4  Live-log tee to disk        complex   worker  1:37   ← lit + spinner
  ○  5  Monitor: task ledger        standard  queued
  …
└──────────────────────────────────────────────────────────┘
┌ ▸ task 4 · worker · codex exec · gpt-5.6-sol · medium ─ live ─┐
  <in-flight task's stream, tailing>
└──────────────────────────────────────────────────────────────┘
```

Rendering contract:

- Panels `box.SQUARE` hairline; a table for the ledger with right-aligned tabular
  elapsed. Row columns `glyph · number · title · tier · phase · elapsed`; title
  `no_wrap`, `overflow="ellipsis"`, sensible min width on resize.
- Palette (truecolor): fg `#d3dae2`, dim `#66717f`, edge `#212a34`, accent/running
  `#5ad0df`, passed `#59c26b`, queued `#3e4753`, halt `#f2683f`. Semantic fills are
  separate from the cyan accent.
- In-flight row: `dots` spinner in the glyph column, a colored `▌` gutter bar, accent
  cells. The lit row's identity and the live-panel header are **always** the same task —
  the header names `task N · phase · model` so the two are provably in sync.
- Each tick re-reads `run.json` and tails the current live log to the last screenful.

### Terminal-state banner

On any terminal `run.json` status the stream freezes on its tail and a full-width
bottom banner is painted; the semantic fill carries the state before a word is parsed.

- Completed (`passed`): green — `✓ RUN COMPLETE — N/N tasks passed ·
  <final-review outcome> · <elapsed>` · `press q to exit`. It claims a clean review
  only when a final review actually ran and passed.
- Halted (`escalated` / `escalated-final-review`): red-orange, two lines —
  `■ HALTED — task N escalated after K attempts` plus the first outstanding finding
  from the receipt (from `final-review.json` for a final-review halt) ·
  `press q to exit`.
- Contract error: red-orange — `■ CONTRACT ERROR — <reason>` · `press q to exit`. A
  task left mid-flight by a contract error renders `interrupted`, not a frozen spinner.
- Banner ≤ 2 lines; a gentle pulse on the halt/error fill is allowed, respecting
  reduced-motion and no-color terminals. The monitor then blocks until `q`/Ctrl-C and
  the final frame stays on screen.

A stale `running` run gets no banner: the top-panel status renders `stalled?` in halt
color and the spinner stops, so a killed runner is visibly distinct from a live one
without asserting a terminal state the runner never wrote. The monitor keeps polling —
staleness is never an exit condition.

### Error handling

- The monitor is defensive: absent, partial, or half-written `run.json` → render what
  is readable (falling back to receipts, as `read_run_state` already does), never
  crash. A missing live log → empty live panel with a `waiting for output…`
  placeholder.
- No run at the target dir → one-line message and non-zero exit, mirroring `--status`'s
  `no run at …`.
- Monitor failure never affects the runner: separate process, and the runner never
  reads the monitor.

## Testing

- pytest, stdlib only. A fake `codex` executable on PATH records argv and plays
  scripted exits and last-messages; runner tests drive real temp git repos.
- Runner: tier → model/effort resolution; dependency ordering; acceptance failure →
  rework; unparseable verdict → loud failure; crash and timeout as failed iterations;
  resume skips `passed` receipts; receipt fields and ledger annotations; `ultra` never
  emitted.
- Commit discipline: dirty tree at invocation start → contract error (exit 1), first
  run and resume; clean start → one commit per passed task; escalation → no commit and
  a halt; per-task review base = prior commit (the packet holds only that task's diff);
  `base_commit` persisted and reused on resume; final review spans the whole plan
  across a resume; empty stage → commit skipped.
- Run state: `--status` renders fixture receipts for running/completed/halted/
  contract-error runs and never spawns codex (the fake-codex argv log stays empty);
  `run.json` written `running` at start and terminal at end; a contract error after the
  run dir exists persists the marker, before it exists writes no `run.json`; the new
  progress fields are written at phase transitions and cleared at terminal state; stale
  detection flips a `running` run past the cutoff.
- Tee: phase header plus streamed body written to `live_path`; correct exit code and
  tail; reviewer-crash tail preserved in the `RuntimeError`; timeout kills the child and
  reports `timed_out`; JSON events split to `events_path` with non-JSON lines teed
  verbatim.
- Monitor render: `rich` `Console(record=True)` snapshots for running / halted /
  completed — lit-row glyph and spinner present, live-panel header names the current
  task, banner text and semantic color per state. `--latest` selects the newest dir; the
  reduced-motion path renders static.
- Manifests: JSON validity and version equality across both plugin manifests.
- Live `codex exec` stream texture is deferred verification on a Codex install, not a
  unit test; the format contract is the phase headers plus verbatim passthrough, which
  is texture-independent.

## Acceptance

- Codex: the plugin installs from the local marketplace, skills are discoverable, and
  the `SessionStart` hook emits flow context only in a repo carrying the forge signal
  directory.
- The runner executes a multi-task plan end-to-end with pinned models per receipt;
  forced reviewer findings drive rework and then a mechanical halt; re-invocation
  resumes past passed tasks.
- A run is watchable end-to-end in a second terminal: the task ledger advances, the
  in-flight task streams live, and completion or halt paints the banner.
- Killing the runner mid-task makes the monitor show `stalled?` within the cutoff, not
  a perpetual live spinner.
- Claude Code behavior is unchanged by anything in this system: the plugin updates and
  loads, and its hooks and skills work as before.

## Risks / constraints

- `codex exec`'s flag surface churns (effort values, output flags). Verified live
  against a real Codex install rather than assumed; the tier table is the single update
  point when model ids move.
- Reviewer JSON discipline: models wrap JSON in prose. The extraction rule — the last
  fenced or parseable JSON object in the message — is specified in the reviewer
  contract and still fails loud when absent.
- The Codex subagent surface is young: custom-agent selection has regressed (v0.137.0),
  spawned agents have silently inherited the parent model, and completed workers pile up
  against the thread limit (openai/codex#19197, #22779). Plan execution sidesteps both
  by construction — one process per task, no inheritance, no accumulation — which is
  why the caveats apply only to ad-hoc in-session subagents.
- Acceptance commands must treat an environment-gated skip as failure: assert the
  required infra is present, or make the skip exit non-zero. A skipped check is not a
  pass.

## Changelog

2026-09-05: consolidated from three dated specs — phase3 codex-dual-harness, codex-exec-runner, forge-run-monitor (#47)
2026-09-05: dropped the runner spec's rework cap (`MAX_ATTEMPTS = 2`), its findings→rework→cap→escalate steps, and its single-shot final-review gate — superseded by the disposition matrix, convergence-based rework, the final-review fix loop and the doc-sync stage, all owned by the `execution` spec; the task loop keeps the Codex-mechanical half (brief, `codex exec` dispatch, acceptance, packet assembly, verdict capture) (#47)
2026-09-05: dropped the runner spec's `--notify`/`fire_notify` and `UserPromptSubmit` hook — the push machinery was deleted on 2026-07-14 after live Codex testing (`osascript` blocked by the sandbox, the hook `exit 127` on every prompt) and survived only in that spec's changelog; Session awareness here states foreground execution and that no notify flag or hook exists (#47)
2026-09-05: dropped the runner spec's "Retirements / doc changes" — a one-time migration worklist (`codex/agents/*.toml` deletion, README and skill rewrites, `_snapshot_worktree` removal, the phase-3 amendment); its two standing rules survive, the env-gated-skip authoring rule under Risks / constraints and the ad-hoc-subagents allowance under Packaging (#47)
2026-09-05: dropped phase3's TOML tier agents (deliverable 2, and the custom-agent contract under Verified harness contracts) — retired by the runner, which takes model and effort as `codex exec` flags and sources contract text from `agents/*.md`; the documented copy-to-`~/.codex/agents` install step goes with them (#47)
2026-09-05: dropped phase3's in-session sequential dispatch (deliverable 4) — nickname pools, the checkbox-as-dispatch-ledger and the no-lifecycle-machinery rule are replaced by runner receipts and the annotated ledger; the orchestrator no-work rule it carried is the `execution` spec's and appears here only as the runner's reduced-orchestrator contract (#47)
2026-09-05: dropped phase3's model list (`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`) — superseded by the GPT-5.6 tier mapping (#47)
2026-09-05: resolved phase3's open hook-wiring item as compatible — one shared `hooks/hooks.json` serves both harnesses; no `hooks/codex-hooks.json` and no `config.toml` snippet (#47)
2026-09-05: dropped the monitor spec's Scope in/out framing — a phase-scoped delta statement; its standing exclusions (no interactivity, no push notification) are stated under Monitor (#47)
2026-09-05: dropped the monitor spec's `_run_teed` runner-private helper name — the tee lives in `forge_common` as `run_teed`, with `run_json_teed` added for the `codex exec --json` event stream (#47)
2026-09-05: monitor gains `--follow`, the standing mode that auto-attaches to each new run, plus the `.forge/watch` launcher the runner prints at start (#47)
