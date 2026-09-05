# Project Memory Engine Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Ship a schema-driven record engine that makes project-memory drift structurally impossible, with GitHub issues as the deferral store and a budgeted constraints file.
**Architecture:** `forge_memory.py` owns record semantics — schema table, validation, canonical render/parse, `fmt` — and the CLI. `forge_memory_store.py` owns persistence behind one interface with GitHub and file implementations. Four enforcement layers wrap them: CLI composition, a `PreToolUse` deny hook, `fmt --check` in `forge_lint.py` plus a pre-commit hook, and a CI workflow.
**Tech stack:** Python 3 stdlib only (argparse, dataclasses, json, re, subprocess); `gh` CLI for the GitHub store; pytest; bash for hooks.
**Global Constraints:** No third-party dependencies. Budgets, field lists, and rendering are code, never config. Every store failure is loud and names its fix — never a silent fallback. Tests stub `gh`; no network.

### Task 1: Record engine
- [ ] Done

**Files:**
- Create: `scripts/forge_memory.py`
- Test: `tests/test_forge_memory.py`

**Spec:** Motivating defect, Records, Rendering

**Interface:**
```python
FieldSpec  # dataclass: name, budget:int|None, required:bool, form:str|None
SCHEMA: dict[str, list[FieldSpec]]   # "constraint", "deferral"

@dataclass
class Record:
    type: str
    fields: dict[str, str]

class SchemaError(Exception): ...            # loud; message names field and limit

def validate(record: Record) -> list[str]   # every defect, never the first only
def render(record: Record) -> str
def parse(text: str, type: str) -> list[Record]
def fmt_check(paths: list[str]) -> list[str]   # every defect in one pass
def fmt_write(paths: list[str]) -> None
```

**Tests:**
- Each field accepted at exactly its budget; rejected one character over, with the field name and limit in the message.
- Missing required field rejected; empty-string field rejected.
- `id` accepts kebab-case; rejects spaces, uppercase, leading/trailing hyphens, and >40 chars.
- Duplicate `id` within one file rejected.
- `follow-up` accepts `backlog`, `drop`, `revisit-when:<cond>`; rejects `roadmap` and unknown values.
- `render(parse(render(r))) == render(r)` for both record types.
- `parse` of a hand-drifted but still-parsable file yields records whose re-render differs from the input — proving normalization.
- `parse` of unparsable text raises naming the offending line number.
- `fmt_check` on a file with three distinct defects reports all three.
- `fmt_write` rewrites a drifted file to canonical form and is idempotent on a second run.
- `validate` returns every defect for a record violating several fields at once.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes.

**Tier:** standard

**Depends on:** nothing.

### Task 2: Stores
- [ ] Done

**Files:**
- Create: `scripts/forge_memory_store.py`
- Test: `tests/test_forge_memory_store.py`

**Spec:** Stores, deferral

**Interface:**
```python
class Store:
    def create(self, record: Record) -> str: ...          # returns ref
    def list(self, type: str, **filters) -> list[Record]: ...
    def retire(self, ref: str, reason: str | None = None) -> None: ...

class FileStore(Store):    # path per record type; constraints.md, deferrals.md
    def __init__(self, path: str): ...

class GitHubStore(Store):  # gh issue create/list/close --json
    LABEL = "forge:deferral"

def select_store(repo_root: str, type: str) -> Store
    # constraint -> always FileStore(docs/forge/constraints.md)
    # deferral   -> config docs/forge/config.json {"deferrals":{"store":...}}
    #               absent => "github"; unknown value => loud error
class StoreUnavailable(Exception): ...   # message names the fix
```

**Tests:**
- `select_store` for `constraint` returns `FileStore` regardless of config content.
- Absent config selects `GitHubStore`; explicit `{"deferrals":{"store":"file"}}` selects `FileStore`; an unknown store value raises naming the legal values.
- Malformed `config.json` raises naming the file, never falls back.
- `gh` absent from PATH raises `StoreUnavailable` naming installation; the message must not suggest the file store as an automatic remedy.
- `gh` present but unauthenticated raises naming `gh auth login`.
- Repo with Issues disabled raises naming the fix.
- `GitHubStore.create` invokes `gh issue create` with the rendered body, `forge:deferral` label, and the follow-up label; returns the issue number.
- `GitHubStore.list` parses `gh issue list --json` output back into records through the shared `parse`.
- An issue body edited by hand out of canonical form is reported by `fmt_check` over `GitHubStore.list` output.
- `FileStore.retire` removes the record entirely, leaving no tombstone; the remaining file is canonical.
- A slug retired by `FileStore` can be re-added afterward.
- `GitHubStore.retire` closes the issue with the reason as a comment.

**Acceptance:** `python3 -m pytest tests/test_forge_memory_store.py -q` passes.

**Tier:** standard

**Depends on:** Task 1.

### Task 3: CLI
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (argparse subcommands, `main`)
- Test: `tests/test_forge_memory.py` (CLI cases)

**Spec:** CLI, constraint, deferral

**Interface:**
```
forge_memory.py add-constraint --id <slug> --rule <text> --because <text>
                               [--scope <glob>] [--issue N | --spec PATH]
forge_memory.py retire-constraint --id <slug>
forge_memory.py list-constraints [--scope <glob>] [--json]
forge_memory.py defer --title <text> --why <text> --follow-up <val> [--from <ref>]
forge_memory.py list-deferrals [--json]
forge_memory.py resolve-deferral --ref <issue-number|slug> --reason <text>
forge_memory.py fmt [--check | --write] [PATH ...]
```
`main(argv) -> int`; exit 0 on success, non-zero on any validation or store failure.

**Tests:**
- Every subcommand present and reachable; unknown subcommand exits non-zero.
- No subcommand accepts a free-form body argument.
- `add-constraint` composes a canonical record; `added` is machine-set to today's date and is not a settable flag.
- `--issue` and `--spec` are mutually exclusive; supplying both exits non-zero.
- Budget overrun exits non-zero with the field name and limit on stderr.
- `add-constraint` with an existing `id` exits non-zero.
- `retire-constraint` on an unknown slug exits non-zero.
- `defer` with `--follow-up roadmap` exits non-zero naming the legal values.
- `list-*` `--json` emits parseable JSON; without it, human-readable text.
- `fmt` with neither `--check` nor `--write` exits non-zero.
- `fmt --check` exits non-zero on a drifted file and zero on a canonical one.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 scripts/forge_memory.py --help` exits 0.

**Tier:** standard

**Depends on:** Task 1, Task 2.

### Task 4: Lint integration
- [ ] Done

**Files:**
- Modify: `scripts/forge_lint.py` (add managed-path memory check to the lint pass)
- Test: `tests/test_forge_lint.py`

**Spec:** Enforcement

**Interface:**
```python
def check_memory_files(repo_root: str) -> list[LintDefect]
```
Reuses the existing `LintDefect` dataclass and the existing every-defect-in-one-run reporting. Managed paths: `docs/forge/constraints.md`, and `docs/forge/deferrals.md` when the file store is configured. A managed path that does not exist is not a defect.

**Tests:**
- A drifted `constraints.md` produces one error defect per drifted record, not just the first.
- A canonical `constraints.md` produces no defects.
- Absent `constraints.md` produces no defects.
- `deferrals.md` is checked only when config selects the file store, and ignored otherwise.
- The new check runs alongside existing plan/spec checks without suppressing them; a plan defect and a memory defect are both reported in one run.
- Existing lint behavior is unchanged when no memory files exist.

**Acceptance:** `python3 -m pytest tests/test_forge_lint.py -q` passes.

**Tier:** standard

**Depends on:** Task 1.

### Task 5: PreToolUse guard hook
- [ ] Done

**Files:**
- Create: `hooks/guard-memory-writes` (bash, executable)
- Modify: `hooks/hooks.json` (register `PreToolUse` matcher)
- Test: `tests/test_forge_memory.py` (hook invoked as a subprocess)

**Spec:** Enforcement

**Interface:** reads the hook JSON payload on stdin; emits `hookSpecificOutput.permissionDecision` of `deny` with a `permissionDecisionReason` naming the exact `forge_memory.py` command to use instead. Emits nothing and exits 0 when not denying. Matcher: `Edit|Write|MultiEdit`. Signal-directory walk reused from `hooks/session-start` (`docs/forge/` or `.forge/`, legacy `docs/theforge/`/`.theforge/`, stopping at the git root).

**Tests:**
- A `Write` to `docs/forge/constraints.md` inside a forge repo is denied, and the reason names `add-constraint`.
- An `Edit` and a `MultiEdit` to the same path are denied identically.
- A write to `docs/forge/specs/foo.md` is allowed.
- A write to `constraints.md` in a directory with no forge signal emits nothing and exits 0.
- A relative `file_path` resolving to a managed path is denied — resolution happens before comparison.
- A path merely containing `constraints.md` as a substring elsewhere (`docs/forge/old-constraints.md`) is allowed.
- `deferrals.md` is denied only when the file store is configured.
- Malformed or empty stdin exits 0 without denying — the hook never blocks on its own failure.
- `hooks.json` remains valid JSON and retains the existing `SessionStart` entry.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 -c "import json;json.load(open('hooks/hooks.json'))"` exits 0.

**Tier:** standard

**Depends on:** nothing.

### Task 6: Guard installers
- [ ] Done

**Files:**
- Create: `templates/forge-memory-check.yml`
- Modify: `scripts/forge_memory.py` (`install-guards` subcommand)
- Create: `.github/workflows/forge-memory-check.yml` (generated by running the installer on this repo)
- Test: `tests/test_forge_memory.py` (installer cases)

**Spec:** Enforcement, Acceptance

**Interface:**
```
forge_memory.py install-guards [--pre-commit] [--ci]
```
`--pre-commit` writes an executable `.git/hooks/pre-commit` running `fmt --check` on managed paths. `--ci` copies `templates/forge-memory-check.yml` to `.github/workflows/`. Neither runs by default and neither is invoked by any other forge stage — installation is always explicit.

**Tests:**
- `install-guards` with no flag installs nothing and exits non-zero explaining that a flag is required.
- `--pre-commit` writes an executable file; the installed hook exits non-zero on a drifted managed file and zero on a canonical one.
- `--pre-commit` refuses to clobber an existing pre-commit hook it did not write, exiting non-zero.
- Re-running `--pre-commit` over its own previously installed hook succeeds and is idempotent.
- `--ci` writes the workflow; re-running is idempotent.
- `--ci` in a repo with no `.github/` creates the directory.
- The template is valid YAML and runs `fmt --check`.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 -c "import json;json.load(open('hooks/hooks.json'))"` exits 0; full suite `python3 -m pytest tests -q` passes.

**Tier:** standard

**Depends on:** Task 3.
