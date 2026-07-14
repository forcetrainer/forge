# Codex execution (no Workflow tool)

Codex CLI has no Workflow tool to spawn/track parallel workers, so plan
execution runs through `scripts/forge-run.py` — a deterministic runner that
drives one fresh `codex exec` process per task instead of in-session
subagent dispatch. The process boundary is what makes it deterministic: no
parent-model inheritance, no child-thread quota accumulation.

**Invocation:** after the execution approval gate, the orchestrator runs:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" <plan.md> --spec <spec.md>
```

That single call is whole-plan scope. The runner owns the task loop
(`Depends on` order, sequential, one worker at a time — no pipelining, no
worktree isolation), brief generation, worker dispatch, acceptance-command
execution, review dispatch, the rework cap, receipts, and plan-checkbox
ledger annotations. It reuses `extract-brief.py` and `review-packet.py` for
all plan/spec parsing — no duplicated heading grammar.

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
receipt status is `passed` and resumes at the escalated task. If `--run-dir`
was not specified on first invocation, it defaults to `.forge/runs/<timestamp>/`
where the timestamp matches the run start time (format: YYYYMMDDTHHmmss); the
operator can find it by checking `ls -t .forge/runs/` or by inspecting the
`run.json` file there. Alternatively, specify `--run-dir` explicitly on first
invocation to control the path. Resolution before re-invoking is a human
decision among:

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

**In-session Codex subagents remain acceptable outside plan execution** —
ad-hoc exploration, one-off review, anything that isn't dispatched by the
runner. No forge machinery spawns them; the runner's `codex exec` calls are
the only dispatch path a plan ever goes through.
