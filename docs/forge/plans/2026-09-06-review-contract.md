# Review Contract Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Make a task's review contract the task's own promises, so the reviewer stops manufacturing findings from spec sections it was never told the task's share of.
**Architecture:** `forge_checklist.py` stops sourcing spec sections for task checklists and gains a `Tests:` source parsed by `extract-brief.py`; `forge_dispose.py` gains an `unverifiable` value that dispositions to `seed` and a `contract_ref` membership check; `forge_lint.py` gains a git-backed rule requiring every changed spec section to be claimed by a task, and a grammar check for `**Tests:**`.
**Tech stack:** Python 3 standard library, pytest.
**Global Constraints:** scripts use the Python 3 standard library only; parsers raise on malformed input naming the cause and the line, never falling back to a default.

### Task 1: Tests-line parser
- [x] Done — passed, 2 attempt(s)

**Files:**
- Modify: `scripts/extract-brief.py` (add `parse_test_cases`, sibling to `parse_spec_names`)
- Test: `tests/test_extract_brief.py`

**Interface:** `parse_test_cases(task_block) -> list[str]` — one entry per `-` bullet under the task's `**Tests:**` marker, in document order. The only legal form is the marker alone on its line followed by `-` bullets, the block ending at the first blank line or next `**Field:**`. `**Tests:** none — <reason>` on one line returns `[]`. An absent field returns `[]`. Fence-masked like `parse_spec_names`.

**Tests:**
- returns one entry per bullet for a multi-case block, in document order
- stops at the first blank line, not at the end of the task block
- stops at the next `**Field:**` marker when no blank line intervenes
- returns an empty list when the `**Tests:**` field is absent
- returns an empty list for `**Tests:** none — prose`
- returns an empty list for `none` with any trailing reason
- ignores a `**Tests:**` marker inside a fenced code block
- raises naming the line for the inline joined form `**Tests:** a; b; c`
- raises naming the line for a `**Tests:**` marker followed by neither bullets nor `none`
- preserves a `;` inside a single bullet as literal text rather than splitting on it

**Acceptance:** `python3 -m pytest -q tests/test_extract_brief.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** nothing.

### Task 2: Task checklist drops spec sections, gains tests
- [x] Done — passed, 1 attempt(s)

**Files:**
- Modify: `scripts/forge_checklist.py` (add `_test_items`; `build_task_checklist` drops `_spec_items`; `build_final_checklist` unchanged)
- Test: `tests/test_forge_checklist.py`

**Spec:** Contract checklist

**Interface:** `_test_items(task_block, task_number) -> list[ChecklistItem]` with `id` of form `t<N>.t<M>`, 1-based, and `source` of `"tests"`.

**Tests:**
- a task checklist contains no `spec:` item even when the task declares `**Spec:**`
- a task checklist contains one `t<N>.t<M>` item per test case
- test item ids are 1-based and follow document order
- a task checklist still contains its `g<N>` global-constraint items
- a task checklist still contains its `t<N>.a<M>` acceptance items
- `build_final_checklist` still contains `spec:` items for every task's declared sections
- a task with no tests, no globals and command-only acceptance raises the existing empty-checklist error

**Acceptance:** `python3 -m pytest -q tests/test_forge_checklist.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 1, Task 8, Task 10.

### Task 3: Unverifiable impact dispositions to seed
- [x] Done — passed, 1 attempt(s)

**Files:**
- Modify: `scripts/forge_dispose.py` (accept `unverifiable` on finding `impact` and coverage `status`; `derive_disposition` routes it; `validate_coverage` exempts it from the backing-finding rule)
- Test: `tests/test_forge_dispose.py`

**Spec:** The disposition matrix, Reviewer verdict contract

**Interface:** `derive_disposition(finding)` returns `"seed"` for any finding whose `impact` is `unverifiable`, at every provenance value.

**Tests:**
- an unverifiable finding dispositions to seed when in-diff
- an unverifiable finding dispositions to seed when in-run
- an unverifiable finding dispositions to seed when pre-existing
- an unverifiable finding with a null `contract_ref` still dispositions to seed
- a coverage entry with status `unverifiable` and non-empty evidence is valid with no backing finding
- a coverage entry with status `unverifiable` and empty evidence is a defect
- a `violated` entry with no backing finding remains a defect

**Acceptance:** `python3 -m pytest -q tests/test_forge_dispose.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** nothing.

### Task 4: contract_ref must name a checklist id
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (add `validate_contract_refs`)
- Modify: `scripts/forge-run.py` (`_verdict_defects` calls it when a checklist is present)
- Test: `tests/test_forge_coverage.py`

**Spec:** Reviewer verdict contract

**Interface:** `validate_contract_refs(verdict, checklist) -> list[str]` — one human-readable defect string per finding whose non-null `contract_ref` is not a checklist id. Returns `[]` when `checklist` is falsy.

**Tests:**
- a finding citing a real checklist id yields no defect
- a finding citing an unknown string yields a defect naming the finding id and the bad ref
- a finding with a null `contract_ref` yields no defect
- an empty checklist yields no defects regardless of refs
- the defect routes through the existing one-retry-then-contract-error path rather than raising directly
- a contract-breaking finding whose ref is rejected downgrades to improvement at disposition

**Acceptance:** `python3 -m pytest -q tests/test_forge_coverage.py tests/test_forge_dispose.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 3.

### Task 5: Verdict instruction text
- [ ] Done

**Files:**
- Modify: `scripts/forge_common.py` (`REVIEW_VERDICT_INSTRUCTION`)
- Test: `tests/test_forge_review.py`

**Spec:** Reviewer verdict contract

**Interface:** no signature change; the instruction string gains the `unverifiable` status and impact values, states that `contract_ref` must name a checklist id supplied in the packet, states that `unverifiable` requires a reason but obliges no backing finding, and states that every finding is reported regardless of provenance — the runner derives the disposition, so a reviewer never withholds a finding on the grounds that the code predates this diff.

**Tests:**
- the instruction names `unverifiable` as a coverage status
- the instruction names `unverifiable` as an impact value
- the instruction states the checklist-id requirement for `contract_ref`
- the instruction states that unverifiable obliges no backing finding
- the instruction no longer describes `contract_ref` as any acceptance criterion or spec section
- the instruction states that findings are reported regardless of provenance

**Acceptance:** `python3 -m pytest -q tests/test_forge_review.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 4.

### Task 6: Plan lint requires changed spec sections to be claimed
- [ ] Done

**Files:**
- Modify: `scripts/forge_lint.py` (add the changed-section mapping check to `lint_plan`)
- Test: `tests/test_forge_lint.py`

**Spec:** Plan lint

**Interface:** the check reads the spec file's committed version via `git show HEAD:<path>` from `repo_root`, compares section bodies by heading, and emits one `error` defect per changed section named by no task's `**Spec:**` line. `## Changelog` is exempt. A spec with no committed version treats every section as changed. A spec path outside a git repo, or a git read that fails, emits no defect.

**Tests:**
- a changed section claimed by a task yields no defect
- a changed section claimed by no task yields an error defect naming the section
- an unchanged section claimed by no task yields no defect
- a changed `## Changelog` yields no defect
- a spec with no committed version requires every non-changelog section to be claimed
- a spec outside a git repo yields no defect
- the defect carries severity `error`
- the check reports every unclaimed section in one run, not the first only

**Acceptance:** `python3 -m pytest -q tests/test_forge_lint.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** complex — determining which sections of a markdown document changed requires choosing a comparison unit (heading-keyed body text) that stays stable under reflow and renamed headings, and that choice decides whether the rule cries wolf or misses real gaps.

**Depends on:** nothing.

### Task 7: Agent contracts and skill prose parity
- [ ] Done

**Files:**
- Modify: `agents/forge-standard.md` (review paragraph points at the schema value)
- Modify: `agents/forge-deep.md` (same)
- Modify: `skills/planning/SKILL.md` (Reviewer verdict contract, Coverage checklist generation, disposition matrix table)
- Modify: `skills/planning/codex-execution.md` (Disposition matrix, Coverage checklist paragraphs)

**Spec:** Reviewer verdict contract, The disposition matrix, Contract checklist

**Interface:** no code. The two agent contracts replace the bare `"Can't verify from diff" is a valid verdict` sentence with one naming `impact: "unverifiable"` as where that answer goes, and extend `Report every finding with a severity; do not silently fix anything.` to say that this holds regardless of provenance — the runner decides the disposition. The two skill documents restate the amended checklist sources, the `contract_ref` membership rule, and the matrix's `unverifiable` column.

**Tests:** none — prose.

**Acceptance:** `grep -c 'unverifiable' agents/forge-standard.md agents/forge-deep.md skills/planning/SKILL.md skills/planning/codex-execution.md` reports a non-zero count for each of the four files; `grep -c 'provenance' agents/forge-standard.md agents/forge-deep.md` reports a non-zero count for each; `grep -n 'spec section named on a' skills/planning/SKILL.md` returns nothing, confirming the task-checklist source list was updated; `python3 scripts/forge_lint.py --specs` exits zero; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 5.

### Task 8: Review packet carries spec context
- [x] Done — passed, 1 attempt(s)

**Files:**
- Modify: `scripts/review-packet.py` (`build_packet` gains a spec-context section)
- Modify: `scripts/forge_git.py` (`_packet_for` resolves the task's spec sections and passes them)
- Modify: `scripts/forge-run.py` (`execute_task` passes `spec_path` into `_packet_for`)
- Test: `tests/test_review_packet.py`

**Spec:** Contract checklist

**Interface:** `build_packet(task_block, base, diff_output, prior_findings=None, checklist=None, review_kind=None, spec_sections=None)` — `spec_sections` is a list of `(heading, body)` pairs rendered as a `## Spec context` section, omitted entirely when None or empty. `_packet_for(task, plan_path, run_dir, base, cwd, prior_findings=None, checklist=None, spec_path=None)` resolves the task's `**Spec:**` names via `find_spec_sections` and passes them.

**Tests:**
- a packet built with spec sections contains a `## Spec context` heading
- the rendered section carries each named section's heading and body
- a packet built with no spec sections contains no `## Spec context` heading
- the CLI's output is unchanged when no spec sections are supplied
- `_packet_for` with a `spec_path` and a task declaring `**Spec:**` produces a packet containing the section body
- `_packet_for` with a task declaring no `**Spec:**` produces a packet with no spec-context section
- the verification packet is unaffected

**Acceptance:** `python3 -m pytest -q tests/test_review_packet.py tests/test_forge_verification_packet.py` passes; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** nothing.

### Task 9: Plan lint checks the Tests grammar
- [ ] Done

**Files:**
- Modify: `scripts/forge_lint.py` (add the `**Tests:**` grammar check to `lint_plan`)
- Test: `tests/test_forge_lint.py`

**Spec:** Plan lint

**Interface:** for every task, the check calls `parse_test_cases` and converts its raise into an `error` defect naming the task and the offending line. A task with no `**Tests:**` field is legal and yields no defect.

**Tests:**
- a task using the bulleted form yields no defect
- a task using `none — <reason>` yields no defect
- a task with no `**Tests:**` field yields no defect
- a task using the inline joined form yields an error defect naming the task
- a task whose `**Tests:**` marker is followed by neither bullets nor `none` yields an error defect
- every offending task is reported in one run, not the first only
- the defect carries severity `error`

**Acceptance:** `python3 -m pytest -q tests/test_forge_lint.py` passes; `python3 scripts/forge_lint.py docs/forge/plans/2026-09-06-review-contract.md --spec docs/forge/specs/execution.md` exits zero; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 1.

### Task 10: Owned lint fixtures replace live plan documents
- [x] Done — passed, 1 attempt(s)

**Files:**
- Create: `tests/fixtures/plans/legacy-dispatch-parity.md`
- Create: `tests/fixtures/specs/legacy-dispatch-parity-design.md`
- Create: `tests/fixtures/plans/legacy-halt-precision.md`
- Create: `tests/fixtures/specs/legacy-halt-precision-design.md`
- Modify: `tests/test_forge_lint.py` (the two real-document tests read the fixtures instead)

**Spec:** Plan lint

**Interface:** no code. `test_real_phase12b_plan_lints_clean` and `test_real_phase14_plan_lints_clean` read `tests/fixtures/`, not `docs/forge/plans/` or `docs/forge/archive/specs/`. Each fixture is derived from the document it replaces, trimmed to what the test exercises, and conforms to current plan and spec grammar — including the bulleted `**Tests:**` form.

**Tests:**
- each fixture plan lints clean against its fixture spec
- a fixture plan with one task's `**Tier:**` justification removed produces an error defect, proving the fixture still exercises real lint rules
- no test in the suite reads a path under `docs/forge/plans/` or `docs/forge/archive/`
- each fixture plan carries more than one task, so multi-task lint paths stay exercised

**Acceptance:** `python3 -m pytest -q tests/test_forge_lint.py` passes; `grep -rn 'REPO_ROOT, "docs/' tests/` returns nothing; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** nothing.
