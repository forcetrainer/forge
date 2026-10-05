# Reviewer Write Discipline Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Reviewers run without a read-only sandbox and verify by executing in a scratch copy, while the runner enforces an unchanged repository, hands reviewers the acceptance results, persists approvals and the final review's unverified set, and captures untracked files in the repair delta.
**Architecture:** One fingerprint helper in `forge_git` (working tree via the freeze code's temporary-index capture, plus index tree, HEAD and branch) is taken around every reviewer dispatch by the runner and exposed as a CLI for the Claude loop; a mismatch raises an error outside the `RuntimeError` family so the resume wrappers cannot recover it. The packet builder renders acceptance results; run.json gains run-level `approved` and `unverified` fields; a non-empty unverified set at final-review pass halts with a new class that the existing `--resolve` verbs answer.
**Tech stack:** Python 3 standard library, git, unittest.
**Global Constraints:**
- Scripts use the Python 3 standard library only.
- The real git index and working tree are never mutated by any fingerprint, snapshot or delta operation.
- Neither the repository-changed error nor the fingerprint-failure error is a `RuntimeError` subclass.
**Spec files:**
- docs/forge/specs/execution.md
- docs/forge/specs/codex-runner.md

### Task 1: Repository fingerprint, tree diff, and CLI
- [ ] Done

**Files:**
- Modify: `scripts/forge_git.py` (factor the temporary-index capture out of `freeze_attempt`; add fingerprint, fingerprint diff, tree diff; `snapshot_tree` uses the capture)
- Create: `scripts/forge_fingerprint.py` (CLI: `snapshot`, `verify`)
- Modify: `scripts/forge-run.py` (both verification-packet sites, in `execute_task` and `run_final_review_loop`, call `forge_git.repair_delta(cwd, repair_snapshot)` instead of `_git_diff`)
- Test: `tests/test_forge_fingerprint.py`
- Test: `tests/test_forge_verification_packet.py` (repair delta cases)

**Spec:** [execution] Reviewer write discipline, [execution] Delta-scoped verification packets, [codex-runner] Worker isolation

**Interface:**
- `forge_git.capture_tree(cwd) -> str` — tree sha of tracked plus untracked non-ignored content via a temporary index seeded from HEAD; `freeze_attempt` calls it.
- `forge_git.repo_fingerprint(cwd) -> dict` — keys `tree`, `index`, `head`, `branch` (branch is the symbolic ref name or `null` when detached).
- `forge_git.fingerprint_diff(cwd, before, after) -> list[str]` — human-readable lines naming each changed component; changed `tree` lines name the paths (`git diff-tree --name-status` between the two trees); empty list when equal.
- `forge_git.tree_diff(cwd, old_tree, new_tree) -> str` — `git diff <old_tree> <new_tree>` text.
- `class forge_git.RepositoryChangedError(Exception)` — carries `changes: list[str]`.
- `class forge_git.FingerprintError(Exception)` — raised when any git call inside `capture_tree` or `repo_fingerprint` fails, naming the command.
- `forge_git.freeze_tree(cwd, tree, ref_name, parent) -> str` — commits an already-captured tree with `parent` as its parent under `ref_name`, then resets the working tree to `parent` plus `git clean -fd`, the tail of `freeze_attempt` factored out; `freeze_attempt` becomes `capture_tree` followed by it with `parent` = HEAD.
- `forge_git.restore_refs(cwd, fingerprint) -> None` — points the recorded branch at the recorded HEAD sha and re-attaches HEAD to it (detached when `branch` is `null`); the only ref-moving call outside `freeze_tree`.
- `forge_git.repair_delta(cwd, snapshot_tree) -> str` — `tree_diff(cwd, snapshot_tree, capture_tree(cwd))`; the only delta the task and final verification packets use.
- `forge_git.snapshot_tree(cwd) -> str | None` — now returns `capture_tree(cwd)`; `None` outside a git repo.
- `forge_fingerprint.py snapshot` prints the fingerprint as one JSON object; `forge_fingerprint.py verify <json-or-path>` exits 0 when equal, exits 2 printing each change line when not, exits 1 on a git failure.

**Tests:**
- fingerprint is stable across two consecutive calls on an unchanged repo
- fingerprint changes on a tracked edit, a new untracked file, a tracked deletion, a `git add`, a commit, and a branch switch, each in isolation
- fingerprint is stable across a write to a gitignored path
- `git status --porcelain` output is identical before and after `repo_fingerprint`
- `fingerprint_diff` names the changed path on a tracked edit and the component on a commit
- `verify` exits 2 and prints the changed path after an edit, exits 0 when unchanged
- `snapshot_tree` captures an untracked file so `tree_diff` against a later capture shows its edit as a hunk, its deletion as a deletion, and an unchanged formerly-untracked file as no hunk
- `freeze_attempt` behavior is unchanged: a task of only new files freezes non-empty
- `freeze_tree` given a captured tree parks exactly that content under the ref and leaves the tree clean, with a file created after the capture absent from the freeze commit
- `restore_refs` after a commit on the branch moves the branch back to the recorded sha and leaves the commit unreachable but present; after a branch switch it re-attaches HEAD to the recorded branch
- a git failure inside `repo_fingerprint` raises `FingerprintError`, not `RuntimeError`
- a task verification packet and a final verification packet written by the runner after a repair that edited one formerly-untracked file, deleted another, added a third and left a fourth unchanged contain exactly one hunk each for the edit, the deletion and the addition, and nothing for the unchanged file
- no verification-packet site in `scripts/forge-run.py` calls `_git_diff` with the repair snapshot

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_fingerprint.py` passes
- `python3 -m unittest discover -s tests -p test_forge_verification_packet.py` passes
- `python3 scripts/forge_fingerprint.py snapshot` prints `"tree"`

**Tier:** standard

**Depends on:** nothing.

### Task 2: Acceptance results in the task discovery packet
- [ ] Done

**Files:**
- Modify: `scripts/review-packet.py` (`build_acceptance_section`; `build_packet` takes `acceptance_results`)
- Test: `tests/test_review_packet.py`

**Spec:** [execution] The dispatch loop, [codex-runner] Testing

**Interface:**
- `review-packet.build_acceptance_section(results: list[dict]) -> str` — renders `## Acceptance results`, opened by the assertion text (`ACCEPTANCE_ASSERTION` module constant: these command clauses have already been run by the runner and are not to be re-run on this tree; prose clauses remain the reviewer's to check), then one row per result with `command`, `outcome`, `exit_code`, `passed`, `output_tail`.
- `review-packet.build_packet(..., acceptance_results=None)` — appends the section when the argument is not `None`; `build_verification_packet` takes no such argument.

**Tests:**
- a discovery packet built with two results contains the section, the assertion text, and for each row its command, its stated outcome, its exit code, its pass flag and its output tail, with distinct fixture values per row
- a discovery packet built with an empty list contains the section and assertion with no rows
- a discovery packet built without the argument contains no `## Acceptance results`
- a verification packet never contains `## Acceptance results`
- a final-review packet never contains `## Acceptance results`

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_review_packet.py` passes

**Tier:** standard

**Depends on:** nothing.

### Task 3: Reviewer dispatch without a sandbox, fingerprinted
- [ ] Done

**Files:**
- Modify: `scripts/forge_common.py` (remove `CODEX_REVIEWER_SANDBOX_ARGS`)
- Modify: `scripts/forge-run.py` (reviewer argv carries no sandbox override; fingerprint before and after every reviewer dispatch; `RepositoryChangedError` halts as `reviewer-wrote` with a freeze; `FingerprintError` is a contract error; `_packet_for` passes acceptance results)
- Modify: `tests/test_forge_isolation_flags.py`
- Modify: `tests/test_forge_review.py`
- Modify: `tests/test_forge_final_review.py`
- Modify: `tests/test_forge_resume.py`

**Spec:** [execution] Reviewer write discipline, [execution] The dispatch loop, [codex-runner] Worker isolation, [codex-runner] Testing

**Interface:**
- `forge-run._dispatch_review_call(...)` — takes `repo_fingerprint` before spawning and compares on every exit (verdict, non-zero exit, timeout); raises `RepositoryChangedError` on a mismatch before any other failure is reported, and lets `FingerprintError` propagate; `forge_common.CODEX_REVIEWER_SANDBOX_ARGS` no longer exists.
- `forge-run.dispatch_final_review(...)` — same wrap.
- The `except RuntimeError` resume fallbacks and the coverage-retry path in `execute_task` and `run_final_review_loop` leave `RepositoryChangedError` and `FingerprintError` uncaught. `execute_task` turns `RepositoryChangedError` into an `escalated` receipt with `halt_reason: "reviewer-wrote"` and the change lines in the halt record, freezing the pre-review capture's tree via `freeze_tree` under the task ref; `run_final_review_loop` returns `escalated` with the same class and the stage freeze uses `freeze_tree` on the pre-review capture the same way. `FingerprintError` reaches `run_plan` as a contract error naming the failed git command. Neither path dispatches another reviewer or commits.
- `_packet_for(..., acceptance_results=None)` — forwards to `build_packet`; `execute_task` passes the attempt's acceptance results on discovery and `None` on verification.

**Tests:**
- no recorded `codex exec` argv carries `sandbox_mode`, on all nine shapes
- a task reviewer stub that writes one file halts the run with exit 2, receipt `escalated`, `halt_reason: "reviewer-wrote"` naming that path, the pre-review capture frozen under the task ref with the stray file absent from the freeze commit and named in the halt record, the tree clean, no second reviewer dispatch and no task commit, on cold and on resume
- a final reviewer stub that writes one file halts the same way as a `final-review` stage halt, cold and resume
- a resumed task reviewer stub that writes then exits non-zero halts as `reviewer-wrote`, not a resume fallback
- a resumed task reviewer stub that writes then times out halts as `reviewer-wrote`
- a task reviewer stub that commits halts as `reviewer-wrote` with the branch and HEAD restored to the recorded sha and the stub's commit sha in the halt record
- a task reviewer stub that switches branch halts as `reviewer-wrote` with HEAD re-attached to the recorded branch
- a `reviewer-wrote` halt resumes: the next invocation reconciles the frozen worker attempt as any task halt does, and the class-aware brief names the reviewer write as the cause
- a reviewer stub that writes nothing passes through unchanged
- a `FingerprintError` injected before a cold task reviewer dispatch, and one injected on its exit, each end the run as `contract-error` (exit 1) naming the git command, with no fallback dispatch, no coverage retry and no commit
- the same two injections on a resumed task reviewer and on a cold and resumed final reviewer halt the same way
- the task discovery packet the runner writes contains `## Acceptance results` with that task's clauses; the verification packet does not

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_isolation_flags.py` passes
- `python3 -m unittest discover -s tests -p test_forge_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_resume.py` passes
- `grep -c CODEX_REVIEWER_SANDBOX_ARGS scripts/forge_common.py scripts/forge-run.py` prints `0`

**Tier:** standard

**Depends on:** Task 1, Task 2.

### Task 4: Approvals outlive the halt record and reach final review
- [ ] Done

**Files:**
- Modify: `scripts/forge_receipts.py` (`write_run_json` takes `approved`; `_read_approved`)
- Modify: `scripts/forge-run.py` (approved ids read from the run-level field, merged with `--resolve`, written on every run.json write, passed to `run_final_review_loop`)
- Modify: `tests/test_forge_resume.py`
- Modify: `tests/test_forge_final_review.py`

**Spec:** [execution] Halt resolution, [execution] Receipts and run state

**Interface:**
- `forge_receipts.write_run_json(..., approved=None)` — writes `approved` as a sorted list of canonical ids; omitted on `None`.
- `forge_receipts._read_approved(run_dir) -> list[str]` — empty list when absent.
- `forge-run.run_final_review_loop(..., approved_ids=frozenset())` — threads into every `convergence_decision` call.

**Tests:**
- an id approved by `--resolve` is present in run.json `approved` after the resumed task passes and the halt record is cleared
- a run resumed twice keeps the first invocation's approval without restating `--resolve`
- a final reviewer that re-raises an approved canonical id as pre-existing contract-breaking passes instead of halting

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_resume.py` passes
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_receipts.py` passes

**Tier:** standard

**Depends on:** nothing.

### Task 5: Terminal unverified set and the close-out halt
- [ ] Done

**Files:**
- Modify: `scripts/forge_receipts.py` (`write_run_json` takes `unverified`; `_read_unverified`; `write_final_review_receipt` records `unverified`)
- Modify: `scripts/forge-run.py` (collect seed findings and unverifiable coverage entries across the final-review loop; `unverified` stage halt before doc-sync under the stage-freeze rule; `--resolve` verbs `accept:<evidence>`, `defer`, `repair` on entry ids; resumed run re-runs the final review; `--status` prints open entries)
- Modify: `tests/test_forge_final_review.py`
- Modify: `tests/test_forge_resume.py`
- Modify: `tests/test_forge_status.py`
- Modify: `tests/test_forge_receipts.py`
- Modify: `scripts/forge-monitor.py` (unverified banner)
- Modify: `tests/test_forge_monitor.py`

**Spec:** [execution] The disposition matrix, [execution] Receipts and run state, [execution] Deferral handling, [execution] Halt resolution, [codex-runner] Halt / escalation, [codex-runner] Resume, [codex-runner] Terminal-state banner, [codex-runner] Runner

**Interface:**
- `forge_receipts.write_run_json(..., unverified=None)` — list of entries `{kind: "finding"|"coverage", id, reason, call: null|{verb: "accept"|"defer"|"repair", evidence: str|null}}`; omitted on `None`.
- `forge_receipts._read_unverified(run_dir) -> list[dict]` — empty list when absent.
- `forge_receipts.write_final_review_receipt(..., unverified=None)` — records the loop's entries on `final-review.json`.
- `forge-run.FinalReviewOutcome` gains `unverified: list[dict]` — every seed-disposition finding and every coverage entry with status `unverifiable` across the loop's attempts, accumulated by id and merged with the entries read back from run.json, so a verification lap or a later invocation never drops one; a prior invocation's `call` survives the merge.
- After a final-review pass, when any entry's `call` is `null`: `overall = "escalated-final-review"`, `halt_reason: "unverified"`, stage `final-review`, frozen via the existing stage-freeze helper, outstanding = the open entries with reasons, doc-sync skipped.
- `--resolve <id>=accept:<evidence>|defer|repair` — parsed by the existing `--resolve` parser extended for the `accept:` form; on an `unverified` halt the id must name an entry (absent raises naming it); `accept` requires non-empty evidence and is a contract error on a `scope-decision` id; `defer` additionally stages a deferral from the entry; `repair` and `defer` carrying evidence text are a contract error. The call is written onto the entry before the final review re-runs.
- A resumed run after an `unverified` halt re-runs the final review from scratch and halts again only on an entry whose `call` is still `null`; with none open it continues to doc-sync.
- `--status` prints `HALTED — final review: N unverified entries` and one line per open entry with kind, id and reason; `run.json` status is `escalated-final-review`.
- `scripts/forge-monitor.py` renders the same banner line plus the first open entry from run.json's `unverified`.

**Tests:**
- a final review whose verdict carries an unverifiable coverage entry and no findings halts as `unverified` with that entry's id and reason, frozen under the stage ref helper, doc-sync not dispatched
- a final review whose discovery verdict seeds f1 and whose verification verdict omits f1 still halts with f1 open
- a second invocation whose final-review discovery verdict seeds f2 halts with both f1 (carrying the first invocation's call when one was given) and f2 in run.json
- `--resolve f1=accept:"ran the migration by hand against prod snapshot"` clears the halt, re-runs the final review, and completes with the evidence recorded on the entry
- `--resolve f1=defer` stages a deferral whose title names f1 and completes
- `--resolve f1=repair` re-runs the final review and completes
- a resume that resolves one of two open entries halts again naming only the other
- `--resolve f1=accept` with empty evidence, `--resolve f1=repair:text`, `--resolve f1=defer:text`, and `accept` on a `scope-decision` id are each a contract error naming the form
- `--resolve f9=repair` on an `unverified` halt whose open entries are f1 and f2 is a contract error naming f9, and run.json's `unverified` and `halt` records are unchanged
- the final-review receipt lists every unverified entry with kind, id and reason
- `--status` on the halted run prints `HALTED — final review: 2 unverified entries` and each open entry
- the monitor banner on that run dir reads `HALTED — final review: 2 unverified entries` with the first entry's id and reason

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_resume.py` passes
- `python3 -m unittest discover -s tests -p test_forge_status.py` passes
- `python3 -m unittest discover -s tests -p test_forge_receipts.py` passes
- `python3 -m unittest discover -s tests -p test_forge_monitor.py` passes

**Tier:** standard

**Depends on:** Task 4.

### Task 6: Agent contracts and loop prose
- [ ] Done

**Files:**
- Modify: `agents/forge-standard.md` (review paragraph)
- Modify: `agents/forge-deep.md` (review paragraph)
- Modify: `skills/planning/SKILL.md` (Claude loop: fingerprint CLI around review spawns, acceptance JSON path in the reviewer prompt, unverified close-out call)
- Modify: `skills/planning/codex-execution.md` (worker isolation paragraph; unverified halt and `--resolve` verbs)
- Modify: `docs/forge/execution-loop.md` (read-only reviewer references)
- Test: `tests/test_forge_docs.py`

**Spec:** [execution] Reviewer write discipline, [execution] The dispatch loop, [codex-runner] Worker isolation, [codex-runner] Testing

**Interface:** none — prose only.

**Tests:**
- each of the two agent contracts contains the phrases `scratch copy`, `git stash`, `baseline`, `one mutant`, `restore`, `attributable`, and `stays green`, and does not contain `never modify files`
- `skills/planning/SKILL.md` names `forge_fingerprint.py snapshot` before and `forge_fingerprint.py verify` after each reviewer spawn in its Claude dispatch loop, and names the validation retry among the exits it covers
- `skills/planning/SKILL.md` names the acceptance-results JSON path in the Claude reviewer prompt together with the do-not-re-run assertion and the prose-clause obligation
- `skills/planning/SKILL.md` names the close-out call on unverified entries with the verbs accept, defer and repair
- `skills/planning/codex-execution.md` does not say reviewers carry `sandbox_mode="read-only"` in its runner dispatch paragraph

**Acceptance:**
- `python3 -m pytest tests/test_forge_docs.py -q` passes
- `grep -c 'never modify files' agents/forge-standard.md agents/forge-deep.md` prints `0`
- The Claude dispatch loop in `skills/planning/SKILL.md` states that a `verify` mismatch is a `reviewer-wrote` halt with no fallback spawn, no coverage retry and no commit, on task and final reviews alike, and that a `verify` failure is a contract error.
- The Claude dispatch loop in `skills/planning/SKILL.md` states that the orchestrator writes the acceptance result records as JSON to the scratch directory and names that path in the reviewer prompt with the do-not-re-run assertion.
- The Claude close-out gate in `skills/planning/SKILL.md` presents staged deferrals and open unverified entries together, each with its reason, and records a call per entry before filing or completion.
- The document-review command in `skills/planning/codex-execution.md` under Document reviews on Codex keeps its read-only flag; this task does not change it.

**Tier:** standard

**Depends on:** nothing.

### Task 7: Conformance of code to the amended prose sections
- [ ] Done

**Files:**
- Test: `tests/test_forge_dispose.py` (gate-mode and parity-gap cases)
- Test: `tests/test_forge_checklist.py` (final citable set case)

**Spec:** [execution] Execution, [execution] Contract checklist, [execution] Reviewer verdict contract, [execution] Autonomy flag, [execution] The shared decision helper, [execution] Terminal doc-sync stage, [codex-runner] Runner

**Interface:** none — these sections were reworded to match behavior the code already has; this task pins that behavior with tests so the prose cannot drift again.

**Tests:**
- in gate mode an in-diff improvement finding halts as `gate` with `repair_task` null and no validation defect
- in gate mode an unverifiable finding halts as `gate` with `repair_task` null
- a pre-existing contract-breaking finding without `repair_task` is a validation defect in either mode
- `final_citable_refs` equals the final checklist ids plus every task's `t<N>.t<M>` ids, and is a strict superset of the final coverage items when any task declares a test case
- the `forge_dispose` CLI given a verdict on a line changed by an earlier task of the run, with no run diff, classifies it `pre-existing`; the in-process call with `run_diff` classifies it `in-run`

**Acceptance:**
- `python3 -m pytest tests/test_forge_dispose.py -q` passes
- `python3 -m pytest tests/test_forge_checklist.py -q` passes

**Tier:** standard

**Depends on:** nothing.
