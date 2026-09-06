"""forge_status — run-state reader and renderer for `forge-run.py --status`.

Reads a run dir (`run.json` + per-task receipts) into a plain state dict and
renders the multi-line `--status` summary. Pure file reads — no dispatch, no
git, no subprocess — so a status check never perturbs a run.
"""
import datetime
import json
import os
import re
import shlex
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


def is_terminal(status):
    """True if run.json's top-level ``status`` (the same field
    ``read_run_state`` maps through ``_STATE_MAP``) names a finished run —
    the single public definition of that split, implemented directly
    against ``_STATE_MAP`` so a caller outside this module (e.g.
    forge_memory's ``defer --run``, which must refuse to write into a
    run.json that is still in progress) never needs its own copy of this
    vocabulary. An unrecognized status is NOT terminal, matching
    ``_STATE_MAP.get(raw, "running")``'s existing default — a status this
    version doesn't know about is presumed still running, the same way
    ``read_run_state`` already treats it, rather than guessed at."""
    return _STATE_MAP.get(status, "running") != "running"


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

    # Per-task list: prefer run.json summaries, fall back to receipts. A resumed
    # run whose remaining tasks were all already `passed` can leave a summary
    # stamped `queued` from the seed write (see forge-run.py) even though a
    # receipt already recorded the pass — a receipt's `passed` always outranks
    # a summary's stale `queued` for the same task number.
    if run and run.get("tasks"):
        summaries = run["tasks"]
        for s in summaries:
            r = receipts.get(s.get("number"))
            if r and s.get("status") == "queued" and r.get("status") == "passed":
                s["status"] = "passed"
                s["attempts"] = r.get("attempt", s.get("attempts", 1))
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
        # Raw run.json `status` (untranslated through _STATE_MAP), kept
        # alongside the mapped `state` so a caller that needs the exact
        # terminal/non-terminal split (render_status gating the staged-
        # deferrals review surface) can call `is_terminal` directly against
        # the same field it was written for, rather than re-deriving the
        # split from `state`.
        "status": raw_status,
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
        # The terse one-liner is for a run still in progress. Once the run
        # is terminal, the full close-out review surface (untruncated
        # summary + a pasteable `defer` command template per unfiled entry)
        # REPLACES it rather than adding to it — showing both would restate
        # every summary twice, in two different truncations, which degrades
        # the one thing this surface exists to do. `is_terminal` is the one
        # public definition of that split, checked against the same raw
        # `status` field it was written for (never re-derived from the
        # mapped `state`). Task 2's `defer --run` also refuses to write into
        # a non-terminal run.json, so a running run must never show a
        # filing command guaranteed to be rejected.
        if is_terminal(state.get("status")):
            run_json_path = os.path.join(state["run_dir"], "run.json")
            lines.append("")
            lines.extend(render_staged_deferrals(state, run_json_path))
        else:
            summaries = [_truncate(d.get("summary", "?")) for d in state["deferrals"]]
            lines.append("deferrals: {} — {}".format(len(summaries), "; ".join(summaries)))
    return "\n".join(lines)


def deferral_provenance(plan, entry, run_json_path):
    """The ``from`` field value for a runner-staged deferral: the plan the
    run executed, plus the stage that produced the entry — the
    ``"<plan>, Task N"`` shape ``skills/project-memory`` documents.

    One definition, used by both sides of the seam: the template
    ``render_staged_deferrals`` emits, and ``forge_memory.py defer``'s own
    derivation when a ``--run`` filing omits ``--from``. The one value it
    must never produce is ``user``, which the spec reserves for a deferral
    a human asked for directly (no close-out gate) — so when run.json
    records no plan, the fallback is the run.json path itself: still real
    provenance, and still not a claim that a human initiated it. That
    fallback is normalized to an absolute path because the two callers
    reach it by different routes — ``render_staged_deferrals`` derives it
    from the run dir, ``cmd_defer`` uses the path the user typed at
    ``--run`` — and one definition that returned two different strings for
    the same file would defeat the point of there being one.

    A staged entry is the runner's finding dict (``forge_common.
    finding_to_dict``) plus the stamp ``forge-run.py``'s ``stage_deferrals``
    adds. A per-task entry carries ``task_number`` (a bare ``task`` is also
    accepted). A final-review entry belongs to no single task, so it carries
    ``stage: "final-review"`` instead and reads ``"<plan>, final review"`` —
    named, rather than collapsing to a bare plan path indistinguishable from
    a task entry, and never given an invented task number."""
    source = plan or os.path.abspath(run_json_path)
    task = entry.get("task_number")
    if task is None:
        task = entry.get("task")
    if task is not None and str(task).strip() != "":
        return "{}, Task {}".format(source, task)
    if entry.get("stage") == "final-review":
        return "{}, final review".format(source)
    return str(source)


def render_staged_deferrals(state, run_json_path):
    """The close-out review surface for `state["deferrals"]`: one block per
    staged deferral.

    An entry already carrying an ``issue`` key (recorded by `forge_memory.py
    defer --run ... --finding-id ...`) renders as already filed, with its
    issue number, and emits no command — presence of the key is the whole
    test (Task 2 pins `issue` as always a string; never check its type or
    whether it looks numeric, since a file-store ref is an arbitrary slug,
    not a number).

    An unfiled entry renders its finding id, its FULL untruncated summary (so
    whoever files it has the material to write from), and a `forge_memory.py
    defer` command TEMPLATE with `--title` and `--why` left as visible
    placeholders. This module never fabricates a title or a why: a reviewer
    finding summary runs 100-200 chars against the deferral schema's 80-char
    title budget, budget overrun is an error not a truncation, and this is
    deterministic Python with no way to author prose within budget —
    authorship happens at the review gate, by a human or an LLM holding
    judgment this module doesn't have.

    The command carries ``--from`` explicitly. ``forge_memory.py defer``
    defaults an omitted ``from`` to ``user``, and ``from: user`` is the
    value the spec reserves for a deferral a human asked for directly —
    one that deliberately SKIPS this review gate. A template that omitted
    the flag would therefore file every runner-staged deferral under the
    one provenance it certainly does not have; ``deferral_provenance``
    supplies the real one from the run itself.

    ``--by agent`` is fixed, not derived: everything this function renders
    is a finding a REVIEWER raised during the run, so the issue it becomes
    is one an agent noticed. A deferral a human asked for directly never
    passes through here — it files immediately, with ``--by human``.
    The KIND label (feature/defect/debt/risk) is deliberately absent from
    the template: it is a human judgment, made after the issue exists.

    ``--occurrence`` is emitted only for a finding id that more than one
    staged entry shares. Finding ids are reviewer-authored per review and
    never namespaced, so a run's ``deferrals`` list can hold two entries
    with id ``F1``; the ordinal is what tells ``defer`` which of them a
    command means, so both can be filed and neither is attributed to the
    other's finding.

    ``run_json_path``, each finding id, and the provenance value are
    shlex-quoted before being interpolated into the emitted command, so the
    line is safe to paste even when a path or id carries a space, quote, or
    newline."""
    deferrals = state.get("deferrals") or []
    if not deferrals:
        return []

    all_ids = [e.get("id", "?") for e in deferrals]
    seen = {}

    lines = []
    for entry in deferrals:
        finding_id = entry.get("id", "?")
        seen[finding_id] = seen.get(finding_id, 0) + 1
        summary = (entry.get("summary") or "").strip()
        lines.append("deferral {}:".format(finding_id))
        lines.append("  {}".format(summary))
        issue = entry.get("issue")
        if issue is not None:
            lines.append("  filed as issue #{}".format(issue))
        else:
            command = (
                "  forge_memory.py defer --title <title> --why <why> "
                "--by agent --from {} --run {} --finding-id {}".format(
                    shlex.quote(
                        deferral_provenance(
                            state.get("plan"), entry, run_json_path,
                        )
                    ),
                    shlex.quote(run_json_path),
                    shlex.quote(finding_id),
                )
            )
            if all_ids.count(finding_id) > 1:
                command += " --occurrence {}".format(seen[finding_id])
            lines.append(command)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines
