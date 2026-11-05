# Scope Widening Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Replace the `repair-out-of-scope` halt with justify-and-arbitrate: a worker edit to an existing file outside its task's `**Files:**` is recorded with a reason, ruled on by the reviewer, reworked on rejection, and listed at close-out; only an out-of-scope deletion halts (`scope-delete`).
**Architecture:** `**Files:**` becomes a machine-read plan field (`forge_plan`, `forge_lint`). `forge_dispose.scope_widenings` is the one detection implementation, exposed in-process to the Codex runner and as the `forge_dispose.py widened` subcommand on Claude; the decision helper validates the verdict's `widenings` rulings against its own recomputation, turns a rejection into a runner-raised fix finding, and carries accepted widenings in the convergence state. The Codex runner checks every worker turn, the review packet lists `## Widened files`, and receipts and `run.json` persist accepted and unarbitrated widenings.
**Tech stack:** Python 3 standard library, unittest.
**Global Constraints:**
- Scripts use the Python 3 standard library only.
- No new code emits the halt reason `repair-out-of-scope`; it is only read from halt records written before this plan.
- Widening detection has exactly one implementation, `forge_dispose.scope_widenings`; no caller recomputes a widening set.
**Spec files:**
- docs/forge/specs/execution.md
- docs/forge/specs/pipeline.md
- docs/forge/specs/codex-runner.md

### Task 1: Plan fixtures declare their files
- [ ] Done

**Files:**
- Modify: `tests/_forge_support.py` (every plan builder and inline plan gains a strict `**Files:**` block)
- Modify: `tests/test_forge_loop.py`
- Modify: `tests/test_forge_review.py`
- Modify: `tests/test_forge_verification_packet.py`
- Modify: `tests/test_forge_final_review.py`
- Modify: `tests/test_forge_seed.py`
- Modify: `tests/test_forge_coverage.py`
- Modify: `tests/test_forge_resume.py`
- Modify: `tests/test_forge_commit.py`
- Modify: `tests/test_forge_status.py`
- Modify: `tests/test_forge_run_specs.py`
- Modify: `tests/test_forge_lint.py`
- Modify: `tests/test_forge_planreview.py`
- Modify: `tests/test_forge_checklist.py`
- Modify: `tests/test_extract_brief.py`
- Modify: `tests/test_review_packet.py`
- Modify: `tests/test_forge_plan.py`

**Spec:** [pipeline] Plan documents

**Interface:** none. Test-harness change only: every plan text a test hands to `forge-run.py`, `forge_lint.py`, `forge_docreview.py` or `forge_checklist.py` as a *valid* plan carries, on every task, a `**Files:**` block in the bulleted grammar (`- Create:` / `- Modify:` / `- Test:` followed by backticked repo-relative paths), whose paths cover every existing file that test's fake worker modifies. Plans a test uses as deliberately malformed input are left malformed.

**Tests:** none — fixture-only harness change; the unchanged suite passing is its check, and Task 2 making the field required is what proves it complete.

**Acceptance:**
- `python3 -m unittest discover -s tests` passes
- Every fixture's `**Files:**` entries put one backticked path or a `, `-separated list of backticked paths after the kind, with any note in one trailing parenthetical.

**Tier:** standard

**Depends on:** nothing

### Task 2: Parse and lint the Files field
- [ ] Done

**Files:**
- Modify: `scripts/forge_plan.py` (`parse_files_field`; `parse_plan_tasks` fills `Task.files`)
- Modify: `scripts/forge_common.py` (`Task.files`)
- Modify: `scripts/forge_lint.py` (`**Files:**` presence and grammar row; `**Files:**` joins the field clause prefixes, bulleted form only)
- Test: `tests/test_forge_plan.py`
- Test: `tests/test_forge_lint.py`

**Spec:** [pipeline] Plan documents, [execution] Plan lint

**Interface:**
- `forge_common.Task.files: list = field(default_factory=list)` — the task's declared repo-relative paths, all three kinds, in plan order, duplicates removed.
- `forge_plan.parse_files_field(block, task_number, first_line) -> tuple[list[str], list[str]]` — `(paths, defects)`; each defect names the task, the line the entry starts on (1-based in the plan), and quotes the entry.
- `forge_plan.parse_plan_tasks(plan_path)` — raises `RuntimeError` with the first `parse_files_field` defect when any exist; otherwise every `Task.files` is set.
- `forge_lint` reports every `parse_files_field` defect for every task in one run, plus a missing `**Files:**` per task.

**Tests:**
- a Create, Modify and Test entry each contribute their paths to Task.files
- a comma-separated list of backticked paths yields every path
- backticks inside a trailing parenthetical note are not read as paths
- a continuation line joins its bullet before parsing and an error names the bullet's starting line
- an unknown kind is a defect naming task and line
- an entry with no backticked path is a defect
- prose between two paths is a defect
- a path containing `*`, `?` or `[` is a defect
- a path ending in `/` is a defect
- a path resolving outside the repository root is a defect
- the marker with a value on its own line is a defect
- a marker followed by no bullet is a defect
- a task with no Files field is a lint error naming the task
- lint reports every Files defect across all tasks in one run
- a path that does not exist on disk is accepted

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_plan.py` passes
- `python3 -m unittest discover -s tests -p test_forge_lint.py` passes
- `python3 -m unittest discover -s tests` passes
- `python3 scripts/forge_lint.py docs/forge/plans/2026-11-04-scope-widening.md` passes

**Tier:** standard

**Depends on:** Task 1

### Task 3: Detect widenings and the worker record
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (`scope_widenings`; `widened` subcommand)
- Modify: `scripts/forge_common.py` (`WIDENING_INSTRUCTION`)
- Modify: `scripts/extract-brief.py` (`--widenings-record` appends the record path and `WIDENING_INSTRUCTION`)
- Create: `tests/test_forge_widening.py`
- Test: `tests/test_extract_brief.py`

**Spec:** [execution] Scope widening, [execution] The shared decision helper

**Interface:**
- `forge_dispose.Widening` — dataclass: `path: str`, `reason: str | None`, `serves: str | None`, `added: int`, `removed: int`.
- `forge_dispose.WideningResult` — dataclass: `widened: list[Widening]` (sorted by path), `deleted: list[str]` (sorted), `record_defects: list[str]`, `due: list[Widening]` (the widenings due a ruling).
- `forge_dispose.scope_widenings(cwd, plan_path, spec_path, scope, base, state, record_path) -> WideningResult` — `scope` is `t<N>` or `final`; `base` is the review base (`HEAD` for a task, `run_base` for final); `state` is a `ConvergenceState` (or `None` for fresh); `record_path` may name an absent file. Raises `RuntimeError` naming the cause on any git failure.
- `forge_dispose.render_widened_section(result) -> str` — the `## Widened files` markdown: each due path as a JSON string, then `reason` and `serves` (or `serves: none`), or `no justification given`.
- `forge_common.WIDENING_INSTRUCTION: str` — the worker-record instruction shared verbatim by both harnesses.
- CLI: `forge_dispose.py widened --plan <plan> [--spec <spec>] --base <sha> --scope <t<N>|final> [--state <state.json>] [--widenings <record.json>] [--format json|md]`; JSON is `{"widened": [{"path", "reason", "serves"}], "deleted": [...], "record_defects": [...]}`; exit 1 naming the cause on a git failure.
- `extract-brief.py <plan> <N> --out <dir> [--widenings-record <path>]` — the brief gains a section naming the record path and carrying `WIDENING_INSTRUCTION`.

**Tests:**
- an existing file outside the task's Files modified in the working tree is a widening
- an edit inside a declared file is not a widening
- a created file is not a widening
- the plan file and every declared spec file are never widenings
- a legacy plan's --spec file is never a widening
- an out-of-scope deleted file is listed in deleted, not widened
- a rename of an out-of-scope file is a delete plus an add and lists the old path as deleted
- final scope includes every task's Files and every path changed between run_base and HEAD
- a file no task declared and the run never touched, edited during the final review, is a widening
- the record's reason and serves attach to their path
- an absent record leaves every widening unjustified
- a record that does not parse is a record defect
- a record entry for a path that is not a widening is a record defect
- a duplicate record path is a record defect
- an empty reason is a record defect
- a serves outside the citable set and the outstanding identities is a record defect
- a null serves is accepted
- a serves naming an outstanding finding identity from the state is accepted
- a widening accepted at its current blob is not due a ruling
- a widening whose content changed since acceptance is due a ruling again
- a git failure raises naming the cause and the CLI exits 1
- the md format renders each due path as a JSON string with its justification or no justification given
- the brief names the record path and carries WIDENING_INSTRUCTION verbatim when --widenings-record is given

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_widening.py` passes
- `python3 -m unittest discover -s tests -p test_extract_brief.py` passes
- `python3 -m unittest discover -s tests -p test_forge_dispose.py` passes

**Tier:** standard

**Depends on:** Task 2

### Task 4: Arbitrate widenings in the decision
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (verdict `widenings` parse and validation; rejected-widening finding; `accepted_widenings` state; `scope-delete`; decision.json `widenings`; `--plan` and `--widenings`; `repair_scope_violations` and `--repair-snapshot` removed)
- Modify: `scripts/forge_common.py` (`HALT_REASONS`; `REVIEW_VERDICT_INSTRUCTION` widenings text)
- Test: `tests/test_forge_widening.py`
- Test: `tests/test_forge_dispose.py`
- Test: `tests/test_forge_convergence.py`

**Spec:** [execution] Reviewer verdict contract, [execution] Scope widening, [execution] Rework loop and convergence, [execution] The shared decision helper, [execution] The disposition matrix

**Interface:**
- `forge_common.Verdict.widenings: list = field(default_factory=list)` — `[{"path", "ruling", "reason"}]` as emitted.
- `forge_common.HALT_REASONS` — `scope-decision`, `regression`, `stuck`, `backstop`, `gate`, `scope-delete`. A separate `forge_common.LEGACY_HALT_REASONS = ("repair-out-of-scope",)` is accepted wherever a persisted halt record's reason is read.
- `forge_dispose.validate_widenings(verdict, due_paths) -> list[str]` — defects for a missing, extra or duplicated path, an unknown ruling, or an empty reason.
- `forge_dispose.rejected_widening_finding(path, reason, scope) -> Finding` — identity `<scope>:widen:<path>`, provenance `in-diff`, impact `improvement`, `contract_ref` None, disposition `fix`.
- `forge_dispose.validate_finding_ids(verdict)` — additionally a defect for a reviewer finding whose `id` or `carried_from` begins `widen:` or `<scope>:widen:`.
- `forge_dispose.ConvergenceState.accepted_widenings: dict` — `{path: {"blob", "reason", "serves", "ruling_reason"}}`; absent in a state file reads as `{}`; an entry whose path is no longer a widening is dropped.
- `forge_dispose.convergence_decision(...)` — gains `deleted: list[str]` in place of `scope_violations`; a non-empty `deleted` returns `("halt", "scope-delete")` at step 1a.
- CLI: `--plan` required, `--widenings` optional, `--repair-snapshot` an argparse usage error naming the flag; decision.json drops `repair_out_of_scope` and gains `widenings: {"accepted": [...], "rejected": [{"path", "identity"}], "deleted": [...]}` with `accepted` rebuilt from `accepted_widenings` and line counts from `HEAD` against the working tree.

**Tests:**
- an accepted ruling records the path's blob and justification in accepted_widenings
- a rejected ruling raises a fix finding with identity scope:widen:path and the stated provenance, impact and contract_ref
- a verdict missing a ruling for a due path is a validation defect
- a verdict ruling on a path not due is a validation defect
- a duplicate ruling, an unknown ruling value, or an empty reason is a validation defect
- rulings never change the verdict value
- a reviewer finding id beginning widen: is a validation defect
- a reverted rejected widening resolves without a ruling
- a rejected widening rejected again with nothing resolved halts stuck
- a rejected widening accepted on the next ruling resolves
- a widening resolved then rejected again later halts regression
- gate mode halts on a rejected widening
- an out-of-scope deletion halts scope-delete whatever the findings and rulings
- passing --repair-snapshot is a usage error naming the flag
- decision.json widenings lists every accepted widening so far with line counts and drops one no longer widened
- a state file without accepted_widenings reads as empty
- a verdict with widenings is checked against the helper's own recomputation, not a caller list
- HALT_REASONS no longer contains repair-out-of-scope and LEGACY_HALT_REASONS does

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_widening.py` passes
- `python3 -m unittest discover -s tests -p test_forge_dispose.py` passes
- `python3 -m unittest discover -s tests -p test_forge_convergence.py` passes
- `grep -n "repair_scope_violations" scripts/forge_dispose.py` exits 1

**Tier:** standard

**Depends on:** Task 3

### Task 5: Codex runner checks every worker turn
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (detection after every worker turn before acceptance is acted on; `scope-delete` escalation; record paths `task-<N>/widenings.json` and `final/widenings.json` passed to every brief; verdict rulings to the decision; repair snapshot scope check removed; trivial-batch union scope; `scope-delete` halt guidance text)
- Modify: `scripts/review-packet.py` (`## Widened files` section on task, verification and final packets)
- Test: `tests/test_forge_loop.py`
- Test: `tests/test_forge_final_review.py`
- Test: `tests/test_forge_verification_packet.py`
- Test: `tests/test_forge_review.py`
- Test: `tests/test_review_packet.py`

**Spec:** [codex-runner] Task loop, [execution] The dispatch loop, [execution] Delta-scoped verification packets, [execution] Scope widening

**Interface:**
- `review-packet.py ... [--widened <md file>]` — inserts the file's `## Widened files` section verbatim; absent flag, no section.
- `forge-run.py` halt guidance for `scope-delete` names the deleted paths and says the human restores or approves the deletion before re-invoking; no `--resolve` is accepted for it.

**Tests:**
- a worker modifying an undeclared existing file gets a review packet listing it under Widened files with its record justification
- a cold worker turn, a rework resume and a final-review fixer turn each run detection
- an out-of-scope deletion escalates scope-delete before any reviewer dispatch and exits 2 frozen
- an out-of-scope deletion in a turn whose acceptance failed still escalates scope-delete
- a defective record entry is a rework iteration naming the entry and no reviewer is dispatched for that turn
- a rejected widening drives a rework whose worker prompt carries the rejection
- an accepted widening is not re-listed on the next verification packet
- a trivial batch is checked once against the union of its tasks' Files
- every worker brief names its record path
- the runner never passes a repair snapshot to the decision

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_loop.py` passes
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_verification_packet.py` passes
- `python3 -m unittest discover -s tests -p test_forge_review.py` passes
- `python3 -m unittest discover -s tests -p test_review_packet.py` passes
- `grep -n "repair_scope_violations\|repair_snapshot" scripts/forge-run.py` exits 1

**Tier:** standard

**Depends on:** Task 4

### Task 6: Widenings on receipts, run state and resume
- [ ] Done

**Files:**
- Modify: `scripts/forge_receipts.py` (receipt `widenings` block; `run.json` `widenings` aggregate from passing receipts only, accepted and `unarbitrated` entries)
- Modify: `scripts/forge-run.py` (read-back of `widenings` on resume; `accepted_widenings` carried in the halt record's state; end-of-plan summary and close-out lists accepted and not-reviewed widenings; a legacy `repair-out-of-scope` halt record resumes)
- Modify: `scripts/forge_status.py` (`scope-delete` in status rendering; legacy reason still rendered)
- Test: `tests/test_forge_receipts.py`
- Test: `tests/test_forge_resume.py`
- Test: `tests/test_forge_status.py`

**Spec:** [execution] Receipts and run state, [execution] Deferral handling, [codex-runner] Receipts and run state, [codex-runner] Resume, [codex-runner] Testing

**Interface:**
- Task and final receipts gain `widenings` — the review's decision.json block; a trivial task's receipt carries `widenings.unarbitrated: [{"path", "added", "removed", "reason", "serves"}]`.
- `run.json` gains `widenings: [{"scope", "path", "added", "removed", "reason", "serves", "ruling_reason" | null, "unarbitrated": bool}]`, appended only from a passing receipt and read back on resume.

**Tests:**
- a passing task receipt carries the decision's widenings block
- run.json aggregates accepted widenings from passing receipts only and never from an escalated one
- a trivial task's unarbitrated widenings reach run.json marked unarbitrated
- run.json widenings survive a halt and resume
- after a resume a path accepted before the halt is not re-listed while its content holds
- an accepted widening later reverted is not in run.json
- the end-of-plan summary lists accepted widenings and marks unarbitrated ones not reviewed
- a halt record naming repair-out-of-scope resumes and the reconciled task runs under widening rules
- status renders a scope-delete halt with its paths

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_receipts.py` passes
- `python3 -m unittest discover -s tests -p test_forge_resume.py` passes
- `python3 -m unittest discover -s tests -p test_forge_status.py` passes
- `python3 -m unittest discover -s tests` passes

**Tier:** standard

**Depends on:** Task 5

### Task 7: Skill canon for both harnesses
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (Task structure: `**Files:**` grammar and requirement; field clause grammar names `**Files:**`; Claude dispatch loop: `forge_dispose.py widened` after every worker turn, record path in every brief, `## Widened files` pasted into the reviewer prompt, `--plan`/`--widenings` on the decision, `--repair-snapshot` and `repair-out-of-scope` removed; inline: widenings to the close-out list, deletion halts; close-out lists accepted and not-reviewed widenings)
- Modify: `skills/planning/codex-execution.md` (`scope-delete` halt and its guidance; widenings at close-out; `repair-out-of-scope` removed)

**Spec:** [execution] Scope widening, [execution] Inline execution, [execution] The dispatch loop, [execution] Deferral handling, [pipeline] Plan documents

**Interface:** none — skill prose.

**Tests:** none — prose artifact; acceptance is mechanical text checks.

**Acceptance:**
- `grep -rn "repair-snapshot\|repair-out-of-scope\|repair_out_of_scope" skills/planning` exits 1
- `grep -c "forge_dispose.py widened" skills/planning/SKILL.md` passes
- `grep -c "scope-delete" skills/planning/codex-execution.md` passes
- `grep -c "## Widened files" skills/planning/SKILL.md` passes
- The Task structure template's `**Files:**` example and its prose state the bulleted-only grammar and that prose or glob entries are lint errors.
- The inline finding-handling paragraph says a widening is listed at close-out unarbitrated and only a deletion halts.

**Tier:** standard

**Depends on:** Task 4
