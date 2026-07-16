# Forge Run Monitor — design

Codex-runner live monitor: an attach-from-outside TUI that shows where a `forge-run.py` execution is in its plan and streams the in-flight task's `codex exec` output. Read-only observer; never touches execution semantics.

Reverses the 2026-07-15 rejection of the monitor terminal (DECISIONS: "Rejected: the monitor terminal … YAGNI"). Rationale changed: "foreground execution" is a blocking subprocess loop with no live visibility into an in-flight task — a task can run minutes emitting one stdout line, indistinguishable from a hang. See DECISIONS entry for this spec.

## Scope

- In: runner tees each subprocess's output to disk; `run.json` gains a live progress pointer + timestamps; a new `scripts/forge-monitor.py` renders a two-panel `rich` TUI with a terminal-state banner.
- Out: the Claude/dispatch path (Phase 8, separate spec). Interactivity — scrollback, filtering, keyboard nav (YAGNI; read-only). Any push/remote/away notification (the rejected direction stays rejected).

## Architecture

Three units, each independently testable:

1. **Tee** (runner) — every worker/acceptance/reviewer subprocess streams its combined stdout+stderr line-by-line into a per-task live log while still yielding the exit code + output tail the loop needs.
2. **Progress state** (runner) — `run.json` carries `current_task`, `current_phase`, run- and task-level timestamps, and a heartbeat, written at each phase transition.
3. **Monitor** (`scripts/forge-monitor.py`) — a separate read-only process; polls the run dir via `forge_status.read_run_state` (extended), tails the current live log, renders the TUI. Never dispatches, never touches git, never imports the runner's dispatch code.

Data flow: runner → (`run.json` + `task-N-live.log`) → monitor. The run dir is the only contract between them.

## Unit 1 — Tee

Replace the three `subprocess.run(..., capture_output=True)` call sites (`dispatch_worker`, `run_acceptance`, `_dispatch_review_call`) with one helper:

```
_run_teed(argv, *, cwd=None, shell=False, timeout, live_path, header) -> TeeResult
# TeeResult: exit_code:int|None, timed_out:bool, tail:str
```

Contract:
- Appends `header` (see log format) to `live_path`, then streams the child's merged stdout+stderr line-by-line to `live_path` (flushed per line — the monitor tails it live).
- Returns `tail` = last `_ACC_TAIL_CHARS` of the merged stream, so existing behavior that reads output tails is preserved.
- **Preserves fail-loud diagnostics:** the reviewer-crash `RuntimeError` in `_dispatch_review_call` currently quotes `proc.stderr`; it must instead quote `TeeResult.tail` — teeing must not lose the stderr tail used in the crash message. (This is the exact regression the mock's halt frame depicts; it is a hard requirement, not a nicety.)
- **Preserves the dead-man's switch:** on `timeout`, kill the child (process group) and return `timed_out=True` — identical to today's `TimeoutExpired` path. A hung `codex exec` never hangs the run.
- Worker last-message capture (`--output-last-message` file) is unchanged and independent of the tee.

## Unit 2 — Progress state (`run.json`)

Additions (existing fields unchanged):

```
{
  "status": "running",              // unchanged vocabulary
  "base_commit": "9f0aa21",         // unchanged
  "plan": "...", "spec": "...",     // unchanged
  "started_at": "2026-07-15T09:11:42Z",   // NEW run start (UTC ISO-8601)
  "updated_at": "2026-07-15T09:17:03Z",   // NEW heartbeat, rewritten every phase transition
  "pid": 48213,                            // NEW runner pid (liveness hint when same host)
  "current_task": 4,                       // NEW in-flight task number; null between/at terminal
  "current_phase": "worker",               // NEW worker|acceptance|review|final-review; null at terminal
  "tasks": [
    { "number": 1, "title": "...", "tier": "standard", "status": "passed",
      "attempts": 1, "commit": "abc1234",
      "started_at": "...", "ended_at": "..." }   // NEW per-task stamps
  ]
}
```

- `current_task`/`current_phase` set at the start of each phase, cleared to `null` when the run reaches any terminal status (`passed`/`escalated`/`escalated-final-review`/`contract-error`).
- Timestamps are UTC ISO-8601. (`datetime.datetime.now`/`utcnow` is available to the runner — already used for the run-dir stamp.)
- **Heartbeat / stale-run detection** (resolves DEFERRALS 2026-07-15 "hard-killed run stays `running`"): a run whose `status` is `running` but whose `updated_at` (or newest live-log mtime) is older than a cutoff is reported *likely-dead*; when `pid` is present and on the same host, `os.kill(pid, 0)` confirms. The monitor renders a stale `running` run as `stalled?`, not a live spinner.

## Unit 3 — Live log format

One file per task: `run_dir/task-<N>-live.log`. Final review: `run_dir/final-review-live.log`. Appended across phases with a header rule per phase:

```
── worker · codex exec · opus · xhigh ──
<streamed worker stdout/stderr, verbatim>
── acceptance ──
$ pytest -q
<streamed acceptance stdout/stderr>
── review · codex exec · sol · high ──
<streamed reviewer stdout/stderr>
```

On a rework attempt the loop appends fresh phase headers (the file is the full attempt history; the monitor shows the tail). Exact `codex exec` stream texture is a **deferred live-verify item** on a Codex box (CLI absent from dev env; Phase 4 flag-check precedent) — the format contract here is the phase headers + verbatim passthrough, which is texture-independent.

## Unit 3 — Monitor CLI & rendering

```
forge-monitor.py [--run-dir DIR | --latest] [--poll SECONDS]
```

- `--latest`: newest dir under `.forge/runs/`. `--run-dir`/`--latest` mutually exclusive; one required.
- Read-only. Reuses `forge_status.read_run_state` (extended to surface `current_task`, `current_phase`, timestamps, heartbeat/stale) — imports it, never shells out to `--status`. Consistent with "`--status` + hook share `forge_status.py`."
- `rich` + `rich.live.Live`, `refresh_per_second≈10` (smooth spinner). Each tick: re-read `run.json` (cheap), tail the current live log to the last screenful. No dependency beyond `rich`; `rich` is read-only rendering — Textual only if interactivity is ever wanted (it isn't).
- Runner announces at start (stdout): `monitor: python scripts/forge-monitor.py --latest`.

Layout (Direction B — "Instrument"; locked via mock):

```
┌ FORGE RUN ─────────────────────────────────── ● running ┐
  plan  2026-07-15-forge-run-monitor.md
  run   .forge/runs/20260715T091142
  3/7 tasks · elapsed 04:12 · 1 rework
  ✓  1  Parse plan tasks            standard  passed  0:41
  ✓  2  run.json incremental        standard  passed  1:03
  ✓  3  --status reader             trivial   passed  0:12
  ⠙  4  Live-log tee to disk        complex   worker  1:37   ← lit + spinner
  ○  5  Monitor: task ledger        standard  queued
  …
└──────────────────────────────────────────────────────────┘
┌ ▸ task 4 · worker · codex exec · opus · xhigh ──── live ─┐
  <in-flight task's stream, tailing>
└──────────────────────────────────────────────────────────┘
```

Rendering contract:
- Panels: `box.SQUARE` hairline. Table for the ledger (right-aligned tabular elapsed).
- Row columns: `glyph · number · title · tier · phase · elapsed`. Title `no_wrap`, `overflow="ellipsis"`; sensible min width on resize.
- Palette (truecolor): fg `#d3dae2`, dim `#66717f`, edge `#212a34`, accent/running `#5ad0df`, passed `#59c26b`, queued `#3e4753`, halt `#f2683f`. Semantic fills are separate from the cyan accent.
- In-flight row: `dots` spinner in the glyph column + a colored `▌` gutter bar (terminal equivalent of the mock's inset rail) + accent-colored cells. Row identity == live-panel header task, always.
- Live panel header names `task N · phase · model` so the lit row and the scrolling output are provably the same task.

### Terminal-state banner

On any terminal `run.json` status, the stream freezes on its tail and a **full-width bottom banner** is painted (semantic fill carries the state; it reads before a word is parsed):

- Completed (`passed`): green fill — `✓ RUN COMPLETE — N/N tasks passed · <final-review outcome> · <elapsed>` · `press q to exit`.
- Halted (`escalated` / `escalated-final-review`): red-orange fill, two lines — `■ HALTED — task N escalated after K attempts` + the first outstanding finding (from the receipt) on line 2 · `press q to exit`.
- Contract error (`contract-error`): red-orange fill — `■ CONTRACT ERROR — <reason>` · `press q to exit`.
- Banner ≤ 2 lines; a gentle pulse on the halt/error fill is allowed (respect `prefers-reduced-motion` / no-color terminals). Monitor then blocks until `q`/Ctrl-C; final frame stays on screen.

Stale `running` (heartbeat past cutoff, pid dead/absent): not a banner — the top-panel status renders `stalled?` in halt color and the spinner stops, so a killed runner is visibly distinct from a live one without asserting a terminal state the runner never wrote.

## Error handling

- Monitor is defensive: absent/partial/half-written `run.json` → render what's readable (fall back to receipts, as `read_run_state` already does), never crash. Missing live log → empty live panel with a `waiting for output…` placeholder.
- No run at the target dir → one-line message + exit non-zero (mirrors `--status`'s "no run at …").
- Monitor failure never affects the runner (separate process; the runner never reads the monitor).
- Runner tee failure (can't open live log) must not fail the run — degrade to no-tee for that phase, keep executing (observability is best-effort; execution is not).

## Testing

- Tee helper: writes phase header + streamed body to `live_path`; returns correct exit/tail; **reviewer-crash tail preserved** in the `RuntimeError`; timeout kills child and returns `timed_out`. Drive with the existing fake `codex` binary + real temp git repos (no new harness).
- `run.json`: new fields written at phase transitions and cleared at terminal state; timestamps present; `pid` recorded. Resume path still reads `base_commit`.
- `read_run_state` extension: surfaces `current_task`/`current_phase`/timestamps; stale detection flips a `running` run past cutoff to a stale marker.
- Monitor render: `rich` `Console(record=True)` snapshot for running / halted / completed — assert lit-row glyph + spinner presence, live-panel header names the current task, banner text + semantic color per state. `--latest` selects the newest dir. Reduced-motion path renders static.
- Live `codex exec` stream texture: deferred verify on a Codex install.

## Acceptance

- A `forge-run.py` run over a multi-task plan is watchable end-to-end in a second terminal: task ledger advances, the in-flight task streams live, and completion/halt paints the banner.
- Killing the runner mid-task makes the monitor show `stalled?` within the cutoff, not a perpetual live spinner.
- All existing forge-run tests stay green (tee is behavior-preserving for exit codes, tails, and timeouts).

## Changelog

2026-07-15: Built (Phase 9). Final-review hardening from the integration review: `stale` is display-only (`stalled?`), never a terminal/exit condition — the monitor keeps polling so a long silent-but-healthy phase isn't abandoned; `_is_stale` made bidirectional (a fresh heartbeat or live pid clears staleness, per the pid-confirms contract); the final-review halt banner surfaces the verdict's finding from `final-review.json`; the completed banner claims "review clean" only when a final review actually ran and passed; a task left mid-flight by a contract error renders `interrupted`, not a frozen spinner; the monitor blocks on `q`/Ctrl-C after a terminal state.
