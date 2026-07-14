# Codex Exec Runner Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Deterministic whole-plan task runner (`scripts/forge-run.py`) that executes approved forge plans on Codex via one `codex exec` process per task, replacing in-session subagent dispatch.
**Architecture:** Single stdlib Python script owning the sequential task loop: brief → worker dispatch → acceptance commands → review dispatch → rework cap (loop counter) → receipts + plan-checkbox ledger, halting mechanically on escalation. Reuses `extract-brief.py` and `review-packet.py` for all plan/spec parsing. TOML tier agents and their tests are retired; docs (codex-execution.md, README) rewritten around the runner.
**Tech stack:** Python 3 stdlib only; pytest for tests; Codex CLI (`codex exec`).
**Global Constraints:** No third-party dependencies in `scripts/` or `tests/`. Tier→model/effort mapping exists in exactly one table in `forge-run.py`. All parse failures raise loudly naming the cause — never degrade, never guess (DECISIONS 2026-07-11). `ultra` reasoning effort is never emitted (DECISIONS 2026-07-13).

### Task 1: Live codex exec flag verification
- [ ] Done — deferred: Codex CLI unavailable on this machine (DEFERRALS.md 2026-07-13)

**Files:**
- Modify: none (verification only; findings reported back, spec amended only if flags diverge)

**Spec:** Tier mapping, Risks / constraints

**Interface:** none.

**Tests:** none (live harness check, explicitly excluded from unit tests by the spec).

**Acceptance:** `codex exec --help` shows `-m/--model` and `-c` config overrides; a one-line prompt run as `codex exec -m gpt-5.6-luna -c model_reasoning_effort=medium --output-last-message <tmpfile> "reply with exactly OK"` exits 0 and writes the last message to the tmpfile. Report the exact working flag forms in the one-paragraph report.

**Tier:** trivial

**Depends on:** nothing.

### Task 2: forge-run.py — plan loop, dispatch, receipts, ledger
- [x] Done — passed, 3 attempts (2 escalation-gate reworks)

**Files:**
- Create: `scripts/forge-run.py`
- Test: `tests/test_forge_run.py`

**Spec:** Runner, Tier mapping, Task loop, Receipts

**Interface:**
- CLI: `forge-run.py <plan.md> --spec <spec.md> [--run-dir DIR] [--codex-bin PATH]`. `--run-dir` defaults to `.forge/runs/<timestamp>/` (created); `--codex-bin` defaults to `codex` on PATH (test seam).
- Exit codes: `0` all tasks passed; `1` contract/usage error (malformed plan, brief/packet generation failure, unparseable verdict); `2` halted with an escalated task.
- Module constants: `TIER_MAP = {"trivial": ("gpt-5.6-luna", "medium"), "standard": ("gpt-5.6-terra", "high"), "complex": ("gpt-5.6-sol", "high")}`; reviewer routing `REVIEW_MAP = {"standard": ("gpt-5.6-terra", "high"), "complex": ("gpt-5.6-sol", "high")}`.
- Functions (signatures only): `parse_plan_tasks(plan_path) -> list[Task]` (Task: number, title, tier, depends_on, acceptance_commands, checkbox line span — reuses/wraps `extract-brief.py` parsing, no duplicated heading grammar); `dispatch_worker(task, brief_path, codex_bin, run_dir) -> WorkerResult` (argv: `exec -m <model> -c model_reasoning_effort=<effort> --output-last-message <path>`; prompt = contract preamble from `agents/<tier-agent>.md` body + brief); `run_acceptance(task, cwd) -> list[AcceptanceResult]`; `write_receipt(run_dir, task, attempt, receipt_dict)` (fields per spec Receipts section, verbatim); `annotate_ledger(plan_path, task, status_line)`.
- Worker contract-source mapping: trivial → `agents/forge-light.md`, standard → `agents/forge-standard.md`, complex → `agents/forge-deep.md`; body text only (frontmatter stripped).

**Tests:** fake `codex` executable (fixture script recording argv, replaying scripted exit codes and last-messages) — tier resolution emits exact model/effort argv per tier; `ultra` never appears in any emitted argv; `Depends on` order respected, dependent task never dispatched before dependency passes; acceptance-command failure marks attempt failed; worker non-zero exit marks attempt failed; receipt written per attempt with all spec fields including brief SHA-256 matching the generated brief; `run.json` summarizes task statuses; ledger checkbox annotated `passed, N attempt(s)` on pass; malformed plan (bad heading, duplicate task number) exits 1 naming the cause; missing agents/*.md contract source exits 1.

**Acceptance:** `python3 -m pytest tests/test_forge_run.py -k "not review and not resume" -q` passes; `python3 scripts/forge-run.py --help` exits 0.

**Tier:** complex

**Depends on:** Task 1.

### Task 3: forge-run.py — review, rework cap, halt, resume, final review
- [x] Done — passed, 2 attempts + cosmetic cleanup

**Files:**
- Modify: `scripts/forge-run.py` (extends Task 2 module; new functions only, no rework of Task 2 interfaces)
- Test: `tests/test_forge_run.py` (extend)

**Spec:** Task loop, Resume, Halt, Receipts

**Interface:**
- `dispatch_reviewer(task, packet_path, codex_bin, run_dir) -> Verdict` — reviewer via `codex exec` per `REVIEW_MAP`; packet from `review-packet.py`.
- `parse_verdict(last_message: str) -> Verdict` — extraction rule: last parseable JSON object in the message (fenced or bare) matching `{"verdict": "pass"}` or `{"verdict": "findings", "findings": [str, ...]}`; anything else raises (exit 1).
- Rework loop: findings or failed acceptance → re-dispatch worker with findings appended to brief; cap at 2 iterations (initial attempt + 1 rework), then status `escalated`, receipt carries outstanding findings, no further tasks start, exit 2.
- `resume(plan_path, spec_path, run_dir)` — re-invocation with an existing `--run-dir` skips tasks whose latest receipt status is `passed`.
- Final review: after last task passes, one `codex exec` sol/high reviewer against whole-plan diff + spec (packet via `review-packet.py`); findings → exit 2 with `run.json` status `escalated-final-review` (no rework loop at plan level — human gate).

**Tests:** trivial tier skips reviewer dispatch entirely (no reviewer argv recorded); standard/complex dispatch reviewer with mapped model/effort after acceptance passes; `pass` verdict → task passed; findings → rework dispatch carries findings text in prompt; second findings verdict → halt: receipt `escalated` with findings, subsequent task not dispatched, exit 2; unparseable verdict (prose, malformed JSON) → exit 1 naming the cause; verdict embedded in prose/fence extracted correctly; worker crash counts as failed iteration within the cap; resume run skips `passed` tasks (no re-dispatch argv) and resumes at escalated task; ledger annotated `escalated: <one-liner>` on halt; final review dispatched sol/high after all tasks pass; final-review findings → exit 2.

**Acceptance:** `python3 -m pytest tests/test_forge_run.py -q` passes (full file); `python3 -m pytest -q` passes (whole suite).

**Tier:** complex

**Depends on:** Task 2.

### Task 4: Retire TOML tier agents
- [x] Done — passed, 1 attempt

**Files:**
- Delete: `codex/agents/forge-light.toml`, `codex/agents/forge-standard.toml`, `codex/agents/forge-deep.toml` (and the now-empty `codex/` tree)
- Delete: `tests/test_codex_agents.py`

**Spec:** Retirements

**Interface:** none.

**Tests:** none new — deletion verified by suite pass.

**Acceptance:** `python3 -m pytest -q` passes; `ls codex/agents/ 2>&1` reports no such directory; `git grep -l "codex/agents"` returns only docs/forge/ historical specs/plans/DECISIONS.

**Tier:** trivial

**Depends on:** Task 3 (runner owns tier routing before the old mechanism is removed).

### Task 5: Docs — codex-execution.md rewrite, README Codex section
- [x] Done — passed, 2 attempts + halt-contract fix

**Files:**
- Modify: `skills/planning/codex-execution.md` (full rewrite)
- Modify: `README.md` (Codex install/usage section)

**Spec:** Runner, Halt, Retirements

**Interface:** codex-execution.md contract content — runner invocation line (`python3 <plugin-root>/scripts/forge-run.py <plan.md> --spec <spec.md>`); orchestrator role reduced to: invoke runner, relay escalation receipts verbatim, hold human gates, never absorb work inline; resume procedure (re-invoke with same `--run-dir` after human resolution: amend brief source, re-tier, bump one task to `max`, or defer); in-session subagents acceptable outside plan execution only. README: drop agent-copy install step and re-copy note; add runner invocation and `.forge/` gitignore note.

**Tests:** none (prose contracts; content checked at review).

**Acceptance:** `grep -c "codex exec" skills/planning/codex-execution.md` ≥ 1; `grep -c "nickname" skills/planning/codex-execution.md` = 0; `grep -c "codex/agents" README.md` = 0; `python3 -m pytest -q` passes.

**Tier:** standard

**Depends on:** Task 3.

### Task 6: Lockstep version bump
- [x] Done — passed, 1 attempt

**Files:**
- Modify: `.claude-plugin/plugin.json` (version bump)
- Modify: `.codex-plugin/plugin.json` (same version)

**Interface:** minor version bump (behavior change, no breaking manifest change); identical version string in both files.

**Tests:** existing `tests/test_manifests.py` version-equality test covers it.

**Acceptance:** `python3 -m pytest tests/test_manifests.py -q` passes.

**Tier:** trivial

**Depends on:** Task 4, Task 5.
