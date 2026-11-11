# Evaluative Spec Authoring Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Hold spec authors to the conditions the spec reviewer checks, give spec review the author's dependency record, and bound the reviewer's findings.
**Architecture:** The conditions live once in `skills/brainstorming/design-anti-patterns.md`, loaded by reference by author and reviewer. `scripts/forge_docreview.py` gains a required `--record` input for spec review: it validates the record, carries it in the packet, and checks the verdict against it. Skill prose carries the draft, checker and digest rules; no new script.
**Tech stack:** Python 3 standard library, pytest, Markdown skill files.
**Global Constraints:**
- `scripts/` uses the Python 3 standard library only
- a malformed dependency record raises a contract error naming the entry and the cause and is never defaulted or repaired
**Spec files:**
- docs/forge/specs/pipeline.md
- docs/forge/specs/execution.md

### Task 1: Skill prose, conditions file, and wording-test removal
- [ ] Done

**Files:**
- Modify: `skills/brainstorming/SKILL.md` (flow steps 1, 3, 4, 5, 7 and 8)
- Modify: `skills/brainstorming/design-anti-patterns.md` (six conditions, entries under them, reviewer bounds)
- Modify: `skills/planning/codex-execution.md` (checker prompt beside the document review command)
- Modify: `skills/planning/SKILL.md` (Plan review: a not-constrained behaviour is not a requirement)
- Modify: `README.md` (Brainstorming paragraph)
- Test: `tests/test_forge_docs.py` (wording tests removed, two kept tests adjusted)

**Spec:** [pipeline] Brainstorming flow contracts, [pipeline] Spec authoring, [pipeline] Spec review, [execution] Plan review verdict

**Interface:** none — prose. `design-anti-patterns.md` keeps the sentence form the field-agreement check reads: the text between "required verdict fields" and "Deleting" names exactly `dependencies_read` and `replaced_system`.

**Tests:** none — prose artifacts and test deletions; verification is the existing suite, spec lint and the prose clauses below

**Acceptance:**
- `uv run --no-project --with pytest --python 3.13 python -m pytest -q tests/test_forge_docs.py tests/test_forge_docreview.py` passes
- `python3 scripts/forge_lint.py --specs docs/forge/specs` passes
- the brainstorming skill states that input is unconstrained, drops the fixed question-batch rule, and has the author ask the user only where a condition is unmet and reading the code cannot meet it, each question carrying a proposed default
- the brainstorming skill states that the design is approved from one decision digest of what was chosen, what it forecloses and what is assumed, that sections with a check-in replace the single digest when the design spans several specs, is too large to judge in one read, or the user asks, that the digest is derived from the draft, and that nothing is written to a spec before the approval
- the brainstorming skill states the scope of spec authoring: a new spec and an amendment written ahead of a build are inside it; a gear 2 change and an amendment made in answer to a review finding are outside it; a review-driven amendment keeps the dependency record current and re-enters spec review with it
- the brainstorming skill points the author at the conditions file by the `@design-anti-patterns.md` reference form and restates no condition
- the brainstorming skill states that the author reads existing code before designing against it, that every piece of existing code the design relies on has a dependency record entry, and that parallel read-only scouts returning facts with file and line are the means when the design depends on more than one part of the code, their number being the author's call
- the brainstorming skill states that before approval the author writes what the spec will say to a draft file outside the repository's tracked files, that the checker reads the draft, and that its text goes into the spec after approval
- the brainstorming skill gives the dependency record's JSON shape with `dependencies` entries of `symbol`, `file` and `behavior` and a `waiver`, states that it is kept with the draft, never in the spec and never committed, and that empty `dependencies` is legal only with a non-null `waiver`
- the brainstorming skill states that the checker is a separate agent and never the author, that its material is one file assembled by the author holding the draft, the dependency record, every document the user supplied and the path of the conditions file, that it reads the code, and that it returns per unmet condition the condition, the place in the draft and the evidence, or states that none is unmet
- the brainstorming skill states that the checker runs at least once in the foreground before the design is presented for approval on both harnesses and may also run in the background where the harness supports it, that an unmet condition that is a fact about the code sends the author to read and one that is a judgment becomes a question to the user, that a condition still unmet at presentation is stated in the digest, and that the checker is not the gate
- the brainstorming skill states how the checker starts: on Claude a spawned agent at the standard tier whose prompt is the material file's path; on Codex the document review command under Document reviews on Codex used as written except for its prompt; and that when the checker cannot be started the session stops and tells the user, never substituting the author's own check, never retrying with a different model, and never presenting the design without the run
- the brainstorming skill states that a behaviour the user leaves to the implementer is written where it lives as `<behaviour>: not constrained`
- step 8 of the brainstorming skill gives the packet and verdict commands with `--record <path>`, states that a record defect is the author's and is fixed before regenerating, and states that every re-review after an amendment runs on a fresh reviewer with only the verdict-validation retry resuming
- the conditions file states the six conditions Grounded, Consistent, Testable, Decided, Bounded and Complete, each with what must hold and what shows that it holds, states that they are fixed by the pipeline spec, and gives the mapping of Grounded to `groundedness`, Consistent to `contradiction`, and Testable, Decided, Bounded and Complete to `sufficiency`
- the conditions file keeps its two existing entries in Trigger / Gate / Instead form, the first under Grounded and the second under Testable, and states that the two-independent-observations bar applies to entries and not to the six conditions
- the conditions file states the reviewer's bounds: the reviewer checks each dependency record entry against the code and never accepts a claim because a citation is present; a record entry the code contradicts and a dependency the design relies on that the record omits are `groundedness` findings; a finding is a failed condition and never a preferred design; a `sufficiency` finding carries a demonstration; an unmentioned edge with no such case is not a finding; a behaviour stated as not constrained is closed
- the Grounded entry's evidence cites the 2026-11 spec-review findings as an observation independent of the September 2026 run
- the Codex execution notes in `skills/planning/codex-execution.md` give, beside the document review command, the checker prompt that names the material file and asks for the unmet conditions, and add no second `codex exec` command and no further place that names the Codex model
- the planning skill's Plan review section states that a behaviour a spec states as not constrained is not a requirement and is not listed in coverage
- the README's Brainstorming paragraph describes open input, conditions checked by a separate agent before approval, and a spec review that validates the author's dependency record
- the test file `tests/test_forge_docs.py` no longer contains `test_names_itself_the_tuning_surface`, `test_states_two_observation_bar`, `test_every_entry_has_trigger_gate_instead`, `test_schema_backed_entries_name_their_fields`, `test_spec_review_step_between_self_review_and_close_out`, `test_planning_does_not_begin_until_spec_review_passes`, `test_skill_carries_the_at_reference_form`, `test_skill_names_the_docreview_module`, `test_skill_states_the_invalid_verdict_retry_rule`, `test_step_8_names_the_packet_generation_command`, `test_step_8_states_the_reviewer_is_a_fresh_agent_prompted_with_the_packet_path`, `test_step_8_states_verdict_validation_and_nonzero_exit_meaning`, `test_step_8_states_the_caller_owns_the_single_retry`, `test_step_8_does_not_describe_tier_routing_rework_or_convergence`, `test_step_8_states_amendments_are_whole_document_not_scoped`, `test_codex_doc_review_section_names_no_claude_model`, `test_no_self_review_rule_is_stated_where_a_reviewer_is_started`, `test_both_review_steps_point_codex_at_the_document_review_section`, `test_agent_contracts_carry_the_break_the_code_step_and_no_read_only_claim`, `test_claude_loop_fingerprints_around_every_reviewer_spawn`, `test_claude_loop_hands_acceptance_results_by_json_path`, `test_close_out_gate_records_a_call_per_unverified_entry`, `test_codex_runner_paragraph_does_not_claim_read_only_reviewers` or `test_routing_table_check_catches_a_stale_model`, and no helper left unused by their removal
- the test `test_agreement_check_catches_a_field_removed_from_the_doc` still proves the field-agreement check fails when a field is removed from the conditions file, against the file's rewritten text
- the test `test_codex_doc_review_command_uses_the_runners_model_and_flags` compares the command's model, effort and isolation flags with `forge_common` and asserts no hard-coded flag strings
- no Gate sentence of the conditions file is restated in any other tracked file

**Tier:** standard

**Depends on:** nothing

### Task 2: Spec review takes and carries the dependency record
- [ ] Done

**Files:**
- Modify: `scripts/forge_docreview.py` (`--record`, record loading and validation, packet section)
- Test: `tests/test_forge_docreview.py`

**Spec:** [pipeline] Spec review, [execution] Document review contract

**Interface:**
- CLI: `forge_docreview.py --spec <path> --record <path> --out <path>` and `forge_docreview.py --spec <path> --record <path> --verdict <file>`; `--record` is required with `--spec` alone and rejected with `--plan`.
- `load_record(record_path, repo_root) -> list[dict]` — returns the record's `dependencies` entries; raises `RecordError` naming the entry and the cause.
- `build_packet(spec_path, repo_root, record) -> str` — the packet gains a `# Dependency record` section after `# Reference table`, one line per entry giving `symbol`, `file` and `behavior`, or the waiver reason when the record is empty.
- A `RecordError` is a contract error: exit 1, the message on stderr, no packet and no disposition written.

**Tests:**
- a spec packet build without `--record` exits 1 naming the missing record and writes no packet
- a verdict validation without `--record` exits 1 naming the missing record
- `--plan` with `--record` exits 1 naming `--record` as not taken by plan review
- a record file that is not valid JSON is a contract error naming the file
- a record that is not an object with `dependencies` and `waiver` keys is a contract error naming the missing key
- a record with empty `dependencies` and a null `waiver` is a contract error
- a record with empty `dependencies` and a non-null `waiver` loads, and the packet's Dependency record section states the waiver reason
- a record entry missing `symbol`, `file` or `behavior`, or carrying a blank one, is a contract error naming the entry and the field
- a record entry whose `file` is not tracked is a contract error naming the entry and the file
- a record entry whose `symbol` does not appear in its `file` is a contract error naming the entry and the symbol
- a record entry's `file` accepts the `path`, `path:N` and `path:N-M` forms
- a valid record's entries appear in the packet's Dependency record section, one per entry, after the Reference table section
- a record defect is reported before any packet is written, and a spec lint defect is still reported when the record is valid

**Acceptance:**
- `uv run --no-project --with pytest --python 3.13 python -m pytest -q tests/test_forge_docreview.py tests/test_forge_planreview.py tests/test_forge_docs.py` passes
- the record's `file` and `symbol` checks reuse the validation applied to `dependencies_read` entries and do not duplicate it

**Tier:** standard

**Depends on:** Task 1

### Task 3: The verdict covers the record, demonstrates sufficiency, and keeps a downgraded kind
- [ ] Done

**Files:**
- Modify: `scripts/forge_docreview.py` (`validate_verdict`, `dispose`, required-fields packet text)
- Test: `tests/test_forge_docreview.py`

**Spec:** [execution] Document review contract

**Interface:**
- `validate_verdict(..., record)` — `record` is the list `load_record` returns; a record entry is covered when some `dependencies_read` entry has the same `symbol` and the same `file` path with any `:N` or `:N-M` suffix removed from both.
- `findings[].demonstration` — string or null; required non-blank when `kind` is `sufficiency`.
- `dispose` — a `groundedness` finding downgraded for a missing or unresolvable citation is surfaced with `kind: "sufficiency"` and `original_kind: "groundedness"`.
- The packet's Required verdict fields section names `demonstration` and states the record-coverage rule.

**Tests:**
- a verdict whose `dependencies_read` has an entry for every record entry is valid
- a verdict missing a `dependencies_read` entry for one record entry is invalid, the defect naming the uncovered record entry
- a record entry is covered by a `dependencies_read` entry naming the same file with a different line suffix
- a record entry is not covered by a `dependencies_read` entry with the same symbol in a different file
- `dependencies_read` entries beyond the record are accepted
- with an empty record and a waiver, an empty `dependencies_read` still requires a non-null `dependencies_waiver`
- a `sufficiency` finding with no `demonstration`, a null one or a blank one is invalid, the defect naming the finding
- a `sufficiency` finding with a non-blank `demonstration` is valid
- a `groundedness` or `contradiction` finding with no `demonstration` is valid
- a `groundedness` finding with no valid citation and no `demonstration` validates, surfaces as `sufficiency`, and carries `original_kind` equal to `groundedness`
- a finding that was emitted as `sufficiency` carries no `original_kind` in the disposition output
- the packet's Required verdict fields section names `demonstration` and the record-coverage rule

**Acceptance:**
- `uv run --no-project --with pytest --python 3.13 python -m pytest -q tests/test_forge_docreview.py tests/test_forge_planreview.py tests/test_forge_docs.py` passes
- `python3 scripts/forge_lint.py --specs docs/forge/specs` passes

**Tier:** standard

**Depends on:** Task 2
