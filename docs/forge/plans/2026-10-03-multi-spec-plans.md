# Multi-Spec Plans Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** A plan declares the spec files it implements and names each section by spec, so one plan covers any number of specs and every tool reads them from the plan.
**Architecture:** `extract-brief.py` owns the two new parsers (the `**Spec files:**` header and `[<spec id>] <heading>` entries), the spec set loader and section resolution, with exact match winning over prefix. Every other tool calls those: the checklist builds ids from resolved sections, lint runs its spec rules per declared spec, plan review builds its tables across the set, and the Codex runner threads the set through briefs, packets, the run record and resume. `--spec` stays on every CLI as the legacy path for a plan with no header.
**Tech stack:** Python 3 stdlib, pytest with the fake `codex` in `tests/_forge_support.py`.
**Global Constraints:**
- Scripts under `scripts/` import the Python 3 standard library only.
- A parser or validator names the cause of a malformed input and never guesses, defaults or normalizes it.
- A legacy plan, one with no `**Spec files:**` header run with `--spec`, produces the same briefs, checklist ids, packets and lint results as before, except that an exact heading match now wins over a prefix match.
- A plan declaring exactly one spec file produces unprefixed section ids and labels, identical to a legacy plan's.

This plan is itself a legacy plan run with `--spec docs/forge/specs/pipeline.md`, because the header it introduces is not readable until Task 1 lands. It also implements `docs/forge/specs/execution.md` and `docs/forge/specs/codex-runner.md`; tasks that need those name the sections under **Files**.

### Task 1: Spec set and section resolution
- [ ] Done

**Files:**
- Modify: `scripts/extract-brief.py` (`parse_spec_files`, `load_spec_set`, `parse_spec_entries`, `resolve_entries`, `SpecFile`, `ResolvedSection`; `match_heading_names` exact-match rule; `build_brief` and the CLI read the spec set)
- Test: `tests/test_extract_brief.py`

**Spec:** Plan documents, `scripts/extract-brief.py`

**Interface:**
- `SpecFile` — dataclass, `path: str` (absolute), `spec_id: str | None` (the frontmatter `system` value; `None` only for a legacy spec file with no frontmatter)
- `ResolvedSection` — dataclass, `spec: SpecFile`, `heading: str` (resolved heading text, whitespace-collapsed), `lines: list[str]` (the section's text), `label: str` (`[<spec id>] <heading>` when the set holds more than one spec file, else `<heading>`)
- `parse_spec_files(plan_lines) -> list[str]` — the header's declared paths, backticks removed, in order; `[]` when the header has no `**Spec files:**`
- `load_spec_set(plan_path, legacy_spec_path=None, repo_root=None) -> list[SpecFile]` — the plan's spec set; `[]` for a plan with no spec; raises `RuntimeError` when both a header and `legacy_spec_path` are given, when a declared path names no file, and when two declared files share a spec id
- `parse_spec_entries(task_block) -> list[tuple[str | None, str]]` — each `**Spec:**` entry as `(spec id or None, heading name)`
- `resolve_entries(entries, spec_set, task_number) -> list[ResolvedSection]` — raises `RuntimeError` naming the task and entry for an undeclared id, a bare entry in a multi-spec set, an unmatched name and an ambiguous name
- `match_heading_names(name, headings)` — returns the exact matches when any heading equals the name, else the prefix matches
- `build_brief(plan_path, task_number, spec_path=None) -> str` — `spec_path` is the legacy spec
- `parse_spec_names` keeps its current return value for existing callers

**Tests:**
- a header with a bulleted `**Spec files:**` block yields its paths in order
- a single-line `**Spec files:**` value yields one path
- a path written inside one pair of backticks yields the same path as the plain form
- a bulleted `**Spec files:**` block ends at the first blank line, the next `**Field:**` or a heading, and no line after that is read as a path
- a line inside the block that does not begin with `-` continues the preceding bullet
- a plan with no `**Spec files:**` yields no paths, and its spec set is the legacy path when one is given and empty when none is
- a declared path is resolved against the repository root, not the working directory
- a header together with a legacy spec path raises an error naming both
- a declared path naming no file raises an error naming the path
- two declared files with the same `system` value raise an error naming both paths
- each declared file's spec id is its frontmatter `system` value
- a `**Spec:**` entry `[execution] Plan lint` parses to id `execution` and name `Plan lint`
- a bare entry parses to no id and its name
- an entry splits at its first `]`, so a heading name containing `]` after that is kept whole
- whitespace inside the brackets is an error naming the entry
- an id that differs from a declared id only in case is an error listing the declared ids
- a bare entry in a plan declaring two spec files is an error listing the declared ids
- a bracketed entry in a plan declaring one spec file resolves when the id matches and is an error when it does not
- a bracketed entry in a legacy plan is an error naming the task and entry, even when the id equals the legacy spec's `system` value
- a name equal to one heading resolves to it when another heading begins with that name
- a name that equals no heading and prefixes exactly one resolves to that heading
- a name that equals no heading and prefixes two is an error naming both candidates
- two headings with identical text make a name equal to them an error naming both
- heading comparison ignores case, collapses whitespace on both sides, and strips leading numbering from the heading only
- a name beginning with a digit is not stripped of it
- a section named in a second spec file is taken from that file, not from the first
- a brief for a two-spec plan labels each section `[<spec id>] <heading>`
- a brief for a one-spec plan and for a legacy plan carries unlabeled section headings
- the CLI run on a header plan with no `--spec` writes a brief containing both specs' named sections
- the CLI run on a task that declares `**Spec:**` in a plan with no spec exits non-zero naming the task

**Acceptance:**
- `python3 -m pytest -q tests/test_extract_brief.py tests/test_forge_docreview.py` passes

**Tier:** standard

**Depends on:** nothing.

### Task 2: Checklist ids and tables across specs
- [ ] Done

**Files:**
- Modify: `scripts/forge_checklist.py` (`build_task_checklist`, `build_final_checklist`, `citable_refs`, `final_citable_refs`, `build_section_table` and the CLI resolve sections through the spec set)
- Test: `tests/test_forge_checklist.py`
- Read: `docs/forge/specs/execution.md` section `Contract checklist` (the `spec:<heading>` row and the CLI line)

**Spec:** Plan documents

**Interface:**
- `build_task_checklist(plan_path, spec_path, task_number)`, `build_final_checklist(plan_path, spec_path)`, `citable_refs(plan_path, spec_path, task_number)`, `final_citable_refs(plan_path, spec_path)`, `build_section_table(plan_path, spec_path)` — signatures unchanged; `spec_path` is the legacy spec and is `None` for a header plan
- Section id: `spec:<heading>` when the spec set holds one file, `spec:[<spec id>] <heading>` when it holds more than one
- `SectionEntry.heading` carries the same `[<spec id>] ` prefix under the same condition
- CLI: `--spec` optional

**Tests:**
- a two-spec plan's final checklist holds `spec:[<spec id>] <heading>` ids for sections in both files
- two specs each with a section of the same heading text yield two distinct ids
- a bare entry and a bracketed entry naming the same section yield one id
- the id uses the resolved heading text, not the prefix or casing the entry was written with
- a one-spec header plan's ids are `spec:<heading>` and equal a legacy plan's for the same spec
- `citable_refs` for a task of a two-spec plan holds the prefixed ids of the sections that task names
- the section table of a two-spec plan lists sections from both files with prefixed headings and the tasks naming each
- a header plan passed a legacy `spec_path` as well raises the both-given error
- a task declaring `**Spec:**` in a plan with no spec raises naming the task
- the CLI run on a header plan with no `--spec` emits the checklist

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_checklist.py tests/test_forge_coverage.py tests/test_forge_dispose.py` passes

**Tier:** standard

**Depends on:** Task 1

### Task 3: Lint across declared specs
- [ ] Done

**Files:**
- Modify: `scripts/forge_lint.py` (header and entry checks; the changed-section rule per declared spec; living-spec rules on every declared spec; the undeclared-changed-spec warning)
- Test: `tests/test_forge_lint.py`
- Read: `docs/forge/specs/execution.md` section `Plan lint` (the check table and the paragraphs "The rule runs once per declared spec" and "An undeclared changed spec is a warning")

**Spec:** Lint, Plan documents

**Interface:**
- `lint_plan(plan_path, spec_path=None, *, repo_root)` — signature and return shape unchanged; the undeclared-changed-spec finding is returned as a warning, the way the empty-checklist warning is
- New error conditions: `**Spec files:**` fails the field clause grammar; a declared path names no file; a declared file fails a living-spec rule; two declared files share a spec id; `**Spec files:**` and `spec_path` both given; a `**Spec:**` entry with an undeclared id; a bare entry in a multi-spec plan

**Tests:**
- a header plan declaring two valid specs with every changed section named lints clean
- a changed section in the second declared spec that no task names is an error naming that spec and section
- a section named only by an entry carrying the other spec's id does not count as claimed
- a bare entry in a one-spec header plan claims that spec's section
- an entry that does not resolve is reported once, by the entry check, and not again as an unclaimed section
- a declared path naming no file is an error naming the path
- a declared file with no frontmatter is an error naming the path and the rule
- two declared files with one `system` value are an error naming both
- a header plan linted with `spec_path` also given is an error naming both
- an entry with an undeclared id is an error naming the task and entry and listing the declared ids
- a bare entry in a two-spec plan is an error naming the task and entry
- a `**Spec files:**` marker followed by neither a bullet nor a value is an error
- a spec file modified since the merge base, in the directory of a declared spec and not declared, produces a warning naming the file and no error
- a spec file added since the merge base, in the directory of a declared spec and not declared, produces a warning naming the file
- an undeclared spec file changed only in its `## Changelog` produces no warning
- an undeclared spec file deleted since the merge base produces no warning
- an undeclared changed file in a subdirectory of a declared spec's directory produces no warning
- a legacy plan produces no undeclared-spec warning
- with no resolvable merge base, the undeclared-spec check produces no warning
- a legacy plan with `spec_path` lints as before

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_lint.py tests/test_forge_docs.py` passes

**Tier:** standard

**Depends on:** Task 1, Task 2

### Task 4: Plan review across specs
- [ ] Done

**Files:**
- Modify: `scripts/forge_planreview.py` (`build_packet` and `run` take the spec set; finding sections validated across every declared spec)
- Modify: `scripts/forge_docreview.py` (`--spec` optional when `--plan` is given)
- Test: `tests/test_forge_planreview.py`
- Read: `docs/forge/specs/execution.md` section `Plan review verdict` (the `coverage` and finding `section` bullets)

**Spec:** Plan review

**Interface:**
- `forge_planreview.build_packet(plan_path, spec_path=None) -> str` and `run(plan_path, spec_path, verdict_path, out_path) -> int` — `spec_path` is the legacy spec
- The packet lists every declared spec path; the section table and the legal finding sections carry `[<spec id>] ` when the plan declares more than one spec file
- CLI: `forge_docreview.py --plan <plan> [--spec <spec>] ...`; `--plan` on a plan with no spec exits 1 naming the cause; without `--plan`, `--spec` is still required

**Tests:**
- the packet for a two-spec plan lists both spec paths and a section table with prefixed headings from both files
- a verdict whose coverage entries use the prefixed headings validates
- a coverage entry using an unprefixed heading in a two-spec plan is a defect
- a `contradiction` finding citing `[<spec id>] <heading>` for any heading in either declared spec is valid
- a finding citing a heading under the wrong spec id is a defect
- a one-spec header plan's packet and legal sections are unprefixed and equal a legacy plan's
- the packet states that a `pass` verdict still carries an empty `findings` list
- `--plan` on a header plan with no `--spec` emits the packet and exits 0
- `--plan` on a header plan with `--spec` also given exits 1 naming both
- `--plan` on a plan with no spec exits 1 naming the cause and writes no packet
- `forge_docreview.py` with neither `--plan` nor `--spec` exits non-zero

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_planreview.py tests/test_forge_docreview.py` passes

**Tier:** standard

**Depends on:** Task 2

### Task 5: Runner reads the spec set, records it and checks it on resume
- [ ] Done

**Files:**
- Modify: `scripts/forge-run.py` (`--spec` optional; `run_plan`, `resume`, `execute_task`, `_brief_for` use the spec set; the both-given and resume-mismatch contract errors; module docstring usage line)
- Modify: `scripts/forge_receipts.py` (`write_run_json` records `specs`; readers accept a legacy `spec` string)
- Test: `tests/test_forge_run_specs.py`
- Read: `docs/forge/specs/codex-runner.md` sections `Runner`, `Receipts and run state`, `Resume`, `Halt / escalation` and the "Spec sets" bullet of `Testing`

**Interface:**
- CLI: `forge-run.py <plan.md> [--spec <spec.md>] ...`
- `forge_receipts.write_run_json(run_dir, plan_path, spec_paths, ...)` — third parameter is a list of paths, possibly empty; `run.json` gains `"specs": [<absolute path>, ...]` and no longer writes `"spec"`
- `forge_receipts.read_run_specs(run) -> list[str]` — the recorded spec paths from a loaded `run.json` dict: its `specs` list, or a one-element list from a legacy `spec` string, or `[]`
- Contract errors, exit 1: `--spec` given for a plan with a `**Spec files:**` header, before any run dir is created; a resumed run whose spec set differs from the recorded one, naming the added and removed paths

**Tests:**
- a plan declaring two spec files runs with no `--spec`, and the brief for a task naming a section in each contains both sections
- lint, the checklist and the review packet for that run each receive both specs
- `--spec` given for a header plan exits 1, names both, and creates no run dir
- a legacy plan with `--spec` runs and its `run.json` lists that one path under `specs`
- a plan with no header and no `--spec` runs to completion, and its `run.json` has an empty `specs` list
- `run.json` for a two-spec run lists both absolute paths under `specs` and has no `spec` key
- a resumed run whose plan header gained a spec file exits 1 naming the added path
- a resumed run whose plan header lost a spec file exits 1 naming the removed path
- a legacy run resumed with a different `--spec` exits 1 naming the added and removed paths
- a resumed run whose header lists the same files in a different order resumes
- a resumed run reached through a different spelling of the same file path resumes
- a run directory whose `run.json` carries only a legacy `spec` string is read as a one-element set by resume and by `--status`
- neither contract error exits 2

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_run_specs.py tests/test_forge_resume.py tests/test_forge_receipts.py tests/test_forge_status.py` passes

**Tier:** standard

**Depends on:** Task 1, Task 2, Task 3

### Task 6: Packets list spec paths; per-task packets label sections
- [ ] Done

**Files:**
- Modify: `scripts/forge_git.py` (`_final_packet` lists spec paths and named sections, no spec text)
- Modify: `scripts/forge-run.py` (`_doc_sync_brief`, `run_final_review_loop`, `dispatch_doc_sync` take the spec set)
- Modify: `scripts/review-packet.py` (per-task spec context labeled across specs)
- Create: `tests/live/check_codex_spec_by_path.sh` (live check that a read-only `codex exec` reviewer opens a spec file named by path; not run by the suite)
- Test: `tests/test_forge_run_specs.py`, `tests/test_review_packet.py`
- Read: `docs/forge/specs/codex-runner.md` section `Runner` (the bullets "Whole specs are read by path, not pasted" and the no-spec plan) and `Task loop` step 4

**Spec:** `scripts/review-packet.py`, Shared behavior

**Interface:**
- `forge_git._final_packet(spec_paths, base, diff, run_dir, ...)` — first parameter is the spec path list; the packet has a section listing each spec path and, per spec, the section labels the plan's tasks name
- `forge-run._doc_sync_brief(spec_paths, diff, run_dir)` — same listing, no spec text
- A per-task packet's spec context labels each pasted section `[<spec id>] <heading>` when the plan declares more than one spec file
- `tests/live/check_codex_spec_by_path.sh` exits 0 when the reviewer's last message quotes a marker line that exists only in the spec file, exits 1 otherwise, and exits 2 with a message when `codex` is not installed

**Tests:**
- the final-review packet for a two-spec run lists both spec paths and the named section labels and contains no line of either spec's body
- the final-review packet for a one-spec run contains the spec's path and no line of its body
- the final-review packet for a plan with no spec lists no spec and still carries the diff
- the doc-sync brief lists both spec paths and contains no line of either spec's body
- the doc-sync stage runs for a plan with no spec
- a per-task packet for a two-spec plan pastes the named sections labeled with their spec ids
- a per-task packet for a one-spec plan pastes its sections unlabeled, as before
- the diff block in every packet is unchanged
- a two-spec run through the runner, with a standard-tier task naming a section in each spec, completes its task review, final review and doc-sync, and each of those stages receives both specs
- a run through the runner of a plan with no spec that produces a diff completes final review and doc-sync
- no call site in the runner passes only the first declared spec to a packet or brief

**Acceptance:**
- `python3 -m pytest -q tests/test_forge_run_specs.py tests/test_review_packet.py tests/test_forge_final_review.py tests/test_forge_docsync.py` passes
- `bash -n tests/live/check_codex_spec_by_path.sh` passes

**Tier:** standard

**Depends on:** Task 5

### Task 7: Skills and docs describe multi-spec plans
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (plan header template, `**Spec:**` paragraph, Self-review, Plan review, Plan lint paragraph, the Claude dispatch commands)
- Modify: `skills/planning/codex-execution.md` (`--spec` optional; the spec set comes from the plan)
- Modify: `docs/forge/running-on-codex.md` (the run command)
- Read: `docs/forge/specs/pipeline.md` sections `Plan documents` and `Plan review`; `docs/forge/specs/execution.md` section `Plan lint`

**Spec:** Plan documents, Plan review

**Tests:** none — skill and doc prose; nothing executes, so acceptance is mechanical text checks and reviewer-read clauses.

**Acceptance:**
- `grep -c 'Spec files:' skills/planning/SKILL.md` passes
- `grep -n 'one spec file per task' skills/planning/SKILL.md` prints nothing
- `python3 -m pytest -q tests/test_forge_docs.py` passes
- The plan header template in the planning skill shows a `**Spec files:**` field and says it lists the spec files the plan implements, read from the plan by every tool.
- The `**Spec:**` paragraph gives the `[<spec id>] <heading>` entry form, says the id is required when the plan declares more than one spec file, and states the resolution order: exact match, then unique prefix, then an error.
- The Self-review list includes checking that every spec the plan implements is declared in `**Spec files:**`.
- The Plan review section's two commands are shown without `--spec`, with a note that `--spec` is for a legacy plan with no header.
- The Plan review section says a lint warning about an undeclared changed spec is settled before the offer: the author declares the spec or records why not, and the execution offer lists each warning with its reason.
- The Claude dispatch commands for briefs, checklists and citable refs are shown without `--spec`, with the same legacy note.
- Both `skills/planning/codex-execution.md` and `docs/forge/running-on-codex.md` show `forge-run.py <plan>` with `--spec` optional and say the specs come from the plan's header.
- Nothing in the three files still says a run or a task takes exactly one spec file.

**Tier:** standard

**Depends on:** Task 4, Task 5
