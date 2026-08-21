# Review Continuity & Contract Coverage (Phase 13) Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Make discovery reviews exhaustive via a machine-checked contract checklist, and stop every rework lap from rebuilding context, by resuming the worker and the verification reviewer instead of respawning them.
**Architecture:** A new shared `scripts/forge_checklist.py` derives a contract checklist from existing plan/spec grammar (one implementation, two harness callers — the `forge_dispose.py` pattern). The reviewer verdict contract gains a required `coverage` array; `forge_dispose.py` validates it. On Codex, every `codex exec` dispatch moves to `--json` so the runner can capture `thread.started`, persist thread ids in `run.json`, and resume the worker for rework and the reviewer for verification; verification packets carry only the repair delta and a reduced checklist. On Claude the same canon is expressed as `Agent`/`SendMessage` in `SKILL.md`.
**Tech stack:** Python 3 stdlib (argparse, json, subprocess, dataclasses, re); pytest.
**Global Constraints:** No live `codex` binary is available in this environment — every dispatch test is fixture-based: argv shape asserted and event streams replayed from fixtures, never executed. `tests/test_forge_convergence.py` and `tests/test_forge_classify.py` must pass **unchanged** — that is the mechanical proof this phase did not alter loop authority; an assertion needing an edit there means the change is wrong, not the test. Convergence rules, the disposition matrix, the 5-lap backstop, and `--autofix auto|gate` semantics are untouched. `forge_checklist.py` imports `forge_common` as a plain module (`sys.path.insert` then `import forge_common`), preserving one `Finding`/`Verdict` class identity (2026-07-14 decomposition hazard). Live-log work is limited to parity with today's output — no readability redesign (DEFERRALS 2026-08-21). Baseline suite: 284 passed, 2 skipped.

## File structure

- `scripts/forge_checklist.py` (create) — checklist derivation + rendering + CLI. Single responsibility: turn a plan+spec into checklist items; never validates, never dispatches.
- `scripts/forge_common.py` (modify) — `CoverageEntry` dataclass, `coverage` field on `Verdict`, `coverage` requirement in `REVIEW_VERDICT_INSTRUCTION`.
- `scripts/forge_dispose.py` (modify) — parse `coverage`, `validate_coverage`, `--checklist` CLI input, coverage fields on `decision.json`. Still decides only; never dispatches.
- `scripts/review-packet.py` (modify) — checklist section in packets; verification-packet mode (delta + reduced checklist).
- `scripts/forge-run.py` (modify) — `--json` dispatch, `thread.started` capture, event→live-log rendering, thread persistence, resume dispatch + fallback, checklist wiring, coverage retry, delta verification packets, de-pasted final-review fixer brief.
- `scripts/forge_git.py` (modify) — pre-repair tree snapshot helper.
- `skills/planning/SKILL.md` (modify) — cross-harness canon: coverage contract, discovery-cold/verification-resumed rule, Claude `Agent`/`SendMessage` adapter.
- `skills/planning/codex-execution.md` (modify) — runner specifics: `--json`, thread ids, resume, fallback, coverage retry.
- `tests/test_forge_checklist.py`, `tests/test_forge_coverage.py`, `tests/test_forge_threads.py`, `tests/test_forge_resume.py`, `tests/test_forge_verification_packet.py` (create).
- `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md`, `docs/forge/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md`, `docs/forge/ROADMAP.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (modify) — changelog pointers, phase status, lockstep 0.9.0.

### Task 1: Contract checklist generator
- [ ] Done

**Files:**
- Create: `scripts/forge_checklist.py`
- Test: `tests/test_forge_checklist.py`

**Spec:** Contract checklist

**Interface:**
- `ChecklistItem` dataclass: `id: str`, `source: str`, `text: str`.
- `build_task_checklist(plan_path, spec_path, task_number) -> list[ChecklistItem]` — the task's own `**Spec:**` sections + plan `**Global Constraints:**` clauses + that task's `**Acceptance:**` prose clauses.
- `build_final_checklist(plan_path, spec_path) -> list[ChecklistItem]` — union of every task's `**Spec:**` sections + global constraints + every task's acceptance prose clauses + one `t<N>` integration item per task title.
- `reduce_checklist(items, findings) -> list[ChecklistItem]` — subset whose ids appear as a finding's `contract_ref`; used for verification packets.
- `render_section(items) -> str` — a `## Contract checklist` markdown section, one line per item as `- <id> — <text>`.
- `main(argv)` CLI: `forge_checklist.py <plan.md> --spec <spec.md> (--task N | --final) [--out PATH] [--format json|md]`, default `json` to stdout.
- Id grammar: `spec:<heading>` (numbering token stripped via `eb.strip_heading_text`, whitespace-collapsed), `g<N>` (1-based), `t<N>.a<M>` (1-based per task), `t<N>`.
- Section resolution reuses `extract-brief.py`'s `find_spec_sections`; `**Global Constraints:**` and `**Acceptance:**` text read via `forge_plan`'s field readers. Acceptance clauses split on `;`; a clause whose content is solely an inline-code span is dropped (already executed by the acceptance runner).

**Tests:** id forms per source; numbering-token stripping and whitespace collapse in `spec:` ids; acceptance clause split on `;`; clause that is solely an inline-code span is excluded; clause mixing prose and code is included; `--final` unions spec sections across tasks without duplicates and adds one `t<N>` per task; `--task N` includes only task N's acceptance clauses; unresolvable `**Spec:**` name raises naming the heading; ambiguous `**Spec:**` name raises; empty checklist raises naming the absent source; `reduce_checklist` keeps only ids named by a finding's `contract_ref` and returns empty when none match; `render_section` output is stable and parseable; CLI `--format md` and `--format json` round-trip.

**Acceptance:** `python3 -m pytest -q tests/test_forge_checklist.py` all pass; `python3 scripts/forge_checklist.py docs/forge/plans/2026-08-21-phase13-review-continuity.md --spec docs/forge/specs/2026-08-21-review-continuity-design.md --final` emits well-formed JSON covering every task in this plan.

**Tier:** `standard`

**Depends on:** nothing.

### Task 2: Coverage in the verdict contract + validation
- [ ] Done

**Files:**
- Modify: `scripts/forge_common.py` (`CoverageEntry`, `Verdict.coverage`, `REVIEW_VERDICT_INSTRUCTION`)
- Modify: `scripts/forge_dispose.py` (`_coverage_from_obj`, `parse_verdict` populates coverage, `validate_coverage`, `--checklist` CLI flag, `decision.json` fields)
- Test: `tests/test_forge_coverage.py`

**Spec:** Reviewer verdict contract

**Interface:**
- `CoverageEntry` dataclass in `forge_common`: `id: str`, `status: str` (`satisfied` | `violated` | `n/a`), `evidence: str`.
- `Verdict` gains `coverage: list = field(default_factory=list)`.
- `REVIEW_VERDICT_INSTRUCTION` requires `coverage` on **every** verdict including `{"verdict": "pass"}`, one entry per supplied checklist id, `evidence` non-empty, `n/a` requiring a reason in `evidence`.
- `validate_coverage(verdict, checklist) -> list[str]` — returns human-readable defect strings, empty list = valid. Defects: missing ids (listed), unknown ids (listed), duplicate ids, empty `evidence`, `violated` id not named by any finding's `contract_ref`.
- `forge_dispose.py` CLI gains `--checklist PATH` (optional); when given, `decision.json` gains `"coverage_valid": bool` and `"coverage_defects": [str]`. Coverage defects do **not** change `action` — the caller decides to retry; the decision helper only reports.

**Tests:** complete coverage → valid; missing id → defect naming it; unknown id → defect naming it; duplicate id → defect; empty `evidence` → defect; `violated` without a backing `contract_ref` → defect; `violated` backed by a finding → valid; coverage required on a `pass` verdict (absent → defect); `parse_verdict` populates `Verdict.coverage` from JSON and tolerates absent coverage as an empty list at parse time (validation, not parsing, rejects it); `--checklist` absent → `decision.json` omits coverage fields and behaves exactly as today; `--checklist` present → fields added, `action` unchanged versus the same verdict without it.

**Acceptance:** `python3 -m pytest -q tests/test_forge_coverage.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py tests/test_forge_classify.py tests/test_forge_dispose.py` pass **unchanged**.

**Tier:** `standard`

**Depends on:** Task 1.

### Task 3: `--json` dispatch, thread capture, live-log parity
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (`--json` on every `codex exec` argv, event-stream parsing, `_render_event_line`, thread persistence in `run.json`)
- Test: `tests/test_forge_threads.py`

**Spec:** Codex mechanics, Live-log rendering

**Interface:**
- Every `codex exec` argv gains `--json`. `--ephemeral` is never emitted.
- `_render_event_line(event: dict) -> str | None` — maps one JSONL event to a plain-text live-log line; returns `None` for events with no textual content. Raw JSONL is retained at `<role>-events.jsonl` alongside the rendered `task-N-live.log`.
- `run_teed` (or a wrapper) parses the event stream while teeing, returning the captured `thread_id` alongside the existing exit code / tail.
- `WorkerResult` gains `thread_id: str | None`.
- `run.json` gains `"threads": {role: thread_id}` where role is one of `task-<N>-worker`, `task-<N>-reviewer`, `final-reviewer`, `final-fixer`. Cleared at invocation start — thread ids are never carried across runner invocations.
- Thread id source is the first `{"type": "thread.started", "thread_id": ...}` event. `~/.codex/sessions` is never read; `--last` is never used.

**Tests:** `--json` present and `--ephemeral` absent in every dispatch argv; `thread.started` captured from a fixture stream and returned; a stream with no `thread.started` yields `thread_id=None` without raising; `run.json` threads map written per role and cleared at invocation start; `_render_event_line` produces plain text for message/output events and `None` for control events; the rendered live log contains **no** raw JSON object lines for a representative fixture stream; raw JSONL retained separately; existing monitor render tests pass unchanged.

**Acceptance:** `python3 -m pytest -q tests/test_forge_threads.py` all pass; `python3 -m pytest -q` shows no regression against the 284-passed baseline; `grep -c '"type"' <rendered live log fixture output>` is 0.

**Tier:** `standard`

**Depends on:** nothing.

### Task 4: Checklist into packets + coverage retry
- [ ] Done

**Files:**
- Modify: `scripts/review-packet.py` (`--checklist PATH` → renders the checklist section into the packet)
- Modify: `scripts/forge-run.py` (generate the checklist per review, validate the verdict's coverage, one retry, then contract error)
- Test: `tests/test_forge_coverage.py` (extend), `tests/test_review_packet.py` (extend)

**Spec:** Contract checklist, Reviewer verdict contract

**Interface:**
- `review-packet.py` gains `--checklist PATH`; the rendered section is appended after the diff, before any prior-findings section.
- `build_packet(task_block, base, diff_output, prior_findings=None, checklist=None)` — `checklist` is a list of `ChecklistItem` or `None`.
- The runner generates a task checklist before each per-task review and a final checklist before the final review, validates the returned verdict with `validate_coverage`, and on defects re-dispatches **once** with the defects named in the prompt. A second invalid verdict raises a contract error (exit 1) naming the defects — same class as an unparseable verdict.
- The coverage retry is **not** a rework attempt: it does not increment the convergence attempt counter, does not touch `ConvergenceState`, and is recorded on the receipt as `coverage_retry: true`.

**Tests:** packet contains the checklist section when `--checklist` given and is byte-identical to today when omitted; section ordering (task block → diff → checklist → prior findings); runner validates and accepts a complete verdict first try; an incomplete verdict triggers exactly one re-dispatch whose prompt names the missing ids; a second incomplete verdict raises a contract error naming the defects; the retry does not advance the attempt counter or convergence state; `coverage_retry` recorded on the receipt; trivial-tier tasks generate no checklist and dispatch no reviewer.

**Acceptance:** `python3 -m pytest -q tests/test_forge_coverage.py tests/test_review_packet.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py` passes unchanged.

**Tier:** `standard`

**Depends on:** Task 1, Task 2.

### Task 5: Resume dispatch + cold-spawn fallback
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (`resume_thread` parameter on worker and reviewer dispatch, fallback path, receipt flag)
- Test: `tests/test_forge_resume.py`

**Spec:** Session continuity, Continuity scope and failure

**Interface:**
- `dispatch_worker(..., resume_thread=None)` and the reviewer dispatch helpers gain `resume_thread=None`. When set, argv is `codex exec resume --json --output-last-message <path> -m <model> -c 'model_reasoning_effort="<effort>"' <thread_id> <prompt>` — tier pinning and last-message capture preserved.
- Rework laps resume the task worker with a prompt containing the outstanding findings **only** (no brief re-paste, no spec).
- Verification laps resume the task reviewer; **discovery reviews never resume** — attempt 1 is always a cold spawn.
- Fallback: a resume that fails (non-zero exit before any event, missing session, context overflow) retries **once as a cold spawn with the full packet/brief**, records `resume_fallback: true` on the receipt, and continues. A failed cold spawn is handled exactly as today (worker → execution-failure finding; reviewer → contract error).

**Tests:** resume argv shape and ordering; `-m`/`-c` effort still pinned on resume; `--output-last-message` present on resume; rework prompt contains findings and does not contain brief or spec text; discovery review dispatches cold even when a reviewer thread id exists; verification review resumes when the id exists; missing thread id → cold spawn with full packet, `resume_fallback` set; failed resume → one cold-spawn retry, `resume_fallback` set, loop reaches a normal verdict; a failed cold-spawn worker still yields the execution-failure finding path unchanged.

**Acceptance:** `python3 -m pytest -q tests/test_forge_resume.py` all pass; `python3 -m pytest -q tests/test_forge_convergence.py tests/test_forge_classify.py` pass unchanged.

**Tier:** `standard`

**Depends on:** Task 3.

### Task 6: Delta-scoped verification packets
- [ ] Done

**Files:**
- Modify: `scripts/forge_git.py` (pre-repair tree snapshot helper)
- Modify: `scripts/review-packet.py` (verification packet mode)
- Modify: `scripts/forge-run.py` (snapshot before repair dispatch, verification packet on laps ≥ 2)
- Test: `tests/test_forge_verification_packet.py`

**Spec:** Delta-scoped verification packets

**Interface:**
- `snapshot_tree(cwd) -> str | None` in `forge_git.py` — records the pre-repair working tree via `git stash create` (falling back to `git add -A` + `git write-tree` when the tree is clean and `stash create` yields nothing), returning a commit-ish/tree-ish for later `git diff <ref>`. Never mutates the working tree; returns `None` outside a git repo.
- `build_verification_packet(findings, delta_diff, checklist) -> str` in `review-packet.py` — outstanding findings + the repair delta + the reduced checklist. Excludes the task block, the full spec, and the whole-plan diff.
- The runner snapshots before every repair dispatch and builds the verification packet from `git diff <snapshot>`.

**Tests:** `snapshot_tree` returns a usable ref and leaves `git status --porcelain` byte-identical before and after; clean-tree fallback path; `None` outside a repo; verification packet contains the findings, the delta, and the reduced checklist; verification packet does **not** contain the whole-plan diff, the full spec, or the task block; reduced checklist contains only ids named by the outstanding findings; delta diff equals only the repair's changes, not the task's or plan's; fence-safety preserved for a delta containing a ``` line.

**Acceptance:** `python3 -m pytest -q tests/test_forge_verification_packet.py` all pass; a verification packet built from a fixture repair is strictly smaller than the equivalent full packet and contains no `diff --git` hunk absent from the repair delta.

**Tier:** `standard`

**Depends on:** Task 4, Task 5.

### Task 7: Final-review fixer continuity + de-pasted brief
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (`_final_review_fix_brief`, `dispatch_final_review_fix`, `run_final_review_loop`)
- Test: `tests/test_forge_final_review.py` (extend)

**Spec:** Session continuity, Delta-scoped verification packets

**Interface:**
- `_final_review_fix_brief(findings, run_dir, attempt)` — findings + affected file paths + spec sections named by the findings' `contract_ref`. The full spec and the whole-plan diff are **not** pasted; the fixer reads the repo.
- The first final-review repair is a cold spawn whose thread id is persisted as `final-fixer`; subsequent repairs resume it with the new findings only.
- The final reviewer is cold at discovery and resumed for every verification lap, against the repair delta.
- Commit discipline unchanged: a single `fix: final-review` commit once the loop converges to pass, only if a repair was applied.

**Tests:** fixer brief contains findings and affected paths; contains no `diff --git` line and no full-spec body; second repair resumes the persisted `final-fixer` thread; final reviewer cold on attempt 1, resumed on attempt 2+; single `fix: final-review` commit after multiple repair laps; no commit when no repair was applied; halt payload and `repair_task` unchanged; final-review halt reasons unchanged.

**Acceptance:** `python3 -m pytest -q tests/test_forge_final_review.py` all pass; `python3 -m pytest -q` shows no regression against the 284-passed baseline.

**Tier:** `standard`

**Depends on:** Task 5, Task 6.

### Task 8: Cross-harness canon
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (reviewer verdict contract gains `coverage`; discovery-cold/verification-resumed rule; Claude `Agent`/`SendMessage` adapter and its fallback; checklist generation step in the dispatch loop)
- Modify: `skills/planning/codex-execution.md` (`--json`, thread ids, resume forms, fallback, coverage retry, continuity scope)

**Spec:** Session continuity, Claude adapter, Inline path, Reviewer verdict contract

**Interface:** Prose only — no code. Must state: the `coverage` requirement and its one-retry-then-contract-error handling; that discovery review is a cold spawn on both harnesses and only verification resumes; that Claude uses `Agent` for cold spawns and `SendMessage` to the named agent for resume, falling back to a fresh `Agent` with full context on failure; that continuity is scoped to one invocation and never carried across a halt; that the inline path adopts the coverage half only. Convergence, matrix, backstop, and `--autofix` prose is unchanged.

**Tests:** None (documentation). Verified by the acceptance greps and by Task 9's changelog consistency.

**Acceptance:** `grep -c 'coverage' skills/planning/SKILL.md` ≥ 1 and `grep -c 'SendMessage' skills/planning/SKILL.md` ≥ 1; `grep -c 'resume' skills/planning/codex-execution.md` ≥ 1; `grep -n 'backstop of \*\*5\*\*' skills/planning/SKILL.md` still matches (convergence prose untouched); `python3 -m pytest -q` unchanged.

**Tier:** `standard`

**Depends on:** Task 2, Task 5.

### Task 9: Changelog pointers, roadmap, lockstep version bump
- [ ] Done

**Files:**
- Modify: `docs/forge/specs/2026-07-16-phase7-scope-autonomy-design.md` (changelog line), `docs/forge/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md` (changelog line)
- Modify: `docs/forge/ROADMAP.md` (Phase 13 `[spec'd]` → `[done]`, plan link)
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (0.8.3 → 0.9.0)

**Spec:** Touch points

**Interface:** Changelog lines follow the existing dated one-line form: `2026-08-21 (phase 13): <what changed> (commit <sha>)`. Both plugin manifests carry the identical version string.

**Tests:** None (mechanical edits).

**Acceptance:** `grep -h '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json` shows `0.9.0` twice and nothing else; both amended specs contain a `2026-08-21` changelog line; ROADMAP Phase 13 reads `[done]` with a plan link; `python3 -m pytest -q` shows no regression against the 284-passed baseline.

**Tier:** `trivial` — version strings, two changelog lines, and one status word; no logic, no design content, one call site each.

**Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8.
