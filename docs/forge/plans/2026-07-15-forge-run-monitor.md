# Forge Run Monitor Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Ship a read-only `rich` TUI that watches a live `forge-run.py` execution — plan ledger plus the in-flight task's `codex exec` stream — fed by a runner that tees each subprocess to disk and writes a live progress pointer to `run.json`.

**Architecture:** Runner-side changes are additive and behavior-preserving: a tee helper streams every worker/acceptance/reviewer subprocess to a per-task live log while returning the same exit code + tail the loop already uses, and `run.json` gains a live progress pointer + timestamps + heartbeat. A new standalone `scripts/forge-monitor.py` process reads the run dir via `forge_status.read_run_state` (extended) and renders the TUI. The run dir is the only contract between runner and monitor.

**Tech stack:** Python 3 (stdlib for all runner modules); `rich` for the monitor only (new dependency).

**Global Constraints:** Runner modules stay stdlib-only. `rich` is a monitor-only dependency — `forge-monitor.py` guards the import and exits with an install hint if absent. The tee must not change any existing behavior: worker/acceptance/reviewer exit codes, output tails, `--output-last-message` capture, and timeout kills are identical to today. New `run.json` fields are additive and optional (a runner that omits them, and an old run.json without them, both still parse). Timestamps are UTC ISO-8601. Phase-header strings in the live log are a contract shared by runner and monitor: `── worker · codex exec · <model> · <effort> ──`, `── acceptance ──`, `── review · codex exec · <model> · <effort> ──`.

### Task 1: Tee helper
- [x] Done

**Files:**
- Modify: `scripts/forge_common.py` (add `TeeResult` dataclass + `run_teed` helper)
- Test: `tests/test_forge_tee.py` (create)

**Spec:** Unit 1 — Tee

**Interface:**
```
@dataclass
class TeeResult:
    exit_code: int | None   # None when timed out
    timed_out: bool
    tail: str               # last _ACC_TAIL_CHARS of merged stdout+stderr

def run_teed(argv, *, cwd=None, shell=False, timeout, live_path, header) -> TeeResult
```
- Appends `header` + newline to `live_path`, then streams the child's merged stdout+stderr (`stderr` redirected to `stdout`) line-by-line into `live_path`, flushing per line.
- Accumulates and returns `tail` (reuse `_ACC_TAIL_CHARS`).
- Child started in its own process group (`start_new_session=True`); on `timeout` deadline, kill the group and return `timed_out=True, exit_code=None`.

**Tests:**
- writes `header` then the child's stdout lines to `live_path`
- merges child stderr into the same stream/file
- returns the child's non-zero exit code faithfully
- `tail` holds the last `_ACC_TAIL_CHARS` of merged output
- a child exceeding `timeout` is killed and returns `timed_out=True`
- a second `run_teed` call on the same `live_path` appends (does not truncate)
- `shell=True` path runs a shell command string (acceptance-command shape)

**Acceptance:** `python -m pytest tests/test_forge_tee.py -q` passes.

**Tier:** standard

**Depends on:** nothing

### Task 2: run.json progress fields + reader/stale detection
- [x] Done

**Files:**
- Modify: `scripts/forge_receipts.py` (`write_run_json` gains optional progress kwargs)
- Modify: `scripts/forge_status.py` (`read_run_state` surfaces the new fields + stale detection; `render_status` shows a stalled run)
- Test: `tests/test_forge_receipts.py` (extend), `tests/test_forge_status.py` (extend)

**Spec:** Unit 2 — Progress state

**Interface:**
```
# forge_receipts.py — additive optional keyword params, default None → key omitted/null:
def write_run_json(run_dir, plan_path, spec_path, status, tasks, base_commit,
                   contract_error=None, current_task=None, current_phase=None,
                   started_at=None, updated_at=None, pid=None) -> None
# per-task started_at/ended_at ride inside caller-provided `tasks` summary dicts.

# forge_status.py:
STALE_CUTOFF_S = 180
def read_run_state(run_dir, now=None) -> dict   # now defaults to time.time()
# adds keys: current_task, current_phase, started_at, updated_at, stale (bool)
# stale = (state == "running") and heartbeat older than STALE_CUTOFF_S,
#         where heartbeat = parsed updated_at else latest_mtime;
#         a present-but-dead pid (os.kill(pid,0) raises) forces stale immediately.
```
- `render_status`: a `running` state that is `stale` renders `STALLED?` in the header instead of `RUNNING`.

**Tests:**
- `write_run_json` persists `current_task`/`current_phase`/`started_at`/`updated_at`/`pid` when passed, omits them when None
- a run.json without the new fields still reads (back-compat) — `read_run_state` returns them as None/False
- `read_run_state` surfaces `current_task`/`current_phase`/timestamps
- `stale` is False for a fresh `running` run (recent heartbeat)
- `stale` is True for a `running` run whose heartbeat predates the cutoff (`now` injected)
- a `running` run with a dead `pid` is stale regardless of cutoff
- `render_status` prints `STALLED?` for a stale running run
- terminal states (`passed`/`escalated`/`contract-error`) are never marked stale

**Acceptance:** `python -m pytest tests/test_forge_receipts.py tests/test_forge_status.py -q` passes.

**Tier:** standard

**Depends on:** nothing

### Task 3: Runner integration — tee + progress pointer
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (wire `run_teed` into the three phases; write progress pointer + timestamps; announce the monitor command)
- Test: `tests/test_forge_dispatch.py`, `tests/test_forge_review.py`, `tests/test_forge_loop.py`, `tests/test_forge_receipts.py` (extend as needed)

**Spec:** Unit 1 — Tee, Unit 2 — Progress state, Unit 3 — Live log format

**Interface:** no new public signatures. Behavior:
- `dispatch_worker`, `run_acceptance`, `_dispatch_review_call` call `run_teed` instead of `subprocess.run`, with `live_path = <run_dir>/task-<N>-live.log` (final review → `<run_dir>/final-review-live.log`) and the phase header from Global Constraints. `run_acceptance` writes a `$ <cmd>` line before each command's stream.
- `_dispatch_review_call`'s reviewer-crash `RuntimeError` quotes `TeeResult.tail` (not `proc.stderr`) — the stderr tail must survive teeing.
- Worker/reviewer `--output-last-message` capture unchanged; timeout → `timed_out` handled exactly as today.
- At run start: record `pid = os.getpid()` and `started_at`; print `monitor: python scripts/forge-monitor.py --run-dir <run_dir>` to stdout.
- At each phase start: `write_run_json(..., current_task=N, current_phase="worker|acceptance|review|final-review", updated_at=<now>)`. Per-task `started_at`/`ended_at` recorded in the task summary. At any terminal status: `current_task=None, current_phase=None`.

**Tests:**
- a passed task leaves a `task-<N>-live.log` containing the worker + acceptance + review phase headers
- reviewer-crash error message still contains the stderr tail (now sourced from the tee)
- worker timeout still yields a rework/escalation (behavior unchanged)
- `run.json` mid-run carries `current_task`/`current_phase`; at completion both are null
- per-task `started_at`/`ended_at` present on passed tasks
- run start prints the `monitor:` line
- existing dispatch/review/loop cases stay green (exit codes, tails, resume)

**Acceptance:** `python -m pytest tests/test_forge_dispatch.py tests/test_forge_review.py tests/test_forge_loop.py tests/test_forge_receipts.py -q` passes; then full suite `python -m pytest -q` passes.

**Tier:** complex

**Depends on:** Task 1, Task 2

### Task 4: Monitor TUI
- [x] Done

**Files:**
- Create: `scripts/forge-monitor.py`
- Test: `tests/test_forge_monitor.py` (create)

**Spec:** Unit 3 — Monitor CLI & rendering, Terminal-state banner, Error handling

**Interface:**
```
def main(argv=None) -> int
# CLI: (--run-dir DIR | --latest) required & mutually exclusive; --poll SECONDS (default 0.1)
# helpers:
def _latest_run_dir(root=".forge/runs") -> str | None
def _tail(path, max_lines) -> list[str]
def _render(state, log_lines) -> "rich renderable"   # ledger panel + live panel + optional banner
```
- Guarded import: missing `rich` → stderr `forge-monitor requires 'rich' — install: pip install rich`, return 1.
- Reuses `forge_status.read_run_state` (imported, never shells out). Loop under `rich.live.Live` (`refresh_per_second≈10`); each tick re-reads state + tails the current live log (`task-<current_task>-live.log`, or `final-review-live.log` when phase is `final-review`). On any terminal state or `stale`, stop the live loop, paint the final frame, block until `q`/Ctrl-C.
- Rendering per spec: `box.SQUARE` panels; ledger columns `glyph · number · title · tier · phase · elapsed` (`no_wrap`/ellipsis title, tabular elapsed); Direction-B palette; `dots` spinner + `▌` gutter on `current_task`; live-panel header names `task N · phase · model`; terminal banner full-width with semantic fill — green `RUN COMPLETE`, red-orange `HALTED` (+ first outstanding finding line) / `CONTRACT ERROR`; a stale running run renders `stalled?` (no banner). Honor `NO_COLOR`/non-tty and reduced-motion with a static frame.

**Tests:** (install `rich` first — see Acceptance; render via `rich.console.Console(record=True)` → `export_text`)
- `_latest_run_dir` returns the newest dir; None when `.forge/runs` empty/absent
- `--run-dir` and `--latest` mutually exclusive; neither → usage error
- missing/nonexistent run dir → message + non-zero exit
- running render: lit `current_task` row shows the spinner glyph; live-panel header names that task + phase
- completed render: green `RUN COMPLETE` banner with counts + elapsed
- halted render: red-orange `HALTED` banner naming the escalated task + its first finding
- contract-error render: `CONTRACT ERROR` banner with the reason
- stale running render: `stalled?` shown, no banner
- half-written/partial `run.json` → renders from receipts, no crash
- missing live log → live panel shows a `waiting for output…` placeholder

**Acceptance:** `pip install rich && python -m pytest tests/test_forge_monitor.py -q` passes. (The test imports `rich`; a missing dependency fails the import — never a silent skip.)

**Tier:** complex

**Depends on:** Task 2

### Task 5: Docs + wiring
- [x] Done

**Files:**
- Modify: `README.md` (monitor usage + `rich` install note)
- Modify: `skills/planning/codex-execution.md` (the runner prints a `monitor:` command; watch in a second terminal)

**Spec:** Unit 3 — Monitor CLI & rendering

**Tests:** none (doc change).

**Acceptance:** `grep -q "forge-monitor" README.md skills/planning/codex-execution.md` succeeds.

**Tier:** trivial

**Depends on:** Task 3, Task 4
