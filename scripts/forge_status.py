"""forge_status — run-state reader and renderer for `forge-run.py --status`.

Reads a run dir (`run.json` + per-task receipts) into a plain state dict and
renders the multi-line `--status` summary. Pure file reads — no dispatch, no
git, no subprocess — so a status check never perturbs a run.
"""
import datetime
import json
import os
import re
import time

_ATTEMPT_RE = re.compile(r"^task-(\d+)-attempt-(\d+)\.json$")
_FINDING_MAX = 100

# A `running` run whose heartbeat (newest run.json/live-log write, or `updated_at`)
# is older than this is reported `stalled?` — resolves the killed-run-stuck-running
# deferral. A present-but-dead pid forces stale immediately, before the cutoff.
STALE_CUTOFF_S = 180

# run.json top-level status -> external state vocabulary.
_STATE_MAP = {
    "running": "running",
    "passed": "completed",
    "escalated": "halted",
    "escalated-final-review": "halted",
    "escalated-doc-sync": "halted",
    "contract-error": "contract-error",
}


def _load_run_json(run_dir):
    try:
        with open(os.path.join(run_dir, "run.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _latest_receipts(run_dir):
    """Map task number -> highest-attempt receipt dict."""
    best = {}  # number -> (attempt, dict)
    for name in os.listdir(run_dir):
        m = _ATTEMPT_RE.match(name)
        if not m:
            continue
        number, attempt = int(m.group(1)), int(m.group(2))
        if number in best and best[number][0] >= attempt:
            continue
        try:
            with open(os.path.join(run_dir, name), "r", encoding="utf-8") as f:
                best[number] = (attempt, json.load(f))
        except (OSError, ValueError):
            continue
    return {n: d for n, (a, d) in best.items()}


def _latest_mtime(run_dir):
    newest = 0.0
    for name in os.listdir(run_dir):
        if name.endswith(".json") or name.endswith(".log"):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(run_dir, name)))
            except OSError:
                pass
    return newest


def _parse_iso(value):
    """An ISO-8601 timestamp (``...Z`` accepted) as an epoch float, or None."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _is_stale(state, run_dir, updated_at, pid, now):
    """Whether a `running` run looks dead. Two independent liveness signals, each
    able to clear staleness (a `codex exec` phase can reason silently for minutes,
    so neither alone is sufficient):

    - Heartbeat: newest of ``updated_at`` and the run dir's newest file mtime. A
      heartbeat within STALE_CUTOFF_S means something is still writing → alive.
    - Pid: consulted only once the heartbeat has gone quiet. A live pid rescues a
      quiet-but-working run (spec: pid confirms); a dead pid confirms death; an
      unprobeable pid (cross-namespace/unusable) leaves the quiet heartbeat to
      govern → stale. Terminal states are never stale."""
    if state != "running":
        return False
    now_ts = time.time() if now is None else now
    candidates = [v for v in (_parse_iso(updated_at), _latest_mtime(run_dir)) if v]
    heartbeat = max(candidates) if candidates else 0.0
    if now_ts - heartbeat <= STALE_CUTOFF_S:
        return False  # fresh heartbeat — reliably alive
    if pid is not None:
        try:
            os.kill(int(pid), 0)
            return False  # process alive despite a quiet phase — not stale
        except ProcessLookupError:
            return True  # process gone — dead
        except PermissionError:
            return False  # exists but not ours — alive
        except (ValueError, OverflowError, TypeError):
            return True  # unusable pid — quiet heartbeat governs
    return True  # quiet heartbeat, no pid to consult


def _read_final_review(run_dir):
    """The plan-level final-review verdict dict (``{"verdict": ...}``) from
    ``final-review.json``, or None when no final review has run."""
    try:
        with open(os.path.join(run_dir, "final-review.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _truncate(text):
    text = text.strip().replace("\n", " ")
    return text if len(text) <= _FINDING_MAX else text[:_FINDING_MAX] + "…"


def read_run_state(run_dir, now=None):
    """Parse ``run.json`` + latest receipts into a state dict, or None when the
    dir is absent or holds neither. See module docstring for the shape. ``now``
    (epoch seconds; defaults to wall clock) seams the stale-run cutoff for tests."""
    if not os.path.isdir(run_dir):
        return None
    run = _load_run_json(run_dir)
    receipts = _latest_receipts(run_dir)
    if run is None and not receipts:
        return None

    raw_status = run.get("status") if run else None
    state = _STATE_MAP.get(raw_status, "running") if run else "running"

    # Per-task list: prefer run.json summaries, fall back to receipts.
    if run and run.get("tasks"):
        summaries = run["tasks"]
    else:
        summaries = [
            {"number": n, "status": r.get("status"), "attempts": r.get("attempt", 1)}
            for n, r in sorted(receipts.items())
        ]

    tasks = []
    for s in sorted(summaries, key=lambda x: x.get("number", 0)):
        number = s.get("number")
        finding = None
        halt_reason = None
        if s.get("status") == "escalated":
            r = receipts.get(number)
            outstanding = (r or {}).get("outstanding_findings") or []
            if outstanding:
                finding = _truncate(outstanding[0])
            halt_reason = (r or {}).get("halt_reason")
        tasks.append(
            {
                "number": number,
                "status": s.get("status"),
                "attempts": s.get("attempts", 1),
                "finding": finding,
                "halt_reason": halt_reason,
                "title": s.get("title"),
                "tier": s.get("tier"),
                "started_at": s.get("started_at"),
                "ended_at": s.get("ended_at"),
            }
        )

    # Halt-reason class (Disposition matrix spec: scope-decision | regression |
    # stuck | backstop | gate) rides the escalated receipt — the escalated
    # task's own attempt receipt for a task halt, or `final-review.json`'s
    # `halt_reason` field for a final-review halt. Absent on older/no-receipt
    # runs; `halt_class` stays None there, tolerated by render_status.
    final_review = _read_final_review(run_dir)
    reason = None
    halt_class = None
    if state == "contract-error":
        reason = (run or {}).get("contract_error") or "contract error"
    elif state == "halted":
        if raw_status == "escalated-final-review":
            reason = "final review escalated"
            halt_class = (final_review or {}).get("halt_reason")
        elif raw_status == "escalated-doc-sync":
            # Terminal doc-sync stage halt: the cause is the named doc/contract
            # contradiction on run.json's doc_sync record (no matrix halt class).
            ds = (run or {}).get("doc_sync") or {}
            reason = ds.get("contradiction") or "doc-sync contradiction"
        else:
            first = next((t for t in tasks if t["status"] == "escalated"), None)
            reason = "task {} escalated".format(first["number"]) if first else "escalated"
            halt_class = first["halt_reason"] if first else None

    current_task = run.get("current_task") if run else None
    current_phase = run.get("current_phase") if run else None
    started_at = run.get("started_at") if run else None
    updated_at = run.get("updated_at") if run else None
    pid = run.get("pid") if run else None

    return {
        "run_dir": run_dir,
        "plan": run.get("plan") if run else None,
        "state": state,
        "reason": reason,
        "halt_class": halt_class,
        "latest_mtime": _latest_mtime(run_dir),
        "current_task": current_task,
        "current_phase": current_phase,
        "started_at": started_at,
        "updated_at": updated_at,
        "stale": _is_stale(state, run_dir, updated_at, pid, now),
        "final_review": final_review,
        "tasks": tasks,
        # Scope-autonomy fields (Receipts / run.json spec): additive, absent on
        # old run.json shapes — deferrals defaults to [] (always a list),
        # autofix_mode/doc_sync default to None like the other optional fields.
        "deferrals": (run.get("deferrals") if run else None) or [],
        "autofix_mode": run.get("autofix_mode") if run else None,
        "doc_sync": run.get("doc_sync") if run else None,
    }


def render_status(state):
    """Multi-line ``--status`` output: header line + one line per task."""
    label = "STALLED?" if (state["state"] == "running" and state.get("stale")) else state["state"].upper()
    header = "run {}: {}".format(state["run_dir"], label)
    if state["reason"] and state["state"] in ("halted", "contract-error"):
        header += " — " + state["reason"]
        if state.get("halt_class"):
            header += " ({})".format(state["halt_class"])
    lines = [header]
    for t in state["tasks"]:
        line = "task {}: {}, attempts {}".format(t["number"], t["status"], t["attempts"])
        if t["finding"]:
            line += " — " + t["finding"]
        lines.append(line)
    if state.get("deferrals"):
        summaries = [_truncate(d.get("summary", "?")) for d in state["deferrals"]]
        lines.append("deferrals: {} — {}".format(len(summaries), "; ".join(summaries)))
    return "\n".join(lines)
