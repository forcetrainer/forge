# Adversarial Spec Review Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Build the cold spec-review gate — a mechanical reference table the reviewer owes dispositions on, a document-shaped verdict contract, and a single tuning surface for what the reviewer hunts.
**Architecture:** One module, `scripts/forge_docreview.py`, owns the reference table, the review packet, verdict validation and disposition — the same shape as `forge_dispose.py` owning the code-review decision, with a CLI so the Claude orchestrator reaches identical logic. The reviewer's guidance lives in one document loaded by reference, never inlined.
**Tech stack:** Python 3 standard library. Existing pytest suite.
**Global Constraints:**
- Python 3 standard library only; no new dependency enters the plugin (constraint: `stdlib-only`).
- No file outside `skills/brainstorming/design-anti-patterns.md` restates an anti-pattern Gate; the doc is loaded by reference, never inlined.
- Spec review's reference table is never a lint rule; `lint_living_spec` keeps exactly five rules.
- Resolution and validation failures raise or exit non-zero naming the cause; a tool's fatal error is never collapsed into a legal negative result (constraint: `parsers-fail-loud`).

### Task 1: Reference table
- [ ] Done

**Files:**
- Create: `scripts/forge_docreview.py` (extraction, classification, resolution)
- Test: `tests/test_forge_docreview.py`

**Spec:** Spec review

**Interface:** `extract_references(spec_text)` returns an ordered, de-duplicated list of `Reference(ref, shape, resolved, found_at)`. `shape` is `"path"`, `"symbol"` or `"other"`. A backticked span is `path` when it contains `/` or ends in an extension carried by some file in `git ls-files` — the extension set is derived from the repo at call time, never hardcoded. It is `symbol` when it matches an identifier, dotted name, or `name()` form. Everything else is `other` and is dropped from the table. `resolved` is True when a `path` names an existing tracked file or directory, or a `symbol` is found in a tracked file; `found_at` names where, or is None. `reference_table(spec_text)` returns only the `path` and `symbol` entries. Fenced regions are excluded from extraction via `extract-brief.fence_mask`.

**Tests:**
- a backticked span containing a slash is classified path
- a backticked span ending in an extension present in the repo is classified path
- a backticked span ending in an extension absent from the repo is not classified path
- a flag such as an inline-code double-dash token is classified other and dropped
- a constraint id such as an inline-code kebab token is classified other and dropped
- an identifier present in a tracked file resolves and reports where it was found
- an identifier absent from every tracked file does not resolve
- a path naming an existing directory resolves
- a path naming a file that does not exist does not resolve
- a reference appearing twice yields one table entry
- a backticked span inside a fenced block is not extracted
- the extension set is derived from the repo, not a literal list in the source

**Acceptance:**
- `python3 -m pytest tests/test_forge_docreview.py -q` passes with no skips introduced by this task
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `python3 -c "import sys; sys.path.insert(0,'scripts'); import forge_docreview as d; t=d.reference_table(open('docs/forge/specs/pipeline.md').read()); print(len(t)); assert t, 'empty table'"` — the table is non-empty on a real spec
- `! grep -nE "^[A-Z_]*EXT[A-Z_]* *= *[({\[]" scripts/forge_docreview.py` — no hardcoded extension set

**Tier:** standard

**Depends on:** nothing.

### Task 2: Verdict validation and disposition
- [ ] Done

**Files:**
- Modify: `scripts/forge_docreview.py` (schema validation, citation validation, disposition)
- Read: `docs/forge/specs/execution.md` section "Document review contract" — the authoritative schema for this task
- Test: `tests/test_forge_docreview.py`

**Spec:** Spec review

**Interface:** the schema this task validates is defined in `docs/forge/specs/execution.md` under "Document review contract"; read it before writing anything. `validate_verdict(verdict, unresolved_refs, repo_root)` returns `VerdictResult(valid, defects, findings)`, raising nothing — defects are returned, not thrown, so all are reported in one pass. It enforces: one `references` entry per unresolved ref and no entry naming a ref outside that set; `dependencies_read` non-empty or a non-null `dependencies_waiver`; `replaced_system` present with a boolean `applies` — a missing block, a missing `applies`, or a non-boolean one is a defect naming the field — then False forbidding `guarantees` and True requiring a non-empty list; every finding carrying `id`, `summary`, `kind`, `section`, `evidence`, `proposed_amendment`. `validate_citation(citation, repo_root)` is True when the `<file>:<line>` form names an existing file and a line within it. `dispose(findings, repo_root)` returns `Disposition(amend, surface)` — `kind == "groundedness"` with a valid citation goes to `amend`; a groundedness finding whose citation is absent or unresolvable is rewritten to `kind == "sufficiency"` and goes to `surface`; `sufficiency` and `contradiction` go to `surface`.

**Tests:**
- a verdict missing a references entry for an unresolved ref is invalid and names the ref
- a verdict naming a ref outside the unresolved set is invalid and names the ref
- an unknown disposition value on a references entry is invalid
- an empty dependencies_read with no waiver is invalid
- an empty dependencies_read with a waiver is valid
- replaced_system applies false with a non-empty guarantees list is invalid
- replaced_system applies true with an empty guarantees list is invalid
- a finding missing proposed_amendment is invalid
- an unknown finding kind is invalid
- every defect in one verdict is reported in a single pass, not just the first
- a groundedness finding with a citation resolving to a real file and line disposes to amend
- a groundedness finding whose citation names a missing file downgrades to sufficiency and surfaces
- a groundedness finding whose citation names a line past end of file downgrades to sufficiency and surfaces
- a groundedness finding with a null citation downgrades to sufficiency and surfaces
- a sufficiency finding surfaces and is never auto-amended
- a contradiction finding surfaces and is never auto-amended

**Acceptance:**
- `python3 -m pytest tests/test_forge_docreview.py -q` passes with no skips introduced by this task
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task

**Tier:** standard

**Depends on:** nothing.

### Task 3: Packet build and CLI
- [ ] Done

**Files:**
- Modify: `scripts/forge_docreview.py` (packet assembly, argument parsing, exit codes)
- Test: `tests/test_forge_docreview.py`

**Spec:** Spec review

**Interface:** `build_packet(spec_path, sections=None)` returns the reviewer packet as text: the spec (whole, or the named sections plus the full document as context when `sections` is given), the reference table with each entry's resolution state, the `@design-anti-patterns.md` pointer as a path reference rather than inlined text, and the required-field list the verdict must satisfy. A scoped packet states that the whole-document contradiction question applies regardless of scope. The CLI takes `--spec`, optional repeatable `--section`, `--verdict`, `--repo-root` and `--out`; with `--verdict` it validates and disposes, writing a decision JSON carrying `valid`, `defects`, `amend` and `surface`; without it, it emits the packet. Exit status is 0 on a valid verdict or a successful packet build, non-zero on an invalid verdict or a missing spec.

**Tests:**
- a packet built without sections contains the whole spec
- a scoped packet contains the named sections and still carries the full document as context
- a scoped packet states that the contradiction question applies to the whole document
- the packet references the anti-patterns doc by path and does not inline a Gate line
- the packet lists every unresolved reference the reviewer owes a disposition on
- the packet states the required verdict fields
- the CLI without a verdict emits a packet and exits zero
- the CLI with a valid verdict writes a decision file and exits zero
- the CLI with an invalid verdict exits non-zero and names the defect
- the CLI with a missing spec path exits non-zero naming the path

**Acceptance:**
- `python3 -m pytest tests/test_forge_docreview.py -q` passes with no skips introduced by this task
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `python3 scripts/forge_docreview.py --spec docs/forge/specs/pipeline.md` exits 0 and emits a packet
- `python3 scripts/forge_docreview.py --spec docs/forge/specs/does-not-exist.md; [ $? -ne 0 ]` — a missing spec exits non-zero

**Tier:** standard

**Depends on:** Task 1, Task 2.

### Task 4: The hunting list
- [ ] Done

**Files:**
- Create: `skills/brainstorming/design-anti-patterns.md`
- Test: `tests/test_forge_docs.py`

**Spec:** Spec review

**Interface:** the document opens with a header stating that it is the single tuning surface for document review, the Trigger / Gate / Instead format, and the two-independent-observations bar for adding an entry. Two entries: designing from assumption instead of from the code, carrying gates for an unread dependency's side effects, a claim inferred rather than read, and what a replaced mechanism recomputes each pass; and a criterion a conforming-but-wrong implementation would satisfy. Each entry backed by a required verdict field is marked with the field name, and the marking states that deleting the entry does not remove the field.

**Tests:**
- the document names itself the tuning surface for document review
- the document states the two-observation bar for adding an entry
- every entry carries a Trigger, a Gate and an Instead line
- the entries backed by required verdict fields name those fields
- no file outside the document contains an entry's Gate text

**Acceptance:**
- `python3 -m pytest tests/test_forge_docs.py -q` passes with no skips introduced by this task
- `[ "$(grep -c '^\*\*Gate:\*\*' skills/brainstorming/design-anti-patterns.md)" -eq 2 ]` — one Gate per entry
- `[ "$(grep -c '^\*\*Trigger:\*\*' skills/brainstorming/design-anti-patterns.md)" -eq 2 ]`
- `[ "$(grep -c '^\*\*Instead:\*\*' skills/brainstorming/design-anti-patterns.md)" -eq 2 ]`
- `grep -q 'dependencies_read' skills/brainstorming/design-anti-patterns.md` — the schema-backed entry names its field
- `grep -q 'replaced_system' skills/brainstorming/design-anti-patterns.md`

**Tier:** standard

**Depends on:** nothing.

### Task 5: Wire the gate into the flow
- [ ] Done

**Files:**
- Modify: `skills/brainstorming/SKILL.md` (spec review step between self-review and close-out; `@design-anti-patterns.md` reference)
- Test: `tests/test_forge_docs.py`

**Spec:** Spec review, Brainstorming flow contracts, Lint

**Interface:** the skill gains a spec-review step after self-review and before close-out, stating that planning does not begin until it passes, that discovery is a cold reviewer and a re-review after amendment is a verification lap, and that an amendment re-enters scoped to its changed sections. It names `scripts/forge_docreview.py` as the mechanism and carries the `@design-anti-patterns.md` reference form. A test asserts the doc's schema-backed markings and the module's required-field list still agree. The skill states the invalid-verdict rule: one retry naming the specific defect, then a contract error — the retry belongs to the caller, not the module, matching the reviewer verdict contract's existing behavior.

**Tests:**
- the skill places spec review after self-review and before close-out
- the skill states that planning does not begin until spec review passes
- the skill carries the at-reference form for the anti-patterns doc
- the skill names the docreview module
- the anti-patterns doc's marked fields and the module's required-field list agree
- the agreement test fails when a marked field is removed from the doc
- lint_living_spec still exposes exactly five rules
- the skill states that an invalid verdict gets one retry and then is a contract error

**Acceptance:**
- `python3 -m pytest tests/test_forge_docs.py -q` passes with no skips introduced by this task
- `python3 -m pytest tests/ -q` passes with no skips introduced by this task
- `grep -q '@design-anti-patterns.md' skills/brainstorming/SKILL.md` — the reference form is present
- `! grep -rn 'Trigger:' skills/brainstorming/SKILL.md` — no Gate or entry text is inlined into the skill
- `python3 scripts/forge_lint.py --specs` exits 0

**Tier:** standard

**Depends on:** Task 3, Task 4.
