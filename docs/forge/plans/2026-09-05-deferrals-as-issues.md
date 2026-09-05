# Deferrals as GitHub Issues Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Retire docs/forge/DEFERRALS.md — defer-disposition findings stage during a run and become GitHub issues only through a reviewed close-out gate.
**Architecture:** `forge-run.py` is unchanged: it already aggregates defer findings into `run.json` and writes no durable record. `forge_status.py` gains stage-and-emit rendering; `forge_memory.py`'s `defer` gains run.json write-back so idempotency is mechanical. The remaining change is skill and doc text plus a guided migration of the existing file.
**Tech stack:** Python 3 stdlib only; `gh` CLI via the Phase 1 store; pytest; markdown skill text.
**Global Constraints:** No third-party dependencies. `forge-run.py` must never invoke `gh`. No fallback to the file store on any path. Tasks 1-3 get strict TDD; Task 4 is prose with mechanical acceptance only and adds no test file.

### Task 1: Carried engine fixes
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (`Record.ref` field options)
- Modify: `scripts/forge_memory_store.py` (`GitHubStore.list` signature and return)
- Test: `tests/test_forge_memory.py`, `tests/test_forge_memory_store.py`

**Spec:** Carried fixes

**Interface:**
```python
@dataclass
class Record:
    ref: str | None = field(default=None, compare=False, repr=False)

class Store:
    def scan(self, type, **filters) -> tuple[list[Record], list[tuple[str, str]]]
        # collects; errors are (ref, message) pairs — ref is an issue number or file line
    def list(self, type, **filters) -> list[Record]
        # raises when scan reports any error
# Both implemented by FileStore and GitHubStore. No call site branches on store class.
# GitHubStore.scan additionally accepts state="all".
```

**Tests:**
- Two records with identical fields and different `ref` compare equal; `repr` omits `ref`.
- `ref` still round-trips as data — set, read back, unaffected by render/parse/validate.
- `scan` returns a two-tuple on BOTH stores; the second element is empty when every record parses.
- Two unparsable bodies among three issues return both errors and the one good record.
- Errors carry the issue number (GitHubStore) or the line (FileStore), not just a message.
- `list` raises on both stores when `scan` reports an error, preserving `FileStore.create`'s id-uniqueness guard.
- No caller passes or receives a mutable `errors` argument, and no caller branches on `isinstance(store, GitHubStore)` — grep-level assertions for both.
- `fmt --check` over open issues still reports every bad issue, naming issue numbers.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py tests/test_forge_memory_store.py -q` passes; full suite passes.

**Tier:** standard

**Depends on:** nothing.

### Task 2: CLI write-back for filed deferrals
- [ ] Done

**Files:**
- Modify: `scripts/forge_memory.py` (`defer` subcommand, `_EXPECTED_DESTS`)
- Test: `tests/test_forge_memory.py`

**Spec:** Staging and idempotency

**Interface:**
```
forge_memory.py defer --title <text> --why <text> --follow-up <val> [--from <ref>]
                      [--run <run.json path> --finding-id <id>]
```
`--run` and `--finding-id` are required together; either alone exits non-zero. On a
successful issue creation the resolved issue number is written into that run.json
entry under `issue`. The record composition contract is unchanged — no free-form body
argument is added.

**Tests:**
- `--run` without `--finding-id` exits non-zero, and the reverse.
- A successful `defer --run --finding-id` writes `issue` into the matching run.json entry and leaves every other entry untouched.
- Re-filing an entry that already carries `issue` exits non-zero naming the existing issue, and creates nothing.
- An unknown `--finding-id` exits non-zero naming the id; run.json is not modified.
- A malformed or absent run.json exits non-zero naming the path; no issue is created.
- Issue creation failure leaves run.json unmodified — no `issue` key is written.
- `gh` unavailable (missing, unauthenticated, Issues disabled) exits non-zero naming the fix, writes no `issue` key, and never falls back to the file store.
- run.json is rewritten as valid JSON preserving unrelated keys (`threads`, `seeded_findings`, task summaries).
- `defer` without `--run` behaves exactly as before.
- `_EXPECTED_DESTS` updated deliberately; the allow-list still fails on an unlisted flag.

**Acceptance:** `python3 -m pytest tests/test_forge_memory.py -q` passes; `python3 scripts/forge_memory.py defer --help` exits 0.

**Tier:** standard

**Depends on:** Task 1.

### Task 3: Stage-and-emit at close-out
- [ ] Done

**Files:**
- Modify: `scripts/forge_status.py` (deferral rendering at completion)
- Test: `tests/test_forge_status.py`

**Spec:** Flow, Staging and idempotency, gh unavailable at filing

**Interface:**
```python
def render_staged_deferrals(state, run_json_path) -> list[str]
```
Returns display lines: one block per staged deferral carrying its finding id, its full
finding summary, and a `forge_memory.py defer` command **template** with `--title` and
`--why` left as placeholders and `--follow-up backlog --run <path> --finding-id <id>`
filled in. It never fabricates a title: a finding summary is 100-200 chars against an
80-char budget, and this module has no way to author within budget. Authorship happens
at the gate. Entries already carrying `issue` render as filed with their number and
emit no template.

**Tests:**
- A run with no deferrals renders nothing and changes existing status output not at all.
- Each staged deferral renders exactly one runnable `defer` command carrying `--run` and its `--finding-id`.
- An entry carrying `issue` renders as filed with the number and emits no command.
- A mixed run renders commands only for unfiled entries.
- Emitted values containing spaces, quotes, or newlines are quoted so the template is safe to paste.
- No emitted template contains a fabricated `--title` or `--why` value; both are placeholders.
- The full finding summary is shown untruncated, so the author has the material to write from.
- Rendering makes no `gh` call and no network call — process-level, PATH-stubbed.
- `forge-run.py` on a clean run invokes `gh` zero times — process-level, PATH-stubbed.

**Acceptance:** `python3 -m pytest tests/test_forge_status.py -q` passes; full suite passes.

**Tier:** standard

**Depends on:** Task 2.

### Task 4: Skill and documentation text
- [ ] Done

**Files:**
- Modify: `skills/planning/SKILL.md` (deferral rule → issues + close-out gate; end-of-plan summary lists issue numbers)
- Modify: `skills/planning/codex-execution.md` (DEFERRALS write-back section → stage-and-emit contract)
- Modify: `skills/project-memory/SKILL.md` (DEFERRALS section only; ROADMAP and DECISIONS sections stay)
- Modify: `README.md`, `CONTRIBUTING.md` (project memory is no longer three files)

**Spec:** Documentation and skill changes, Close-out review gate, User-initiated deferrals

**Interface:** none — prose only. Required content: implementers may defer non-spec scope only; spec'd requirements surface at the review gate and are never deferred; defer findings stage during the run and are filed only after the user reviews them at close-out; `follow-up` defaults to `backlog`; user-initiated deferrals file immediately with `from: user` and no gate; the Codex runner stages and emits, never files.

**Tests:** none — this task changes agent-facing prose, which has no assertable behavior. Verification is the mechanical acceptance below.

**Acceptance:**
- `grep -rn "DEFERRALS.md" skills/ scripts/ hooks/ README.md CONTRIBUTING.md` returns no hit that names it as a write target or a live read path.
- `grep -rn "docs/forge/DEFERRALS" .` returns hits only under `docs/forge/archive/`, `docs/forge/specs/`, `docs/forge/plans/`, the single retirement notice in `skills/project-memory/SKILL.md`, and the pre-existing citation comment in `tests/test_forge_review.py`. Naming the retired path is unavoidable when documenting that it is retired.
- The four required content points above each appear in `skills/planning/SKILL.md`.
- Full suite passes (no test changes expected).

**Tier:** standard

**Depends on:** nothing.

### Task 5: Migrate the existing DEFERRALS.md
- [ ] Done

**Files:**
- Delete: `docs/forge/DEFERRALS.md` (via `git mv`)
- Create: `docs/forge/archive/DEFERRALS.md`

**Spec:** Migration

**Interface:** none.

**Tests:** none — a guided content migration with no assertable behavior.

**Acceptance:**
- `docs/forge/DEFERRALS.md` does not exist.
- `docs/forge/archive/DEFERRALS.md` exists and opens with a header stating it is historical, not authoritative, and takes no new entries.
- Every entry judged live has a corresponding open GitHub issue labelled `forge:deferral`.
- Each such issue passes `forge_memory.py fmt --check` with no path arguments.
- Full suite passes.

**Tier:** standard

**Depends on:** Task 1, Task 2, Task 4.

**Execution note:** NOT DISPATCHABLE. Deciding which of the 25 entries are still live is the user's judgment, and every live entry must be re-authored within budget. Runs inline with the user after Tasks 1-4 are committed.
