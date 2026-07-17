# Codex execution (no Workflow tool)

Codex CLI has no Workflow tool to spawn/track parallel workers, so plan
execution runs through `scripts/forge-run.py` — a deterministic runner that
drives one fresh `codex exec` process per task instead of in-session
subagent dispatch. The process boundary is what makes it deterministic: no
parent-model inheritance, no child-thread quota accumulation.

**Invocation:** after the execution approval gate, the orchestrator runs the runner in the **foreground** (not backgrounded) so a halt surfaces in the conversation the instant it happens (see Session awareness):

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" <plan.md> --spec <spec.md> \
  --run-dir .forge/runs/<name> --timeout 900 --autofix auto
```

**`--autofix auto|gate`** (chosen at the execution offer, alongside the disclosed tier routing; default `auto`): `auto` runs the fix/defer/halt disposition matrix (below) so the runner reworks its own in-diff, contract-breaking findings without stopping; `gate` is the conservative escape hatch — any reviewer finding halts, no auto-fix, matching pre-Phase-7 behavior. Disclose the chosen mode in the offer alongside tier routing.

**Precondition — clean working tree:** every invocation (first run and resume) requires `git status --porcelain` to be empty, with `.forge/` self-ignored. A dirty tree causes a contract error (exit 1) naming the dirty paths; the human must commit or discard those changes before re-invoking. The runner never resets or stashes user work.

That single call is whole-plan scope. The runner owns the task loop
(`Depends on` order, sequential, one worker at a time — no pipelining, no
worktree isolation), brief generation, worker dispatch, acceptance-command
execution, review dispatch, the convergence-based rework loop, receipts, and
plan-checkbox ledger annotations. It reuses `extract-brief.py` and
`review-packet.py` for all plan/spec parsing — no duplicated heading grammar.

**Commit discipline:** after each task reaches `passed` and its ledger checkbox is annotated, the runner stages all changes and commits with message `forge: task N — <title>`. Nothing staged (e.g., uncommitted changes from a human pre-fix on resume) means the commit is skipped; no empty commits are created. The `.forge/` directory is never staged; the ledger annotation rides in the task's commit. Escalated tasks commit nothing — the rejected attempt stays uncommitted for the human to resolve. This establishes a clean checkpoint after every passed task, so HEAD is a reliable base for per-task review and resume.

**Orchestrator's role is reduced to four things:** invoke the runner, relay
escalation receipts to the user verbatim, hold the human gates (execution
approval before invoking, resolution decisions on halt), and never absorb
work inline. If a `codex exec` call inside the runner fails or halts, the
fix is a human decision and a re-invocation — not the orchestrator editing
source files or reasoning through the fix itself.

**Disposition matrix (`--autofix auto`):** every reviewer finding is classified on two runner-verified axes — provenance (`in-diff` vs `pre-existing`, checked against the actual diff, never trusted from the reviewer) × contract impact (`contract-breaking` only when the reviewer names an acceptance criterion/spec section; otherwise `improvement`). `in-diff` × `contract-breaking` → **fix** (reworked in-loop, the only auto-fixed cell); `improvement` findings (either provenance) → **defer** (logged, never fixed — no gold-plating the phase's own new code); `pre-existing` × `contract-breaking` → **halt** (a real scope decision, carries a drafted repair task). `--autofix gate` skips the matrix: any finding at all halts.

**Convergence stop (replaces the old 2-iteration cap):** each attempt re-runs worker → acceptance → reviewer → classify, and the runner picks deterministically: any **halt**-disposition finding stops the run (reason `scope-decision`); a **regression** (a finding the runner previously tracked resolved reappears, or acceptance goes green→red) stops the run (reason `regression`); a **stuck** fix finding carried across two consecutive attempts stops the run (reason `stuck`); no fix findings left and acceptance green → pass; otherwise rework. Net progress each round isn't required — a round may resolve one finding and surface a new one and still rework, not halt. A `MAX_ATTEMPTS_BACKSTOP` of **5** (raised from the old 2) is a seatbelt against slow non-convergence only, halting with reason `backstop`. Final review (below) runs the same loop.

**Halt / escalation:** the runner halts mechanically at two points, with
distinct semantics:

- **Task or final-review escalation (exit 2)** — the convergence loop above
  stopped for one of `scope-decision` | `regression` | `stuck` | `backstop` |
  `gate`. A receipt is written with outstanding findings and the halt-reason
  class (`scope-decision` also carries a drafted `repair_task`); the
  orchestrator relays the receipt's contents to the user verbatim, and
  execution stops.

- **Contract error (exit 1)** — malformed plan, brief/review-packet generation
  failure, unparseable reviewer verdict, or reviewer process crash. The runner
  fails loudly to stderr naming the cause (fail-loud contracts, no guess). No
  receipt is written at this stage; the orchestrator relays the stderr cause.

In both cases, the runner stops before starting the next task. The
orchestrator's only job at that point is relaying information to the user —
not summarizing, not softening, not attempting the fix itself.

**Resume:** re-invoke the same command with the same `--run-dir` after the
human has resolved the halt. The runner skips every task whose latest
receipt status is `passed` and resumes at the escalated task. Since passed
tasks are already committed, the clean working tree precondition at resume
start is the normal state. If an escalated task was attempted but not passed,
its uncommitted work must be committed (as a fix) or discarded by the human
before re-invoking — the precondition enforces this.

If `--run-dir` was not specified on first invocation, it defaults to
`.forge/runs/<timestamp>/` where the timestamp matches the run start time
(format: YYYYMMDDTHHmmss); the operator can find it by checking `ls -t .forge/runs/`
or by inspecting the `run.json` file there. Alternatively, specify `--run-dir`
explicitly on first invocation to control the path. Resolution before re-invoking
is a human decision among:

- amend the brief source (plan or spec) to correct what the reviewer flagged;
- re-tier the task (trivial/standard/complex) if routing was wrong for the work;
- bump the escalated task to `max` reasoning effort for one re-run — a
  human-only escalation, never a default, and never `ultra` at any tier
  (prohibited everywhere because it spawns subagents inside the worker,
  breaking brief isolation);
- fix the code directly, matching or accepting the halt's drafted
  `repair_task` (a `scope-decision` halt only — the disposition matrix already
  auto-defers harmless improvement findings, so anything reaching a human halt
  is by construction a real pre-existing/contract-breaking call), then resume.

**Tier routing:** unchanged in substance from the pipelined path — trivial
tasks skip reviewer dispatch (acceptance commands are the whole
verification), standard and complex tasks get a reviewer dispatched via
`codex exec` after acceptance passes. Model/effort per tier lives in
`forge-run.py`'s `TIER_MAP`. Reviewer dispatch is at the **task's own tier**
with fresh context, reading `TIER_MAP` directly — the reviewer's value is an
independent pass, not a stronger model. The formerly-separate reviewer-model
table is retired outright: there is no second table that could go silently
stale against `TIER_MAP` on a model-churn edit.

**Final review:** once every task passes, the runner dispatches one more
`codex exec` call, at the model/effort for the **plan's highest task tier**
(read from `TIER_MAP` — not a pinned sol/high), against the whole-plan diff
and spec — integration issues a per-task review can't see. It now runs
through the **same disposition matrix + convergence loop** as a per-task
review: a fix dispatch reworks its own in-diff/contract-breaking findings,
committing a single `fix: final-review` commit when it applied any; only a
genuine `scope-decision`/`regression`/`stuck`/`backstop`/`--gate` halt stops
the run, with `escalated-final-review` status.

**Terminal doc-sync stage:** once final review passes, the runner dispatches
one more `codex exec` call that reconciles **existing** documentation to the
shipped whole-plan diff — stale references, changed signatures/behavior, spec
changelog entries, ROADMAP status. It never authors new docs (that would be
the gold-plating the disposition matrix already forbids) and never touches
code. Landed edits commit as `docs: sync`; no drift found → no commit. A
doc/contract contradiction it can't mechanically reconcile halts the run for
a human decision, named in `run.json`'s `doc_sync.contradiction`.

**Receipts:** ephemeral, `.forge/runs/<timestamp>/` (or an explicit
`--run-dir`), one JSON receipt per task attempt plus a `run.json` summary.
The runner self-manages `.forge/`'s gitignore on first write — no
target-repo setup required. Plan-file checkboxes remain the durable,
human-readable record (`— passed, N attempt(s)` / `— escalated: <one-liner>`).
Receipts also carry each finding's classification (provenance, impact,
disposition) and, on an escalated receipt, the halt-reason class and any
drafted `repair_task`. `run.json` aggregates every task's and the final
review's defer-disposition findings under `deferrals`, plus `autofix_mode`
and the terminal `doc_sync` record; `--status` surfaces the deferrals
count/list and the halt-reason class alongside the existing per-task summary.

**DEFERRALS write-back:** the runner never writes `docs/forge/DEFERRALS.md`
itself — deferrals stay in `run.json` through the run. At clean completion,
the orchestrator reads the aggregated `deferrals` list from `run.json` (or
`--status`'s summary) and appends them to `docs/forge/DEFERRALS.md` as one
reviewed batch, in the project-memory format (see the project-memory skill).

**Session awareness — run in the foreground:** the runner is run in the foreground, not backgrounded. Foreground is what makes a halt visible: the orchestrator is blocked on the command, so the instant the runner exits non-zero (escalation exit 2, contract error exit 1) control returns to the orchestrator, which reads the receipt/stderr and **relays the halt to the human in the conversation** — "task N escalated: <findings>, needs your decision." A halt that hands control straight back to a waiting orchestrator can't go silent; that is the entire mechanism. No notifications, no hook, no `ps`.

- **A hung task can't sit forever:** `--timeout SECONDS` (recommend ~900) bounds every worker/reviewer `codex exec` call. A genuinely stuck task is killed at the timeout, counts as a failed iteration, and escalates — so even a hang becomes a loud, relayed halt rather than silence. The runner's stdout is a per-task progress narrative (`task N: <title> — starting` / `task N: passed`) streamed live in the Codex TUI; a stalled stream on the last "starting" line tells you where it is.
- **Never background-and-walk-away.** Backgrounding (`… &`) is what reintroduces blindness — the orchestrator fires and moves on, and the halt's exit code is caught by nobody. If a plan is long enough that foregrounding is genuinely painful, that is a signal to split the plan, not to background it.
- **On-demand state** without touching a run:

  ```bash
  python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" --status --run-dir .forge/runs/<name>
  ```

  Prints the run state (`RUNNING` | `COMPLETED` | `HALTED — <reason> (<halt-reason class>)` | `CONTRACT-ERROR — <cause>`), one line per task, and a `deferrals: N — <summaries>` line when any were collected, from `run.json` + receipts; dispatches nothing, exits 0.
- **Live monitor** (optional, second terminal): a full-screen `rich` TUI — the plan ledger with the in-flight task lit and its `codex exec` stream scrolling, plus a terminal-state banner on completion/halt. Best used as a **standing monitor**: leave it open once and it attaches to every run. The runner writes a `.forge/watch` launcher and prints a short command at start:

  ```bash
  sh .forge/watch      # forge-monitor.py --follow — newest run, auto-attaches to each new run
  ```

  One-shot forms: `--latest` (newest run, then exit) or `--run-dir .forge/runs/<name>`. Read-only over the run dir (dispatches nothing); needs `rich` (`pip install rich`). A killed runner renders as `stalled?` (heartbeat + pid), not a stuck spinner. This is a passive view — it does **not** replace foreground halt-relay; the orchestrator still runs the runner in the foreground.

**In-session Codex subagents remain acceptable outside plan execution** —
ad-hoc exploration, one-off review, anything that isn't dispatched by the
runner. No forge machinery spawns them; the runner's `codex exec` calls are
the only dispatch path a plan ever goes through.
