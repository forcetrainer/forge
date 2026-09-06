# Retire ROADMAP.md — Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Replace ROADMAP.md with GitHub issues carrying native sub-issue and blocked-by edges, add audit-issues, and remove every live roadmap read path.
**Architecture:** Two new record types (`program`, `phase`) join the existing schema table in `scripts/forge_memory.py`; `GitHubStore` gains the read and edge primitives they need and stops being write-only. `audit-issues` is a read-only reporter over open issues. Everything else is a documentation sweep plus the hook sentences and their pinning tests.
**Tech stack:** Python 3 stdlib, `gh` CLI (`gh issue`, `gh api`), unittest with `subprocess.run` stubbed.
**Global Constraints:** stdlib only — no third-party dependency enters the plugin. Parsers fail loud: name the cause, never guess intent, never fall back to a default. No version bump in this phase.

### Task 1: GitHub store — kind labels, issue reads, hierarchy edges
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory_store.py` (`GitHubStore` gains kind-label support, three read/edge primitives, and an open-issue listing; `select_store` routes the two new types; the class docstring's "write-only" claim is corrected)
- Test: `tests/test_forge_memory.py`

**Spec:** Programs and phases, audit-issues

**Interface:**
```python
class GitHubStore(Store):
    KIND_LABELS = ("feature", "defect", "debt", "risk")
    def create(self, record, by=None, kind=None): ...   # kind label applied only when given
    def issue_title(self, number): ...                  # -> str
    def sub_issues(self, epic): ...                     # -> list[dict] with "number", in insertion order
    def attach_sub_issue(self, epic, number): ...       # POST issues/<epic>/sub_issues
    def add_blocked_by(self, number, blocker): ...      # POST issues/<n>/dependencies/blocked_by
    def open_issues(self): ...                          # -> list[dict] with "number", "title", "labels"
```

- `sub_issues` and `open_issues` read **structured** `--json` output, never the text of a `gh` message.
- `open_issues` passes an explicit high `--limit`, reusing the `_LABEL_LIST_LIMIT` rationale; `gh`'s default of 30 is never relied on.
- The `sub_issues` and `blocked_by` endpoints take an issue **id**, not a number; resolve the id through the same structured read rather than assuming number == id.
- `create` applies at most one kind label and validates it against `KIND_LABELS`; `defer` passes no kind and its behavior is unchanged.
- `select_store` returns `GitHubStore` for `program` and `phase` unconditionally — `docs/forge/config.json` is not consulted for either, and no file-store fallback exists.

**Tests:**
- `create` with `kind="debt"` applies both the origin and the kind label; with no kind, exactly one label as today.
- `create` with an unknown kind raises before any network call.
- `issue_title` returns the title from structured output.
- `sub_issues` returns numbers in insertion order; an epic with none returns `[]`.
- `attach_sub_issue` and `add_blocked_by` each issue one `gh api` POST against the resolved issue id.
- A non-zero `gh` return from any new primitive raises through `_raise_for_gh_failure`, naming the failure.
- `open_issues` passes an explicit limit and parses number, title, and label names.
- `select_store` returns `GitHubStore` for `program` and `phase` even when `config.json` requests the file store for deferrals.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `grep -n "Write-only" scripts/forge_memory_store.py` returns nothing.

**Tier:** standard

**Depends on:** nothing.

### Task 2: add-program and add-phase
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (`SCHEMA` gains `program` and `phase`; `cmd_add_program`, `cmd_add_phase`, their parsers)
- Test: `tests/test_forge_memory.py`

**Spec:** Programs and phases, Partial failure

**Interface:**
```python
SCHEMA["program"] = [FieldSpec("name", 80, True, None), FieldSpec("why", 300, True, None)]
SCHEMA["phase"]   = [FieldSpec("title", 80, True, None), FieldSpec("why", 300, True, None)]

def cmd_add_program(args, repo_root): ...
def cmd_add_phase(args, repo_root): ...
```
CLI:
```
add-program --name <str> --why <str> --kind feature|defect|debt|risk
add-phase --epic <n> --seq <int> --of <int> --title <str> --why <str> --kind <…>
```

- `add-program` files the epic with its title set to `--name` verbatim and prints the issue number alone on the last stdout line.
- `add-phase` renders the issue title as `<program name> <seq>/<of>: <title>`, reading the program name from `issue_title(epic)`. No code parses a sequence back out of a title.
- Order of operations: validate → `sub_issues(epic)` → `seq` check → create issue → `attach_sub_issue` → `add_blocked_by` (skipped when `seq == 1`, otherwise the blocker is the last entry of the pre-read `sub_issues`).
- `seq != len(sub_issues) + 1` exits non-zero naming both the given `seq` and the expected one; no issue is created.
- `--of` is rendering only; it is not validated against sibling titles.
- Origin is `by:human` unconditionally on both commands — no `--by` flag.
- A failure after issue creation exits non-zero naming the failed call, the created issue number, and which edges landed.

**Tests:**
- `add-program` prints a parseable issue number as its last stdout line and applies the kind and origin labels.
- `add-phase --seq 1 --of 5` renders `"<name> 1/5: <title>"` and files no blocked-by edge.
- `add-phase --seq 2` blocks on the epic's existing last sub-issue.
- `add-phase` with `seq` disagreeing with the sub-issue count exits non-zero naming both numbers and creates no issue.
- An over-budget `--title` or `--why` is rejected before any network call on both commands.
- Missing `--kind` exits non-zero on both commands; no kind is inferred.
- A failing `attach_sub_issue` after creation exits non-zero and reports the created number.
- The subcommand-surface test lists `add-program` and `add-phase` with their exact flag sets.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 scripts/forge_memory.py add-phase --help` lists `--epic --seq --of --title --why --kind` and no `--by`.

**Tier:** standard

**Depends on:** Task 1.

### Task 3: audit-issues
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (`cmd_audit_issues` and its parser)
- Test: `tests/test_forge_memory.py`

**Spec:** audit-issues

**Interface:**
```python
RETIRED_LABELS = ("forge:deferral", "forge:backlog", "via:reported", "via:implementation")
def cmd_audit_issues(args, repo_root): ...   # CLI: audit-issues  (no flags)
```

- Read-only: no `gh` write call, no label mutation, on any path.
- Per open issue, reports: kind-label count ≠ 1, origin-label count ≠ 1, each retired label present.
- One line per offending issue — number, title, failed checks. Exit 1 when any offender exists; otherwise one all-clear line and exit 0.
- Closed issues are not read.

**Tests:**
- An issue with no kind label is reported; with two kind labels, reported; with exactly one, not.
- A missing origin label is reported.
- Each retired label is reported by name.
- One issue failing several checks produces one line naming all of them.
- A clean issue set exits 0 with a single all-clear line.
- Any offender exits 1.
- No `gh` write subcommand (`issue edit`, `label`, `issue close`) is invoked on any path.
- More than 30 open issues are all examined.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 scripts/forge_memory.py audit-issues` against this repo exits 0 or names real offenders.

**Tier:** standard

**Depends on:** Task 1.

### Task 4: rename resolve-deferral to resolve
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (`cmd_resolve_deferral` → `cmd_resolve`, parser name)
- Modify: `hooks/guard-memory-writes` (the subcommand named in the deny message)
- Modify: `skills/project-memory/SKILL.md` (the example invocation)
- Modify: `docs/forge/specs/2026-09-05-project-memory-engine-design.md` (CLI list, plus a dated changelog line recording the rename)
- Test: `tests/test_forge_memory.py`

**Spec:** Status is not forge's

**Interface:** `resolve --ref <issue-number|slug> --reason <text>` — same flags, same behavior against both stores. No alias for the old name.

**Tests:** existing `resolve-deferral` tests move to `resolve` unchanged in behavior; the subcommand-surface test shows `resolve` and no `resolve-deferral`.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `grep -rn "resolve-deferral" --exclude-dir=.git --exclude-dir=archive --exclude-dir=plans .` returns nothing.

**Tier:** trivial — one subcommand name, three literal call sites and a docstring; no logic or flag changes.

**Depends on:** nothing.

### Task 5: session-start hook stops naming the roadmap
- [ ] Done

**Files:**
- Modify: `hooks/session-start` (the `flow` sentence on the current path, line 34, and the legacy path, line 38)
- Test: `tests/test_forge_memory.py` (the two pinned sentence constants around line 2730)

**Spec:** Status is not forge's

**Interface:** the roadmap clause on both paths becomes exactly:
`Open GitHub issues are the work backlog; check them for the current phase.`
The rest of each sentence — the flow description, and the legacy path's migration nudge — is unchanged.

**Tests:**
- The current-path context contains the new sentence and no occurrence of `ROADMAP`.
- The legacy-path context contains the new sentence, still carries the migration nudge, and contains no occurrence of `ROADMAP`.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `grep -c ROADMAP hooks/session-start` returns 0.

**Tier:** trivial — a fixed replacement sentence supplied verbatim, applied to two string literals and their two assertions.

**Depends on:** nothing.

### Task 6: archive the roadmap and sweep the docs
- [ ] Done

**Files:**
- Modify: `docs/forge/ROADMAP.md` → `docs/forge/archive/ROADMAP.md` via `git mv`, with the archive header
- Modify: `skills/project-memory/SKILL.md` (`## ROADMAP.md` section → `## Programs and phases`; frontmatter `description`; opening paragraph; Legacy section gains the unread-roadmap sentence)
- Modify: `skills/brainstorming/SKILL.md` (steps 1 and 2; close-out reconciliation)
- Modify: `skills/planning/SKILL.md` (close-out closes the phase issue; no `in-progress`)
- Modify: `skills/planning/codex-execution.md` (doc-sync no longer reconciles ROADMAP status)
- Modify: `scripts/forge-run.py` (the two doc-sync prompt strings, ~lines 1372 and 1490)
- Modify: `README.md` (project-memory paragraph ~119-122; skills table row ~162)

**Spec:** Migration, Documentation and skill changes, Brainstorming reconciliation, Status is not forge's

**Interface:** none. Content contracts:
- Archive header matches `docs/forge/archive/DECISIONS.md`'s form — historical, not authoritative, no new entries — plus a pointer that phases are GitHub issues now.
- The skill's new section documents `add-program` / `add-phase`, the `<program> <seq>/<of>: <title>` grammar, the two native edges, required `--kind`, unconditional `by:human`, and that open/closed is the only status forge reads.
- Brainstorming step 1 reads open issues (and the epic when the work belongs to a program) instead of ROADMAP.md; step 2 records a decomposition through the two commands; close-out presents the addresses/obsoletes/discovers reconciliation for approval and never closes an issue unattended.
- Planning's close-out closes the phase issue with a reason and marks nothing `in-progress`.

**Tests:** none — prose. Verification is the acceptance greps below.

**Acceptance:** `test -f docs/forge/archive/ROADMAP.md && test ! -e docs/forge/ROADMAP.md`; `grep -rn "ROADMAP" --exclude-dir=.git --exclude-dir=archive --exclude-dir=plans --exclude-dir=specs --exclude-dir=ideas --exclude-dir=__pycache__ .` returns nothing; `grep -n "add-program" skills/project-memory/SKILL.md` and `grep -n "add-phase" skills/brainstorming/SKILL.md` each return a line; `grep -rn "in-progress" skills/planning/SKILL.md` returns nothing; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 2, Task 3, Task 4.

### Task 7: dogfood — file the structured-memory program
- [ ] Done

**Files:** none in the repo. Live GitHub state only.

**Spec:** Migration, Acceptance

**Interface:**
- `add-program --name "structured memory" --why <…> --kind debt` creates the epic.
- #43–#47 already exist, so they are adopted by hand: `gh issue edit <n> --title "structured memory <seq>/5: <title>"`, then `gh api` for the sub-issue attachment and the blocked-by chain. `add-phase` files new phases and never adopts existing ones.
- Issue #46 is closed at close-out with a reason, per the new planning canon.

**Tests:** none — live repo state.

**Acceptance:** the epic's `sub_issues` lists #43–#47 in order; each of #44–#47 reports its predecessor under `dependencies/blocked_by`; all five titles match the grammar; `python3 scripts/forge_memory.py audit-issues` exits 0.

**Tier:** trivial — a fixed sequence of `gh` calls against known issue numbers, no design content.

**Depends on:** Task 2.

**Note:** this task mutates live GitHub issues in a shared repo. It is run by the orchestrator with the user present, never dispatched to a worker, and the exact `gh` commands are shown before any of them run.
