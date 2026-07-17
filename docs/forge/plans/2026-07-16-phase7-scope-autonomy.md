# Phase 7 Runner Scope Autonomy Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Give the Codex runner a fix/defer/halt disposition matrix, convergence-based rework, a final-review fix loop, an `--autofix auto|gate` knob, and a terminal reconcile-only doc-sync stage — replacing the raw 2-iteration cap and single-shot final-review gate.

**Architecture:** Extends `scripts/forge-run.py` and its helper modules. The reviewer verdict grows from `list[str]` to a per-finding model (`Finding`) carrying provenance/impact/convergence; the runner verifies provenance against the actual diff, derives disposition from the matrix, and drives a convergence-based rework loop shared by per-task and final review. Classification and convergence decisions are pure functions for testability; dispatch and commit plumbing reuse existing call sites.

**Tech stack:** Python 3 stdlib only; unittest + `python3 -m pytest`; `codex exec` subprocess dispatch (faked in tests via `_forge_support`).

**Global Constraints:** stdlib only (no new deps); reviewer↔runner JSON contract is the sole coupling — the reviewer proposes classification, the runner verifies/derives; provenance is never trusted from the reviewer over the diff; the runner never writes `docs/forge/DEFERRALS.md` (aggregates into run.json; orchestrator writes at completion).

### Task 1: Verdict model + constants
- [x] Done

**Files:**
- Modify: `scripts/forge_common.py` (Finding dataclass; Verdict.findings → list[Finding]; TaskOutcome fields; constants; REVIEW_VERDICT_INSTRUCTION rewrite; verdict_to_dict/finding_to_dict)
- Test: `tests/test_forge_review.py` (model + serialization + instruction)

**Spec:** Reviewer verdict contract, Disposition matrix, Retirements / doc changes

**Interface:** declarations only —
```python
@dataclass
class Finding:
    id: str
    summary: str
    file: str
    lines: str                       # "12-20" or "12"
    provenance: str                  # "in-diff" | "pre-existing" (reviewer-proposed)
    impact: str                      # "contract-breaking" | "improvement"
    contract_ref: "str | None" = None
    convergence: "str | None" = None # "resolved" | "carried" | "new" | None
    carried_from: "str | None" = None
    repair_task: "dict | None" = None
    disposition: "str | None" = None # "fix" | "defer" | "halt" — set by runner (Task 2)

@dataclass
class Verdict:
    kind: str                        # "pass" | "findings"
    findings: list = field(default_factory=list)   # list[Finding]

# TaskOutcome gains: halt_reason: "str | None" = None; deferrals: list = field(default_factory=list); repair_task: "dict | None" = None
MAX_ATTEMPTS_BACKSTOP = 5            # replaces MAX_ATTEMPTS
AUTOFIX_MODES = ("auto", "gate")
HALT_REASONS = ("scope-decision", "regression", "stuck", "backstop", "gate")

def finding_to_dict(finding) -> dict: ...   # receipt/run.json serialization
def verdict_to_dict(verdict) -> dict: ...   # now serializes Finding list via finding_to_dict
```
`REVIEW_VERDICT_INSTRUCTION` rewritten to specify the per-finding JSON schema (id, summary, location{file,lines}, provenance, impact, contract_ref, convergence [re-reviews only], carried_from, repair_task) and the classification rules: contract-breaking **must** cite a named acceptance criterion in `contract_ref` or it is treated as improvement; a finding outside the diff is pre-existing; repair_task required only on pre-existing+contract-breaking findings.

**Tests:** Finding roundtrips through finding_to_dict; verdict_to_dict for pass and for a findings verdict with two Finding objects; MAX_ATTEMPTS_BACKSTOP == 5 and old MAX_ATTEMPTS name is gone; REVIEW_VERDICT_INSTRUCTION names each schema field.

**Acceptance:** `python3 -m pytest tests/test_forge_review.py -q` passes.

**Tier:** standard

**Depends on:** nothing.

### Task 2: Classification engine
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (parse_verdict/_verdict_from_obj rewrite; diff_line_ranges; verify_provenance; derive_disposition; classify_findings)
- Test: `tests/test_forge_classify.py` (create)

**Spec:** Disposition matrix, Reviewer verdict contract

**Interface:**
```python
def parse_verdict(last_message) -> Verdict: ...          # parses per-finding schema into Finding list; raises RuntimeError on unparseable / contract-breaking missing location
def diff_line_ranges(diff_text) -> dict: ...             # {file_path: [(start,end), ...]} of new-side changed line ranges from unified-diff hunk headers
def verify_provenance(finding, ranges) -> str: ...       # "in-diff" if finding.lines intersect ranges[finding.file] else "pre-existing"
def derive_disposition(finding) -> str: ...              # matrix: contract_ref None → impact=improvement; (provenance,impact) → "fix"|"defer"|"halt"
def classify_findings(verdict, diff_text) -> Verdict: ... # sets each finding's verified provenance + disposition; returns the verdict
```

**Tests:** diff_line_ranges — single hunk, multiple hunks one file, multiple files, added-only hunk; verify_provenance — finding inside a range → in-diff, outside → pre-existing, reviewer claim "in-diff" overridden to pre-existing when lines fall outside; derive_disposition — all four quadrants (in-diff×contract-breaking→fix, in-diff×improvement→defer, pre-existing×contract-breaking→halt, pre-existing×improvement→defer), contract_ref=None downgrades a contract-breaking finding to defer; parse_verdict on the new schema, and RuntimeError when a contract-breaking finding omits location.

**Acceptance:** `python3 -m pytest tests/test_forge_classify.py -q` passes.

**Tier:** complex

**Depends on:** Task 1.

### Task 3: Convergence rework loop
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (ConvergenceState; convergence_decision; advance_state; execute_task rewrite; receipt dict gains per-finding classification + disposition + halt_reason + repair_task)
- Test: `tests/test_forge_convergence.py` (create)

**Spec:** Rework loop & convergence, Autonomy flag

**Interface:**
```python
@dataclass
class ConvergenceState:
    resolved_ids: set = field(default_factory=set)
    carried_streak: dict = field(default_factory=dict)   # finding id → consecutive carried count
    prev_acceptance_ok: "bool | None" = None

def convergence_decision(findings, state, acceptance_ok, attempt, autofix_mode,
                         backstop=MAX_ATTEMPTS_BACKSTOP) -> tuple: ...
    # returns (action, halt_reason): action in {"pass","rework","halt"}
    # precedence: gate mode + any finding → ("halt","gate");
    #   any disposition=="halt" finding → ("halt","scope-decision");
    #   regression (a resolved id reappears, or acceptance_ok went True→False) → ("halt","regression");
    #   stuck (a fix finding carried across two consecutive attempts) → ("halt","stuck");
    #   no fix findings and acceptance_ok → ("pass", None);
    #   attempt >= backstop → ("halt","backstop"); else ("rework", None)

def advance_state(state, findings, acceptance_ok) -> None: ...   # updates resolved_ids, carried_streak, prev_acceptance_ok after a decision
```
`execute_task` uses classify_findings (Task 2) then convergence_decision; an execution failure (worker crash/timeout, acceptance non-zero) is an implicit fix-retry finding with no provenance/impact — never defers/scope-halts, but counts for regression (green→red) and backstop. `--gate` mode short-circuits to halt on any finding.

**Tests:** progress (attempt resolves one fix finding, another fix finding remains → rework); regression via resolved-id reappearance → halt/"regression"; acceptance green→red → halt/"regression"; stuck (same fix id carried two attempts) → halt/"stuck"; clean (no fix findings) → pass; backstop (5th attempt still has fix findings) → halt/"backstop"; gate mode + one improvement finding → halt/"gate"; execution failure on attempt 1 → rework; net-progress-not-required (round resolves one, surfaces a new one → rework, not halt).

**Acceptance:** `python3 -m pytest tests/test_forge_convergence.py -q` passes.

**Tier:** complex

**Depends on:** Task 2, Task 5.

### Task 4: Final-review loop
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (run_final_review_loop; run_plan final-review block rewrite; final-review fix dispatch)
- Test: `tests/test_forge_review.py` (final-review loop cases)

**Spec:** Final review, Rework loop & convergence

**Interface:**
```python
def run_final_review_loop(spec_path, run_base, run_dir, codex_bin, cwd, tier,
                          autofix_mode, timeout=DEFAULT_TIMEOUT) -> TaskOutcome: ...
    # whole-plan diff review under the same convergence_decision; fix dispatch
    # re-runs a worker against the outstanding fix findings; halt carries the
    # drafted repair_task; on pass, commits a single `fix: final-review` commit
    # when the loop applied any fix
```
Reuses convergence_decision + classify_findings; diff base = run-start HEAD; tier = plan's highest task tier (unchanged).

**Tests:** final review with findings → fix → re-review → pass (commit present); pre-existing×contract-breaking finding → halt with repair_task; improvement finding → defer, run completes; `--gate` → halt on any finding.

**Acceptance:** `python3 -m pytest tests/test_forge_review.py -q` passes.

**Tier:** standard

**Depends on:** Task 3.

### Task 5: Prior-findings review packet
- [x] Done

**Files:**
- Modify: `scripts/review-packet.py` (`--prior-findings PATH` argument + packet section)
- Test: `tests/test_review_packet.py` (prior-findings section)

**Spec:** Rework loop & convergence, Reviewer verdict contract

**Interface:** `review-packet.py` gains `--prior-findings PATH` (a JSON file of the prior attempt's findings). When present, the packet appends a "Prior findings — label each current finding resolved/carried/new against these; echo the prior id in carried_from" section listing prior findings with their ids. Absent → packet unchanged from today.

**Tests:** without `--prior-findings` the packet output is byte-identical to current behavior; with it, the packet contains the prior-findings section, each prior id, and the labeling instruction.

**Acceptance:** `python3 -m pytest tests/test_review_packet.py -q` passes.

**Tier:** standard

**Depends on:** Task 1.

### Task 6: Receipts and run.json fields
- [x] Done

**Files:**
- Modify: `scripts/forge_receipts.py` (write_run_json gains deferrals/autofix_mode/doc_sync; write_final_review_receipt carries classified findings via finding_to_dict)
- Test: `tests/test_forge_receipts.py` (new fields)

**Spec:** Receipts / run.json, Deferral handling

**Interface:**
```python
def write_run_json(run_dir, plan_path, spec_path, status, task_summaries, base_commit,
                   *, started_at=None, pid=None, current_task=None, current_phase=None,
                   deferrals=None, autofix_mode=None, doc_sync=None) -> None: ...
    # deferrals: list[dict] aggregated defer-disposition findings; autofix_mode: "auto"|"gate";
    # doc_sync: {"status": "...", "commit": "...", "reconciled": [...]} | None
def write_final_review_receipt(run_dir, verdict) -> None: ...   # serializes Finding classification
```
Backward-compatible: new params default None so existing callers and old run.json shapes stay valid (matches the existing optional-field convention).

**Tests:** write_run_json with deferrals/autofix_mode/doc_sync round-trips into run.json; omitting them yields today's shape; final-review receipt includes per-finding provenance/impact/disposition.

**Acceptance:** `python3 -m pytest tests/test_forge_receipts.py -q` passes.

**Tier:** standard

**Depends on:** Task 1.

### Task 7: Autofix flag, deferral aggregation, doc-sync stage
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (`--autofix` argparse + threading; deferral aggregation into run.json; dispatch_doc_sync + terminal call site)
- Test: `tests/test_forge_dispatch.py` (flag threading + deferral aggregation), `tests/test_forge_docsync.py` (create)

**Spec:** Autonomy flag, Deferral handling, Terminal doc-sync stage, Commit discipline

**Interface:**
```python
# main(): parser.add_argument("--autofix", choices=AUTOFIX_MODES, default="auto")
# run_plan(..., autofix_mode="auto") threads the mode into execute_task and run_final_review_loop
def dispatch_doc_sync(spec_path, run_base, diff, run_dir, tier, codex_bin, cwd,
                      timeout=DEFAULT_TIMEOUT) -> "DocSyncResult": ...
    # reconcile-only: updates existing docs the whole-plan diff made stale
    # (references, signatures, ROADMAP/spec changelog); never authors new docs;
    # commits `docs: sync` when it changed anything; returns a contradiction halt
    # when it cannot mechanically reconcile a doc/contract conflict
```
Deferral aggregation: defer-disposition findings from every task and the final review collect into a run-level list passed to `write_run_json(deferrals=...)`; the runner does not touch DEFERRALS.md. Doc-sync runs once after final review passes, before the terminal run.json write.

**Tests:** `--autofix gate` reaches execute_task/final-review as gate (any finding halts, no fix dispatch); default is `auto`; an invalid `--autofix` value is rejected by argparse; deferrals from tasks aggregate into run.json; doc-sync commits `docs: sync` when a fixture doc is stale; doc-sync makes no commit when no doc drift; doc-sync contradiction → halt with the contradiction named; doc-sync runs only after an all-green final review.

**Acceptance:** `python3 -m pytest tests/test_forge_dispatch.py tests/test_forge_docsync.py -q` passes.

**Tier:** complex

**Depends on:** Task 4, Task 6.

### Task 8: --status surfacing and docs
- [x] Done

**Files:**
- Modify: `scripts/forge_status.py` (read_run_state/render_status surface deferrals + halt-reason class)
- Modify: `skills/planning/codex-execution.md` (--autofix at the offer, disposition matrix, convergence stop, doc-sync stage, DEFERRALS write-back at completion)
- Modify: `docs/forge/specs/2026-07-13-codex-exec-runner-design.md` (changelog pointer: review/rework machinery superseded by the Phase 7 spec)
- Test: `tests/test_forge_status.py` (deferrals + halt reason in status)

**Spec:** Receipts / run.json, Retirements / doc changes

**Interface:** `render_status` output gains a deferrals count/list and the halt-reason class for an escalated run; `read_run_state` reads the new run.json fields (tolerating their absence on old runs).

**Tests:** `--status` on a run.json with deferrals + an escalated halt reason renders both; a run.json without the new fields renders as today (no crash).

**Acceptance:** `python3 -m pytest tests/test_forge_status.py -q` passes; `python3 -m pytest tests -q` (full suite) passes.

**Tier:** standard

**Depends on:** Task 6, Task 7.
