#!/usr/bin/env python3
"""forge-monitor.py — read-only live TUI for a ``forge-run.py`` execution.

An attach-from-outside observer: it reads a run dir (``run.json`` + per-task
receipts + ``task-N-live.log``) via ``forge_status.read_run_state`` and renders
two panels — a task ledger with the in-flight task lit, and a tail of that
task's ``codex exec`` stream — plus a full-width banner when the run reaches a
terminal state. Dispatches nothing, touches no git, never imports the runner's
dispatch code; a run dir is the only contract.

Usage:
    forge-monitor.py (--run-dir DIR | --latest) [--poll SECONDS]

Run it in a second terminal while the runner executes. `rich` is required
(the runner and the rest of forge stay stdlib-only); a missing rich exits 1
with an install hint rather than a traceback.
"""
import argparse
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import forge_status

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text
    _HAVE_RICH = True
except ImportError:  # pragma: no cover - exercised via the install-hint path
    _HAVE_RICH = False

# Direction B — "Instrument" palette.
FG = "#d3dae2"
DIM = "#66717f"
EDGE = "#212a34"
CYAN = "#5ad0df"
GREEN = "#59c26b"
PEND = "#3e4753"
HALT = "#f2683f"

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_GLYPH = {"passed": ("✓", GREEN), "escalated": ("■", HALT),
          "queued": ("○", PEND), "running": (None, CYAN)}


def _latest_run_dir(root=".forge/runs"):
    """The newest run dir under ``root`` (by mtime), or None."""
    try:
        entries = [os.path.join(root, n) for n in os.listdir(root)]
    except OSError:
        return None
    dirs = [p for p in entries if os.path.isdir(p)]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def _tail(path, max_lines):
    """Last ``max_lines`` lines of ``path`` (list, newline-stripped), or []."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return lines[-max_lines:]


def _fmt_elapsed(seconds):
    if seconds is None or seconds < 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    return "{}:{:02d}".format(m, s)


def _elapsed_secs(started_at, ended_at, now):
    start = forge_status._parse_iso(started_at)
    if start is None:
        return None
    end = forge_status._parse_iso(ended_at) if ended_at else now
    return end - start


def _status_label(state):
    st = state["state"]
    if st == "running":
        return "STALLED?" if state.get("stale") else "RUNNING"
    return {"completed": "COMPLETE", "halted": "HALTED",
            "contract-error": "CONTRACT ERROR"}.get(st, st.upper())


def _ledger_panel(state, now, frame):
    tasks = state.get("tasks") or []
    total = len(tasks)
    passed = sum(1 for t in tasks if t.get("status") == "passed")
    started = forge_status._parse_iso(state.get("started_at"))
    overall = _fmt_elapsed(now - started if started else None)
    rework = sum(max(0, (t.get("attempts") or 0) - 1) for t in tasks)

    plan_name = os.path.basename(state.get("plan") or "—")
    status_style = HALT if _status_label(state) in ("STALLED?", "HALTED", "CONTRACT ERROR") else CYAN
    meta = Text()
    meta.append("plan  ", style=DIM); meta.append(plan_name + "\n", style=FG)
    meta.append("run   ", style=DIM); meta.append((state.get("run_dir") or "") + "\n", style=FG)
    meta.append("{}/{} tasks · elapsed {} · {} rework · ".format(passed, total, overall, rework), style=DIM)
    meta.append(_status_label(state), style=Style(color=status_style, bold=True))

    table = Table(box=None, show_header=False, expand=True, padding=(0, 1, 0, 0))
    table.add_column(width=2, justify="center")
    table.add_column(width=2, justify="right")
    table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    table.add_column(justify="left")
    table.add_column(justify="left")
    table.add_column(justify="right")
    run_terminal = state["state"] in ("completed", "halted", "contract-error")
    for t in tasks:
        st = t.get("status")
        glyph, color = _GLYPH.get(st, ("·", DIM))
        if st == "running" and run_terminal:
            # The run ended mid-task (e.g. a contract error) — don't animate a
            # spinner under a terminal banner; show the task as interrupted.
            glyph, color, phase = "·", DIM, "interrupted"
            st = "interrupted"
        elif st == "running":
            glyph = frame
            phase = (state.get("current_phase") or "running")
        else:
            phase = {"passed": "passed", "queued": "queued",
                     "escalated": "escalated"}.get(st, st or "")
        elapsed = _fmt_elapsed(_elapsed_secs(t.get("started_at"), t.get("ended_at"), now)
                               if st in ("passed", "running", "escalated") else None)
        rowstyle = Style(color=FG if st in ("running", "passed") else DIM,
                         bold=(st == "running"))
        table.add_row(
            Text(glyph, style=color),
            Text(str(t.get("number")), style=DIM),
            Text(t.get("title") or "", style=rowstyle),
            Text(t.get("tier") or "", style=DIM),
            Text(phase, style=Style(color=color if st in ("running", "escalated") else DIM)),
            Text(elapsed, style=DIM),
        )

    body = Group(meta, Text(""), table)
    return Panel(body, title="FORGE RUN", title_align="left",
                 border_style=EDGE, box=box.SQUARE, padding=(0, 1))


def _live_panel(state, log_lines):
    cur = state.get("current_task")
    phase = state.get("current_phase")
    if cur is not None:
        title = "▸ task {} · {} · codex exec".format(cur, phase or "…")
    elif phase == "final-review":
        title = "▸ final review · codex exec"
    else:
        title = "▸ log"
    if log_lines:
        body = Text("\n".join(log_lines), style=FG, no_wrap=True, overflow="crop")
    else:
        body = Text("waiting for output…", style=DIM)
    return Panel(body, title=title, title_align="left",
                 border_style=CYAN if cur is not None else EDGE,
                 box=box.SQUARE, padding=(0, 1))


def _banner(state, now):
    tasks = state.get("tasks") or []
    total = len(tasks)
    passed = sum(1 for t in tasks if t.get("status") == "passed")
    started = forge_status._parse_iso(state.get("started_at"))
    elapsed = _fmt_elapsed(now - started if started else None)
    st = state["state"]
    review = state.get("final_review")
    if st == "completed":
        # Only claim "review clean" when a final review actually ran and passed;
        # an empty-diff or non-git run skips the reviewer entirely.
        review_note = " · review clean" if (review and review.get("verdict") == "pass") else ""
        line = Text("✓ RUN COMPLETE — {}/{} tasks passed{} · {}     press q to exit"
                    .format(passed, total, review_note, elapsed), style=Style(color="#08160c", bold=True))
        return Panel(line, box=box.HEAVY, style="on {}".format(GREEN), border_style=GREEN)
    if st == "halted":
        esc = next((t for t in tasks if t.get("status") == "escalated"), None)
        if esc is not None:
            n, k, finding = esc.get("number"), esc.get("attempts") or 0, esc.get("finding")
            head = "■ HALTED — task {} escalated after {} attempts     press q to exit".format(n, k)
        else:
            # Final-review escalation: no escalated task; the finding lives on the
            # final-review verdict, not a task receipt.
            head = "■ HALTED — {}     press q to exit".format(state.get("reason") or "")
            findings = (review or {}).get("findings") if review else None
            finding = findings[0] if findings else None
        ink = Style(color="#1c0d07", bold=True)
        lines = [Text(head, style=ink)]
        if finding:
            lines.append(Text(finding, style=Style(color="#1c0d07")))
        return Panel(Group(*lines), box=box.HEAVY, style="on {}".format(HALT), border_style=HALT)
    if st == "contract-error":
        line = Text("■ CONTRACT ERROR — {}     press q to exit".format(state.get("reason") or ""),
                    style=Style(color="#1c0d07", bold=True))
        return Panel(line, box=box.HEAVY, style="on {}".format(HALT), border_style=HALT)
    return None


def _render(state, log_lines, now=None, frame="⠙"):
    """The full monitor frame: ledger panel + live-tail panel, plus a terminal-state
    banner when the run has finished, halted, or errored. Pure over ``state`` +
    ``log_lines`` (no I/O), so it snapshots cleanly under a recording Console."""
    if now is None:
        now = time.time()
    parts = [_ledger_panel(state, now, frame), _live_panel(state, log_lines)]
    banner = _banner(state, now)
    if banner is not None:
        parts.append(banner)
    return Group(*parts)


def _is_terminal(state):
    # Only a terminal run.json status ends the watch. A stale `running` run is NOT
    # terminal — the monitor keeps polling and shows `stalled?`, because the cutoff
    # can trip on a long silent (but healthy) phase; exiting would abandon a live
    # run. A genuinely dead run just stays on `stalled?` until the user quits.
    return state["state"] in ("completed", "halted", "contract-error")


def _current_log_path(run_dir, state):
    if state.get("current_phase") == "final-review":
        return os.path.join(run_dir, "final-review-live.log")
    cur = state.get("current_task")
    if cur is not None:
        return os.path.join(run_dir, "task-{}-live.log".format(cur))
    return None


def _wait_for_quit():  # pragma: no cover - interactive
    """Block until the user presses q (or Ctrl-C) so the final frame + banner
    stay on screen. No-op when stdin isn't a TTY (piped/non-interactive)."""
    if not sys.stdin.isatty():
        return
    try:
        import termios
        import tty
    except ImportError:
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if not ch or ch.lower() == "q" or ch == "\x03":
                break
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _watch(run_dir, poll):  # pragma: no cover - interactive Live loop
    console = Console()
    log_lines_cap = max(8, console.size.height - 16)
    fi = 0
    terminal = False
    with Live(console=console, refresh_per_second=10, screen=False) as live:
        while True:
            state = forge_status.read_run_state(run_dir)
            if state is None:
                break
            frame = _SPINNER[fi % len(_SPINNER)]
            fi += 1
            log_path = _current_log_path(run_dir, state)
            lines = _tail(log_path, log_lines_cap) if log_path else []
            live.update(_render(state, lines, frame=frame))
            if _is_terminal(state):
                terminal = True
                break
            time.sleep(poll)
    # Keep the final frame + banner up until the operator dismisses it.
    if terminal:
        _wait_for_quit()
    return 0


def main(argv=None):
    if not _HAVE_RICH:
        print("forge-monitor requires 'rich' — install: pip install rich", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        prog="forge-monitor.py",
        description="Read-only live TUI for a forge-run execution.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-dir", help="the run dir to watch (.forge/runs/<stamp>)")
    group.add_argument("--latest", action="store_true",
                       help="watch the newest run under .forge/runs/")
    parser.add_argument("--poll", type=float, default=0.1,
                        help="seconds between state refreshes (default: 0.1)")
    args = parser.parse_args(argv)

    run_dir = args.run_dir if args.run_dir else _latest_run_dir()
    if not run_dir or forge_status.read_run_state(run_dir) is None:
        print("no run at {}".format(run_dir), file=sys.stderr)
        return 1
    return _watch(run_dir, args.poll)


if __name__ == "__main__":
    sys.exit(main())
