# Plan Review Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** A cold reviewer validates a plan against its spec before execution is offered, and a script rejects a verdict that leaves any named spec section unanswered.
**Architecture:** `forge_checklist.py` gains the two tables a plan review is built from: the promise table (every plan promise with an id, including the new command-clause ids) and the section table (each named spec section and the tasks naming it). A new module `forge_planreview.py` owns the plan verdict's validation, disposition and packet text, and takes those tables as arguments. `forge_docreview.py`'s CLI gains `--plan` and delegates to it, so spec review's code path is untouched. The planning skill gains the gate.
**Tech stack:** Python 3 stdlib, pytest.
**Global Constraints:**
- Scripts under `scripts/` import the Python 3 standard library only.
- A parser or validator names the cause of a malformed input and never guesses, defaults or normalizes it.
- `forge_docreview.py` invoked without `--plan` behaves exactly as before.

The second spec this plan implements is `docs/forge/specs/pipeline.md`, section `Plan review`. A run takes one `--spec`, which is `docs/forge/specs/execution.md`, so each task that needs the pipeline section names its path under **Files**.

### Task 1: Promise table and section table
- [ ] Done

**Files:**
- Modify: `scripts/forge_checklist.py` (`build_plan_promises`, `build_section_table`, `SectionEntry`)
- Test: `tests/test_forge_checklist.py`
- Read: `docs/forge/specs/pipeline.md` section `Plan review` (Section table, Promise table)

**Spec:** Contract checklist

**Interface:**
- `forge_checklist.build_plan_promises(plan_path) -> list[ChecklistItem]` — every `g<N>`, `t<N>.t<M>`, `t<N>.a<M>` and `t<N>.c<M>` in the plan, in plan order; a command clause's item has `source="acceptance-command"` and `text` equal to the clause as written
- `forge_checklist.SectionEntry` — dataclass, `heading: str`, `tasks: list[int]`
- `forge_checklist.build_section_table(plan_path, spec_path) -> list[SectionEntry]` — one entry per distinct resolved heading, in first-named order; raises `RuntimeError` when no task names a section
- `build_task_checklist`, `build_final_checklist`, `citable_refs` and `final_citable_refs` are unchanged

**Tests:**
- the promise table lists a global constraint as `g<N>`, a test case as `t<N>.t<M>`, a prose acceptance clause as `t<N>.a<M>` and a command acceptance clause as `t<N>.c<M>`
- command clauses are numbered among command clauses in field order, so a prose clause between two commands consumes no `c` number
- the `t<N>.a<M>` ids in the promise table equal the ids `build_task_checklist` emits for the same task
- `build_task_checklist`, `build_final_checklist`, `citable_refs` and `final_citable_refs` return no `t<N>.c<M>` id for a plan that has command clauses
- a task whose `**Tests:**` is the `none — <reason>` form contributes no `t<N>.t<M>` id
- a plan with no `**Global Constraints:**` block contributes no `g<N>` id
- the section table has one entry per distinct resolved heading, each listing the tasks naming it in ascending order
- a prefix or differently-cased name on a `**Spec:**` line resolves to the heading's full text, whitespace-collapsed
- a section and its subsection named by different tasks produce two entries
- a plan whose tasks name no spec section raises an error stating that no task names a spec section
- an unresolvable `**Spec:**` name raises the existing section-not-found error
- an acceptance clause that does not parse raises `AcceptanceClauseError` from `build_plan_promises`

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_checklist.py` passes

**Tier:** standard

**Depends on:** nothing.

### Task 2: Plan verdict validation and disposition
- [ ] Done

**Files:**
- Create: `scripts/forge_planreview.py` (`validate_verdict`, `dispose`, `PlanVerdictResult`, `PlanDisposition`)
- Test: `tests/test_forge_planreview.py`

**Spec:** Plan review verdict

**Interface:**
- `forge_planreview.PlanVerdictResult` — dataclass, `valid: bool`, `defects: list[str]`, `findings: list[dict]`
- `forge_planreview.PlanDisposition` — dataclass, `amend: list[dict]`, `surface: list[dict]`
- `forge_planreview.validate_verdict(verdict, section_table, promise_ids, spec_headings, task_numbers) -> PlanVerdictResult` — `section_table` is `list[SectionEntry]`; `promise_ids`, `spec_headings` and `task_numbers` are collections of the legal values; never raises on a malformed verdict, and reports every defect found, not the first
- `forge_planreview.dispose(findings) -> PlanDisposition` — called only on a valid verdict's findings
- Finding kinds: `uncovered`, `contradiction`, `spec-defect`

**Tests:**
- a `pass` verdict with every section answered and every requirement covered or marked `na` is valid
- a coverage entry missing for a table section is a defect naming the section
- a duplicate coverage entry for one section is a defect
- a coverage entry naming a section not in the table is a defect
- a coverage entry with an empty `requirements` list is a defect
- a `covered_by` id not in the promise table is a defect naming the id
- a `covered_by` id differing from a real id only in case is a defect, not normalized
- a non-null `na` alongside a non-empty `covered_by` is a defect
- an `na` that is empty or whitespace is a defect
- a requirement with an empty `covered_by` and a null `na` and no `uncovered` finding for its section is a defect
- an `uncovered` finding whose section has no uncovered requirement is a defect
- `verdict: "pass"` with a finding is a defect
- `verdict: "pass"` with an uncovered requirement is a defect
- `verdict: "findings"` with no findings is a defect
- a finding `kind` outside the three values is a defect and the finding is not reclassified
- two findings sharing an `id` is a defect
- an `uncovered` finding whose `section` is a spec heading outside the table is a defect
- a `contradiction` or `spec-defect` finding citing a spec heading outside the table is valid
- a finding whose `section` matches no spec heading is a defect
- a finding `task` naming no task in the plan is a defect, and `null` is accepted
- an `uncovered` finding whose `task` does not name that section is a defect
- a finding missing `summary`, `evidence` or `proposed_amendment`, or carrying a blank one, is a defect naming the finding
- a verdict that is not an object, or whose `coverage` or `findings` is not a list, is reported as a defect without raising
- a verdict with three independent defects reports all three
- `dispose` routes `uncovered` and `contradiction` findings to `amend` and `spec-defect` findings to `surface`

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_planreview.py` passes

**Tier:** standard

**Depends on:** Task 1

### Task 3: Plan review packet and CLI
- [ ] Done

**Files:**
- Modify: `scripts/forge_planreview.py` (`build_packet`)
- Modify: `scripts/forge_docreview.py` (`main` accepts `--plan` and delegates to `forge_planreview`)
- Test: `tests/test_forge_planreview.py`
- Read: `docs/forge/specs/pipeline.md` section `Plan review` (Packet, Verdict, the three checks, Covered means promised)

**Spec:** Plan review verdict, Contract checklist

**Interface:**
- `forge_planreview.build_packet(plan_path, spec_path) -> str` — raises `RuntimeError` on a plan that names no spec section and propagates plan-parse errors unchanged
- CLI: `forge_docreview.py --plan <plan> --spec <spec> [--out <path>]` emits the packet
- CLI: `forge_docreview.py --plan <plan> --spec <spec> --verdict <file> [--out <path>]` validates and disposes
- Decision JSON, valid verdict: `{"valid": true, "defects": [], "amend": [...], "surface": [...]}`; invalid verdict: `{"valid": false, "defects": [...]}` with no `amend` or `surface` key
- Exit codes: `0` for an emitted packet or a valid verdict; `1` for an invalid verdict, an unreadable or non-object verdict file, or a plan or spec that fails to parse

**Tests:**
- the packet names the plan path and the spec path and contains neither document's body text
- the packet's section table lists each named heading with the tasks naming it
- the packet's promise table lists every promise id with its text
- the packet states both reviewer questions: whether any plan element contradicts the spec, and whether each requirement in a named section is covered by a promise
- the packet states that only a test case, an acceptance clause or a global constraint covers a requirement
- the packet lists the required verdict fields, the three finding kinds and the `na` rule
- `--plan` with `--spec` and `--out` writes the packet and exits 0
- a plan naming no spec section exits 1, writes no packet, and its stderr message names both the cause and the fix: add `**Spec:**` lines naming the sections the tasks implement
- a plan with an acceptance clause that does not parse exits 1 naming the task and clause
- `--verdict` with a valid `pass` verdict exits 0 and writes a decision with empty `amend` and `surface`
- `--verdict` with a valid verdict carrying one finding of each kind exits 0 and places each in `amend` or `surface` per its kind
- `--verdict` with an invalid verdict exits 1, lists every defect on stderr and writes a decision with no `amend` or `surface` key
- `--verdict` naming a file that is not a JSON object exits 1 with a named error
- `--plan` without `--spec` exits non-zero

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_planreview.py tests/test_forge_docreview.py` passes
- `python3 scripts/forge_docreview.py --plan docs/forge/plans/2026-10-03-plan-review.md --spec docs/forge/specs/execution.md` prints `t3.c1`

**Tier:** standard

**Depends on:** Task 1, Task 2

### Task 4: Planning skill gains the plan review gate
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (new `## Plan review` section between `## Self-review` and `## Execution`; the Plan lint paragraph no longer says lint is dispatch-only; the `**Spec:**` paragraph and the Self-review list state when a task names its spec sections)
- Read: `docs/forge/specs/pipeline.md` section `Plan review`

**Spec:** Plan lint

**Tests:** none — skill prose; nothing executes, so acceptance is mechanical text checks and reviewer-read clauses.

**Acceptance:**
- `grep -c '^## Plan review$' skills/planning/SKILL.md` prints `1`
- `grep -n 'both harnesses, dispatch only' skills/planning/SKILL.md` prints nothing
- `python3 -m pytest -q tests/test_forge_docs.py` passes
- The `## Plan review` section sits after `## Self-review` and before `## Execution`.
- The section states the three checks: no plan element contradicts the spec, every changed or named spec section is covered, and plan lint.
- The section gives the packet command and the verdict command, each with `--plan` and `--spec`.
- The section says the reviewer is a fresh agent whose prompt is the packet path, that a re-review after a plan amendment resumes it and is whole-plan, and that a failed resume falls back to a fresh reviewer given the full packet.
- The `**Spec:**` paragraph says that in a plan written from a spec every task names the sections it implements, and that a task omits the line only when it implements no spec section; it no longer calls the line optional without that condition (source: `docs/forge/specs/pipeline.md` section `Plan documents`).
- The Self-review list includes checking that every task of a plan written from a spec names its spec sections.
- The section says an invalid verdict gets one retry resuming the reviewer with the defect list, then a contract error.
- The section says `uncovered` and `contradiction` findings are amended by the plan's author and `spec-defect` findings are surfaced to the user.
- The section says plan lint runs before the packet is built and again on the amended plan.
- The section says a plan with no spec skips plan review, and that execution is not offered until plan review passes.
- The Plan lint paragraph says lint also runs at plan review and still runs before dispatch.

**Tier:** standard

**Depends on:** Task 3
