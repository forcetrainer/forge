# Deferrals

## 2026-07-15 — Phase 9 monitor: live verification on a Codex box (env constraint)
**Why:** The monitor's render, run.json contract, tee, and stale detection are fully unit-tested locally (`.venv` with `rich`), but two things can only be confirmed on a real Codex install: (1) the exact texture of the `codex exec` stream that scrolls in the live panel — the format contract here is phase headers + verbatim passthrough, which is texture-independent, but nobody has watched a real Codex worker scroll through it; (2) the end-to-end feel of watching an actual multi-task run in a second terminal (spinner cadence, tail latency, banner on a real halt). Codex CLI is absent from the dev env (same constraint as the Phase 4 flag-check and the Phase 6 hook-firing deferrals).
**From:** Phase 9 forge-run-monitor execution (2026-07-15)
**Follow-up:** when-codex-cli-is-available — watch one real run end-to-end; if the `codex exec` stream needs reformatting for readability (e.g. it emits JSON events rather than prose), add a thin display filter in `forge-monitor.py` (the tee stays verbatim on disk).

## 2026-07-15 — Phase 9 monitor: `rich` isn't installable into the dev/system Python (PEP 668)
**Why:** The monitor needs `rich` importable by whatever Python runs `forge-monitor.py`. The dev machine's Homebrew Python 3.14 is PEP 668 externally-managed, so `pip install rich` (and `--user`) are both refused; the tests run against a project-local `.venv` (gitignored) instead. So on this machine the monitor won't run under bare `python3 scripts/forge-monitor.py` until `rich` is on that interpreter — a real usability gap, since the runner prints a `python3 …/forge-monitor.py` command the user is meant to paste.
**From:** Phase 9 forge-run-monitor execution (2026-07-15)
**Follow-up:** decide the install story for the operator's environment — document `pip install --break-system-packages rich` (reversible, pure-Python) or a `pipx`/venv wrapper, or have `forge-monitor.py`'s missing-rich hint name the exact command for this machine. The guarded import already fails gracefully with an install hint; this is about making the hint actionable on a managed Python.

## 2026-07-15 — Codex sandbox blocks per-task git commits; commit discipline needs a sandbox-compatible path
**Why:** Phase 5 commit discipline has the runner commit each passed task (`git add -A && git commit`), which is what gives the per-task review base and resume their clean checkpoints. Under Codex, execution runs inside the sandbox (workspace-write / Seatbelt), and the git commit can't complete from there — so a plan can't self-commit per task as designed. **Reconcile first:** an earlier run committed task 1 successfully (`Task 1 … was committed`), so the block may be mode-specific, task-specific, or intermittent — pin down the exact failing git operation and the sandbox mode in play before designing a fix (candidates: git can't read `~/.gitconfig` outside the workspace → identity error; a sandbox write-boundary on `.git`; a mode difference between the runner process and the `codex exec` workers). Can't just drop commit-per-task — the per-task review base (`git diff <prior commit>`) and resume both depend on the clean checkpoints.
**From:** live Codex execution of the phase44 plan (2026-07-15)
**Follow-up:** (a) commit from a context with git access — run/grant the runner git outside the sandbox, or a post-task commit step Codex is allowed to run; or (b) relax commit-per-task on the Codex path (stage-only, commit once at the end, or human-commits-at-halt) and rework the per-task review base + resume to not require per-task commits. Step one is diagnosis: exact failing git op + sandbox mode, reconciled against the successful task-1 commit.

## 2026-07-15 — A hard-killed Codex run stays `status: running` indefinitely
**Why:** Phase 6's incremental `run.json` writes `running` at start and a terminal status only on a clean exit. So a run whose process is SIGKILLed mid-task leaves `run.json` stuck at `running`, indistinguishable from an actually-live run — `--status` (and any monitor reading the run dir) reports RUNNING forever. Benign in practice (the next invocation over that run-dir rewrites the status), but it undercuts "distinguish an in-progress run from a dead one." Surfaced by the Phase 6 final integration review. (Originally also concerned the UserPromptSubmit hook, since removed; now applies to `--status` and the planned external monitor.)
**From:** Phase 6 session-awareness final review (2026-07-15)
**Follow-up:** revisit-if-killed-runs-cause-confusion; candidate fix is a liveness signal (pid + `os.kill(pid, 0)` check, or a heartbeat mtime with a cutoff for `running` runs too) so `--status`/the monitor can mark a stale `running` run as likely-dead. FOLDED-IN (2026-07-15): the candidate fix is now specified as part of Phase 9 (live run monitor) — `run.json` gains `updated_at` heartbeat + `pid`, and the monitor renders a stale `running` run as `stalled?` (docs/forge/specs/2026-07-15-forge-run-monitor-design.md, Unit 2). Resolved when Phase 9 ships.

## 2026-07-14 — Silent worker stalls recurred during Phase 5 execution; big forge test file loads workers toward the stall band
**Why:** Phase 5 execution hit the 4b failure mode twice — dispatched `forge-deep` (opus·xhigh) workers went silent mid-turn, no error, right after large context injections (TDD skill load); the workflow buried the hang until a human noticed. Two reinforced findings for the Codex/Claude hardening phases: (1) session awareness (`--notify` + a stall watchdog) is the fix — it would have surfaced the hang instead of waiting on a human, and it is exactly Phase 6 plus the stall-detection deferral preserved at `backup/4b-attempt`; (2) `tests/test_forge_run.py` is ~47KB/1200 lines — a worker ingesting it plus `forge-run.py` (~40KB) plus the TDD skill plus its brief stacks toward the ~80k-context band where the stalls cluster, so splitting that test file is a candidate mitigation independent of transport weather. Phase 5's Task 1 was ultimately completed inline (Claude, this session) after the dispatched workers stalled.
**From:** Phase 5 commit-discipline execution (2026-07-14)
**Follow-up:** fold-into-phase-6-session-awareness-and-stall-watchdog. RESOLVED (part 2, 2026-07-14): the file-size half is done — `scripts/forge-run.py` and `tests/test_forge_run.py` were decomposed into concern-focused modules (see DECISIONS 2026-07-14, "right-sizing forge's own source"); no single forge source/test file now exceeds ~625 lines, so a worker loads only the slice its task touches. Part 1 (session awareness / stall watchdog) remains open for Phase 6.

## 2026-07-13 — forge-run.py re-dispatches the final review on re-invocation of a fully-passed run
**Why:** Resume skips tasks whose receipt is `passed`, per the spec's Resume contract — but the plan-level final review has no receipt-skip, so re-invoking the runner over a run where everything already passed dispatches a fresh sol/high reviewer call. Cost observation from Task 3's escalation review, not a contract violation; re-invoking a fully-passed run is not a normal flow.
**From:** codex-exec-runner plan, Task 3 escalation review
**Follow-up:** revisit-if-re-invocation-of-passed-runs-becomes-a-real-flow; candidate fix is a final-review receipt honored on resume

## 2026-07-13 — Task 1: Live codex exec flag verification (environment constraint)
**Why:** Task 1 (`Live codex exec flag verification`) is an exploratory live-harness check requiring the Codex CLI binary to be available on PATH. Executed in Claude Code environment where codex is not available; the test is designed for and must run on a Codex CLI installation. Expected flags are documented in spec (2026-07-13-codex-exec-runner-design.md Tier mapping section: `-m/--model`, `-c model_reasoning_effort=`, `--output-last-message`); verification must occur on Codex before Task 2 (runner implementation) proceeds. No code changes required; findings are for spec validation only.
**From:** codex-exec-runner plan, Task 1 execution on Claude Code (environment mismatch)
**Follow-up:** when-codex-cli-is-available-for-live-verification; if-flags-diverge-amend-tier-mapping-table-in-spec-and-runner

## 2026-07-13 — Mechanical rework-cap enforcement on the Claude Code path
**Why:** The Codex runner turns the 2-iteration rework cap into a loop counter; on Claude Code the cap remains prose in the planning skill, enforced by orchestrator discipline — the same enforcement gap observed on Codex (reviewer failures absorbed, process never halts) exists there in principle. Scoped out of the runner work at user direction: Codex-only for now.
**From:** codex-exec-runner brainstorm (2026-07-13)
**Follow-up:** revisit-when-a-claude-code-run-blows-through-the-rework-cap

## 2026-07-11 — review-packet untracked-file handling
**Why:** `git diff <base>` omits untracked files, so a dispatch-path task whose only output is new uncommitted files yields a packet honestly reporting `no changes vs <base>` (exit 0) — thin by workflow, not by parsing. The inline execution path commits per task; the dispatch path doesn't state it. Handling deferred to a docstring note in review-packet.py rather than code: detecting/including untracked files guesses at workflow state the script can't verify.
**From:** parser-family audit after #8/#9 (issue #13)
**Follow-up:** revisit-if-a-dispatch-run-produces-an-empty-packet-for-a-real-change; candidate fix is a stderr warning when `git status --porcelain` shows untracked files

**Why:** The tier-down preference (fully enumerated interfaces + tests → lower tier) ships as two sentences in phase 2 without the proposed two-run A/B experiment; observed defects are taste-misses, which are tier-insensitive, and the review pass backstops real errors.
**From:** docs/forge/ideas/2026-07-02-upgrade-cycle.md (§6 open question)
**Follow-up:** revisit-when-phase-2-execution-shows-tier-related-defects

## 2026-06-10 — Skill test harness
**Why:** Superpowers' `tests/` drives the `claude` CLI to verify skills trigger and behave; valuable for continual refinement but heavy to maintain.
**From:** Initial fork plan
**Follow-up:** revisit-when-first-month-of-real-use-shows-trigger-or-behavior-drift

## 2026-06-10 — systematic-debugging port
**Why:** Current models do hypothesis-driven debugging natively; keep the fork lean.
**From:** Initial fork plan
**Follow-up:** revisit-when-debugging-quality-regresses

## 2026-06-10 — Workflow script template for large-plan execution
**Why:** Planning skill describes the shape (implementer + combined review, pipelined); a reusable template is premature before a large plan actually runs.
**From:** Initial fork plan
**Follow-up:** revisit-when-first-large-plan-executes

## 2026-06-10 — Windows/cross-platform hook shim
**Why:** macOS-only environment; superpowers' run-hook.cmd complexity not needed.
**From:** Initial fork plan
**Follow-up:** drop

## 2026-06-10 — foundation-lacrosse legacy path migration
**Why:** Repo still has `docs/superpowers/`; skills offer a one-time `git mv docs/superpowers docs/forge` when the flow next runs there. In-flight Phase 4a plan executes fine under either regime.
**From:** Initial fork plan
**Follow-up:** revisit-when-flow-next-used-in-foundation-lacrosse
