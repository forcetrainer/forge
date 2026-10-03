# Review Retry Resume Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** A verdict-validation retry corrects the original reviewer's verdict instead of re-reviewing blind, and reviewers stop producing the two defects that caused every observed retry.
**Architecture:** `forge_dispose.validate_coverage` replaces the contract_ref-equals-id backing rule with a per-entry `finding` link to an effectively contract-breaking finding. Every reviewer input carries a `## Citable refs` section rendered by one `forge_checklist` renderer, and the shared verdict instruction tells the reviewer to copy refs from it. `forge-run.py`'s `_review_with_coverage` resumes the session that emitted the invalid verdict, falling back cold; the Claude and spec-review paths get the same rule in their skill text.
**Tech stack:** Python 3 stdlib, pytest with the fake `codex` in `tests/_forge_support.py`.
**Global Constraints:**
- Scripts under `scripts/` import the Python 3 standard library only.
- A near-miss citable id is a validation defect, never normalized to the id it resembles.

### Task 1: Violated coverage names its backing finding
- [x] Done — passed, 1 attempt

**Files:**
- Modify: `scripts/forge_common.py` (`CoverageEntry.finding`; `REVIEW_VERDICT_INSTRUCTION` violated/finding clause)
- Modify: `scripts/forge_dispose.py` (`_coverage_from_obj` parses `finding`; `validate_coverage` backing rule; CLI passes `--citable` set to `validate_coverage`)
- Modify: `scripts/forge-run.py` (`_verdict_defects` passes `citable` to `validate_coverage`)
- Test: `tests/test_forge_coverage.py`

**Spec:** Coverage validation, Reviewer verdict contract

**Interface:**
- `CoverageEntry.finding: str | None = None`
- `forge_dispose.validate_coverage(verdict, checklist, citable=None) -> list[str]` — `citable` is the review's citable id set or list; falsy means membership of the backing finding's `contract_ref` is not checked, only its non-nullness
- Wire format: coverage entry `{"id", "status", "evidence", "finding"?}`; `finding` is a string finding id or absent/null

**Tests:**
- a violated entry whose `finding` names a contract-breaking finding with a citable `contract_ref` is valid
- several violated entries naming one finding are valid, and that finding's `contract_ref` need not equal any of their ids
- a violated entry with no `finding` is a defect naming the entry id
- a violated entry whose `finding` names no finding in the verdict is a defect
- a violated entry whose backing finding has impact `improvement` or `unverifiable` is a defect
- a violated entry whose backing finding has a null `contract_ref` is a defect
- a violated entry whose backing finding's `contract_ref` is not in a supplied citable set is a defect
- with no citable set supplied, a backing finding with any non-null `contract_ref` is accepted
- `finding` on a `satisfied`, `n/a` or `unverifiable` entry is a defect
- a violated id that merely equals some finding's `contract_ref`, with no `finding`, is now a defect (old rule removed)
- a non-string, non-null `finding` value fails verdict parsing with an error naming the entry
- the `forge_dispose.py` CLI reports the new defects in `coverage_defects` when given `--checklist` and `--citable`

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_coverage.py tests/test_forge_dispose.py tests/test_forge_review.py` passes
- `grep -q '\\"finding\\"' scripts/forge_common.py` exits 0

**Tier:** standard

**Depends on:** nothing.

### Task 2: Every reviewer input prints its citable ids
- [x] Done — passed, 1 attempt

**Files:**
- Modify: `scripts/forge_checklist.py` (`render_citable_section`; CLI accepts `--citable --format md`; `<slug>` docstrings become `<heading>`)
- Modify: `scripts/review-packet.py` (`build_citable_section`; `citable` keyword on `build_packet` and `build_verification_packet`; CLI `--citable <path>`)
- Modify: `scripts/forge_git.py` (`_packet_for` and `_final_packet` take and pass `citable`)
- Modify: `scripts/forge-run.py` (task discovery, task verification and final packets receive the citable set already computed in `execute_task` and `run_final_review_loop`; `<slug>` comments become `<heading>`)
- Modify: `scripts/forge_dispose.py` (`<slug>` docstring and `--citable` help become `<heading>`)
- Modify: `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION`: `contract_ref` is copied verbatim from the packet's `## Citable refs`; no `slug`)
- Test: `tests/test_forge_checklist.py`
- Test: `tests/test_review_packet.py`
- Test: `tests/test_forge_verification_packet.py`
- Test: `tests/test_forge_final_review.py`

**Spec:** Contract checklist, Delta-scoped verification packets

**Interface:**
- `forge_checklist.render_citable_section(ids) -> str` — `## Citable refs` heading, one `- <id>` line per id, ids sorted
- `review_packet.build_citable_section(ids) -> str` — byte-identical output to `render_citable_section` for the same ids
- `build_packet(task_block, base, diff_output, prior_findings=None, checklist=None, review_kind=None, *, spec_sections=None, citable=None)`
- `build_verification_packet(findings, delta_diff, checklist, review_kind="verification", citable=None)`
- `forge_git._packet_for(..., citable=None)`, `forge_git._final_packet(..., citable=None)`
- CLI: `forge_checklist.py <plan> --spec <spec> --task N --citable --format md` prints the rendered section; `--final --citable --format md` likewise

**Tests:**
- `render_citable_section` lists every id once, sorted, under `## Citable refs`
- a `spec:` id renders as the whitespace-collapsed heading text exactly as `citable_refs` produces it
- `build_citable_section` and `render_citable_section` produce identical text for the same ids
- a task discovery packet built with a citable set contains the section with every coverage id and every declared `spec:` id
- a task verification packet contains the citable section alongside the reduced checklist
- a final-review packet contains the final citable set, including task `t<N>.t<M>` ids
- a packet built with `citable=None` has no citable section and is otherwise unchanged
- `forge_checklist.py --task N --citable --format md` exits 0 and prints the section; `--citable` without `--format md` still emits the JSON array
- the runner's recorded reviewer prompt for a task review contains the citable section (fake codex)

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_checklist.py tests/test_review_packet.py tests/test_forge_verification_packet.py tests/test_forge_final_review.py` passes
- `grep -n 'slug' scripts/forge_checklist.py scripts/forge_dispose.py scripts/forge-run.py scripts/forge_common.py` prints nothing
- `grep -q 'Citable refs' scripts/forge_common.py` exits 0

**Tier:** standard

**Depends on:** Task 1.

### Task 3: The validation retry resumes its reviewer
- [x] Done — passed, 1 attempt (1 coverage retry, resumed)

**Files:**
- Modify: `scripts/forge-run.py` (`_review_with_coverage`; the task and final reviewer dispatch closures; retry prompt files)
- Test: `tests/test_forge_review.py`
- Test: `tests/test_forge_final_review.py`

**Spec:** Coverage validation, Session continuity

**Interface:**
- Reviewer dispatch closures: `dispatch_call(path, fallback_path=None)` — resumes when armed, and on a failed resume dispatches cold with `fallback_path` (or `path` when none), setting the attempt's `resume_fallback`
- `_review_with_coverage(dispatch_call, packet_path, checklist, run_dir, label, review_kind="discovery", citable=None, arm_retry_resume=None)` — `arm_retry_resume()` points the closure's resume state at the thread the first dispatch recorded under the reviewer role
- Retry prompt files: `<label>-retry-defects.md` (defect list plus the resubmit instruction, no packet; sent on resume) and `<label>-coverage-retry.md` (packet plus defect list; sent only on a cold fallback)

**Tests:**
- a discovery task review whose first verdict is invalid retries with `codex exec resume <thread id the first dispatch recorded>`
- the resumed retry's prompt contains every defect and the resubmit instruction and contains no diff text
- a failed retry resume dispatches cold with the packet plus the defect list and the attempt receipt records `resume_fallback: true`
- a final review whose first verdict is invalid retries by resuming the final-reviewer thread
- a verification-lap retry resumes the same thread the verification dispatch used
- the retry still does not advance the attempt counter or convergence state, and a second invalid verdict is still a contract error
- a valid first verdict dispatches exactly once

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_review.py tests/test_forge_final_review.py tests/test_forge_threads.py tests/test_forge_resume.py` passes

**Tier:** standard

**Depends on:** Task 2.

### Task 4: Orchestrator and spec-review skill text
- [x] Done — passed, 1 attempt (1 coverage retry, resumed)

**Files:**
- Modify: `skills/planning/SKILL.md` (Claude retry is a `SendMessage` to the reviewer that emitted the invalid verdict with the defect list only, fresh `Agent` with packet plus defects on failure; reviewer prompt carries the `## Citable refs` section from `forge_checklist.py --citable --format md`; `spec:<slug>` becomes `spec:<heading>`; violated entries name their backing finding)
- Modify: `skills/planning/codex-execution.md` (retry resumes the reviewer's thread; `spec:<slug>` becomes `spec:<heading>`; packets carry `## Citable refs`)
- Modify: `skills/brainstorming/SKILL.md` (step 8: the invalid-verdict retry resumes the same reviewer with the defect list)

**Spec:** Document review contract, Session continuity

**Tests:** none — prose artifacts take mechanical acceptance

**Acceptance:**
- `grep -n 'slug' skills/planning/SKILL.md skills/planning/codex-execution.md` prints nothing
- `grep -q 'Citable refs' skills/planning/SKILL.md` exits 0
- `grep -q 'Citable refs' skills/planning/codex-execution.md` exits 0
- `grep -q '"finding"' skills/planning/SKILL.md` exits 0
- `grep -qi 'retry.*resum' skills/brainstorming/SKILL.md` exits 0
- `grep -qi 'retry.*resum' skills/planning/SKILL.md` exits 0
- `grep -qi 'retry.*resum' skills/planning/codex-execution.md` exits 0

**Tier:** standard

**Depends on:** Task 3.

### Task 5: discovery-review-is-cold admits the validation retry
- [x] Done — passed, 1 attempt

**Files:**
- Modify: `docs/forge/constraints.md` (via `scripts/forge_memory.py update-constraint`, never a hand edit)

**Tests:** none — a constraint record takes mechanical acceptance

**Acceptance:**
- `python3 scripts/forge_memory.py update-constraint --id discovery-review-is-cold --rule "A task's discovery review runs on a fresh agent; only verification laps and the single verdict-validation retry may resume it."` exits 0
- `grep -q 'single verdict-validation retry may resume it' docs/forge/constraints.md` exits 0

**Tier:** trivial — one existing constraint's rule text replaced with given text through the store's own command, no logic

**Depends on:** nothing.

### Task 6: The Citable refs section states its role
- [ ] Done

**Files:**
- Modify: `scripts/forge_checklist.py` (`render_citable_section` emits the role line)
- Modify: `scripts/review-packet.py` (`build_citable_section` stays byte-identical to `render_citable_section`)
- Test: `tests/test_forge_checklist.py`
- Test: `tests/test_review_packet.py`

**Spec:** Contract checklist

**Interface:**
- Rendered section: the `## Citable refs` heading, a blank line, the role line `Ids a finding's contract_ref may cite. Not coverage items — coverage answers the ## Contract checklist only.`, a blank line, then one `- <id>` line per id, sorted

**Tests:**
- `render_citable_section` output has the role line between the heading and the first id
- the role line names `contract_ref` and states the ids are not coverage items
- `build_citable_section` and `render_citable_section` still produce identical text for the same ids
- `forge_checklist.py --task N --citable --format md` prints the role line

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_checklist.py tests/test_review_packet.py tests/test_forge_verification_packet.py tests/test_forge_final_review.py` passes

**Tier:** standard

**Depends on:** Task 2.
