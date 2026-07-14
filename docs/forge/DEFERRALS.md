# Deferrals

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
