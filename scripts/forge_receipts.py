"""forge_receipts — JSON receipts, run.json summary, and plan-checkbox ledger.

The receipts are the only resume state (Resume spec): per-task attempt receipts,
the ``base_commit``/``tasks`` carried in ``run.json``, the final-review receipt,
the self-ignoring ``.forge/.gitignore``, and the plan-checkbox annotations that
double as the durable ledger.
"""
import datetime
import json
import os
import re

from forge_common import verdict_to_dict


def utc_iso():
    """Current UTC time as an ISO-8601 ``...Z`` string (run.json timestamps)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update_run_progress(run_dir, current_task, current_phase):
    """Partial-update run.json's live pointer (``current_task``/``current_phase``/
    ``updated_at``) at a phase transition, preserving every other field, so the
    monitor's top panel tracks the in-flight task and phase. Silent no-op if
    run.json is missing or unreadable — a progress ping must never break a run."""
    path = os.path.join(run_dir, "run.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    data["current_task"] = current_task
    data["current_phase"] = current_phase
    data["updated_at"] = utc_iso()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_receipt(run_dir, task, attempt, receipt_dict):
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "task-{}-attempt-{}.json".format(task.number, attempt))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(receipt_dict, f, indent=2)
    return path


def _read_base_commit(run_dir):
    """The ``base_commit`` persisted in an existing ``run.json`` (first-invocation
    HEAD, the whole-plan final-review diff base), or ``None`` when there is no
    prior run.json — so a resume reuses the original base rather than a HEAD that
    has advanced past already-committed tasks."""
    path = os.path.join(run_dir, "run.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("base_commit")
    except (OSError, ValueError):
        return None


def _read_started_at(run_dir):
    """The ``started_at`` persisted in an existing ``run.json``, or None — so a
    resume keeps the original run start (and elapsed) rather than resetting it."""
    path = os.path.join(run_dir, "run.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("started_at")
    except (OSError, ValueError):
        return None


def _read_run_tasks(run_dir):
    """The ``tasks`` list from an existing ``run.json`` (used on resume to carry
    a passed task's recorded commit SHA forward), or ``None`` when absent."""
    path = os.path.join(run_dir, "run.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("tasks")
    except (OSError, ValueError):
        return None


def write_run_json(run_dir, plan_path, spec_path, status, task_summaries, base_commit,
                   contract_error=None, current_task=None, current_phase=None,
                   started_at=None, updated_at=None, pid=None,
                   deferrals=None, autofix_mode=None, doc_sync=None):
    """Write ``run.json``. The progress fields (``current_task``/``current_phase``/
    ``started_at``/``updated_at``/``pid``) and the scope-autonomy fields
    (``deferrals``/``autofix_mode``/``doc_sync``) are additive and optional —
    omitted when None, so an old run.json shape and a caller that passes none
    both stay valid. Per-task ``started_at``/``ended_at`` ride inside the
    caller's task summaries. ``deferrals`` is the aggregated defer-disposition
    finding list (Deferral handling spec); ``autofix_mode`` is ``"auto"`` |
    ``"gate"``; ``doc_sync`` is the terminal reconcile-stage record."""
    os.makedirs(run_dir, exist_ok=True)
    data = {
        "plan": os.path.abspath(plan_path),
        "spec": os.path.abspath(spec_path),
        "status": status,
        "base_commit": base_commit,
        "tasks": task_summaries,
    }
    if contract_error is not None:
        data["contract_error"] = contract_error
    for key, value in (
        ("current_task", current_task),
        ("current_phase", current_phase),
        ("started_at", started_at),
        ("updated_at", updated_at),
        ("pid", pid),
        ("deferrals", deferrals),
        ("autofix_mode", autofix_mode),
        ("doc_sync", doc_sync),
    ):
        if value is not None:
            data[key] = value
    path = os.path.join(run_dir, "run.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def write_final_review_receipt(run_dir, verdict, halt_reason=None):
    """Persist the plan-level final-review verdict alongside the task receipts.
    ``halt_reason`` (the convergence loop's disposition-matrix class —
    scope-decision | regression | stuck | backstop | gate) is additive and
    optional, like the per-task receipt's field of the same name — omitted
    when None so an in-progress or passed final review's receipt is unchanged,
    and read back by `forge_status.read_run_state` for an
    ``escalated-final-review`` run's halt class."""
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "final-review.json")
    data = verdict_to_dict(verdict)
    if halt_reason is not None:
        data["halt_reason"] = halt_reason
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


_ATTEMPT_RE = re.compile(r"^task-(\d+)-attempt-(\d+)\.json$")


def _read_latest_receipt(run_dir, task_number):
    """The highest-attempt receipt dict for a task in ``run_dir``, or None. The
    receipts are the only resume state — no separate store (Resume spec)."""
    if not os.path.isdir(run_dir):
        return None
    best_attempt = -1
    best_name = None
    for name in os.listdir(run_dir):
        m = _ATTEMPT_RE.match(name)
        if m and int(m.group(1)) == task_number and int(m.group(2)) > best_attempt:
            best_attempt = int(m.group(2))
            best_name = name
    if best_name is None:
        return None
    with open(os.path.join(run_dir, best_name), "r", encoding="utf-8") as f:
        return json.load(f)


def latest_status(run_dir, task_number):
    """Latest receipt status for a task (``passed`` | ``rework`` | ``escalated``),
    or None when the task has no receipt yet."""
    receipt = _read_latest_receipt(run_dir, task_number)
    return receipt.get("status") if receipt else None


def _clear_task_receipts(run_dir, task_number):
    """Remove a task's prior receipts, plus its stale reviewer last-message file,
    so a re-run writes a clean attempt sequence (attempt-1, attempt-2) and can
    never re-read a prior run's verdict — the reviewer call also clears the file,
    this closes the gap on resume when the reviewer is never reached."""
    if not os.path.isdir(run_dir):
        return
    for name in os.listdir(run_dir):
        m = _ATTEMPT_RE.match(name)
        if m and int(m.group(1)) == task_number:
            os.remove(os.path.join(run_dir, name))
    stale_review = os.path.join(run_dir, "task-{}-review-last.txt".format(task_number))
    if os.path.exists(stale_review):
        os.remove(stale_review)


def ensure_forge_gitignore(cwd):
    """Self-ignoring ``.forge/.gitignore`` containing ``*`` — no target-repo
    setup required (Receipts spec, 2026-07-13 amendment). Idempotent: only
    written if absent."""
    forge_dir = os.path.join(cwd, ".forge")
    os.makedirs(forge_dir, exist_ok=True)
    path = os.path.join(forge_dir, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("*\n")
    return path


def write_watch_launcher(cwd, monitor_path):
    """Write ``.forge/watch`` — a one-line launcher for the standing monitor
    (`forge-monitor.py --follow`) with the monitor's absolute path baked in, so the
    runner can print a short `sh .forge/watch` instead of a long plugin path that
    line-wraps in the session and is hard to click/copy. Idempotent; `.forge/` is
    gitignored, so the launcher never dirties the tree."""
    forge_dir = os.path.join(cwd or ".", ".forge")
    os.makedirs(forge_dir, exist_ok=True)
    path = os.path.join(forge_dir, "watch")
    with open(path, "w", encoding="utf-8") as f:
        f.write('#!/bin/sh\nexec python3 "{}" --follow "$@"\n'.format(monitor_path))
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return path


def annotate_ledger(plan_path, task, status_line):
    """Append ``— <status_line>`` to the task's plan checkbox line. Only a
    passed outcome checks the box (``[x] ... — passed, N attempt(s)``); an
    escalated outcome (``status_line`` starting with ``escalated``) leaves the
    checkbox unchecked. Idempotent: replaces any prior annotation and prior
    check state."""
    if task.checkbox_line < 0:
        return
    with open(plan_path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines(keepends=True)
    raw = lines[task.checkbox_line]
    nl = "\n" if raw.endswith("\n") else ""
    body = raw[: len(raw) - len(nl)] if nl else raw
    box = "[ ]" if status_line.startswith("escalated") else "[x]"
    body = re.sub(r"\[[ xX]\]", box, body, count=1)
    body = re.sub(r"\s+—\s.*$", "", body)
    lines[task.checkbox_line] = body + " — " + status_line + nl
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
