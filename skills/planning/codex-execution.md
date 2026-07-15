# Codex execution (no Workflow tool)

Codex CLI has no Workflow tool to spawn/track parallel workers, so plan
execution runs through `scripts/forge-run.py` — a deterministic runner that
drives one fresh `codex exec` process per task instead of in-session
subagent dispatch. The process boundary is what makes it deterministic: no
parent-model inheritance, no child-thread quota accumulation.

**Invocation:** after the execution approval gate, the orchestrator backgrounds the runner to a log and walks away — terminal-event notifications are on by default (see Session awareness — never background blind):

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" <plan.md> --spec <spec.md> \
  --run-dir .forge/runs/<name> > .forge/runs/<name>/run.log 2>&1 &
```

**Precondition — clean working tree:** every invocation (first run and resume) requires `git status --porcelain` to be empty, with `.forge/` self-ignored. A dirty tree causes a contract error (exit 1) naming the dirty paths; the human must commit or discard those changes before re-invoking. The runner never resets or stashes user work.

That single call is whole-plan scope. The runner owns the task loop
(`Depends on` order, sequential, one worker at a time — no pipelining, no
worktree isolation), brief generation, worker dispatch, acceptance-command
execution, review dispatch, the rework cap, receipts, and plan-checkbox
ledger annotations. It reuses `extract-brief.py` and `review-packet.py` for
all plan/spec parsing — no duplicated heading grammar.

**Commit discipline:** after each task reaches `passed` and its ledger checkbox is annotated, the runner stages all changes and commits with message `forge: task N — <title>`. Nothing staged (e.g., uncommitted changes from a human pre-fix on resume) means the commit is skipped; no empty commits are created. The `.forge/` directory is never staged; the ledger annotation rides in the task's commit. Escalated tasks commit nothing — the rejected attempt stays uncommitted for the human to resolve. This establishes a clean checkpoint after every passed task, so HEAD is a reliable base for per-task review and resume.

**Orchestrator's role is reduced to four things:** invoke the runner, relay
escalation receipts to the user verbatim, hold the human gates (execution
approval before invoking, resolution decisions on halt), and never absorb
work inline. If a `codex exec` call inside the runner fails or halts, the
fix is a human decision and a re-invocation — not the orchestrator editing
source files or reasoning through the fix itself.

**Halt / escalation:** the runner halts mechanically at two points, with
distinct semantics:

- **Task escalation (exit 2)** — rework cap reached (2 iterations: initial
  attempt + 1 rework). A receipt is written with outstanding findings; the
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
- defer the finding (`docs/forge/DEFERRALS.md`) if it's non-spec scope, then
  resume.

**Tier routing:** unchanged in substance from the pipelined path — trivial
tasks skip reviewer dispatch (acceptance commands are the whole
verification), standard and complex tasks get a reviewer dispatched via
`codex exec` after acceptance passes. Model/effort per tier lives in
`forge-run.py`, not here or in any per-tier config file — but in two tables,
not one: `TIER_MAP` (worker model/effort, all three tiers) and `REVIEW_MAP`
(reviewer model/effort, standard/complex only). `REVIEW_MAP` currently
duplicates `TIER_MAP`'s standard/complex rows; a model-churn edit must
update both, or reviewer routing goes silently stale.

**Final review:** once every task passes, the runner dispatches one more
`codex exec` call (sol/high) against the whole-plan diff and spec —
integration issues a per-task review can't see. Findings there halt with
`escalated-final-review` status; there's no rework loop at plan level, only
a human gate.

**Receipts:** ephemeral, `.forge/runs/<timestamp>/` (or an explicit
`--run-dir`), one JSON receipt per task attempt plus a `run.json` summary.
The runner self-manages `.forge/`'s gitignore on first write — no
target-repo setup required. Plan-file checkboxes remain the durable,
human-readable record (`— passed, N attempt(s)` / `— escalated: <one-liner>`).

**Session awareness — never background blind:** the runner is always backgrounded to a redirected log, and its terminal events surface on their own — the orchestrator never polls `ps` and never trusts its own memory of where a run is.

- **Notifications (on by default):** on every terminal event — task escalation, contract error, completion — the runner fires a notification. On macOS with no `--notify` given, that is a modal `osascript` alert (completion included — it's the integration decision gate). `--notify "<cmd>"` overrides with any command; it receives the event name and a one-line summary as trailing argv. `FORGE_NOTIFY_DISABLE=1` silences the default modal for non-interactive/CI runs. Notifications are fire-and-forget — a broken command never changes the runner's exit code.
- **On re-entry, trust injected state or `--status` — never memory.** State lives in receipts, not stdout (stdout is a human progress narrative for `tail -f`, never load-bearing). Read the current state with:

  ```bash
  python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" --status --run-dir .forge/runs/<name>
  ```

  It prints the run state (`RUNNING` | `COMPLETED` | `HALTED — <reason>` | `CONTRACT-ERROR — <cause>`) and one line per task, from `run.json` + receipts; it dispatches nothing and exits 0.
- **Auto-injected state (Codex-only):** the `hooks/user-prompt-submit` hook injects a compact live-run block on every prompt so the orchestrator never operates on stale memory. It is **Codex-only** and wired via `~/.codex/config.toml` (see README) — never the shared `hooks/hooks.json`, which both harnesses read. Claude Code has native session awareness and needs none of this.

**In-session Codex subagents remain acceptable outside plan execution** —
ad-hoc exploration, one-off review, anything that isn't dispatched by the
runner. No forge machinery spawns them; the runner's `codex exec` calls are
the only dispatch path a plan ever goes through.
