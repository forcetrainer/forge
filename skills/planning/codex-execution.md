# Codex execution (no Workflow tool)

Codex CLI has no Workflow tool to spawn/track parallel workers. Execution
**mode** is chosen *before* the harness branch, per the planning skill's
Execution section: inline when accumulated context is an asset (few tasks,
later tasks build on earlier output, the change is simple); dispatch
otherwise. Inline is the same act on both harnesses; only the dispatch
mechanism is Codex-specific (the runner below).

**Inline (mode = inline):** the Codex session executes the plan task-by-task
itself — the **tdd** skill (test first, then implementation), an orchestrator
**self-review** before each commit that **disposes of findings by the inline
finding-handling canon in the planning skill's `SKILL.md`** (the four-quadrant
disposition matrix, convergence-based rework, and the draft-a-disposition-then-
surface halt gate — not silent resolution), and a commit per task, on a clean
working tree. Inline does **not** invoke the runner and does **not** dispatch a
separate reviewer — TDD + acceptance commands are the objective check. The
finding-handling canon is shared, so Codex inline follows the **same** process
Claude inline does. Use it for the low end (simple edits, doc updates,
mechanical changes, small plans) that never needed the runner.

**Dispatch (mode = dispatch):** plan execution runs through
`scripts/forge-run.py` — a deterministic runner that drives one fresh
`codex exec` process per task instead of in-session subagent dispatch. The
process boundary is what makes it deterministic: no parent-model inheritance,
no child-thread quota accumulation. The rest of this document specifies the
runner (the dispatch branch). The disposition-matrix and convergence decision
logic described below (Convergence stop) lives in shared `scripts/forge_dispose.py`
(Phase 12b) — the runner calls it in-process; the Claude dispatch path (planning
skill `SKILL.md`) calls the identical logic via its CLI. One tested decision, two
callers, so the two harnesses' rework/halt rules can't drift apart.

**Invocation:** after the execution approval gate, the orchestrator runs the runner in the **foreground** (not backgrounded) so a halt surfaces in the conversation the instant it happens (see Session awareness):

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" <plan.md> --spec <spec.md> \
  --run-dir .forge/runs/<name> --timeout 900 --autofix auto
```

**`--autofix auto|gate`** (chosen at the execution offer, alongside the disclosed tier routing; default `auto`): `auto` runs the fix/defer/halt disposition matrix (below) so the runner reworks its own in-diff, contract-breaking findings without stopping; `gate` is the conservative escape hatch — any reviewer finding halts, no auto-fix, matching pre-Phase-7 behavior. Disclose the chosen mode in the offer alongside tier routing.

**Precondition — clean working tree:** every invocation (first run and resume) requires `git status --porcelain` to be empty, with `.forge/` self-ignored. A dirty tree causes a contract error (exit 1) naming the dirty paths; the human must commit or discard those changes before re-invoking. The runner never resets or stashes user work.

**Plan lint (`run_plan`, right after the clean-tree check, before the run dir is created or anything dispatches):** `forge_lint.lint_plan(plan_path, spec_path)` validates the plan/spec against documented grammar only — task headings, `Tier:`, `Goal:`, `Spec:`, `Depends on:`, `Acceptance:`, checklist generation for every task and `--final` — and reports every defect in one run, not the first. Every defect line (`[error]`/`[warning]`) prints; any `error` raises a contract error (exit 1) naming the full list, before `run.json` exists. An empty checklist is a `warning` only — it never fails the run.

That single call is whole-plan scope. The runner owns the task loop
(`Depends on` order, sequential, one worker at a time — no pipelining, no
worktree isolation), brief generation, worker dispatch, acceptance-command
execution, review dispatch, the convergence-based rework loop, receipts, and
plan-checkbox ledger annotations. It reuses `extract-brief.py` and
`review-packet.py` for all plan/spec parsing — no duplicated heading grammar.

**The review diff includes untracked files.** Every diff the runner hands a reviewer or feeds the finding classifier — the per-task discovery packet, the repair delta on verification laps, the final-review packet and its fix loop, `forge_dispose.py`'s own recomputed diff, and the standalone `review-packet.py` CLI — comes from one helper, `review-packet.py`'s `git_diff(cwd, base)`: `git diff <base>` followed by a `git diff --no-index /dev/null <path>` new-file hunk for each untracked, non-ignored file (`git ls-files --others --exclude-standard`, so the gitignored `.forge/` never leaks in). Plain `git diff` never looks at untracked files, and the runner only stages a task's work in the commit *after* its review passes, so before 0.10.3 a task whose whole implementation was new files reviewed as "no changes" and a finding on a new file classified as pre-existing. The helper is read-only — nothing is staged, the index is never touched — so the single-commit discipline and `git stash create` snapshots are undisturbed. One consequence: the pre-repair snapshot cannot record untracked files, so a verification lap's delta shows every still-uncommitted new file the task has created, not only the ones this repair touched (DEFERRALS 2026-09-02).

**Commit discipline:** after each task reaches `passed` and its ledger checkbox is annotated, the runner stages all changes and commits with message `forge: task N — <title>`. Nothing staged (e.g., uncommitted changes from a human pre-fix on resume) means the commit is skipped; no empty commits are created. The `.forge/` directory is never staged; the ledger annotation rides in the task's commit. Escalated tasks commit nothing — the rejected attempt stays uncommitted for the human to resolve. This establishes a clean checkpoint after every passed task, so HEAD is a reliable base for per-task review and resume.

**Orchestrator's role is reduced to four things:** invoke the runner, relay
escalation receipts to the user verbatim, hold the human gates (execution
approval before invoking, resolution decisions on halt), and never absorb
work inline. If a `codex exec` call inside the runner fails or halts, the
fix is a human decision and a re-invocation — not the orchestrator editing
source files or reasoning through the fix itself.

**Disposition matrix (`--autofix auto`):** every reviewer finding is classified on two runner-verified axes — provenance (checked against the actual diff, never trusted from the reviewer) × contract impact (`contract-breaking` only when the reviewer names an acceptance criterion/spec section; otherwise `improvement`). `in-diff` × `contract-breaking` → **fix** (reworked in-loop, the only auto-fixed cell); `improvement` findings (any provenance) → **defer** (logged, never fixed — no gold-plating the phase's own new code); `pre-existing` × `contract-breaking` → **halt** (a real scope decision, carries a drafted repair task). `--autofix gate` skips the matrix: any finding at all halts.

**Provenance is three-way; `in-run` seeds instead of halting.** `verify_provenance` (`forge_dispose.py`) takes both the task's own review diff and the run's cumulative diff from run-start HEAD (`run_base`): `in-diff` when the finding's parsed location intersects the review diff; else `in-run` when it intersects the run diff instead (code this run wrote, in an earlier already-committed task, not this task's own changes); else `pre-existing`. `derive_disposition` gains one cell: `in-run` × `contract-breaking` → **seed** — the task is *not* reworked and does *not* halt; the finding is classified, logged into the run's `seeded_findings` accumulator, and the task's own loop proceeds as if it weren't there. Rationale: reworking a task over another task's already-committed diff would edit history outside that task's own vertical slice, breaking the linear per-task review-base invariant (`git diff <prior commit>`). `run_plan` threads `seeded_findings` through every task call and into the final review's `prior_findings` seed, so seeded findings surface in the final review's discovery packet as pre-classified prior findings, not raw diff. `run.json`'s `seeded_findings` (read via `_read_seeded_findings`) is populated across tasks and, deliberately unlike `threads`, **is read back on resume** — a seeded finding names a defect in shipped code, and dropping it on resume would silently ship what it names, where a stale thread id would only cost a cold respawn. In a final review, `run_base` *is* the review's own diff base, so `in-diff`/`in-run` coincide and `seed` never fires there — `classify_findings` needs no special case.

**`convergence: "resolved"` is honored, guarded.** `classify_findings` takes the prior attempt's `carried_ids` (the runner's own outstanding-fix-finding set) and drops any finding whose canonical id (`carried_from` or `id`) is in that set and whose `convergence` is `"resolved"`, before disposition — a listed resolved finding behaves exactly like an omitted one. An id the runner never tracked is not dropped — it is dispositioned normally, closing the self-labeling escape hatch a reviewer could otherwise use to dismiss any finding. `convergence_decision` is unmodified; a dropped finding never reaches it, and a falsely-resolved id reappearing on a later attempt still trips the existing regression rule.

**Location parsing and validation.** `_parse_lines` now accepts a comma-separated list of ranges (`"12-20,45,60-62"`), not just one bare number or range; a finding is `in-diff`/`in-run` when **any** parsed range intersects. `validate_locations` (`forge_dispose.py`) runs in `_verdict_defects` alongside coverage validation, on **every** verdict regardless of review kind or checklist presence: a `contract-breaking` finding with an absent or unparseable `location.lines` is a defect. It shares the existing coverage-defect retry path exactly — `_review_with_coverage` re-dispatches once naming the defect (not a rework lap, no attempt-counter or convergence-state advance), then a second invalid verdict is a contract error. An `improvement` finding with no location is never flagged — it defers regardless of provenance.

**Convergence stop (replaces the old 2-iteration cap):** each attempt re-runs worker → acceptance → reviewer → classify, and the runner picks deterministically: any **halt**-disposition finding stops the run (reason `scope-decision`); a **regression** (a finding the runner previously tracked resolved reappears, or acceptance goes green→red) stops the run (reason `regression`); a **stuck** fix finding carried across two consecutive attempts stops the run (reason `stuck`); no fix findings left and acceptance green → pass; otherwise rework. Net progress each round isn't required — a round may resolve one finding and surface a new one and still rework, not halt. A `MAX_ATTEMPTS_BACKSTOP` of **5** (raised from the old 2) is a seatbelt against slow non-convergence only, halting with reason `backstop`. Final review (below) runs the same loop.

**Session continuity (Codex mechanics):** every `codex exec` dispatch runs with `--json` (never `--ephemeral` — it defeats persistence). The runner parses the first `{"type":"thread.started","thread_id":...}` event and persists `thread_id` into a per-role `threads` map (`task-N-worker`, `task-N-reviewer`, `final-reviewer`, `final-fixer`); the map is cleared at the start of every invocation, including a resume, and never read back from a prior `run.json`, so a stale thread id can never leak across runs. `~/.codex/sessions` is never inspected and `--last` is never used (wrong-session hazard). Resume form: `codex exec resume --json --output-last-message <path> -m <model> -c 'model_reasoning_effort="<effort>"' <thread_id>` — tier pinning and last-message verdict capture are preserved on resume. **The prompt is never an argv element, cold or resumed:** it is written to the child's stdin, which `codex exec` reads whenever no PROMPT argument is given. argv is bounded by `ARG_MAX` (1 MiB on darwin, shared with the environment block) — far below the model's usable context — so a large brief or review packet passed as an argument fails the *spawn* with `E2BIG`. A PROMPT argument must not be passed alongside piped stdin: `codex exec` then appends stdin as a separate `<stdin>` block, duplicating the whole packet. Workers and reviewers are cold on lap 1; on rework laps the worker resumes with the findings-only prompt as its brief, and the task/final reviewer resumes **only for verification** — discovery review (a task's first review, and the final review's first pass) stays **cold on both harnesses**, deliberately: independence is the entire justification for a separate reviewer (DECISIONS 2026-07-16, 2026-07-17), and this is a qualification of the fresh-context rule, not an exception to it. A failed resume (missing thread, non-zero exit before any event) falls back to a cold spawn with the full packet, recorded as `resume_fallback` on the task's receipt — degraded, never fatal. Resume is scoped to **one runner invocation**: a halted run's re-invocation always spawns cold, since a human may have hand-edited code in between and the persisted session's context would then be stale and misleading.

**Coverage checklist:** before each review dispatch, the runner generates the checklist via `scripts/forge_checklist.py` (a task checklist for a task review, a final checklist for the final review) and folds it into the review packet; the reviewer's verdict must carry a `coverage` array satisfying it (Reviewer verdict contract, planning skill `SKILL.md`). Verification laps get the **reduced** checklist — only items referenced by the outstanding findings' `contract_ref` — not the full one. An empty checklist is a runner-level **SKIP**, not an error: the review dispatches without a coverage requirement and the receipt records `coverage_skipped`; `forge_checklist.py` invoked directly still raises on an empty checklist, since the tolerance belongs to the runner, not the generator. `forge_dispose.py` validates the coverage array (missing/unknown ids, an unbacked `violated` id, empty evidence) alongside the verdict parse; an invalid array gets **one retry** naming the specific defect, which does not advance the attempt counter or convergence state, then a contract error on a second invalid verdict.

**Coverage is discovery-only.** Every review packet is built with a `packet_review_kind` — `"discovery"` on a task's first review and any stateful edge case that falls back to the full packet shape, `"verification"` only when the reviewer is resumed *and* a delta-scoped repair packet was actually built — and `build_review_kind_section` embeds a `## Review kind` marker in the packet text so the reviewer sees which contract applies. `_verdict_defects(verdict, checklist, review_kind)` gates coverage validation on that marker: coverage defects are checked only when `checklist` is truthy **and** `review_kind == "discovery"`; a verification packet's verdict is never faulted for omitting `coverage`, and one that includes it anyway is not penalized. `validate_locations` is unconditional — it runs on every verdict, both kinds, checklist or not. Both defect lists share the one retry-then-contract-error path in `_review_with_coverage`.

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
the run, with `escalated-final-review` status. The fix dispatch is **cold on its
first repair, then resumed on every subsequent repair** — not cold every
attempt — with a findings-only resume prompt carrying the new outstanding
findings and affected paths, never the spec and whole-plan diff re-pasted;
the same missing-thread/failed-resume fallback and `resume_fallback` receipt
field apply.

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
