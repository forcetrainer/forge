#!/usr/bin/env python3
"""forge-run.py — deterministic whole-plan task runner over ``codex exec``.

Scope: the sequential task loop (dependency order), worker dispatch via one
``codex exec`` process per task, direct acceptance-command execution, standard/
complex reviewer dispatch with a machine-parsed JSON verdict, the 2-iteration
rework cap enforced as a loop counter, mechanical halt on escalation, resume
(skip tasks already ``passed`` in the run-dir), a plan-level final review against
the whole-plan diff + spec, JSON receipts, a ``run.json`` summary, and plan-
checkbox ledger annotations.

Usage:
    forge-run.py <plan.md> --spec <spec.md> [--run-dir DIR] [--codex-bin PATH]

Exit codes:
    0  every task passed
    1  contract/usage error (malformed plan, brief generation failure,
       review-packet generation failure, unparseable reviewer verdict,
       reviewer process crash)
    2  halted on an escalated task

Structure: this module owns dispatch, review, the per-task rework loop, the plan
loop, and the CLI. Its supporting concerns live in sibling modules, imported
below and re-exported into this namespace (the test suite and ``codex-execution``
docs address them as ``forge_run.<name>``):
    forge_common    — dataclasses, tier/effort constants, ``eb``/``rp`` loaders
    forge_plan      — plan parsing, task ordering, effort-override parsing
    forge_git       — git helpers + review-packet assembly
    forge_receipts  — receipts, run.json, plan-checkbox ledger

Reuses ``extract-brief.py``/``review-packet.py`` (via forge_common) for all
plan/spec parsing and packet assembly — no duplicated heading grammar. Tier ->
model/effort mapping lives in exactly one table (``TIER_MAP``). All parse
failures raise loudly naming the cause (DECISIONS 2026-07-11); ``ultra``
reasoning effort is never emitted (DECISIONS 2026-07-13).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)

# The helper modules are plain (underscore-named) siblings. Put SCRIPTS_DIR on
# the path so they import as normal modules — ``sys.modules`` then caches one
# instance of each (notably forge_common), so the shared dataclasses keep a
# single identity across every module and the test suite. (extract-brief.py /
# review-packet.py stay importlib-loaded inside forge_common: hyphenated names.)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import forge_checklist
import forge_common
import forge_dispose
import forge_git
import forge_plan
import forge_receipts
import forge_status

# Re-export the sibling API into this namespace: the runner's own code below
# calls these by bare name, and tests/docs address them as ``forge_run.<name>``.
from forge_common import (  # noqa: F401
    ALLOWED_EFFORTS,
    AUTOFIX_MODES,
    CONTRACT_AGENT,
    DEFAULT_TIMEOUT,
    MAX_ATTEMPTS_BACKSTOP,
    REVIEW_VERDICT_INSTRUCTION,
    TIER_MAP,
    TIER_ORDER,
    AcceptanceResult,
    Finding,
    Task,
    TaskOutcome,
    Verdict,
    WorkerResult,
    eb,
    finding_to_dict,
    rp,
    run_json_teed,
    run_teed,
    verdict_to_dict,
)
from forge_dispose import (  # noqa: F401
    ConvergenceState,
    _canon,
    _finding_from_obj,
    _is_execution_failure,
    _real_fix_canons,
    _verdict_from_obj,
    advance_state,
    classify_findings,
    convergence_decision,
    derive_disposition,
    diff_line_ranges,
    parse_verdict,
    verify_provenance,
)
from forge_git import (  # noqa: F401
    _final_packet,
    _git_commit_task,
    _git_diff,
    _git_head,
    _packet_for,
    _working_tree_dirty,
)
from forge_plan import (  # noqa: F401
    order_tasks,
    parse_effort_overrides,
    parse_plan_tasks,
)
from forge_receipts import (  # noqa: F401
    _clear_task_receipts,
    _read_base_commit,
    _read_latest_receipt,
    _read_run_tasks,
    _read_started_at,
    annotate_ledger,
    ensure_forge_gitignore,
    latest_status,
    update_run_progress,
    utc_iso,
    write_final_review_receipt,
    write_receipt,
    write_run_json,
    write_watch_launcher,
)


# --- worker dispatch --------------------------------------------------------


def _agents_dir():
    return os.environ.get("FORGE_AGENTS_DIR") or os.path.join(REPO_ROOT, "agents")


def contract_preamble(tier):
    """Worker-contract text: agents/<tier-agent>.md body with YAML frontmatter
    stripped. Missing source raises (a worker with no contract is a silent
    degradation)."""
    agent = CONTRACT_AGENT[tier]
    path = os.path.join(_agents_dir(), agent + ".md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise RuntimeError("worker contract source missing: {}: {}".format(path, e))
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _render_event_line(event):
    """Map one ``codex exec --json`` event to a plain-text ``task-N-live.log``
    line, or ``None`` for an event with no textual content to show. Only
    ``item.completed`` events carry renderable text (the terminal state of one
    output item — ``item.started``/``item.updated`` are progress deltas on the
    same item, and rendering those too would duplicate the same text); every
    other event type here (``thread.started``, ``turn.started``,
    ``turn.completed``, ``turn.failed``, and any other control/bookkeeping
    event) is control-only and renders nothing — that is how a JSONL event
    stream becomes a live log with **zero raw JSON object lines** (the parity
    bar this function exists to meet, not an improvement over the old
    human-readable stream it replaces)."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    if etype == "item.completed":
        item = event.get("item") or {}
        item_type = item.get("item_type") or item.get("type")
        if item_type == "command_execution":
            lines = []
            command = item.get("command")
            if command:
                lines.append("$ {}".format(command))
            output = item.get("aggregated_output")
            if output:
                lines.append(output.rstrip("\n"))
            return "\n".join(lines) if lines else None
        # agent_message, reasoning, file_change, and any other item type this
        # runner doesn't specifically special-case: fall back to its own `text`
        # field when present (still zero raw JSON — a text field is a string).
        text = item.get("text")
        return text if text else None
    if etype == "error":
        message = event.get("message")
        return "error: {}".format(message) if message else None
    return None


def dispatch_worker(task, brief_path, codex_bin, run_dir, threads=None,
                     effort_override=None, timeout=DEFAULT_TIMEOUT,
                     resume_thread=None):
    """One ``codex exec`` worker process, tier-pinned model/effort (``effort_
    override`` replaces only the effort, never the model, for a per-task
    ``--effort N=LEVEL`` bump). Cold (``resume_thread=None``): prompt = contract
    preamble + brief. Resumed (``resume_thread`` set to a prior ``codex exec``
    thread id): prompt = ``brief_path``'s content verbatim, no preamble — the
    resumed worker already holds the contract and the original brief from its
    cold-spawn attempt (Session continuity spec), so the caller passes a
    findings-only prompt file on a rework resume rather than the full brief.
    Argv is ``codex exec resume --json --output-last-message <path> -m <model>
    -c 'model_reasoning_effort="<effort>"' <thread_id> <prompt>`` on resume —
    tier pinning and last-message capture preserved exactly as on a cold spawn.
    Returns the exit code, last message, and the exact argv emitted. A hung
    process is killed at ``timeout`` seconds and reported as ``timed_out=True``
    — the caller treats that exactly like a failed iteration, never hangs the
    run. Dispatched with ``--json`` (never ``--ephemeral``); the captured
    ``thread_id`` (Codex mechanics spec) is written into ``threads`` under this
    task's worker role and also carried on the returned ``WorkerResult``.
    ``threads`` is optional — omitted (or None), a fresh dict is used and the
    captured id is simply not persisted anywhere the caller can see."""
    if threads is None:
        threads = {}
    model, effort = TIER_MAP[task.tier]
    if effort_override is not None:
        effort = effort_override
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = f.read()
    last_msg_path = os.path.join(run_dir, "task-{}-worker-last.txt".format(task.number))
    if resume_thread:
        prompt = brief
        argv = [
            codex_bin,
            "exec",
            "resume",
            "--json",
            "--output-last-message",
            last_msg_path,
            "-m",
            model,
            "-c",
            'model_reasoning_effort="{}"'.format(effort),
            resume_thread,
            prompt,
        ]
    else:
        preamble = contract_preamble(task.tier)
        prompt = preamble + "\n\n" + brief
        argv = [
            codex_bin,
            "exec",
            "--json",
            "-m",
            model,
            "-c",
            "model_reasoning_effort={}".format(effort),
            "--output-last-message",
            last_msg_path,
            prompt,
        ]
    live_path = os.path.join(run_dir, "task-{}-live.log".format(task.number))
    events_path = os.path.join(run_dir, "task-{}-worker-events.jsonl".format(task.number))
    header = "── worker · codex exec{} · {} · {} ──".format(
        " resume" if resume_thread else "", model, effort
    )
    result = run_json_teed(
        argv, timeout=timeout, live_path=live_path, events_path=events_path,
        header=header, render_line=_render_event_line,
    )
    role = "task-{}-worker".format(task.number)
    if result.thread_id:
        threads[role] = result.thread_id
    if result.timed_out:
        return WorkerResult(
            exit_code=None, last_message="", argv=argv, timed_out=True,
            thread_id=result.thread_id,
        )
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return WorkerResult(
        exit_code=result.exit_code, last_message=last_message, argv=argv,
        thread_id=result.thread_id,
    )


def run_acceptance(task, cwd, live_path=None):
    """Run each acceptance command directly (shell) in ``cwd``; capture exit code
    and an output tail. Output is tee'd to ``live_path`` (the task's live log) so
    the monitor sees acceptance output scroll; ``live_path=None`` (unit calls)
    tees to os.devnull, preserving the returned tail either way. A timed-out
    command is a non-zero (failed) acceptance."""
    lp = live_path or os.devnull
    results = []
    for cmd in task.acceptance_commands:
        header = "── acceptance ──\n$ {}".format(cmd)
        result = run_teed(
            cmd, shell=True, cwd=cwd, timeout=DEFAULT_TIMEOUT, live_path=lp, header=header
        )
        results.append(
            AcceptanceResult(
                command=cmd,
                exit_code=result.exit_code if not result.timed_out else -1,
                output_tail=result.tail,
            )
        )
    return results


def _dispatch_review_call(model, effort, preamble, packet_path, codex_bin, last_msg_path,
                           live_path, events_path, header, role, threads,
                           timeout=DEFAULT_TIMEOUT, resume_thread=None):
    """Shared plumbing for per-task and final reviewers: one ``codex exec`` call,
    prompt = review preamble + verdict instruction + packet; returns the parsed
    Verdict. Fail-loud on a crashed reviewer, a timed-out reviewer, or an
    unparseable verdict — never silently trusts or reuses stale output (Halt
    spec; ``parse_verdict`` never retries silently). The last-message file is
    cleared before the call so a prior attempt's verdict can never be re-read,
    and the reviewer's own exit code is checked (unlike a worker crash, a
    reviewer crash yields no verdict to judge, so it halts the run rather than
    consuming a rework iteration). A reviewer that hangs past ``timeout`` is
    handled the same way — it also yields no verdict to judge. Dispatched with
    ``--json`` (never ``--ephemeral``); the captured ``thread_id`` (Codex
    mechanics spec) is written into ``threads`` under ``role``. ``resume_thread``
    (Session continuity spec), when set, resumes that prior ``codex exec``
    thread instead of spawning cold — argv becomes ``codex exec resume --json
    --output-last-message <path> -m <model> -c 'model_reasoning_effort=
    "<effort>"' <thread_id> <prompt>``, tier pinning and last-message capture
    preserved exactly as on a cold spawn. The prompt (preamble + verdict
    instruction + packet) is unchanged by resuming — only the dispatch
    mechanics differ; scoping the packet itself down for a resumed verification
    lap is a separate concern (Delta-scoped verification packets)."""
    with open(packet_path, "r", encoding="utf-8") as f:
        packet = f.read()
    prompt = preamble + "\n\n" + REVIEW_VERDICT_INSTRUCTION + "\n\n" + packet
    if resume_thread:
        argv = [
            codex_bin,
            "exec",
            "resume",
            "--json",
            "--output-last-message",
            last_msg_path,
            "-m",
            model,
            "-c",
            'model_reasoning_effort="{}"'.format(effort),
            resume_thread,
            prompt,
        ]
    else:
        argv = [
            codex_bin,
            "exec",
            "--json",
            "-m",
            model,
            "-c",
            "model_reasoning_effort={}".format(effort),
            "--output-last-message",
            last_msg_path,
            prompt,
        ]
    if os.path.exists(last_msg_path):
        os.remove(last_msg_path)  # never re-read a prior attempt's message
    result = run_json_teed(
        argv, timeout=timeout, live_path=live_path, events_path=events_path,
        header=header, render_line=_render_event_line,
    )
    if result.thread_id:
        threads[role] = result.thread_id
    if result.timed_out:
        raise RuntimeError(
            "reviewer process ({} at effort {}) timed out after {}s without a "
            "usable verdict".format(model, effort, timeout)
        )
    if result.exit_code != 0:
        # The raw-stream tail survives (a non-JSON line is still teed into it),
        # so a crashed reviewer still names its cause.
        stderr_tail = (result.tail or "").strip()[:300]
        raise RuntimeError(
            "reviewer process ({} at effort {}) exited {} without a usable "
            "verdict{}".format(
                model,
                effort,
                result.exit_code,
                ": " + stderr_tail if stderr_tail else "",
            )
        )
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return parse_verdict(last_message)


def dispatch_reviewer(task, packet_path, codex_bin, run_dir, threads=None,
                       timeout=DEFAULT_TIMEOUT, resume_thread=None):
    """Per-task reviewer via ``codex exec`` — fresh context at the *same tier as
    the task it reviews* (routed by TIER_MAP[task.tier]; reviewer strength never
    escalates past the task's own tier). Preamble = the tier agent's review
    paragraph. Returns the parsed Verdict. ``threads`` is optional — omitted
    (or None), a fresh dict is used and the captured thread id is not
    persisted anywhere the caller can see. ``resume_thread`` (Session
    continuity spec), when set, resumes that prior thread instead of spawning
    cold — the caller is responsible for the discovery-cold /
    verification-resumed rule; this function only carries out whichever mode
    it is asked for."""
    if threads is None:
        threads = {}
    model, effort = TIER_MAP[task.tier]
    preamble = contract_preamble(task.tier)
    last_msg_path = os.path.join(run_dir, "task-{}-review-last.txt".format(task.number))
    live_path = os.path.join(run_dir, "task-{}-live.log".format(task.number))
    events_path = os.path.join(run_dir, "task-{}-reviewer-events.jsonl".format(task.number))
    header = "── review · codex exec{} · {} · {} ──".format(
        " resume" if resume_thread else "", model, effort
    )
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path,
        live_path, events_path, header, "task-{}-reviewer".format(task.number),
        threads, timeout=timeout, resume_thread=resume_thread,
    )


def dispatch_final_review(packet_path, codex_bin, run_dir, tier, threads=None,
                          timeout=DEFAULT_TIMEOUT, resume_thread=None):
    """Whole-plan final review: one ``codex exec`` call at ``tier`` (TIER_MAP[tier]
    — the plan's highest task tier, not a pinned ceiling) with that tier's
    contract preamble against the whole-plan diff + spec. Returns the parsed
    Verdict; the header records the resolved model+effort. ``threads`` is
    optional — omitted (or None), a fresh dict is used and the captured
    thread id is not persisted anywhere the caller can see. ``resume_thread``
    accepted for interface parity with ``dispatch_reviewer`` (Session
    continuity spec); wiring it into ``run_final_review_loop`` is a later
    task's concern — this runner still dispatches this call cold unless a
    caller explicitly passes one."""
    if threads is None:
        threads = {}
    model, effort = TIER_MAP[tier]
    preamble = contract_preamble(tier)
    last_msg_path = os.path.join(run_dir, "final-review-last.txt")
    live_path = os.path.join(run_dir, "final-review-live.log")
    events_path = os.path.join(run_dir, "final-reviewer-events.jsonl")
    header = "── final review · codex exec{} · {} · {} ──".format(
        " resume" if resume_thread else "", model, effort
    )
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path,
        live_path, events_path, header, "final-reviewer", threads, timeout=timeout,
        resume_thread=resume_thread,
    )


# --- contract checklist + coverage retry ------------------------------------


def _checklist_or_skip(builder, *args):
    """Call a forge_checklist builder (build_task_checklist / build_final_
    checklist). An empty checklist is a *runner-level* skip, not an error: a
    task with no ``**Spec:**``, no plan ``**Global Constraints:**``, and only
    command-only ``**Acceptance:**`` clauses is a legal plan (both fields are
    optional) with no contract material for a reviewer to cover — forcing an
    error there would make legal plans unexecutable (Contract checklist spec,
    2026-08-21 amendment). forge_checklist.py's own CLI/library contract is
    unchanged: it still raises on an empty checklist; this only catches that
    specific raise at the runner boundary. Any other RuntimeError (an
    unresolvable ``**Spec:**`` name, a task declaring ``**Spec:**`` with no
    ``--spec`` given) is a genuine contract error and still propagates
    fail-loud. Returns ``(checklist_or_None, skipped)``."""
    try:
        return builder(*args), False
    except RuntimeError as e:
        if "is empty" in str(e):
            return None, True
        raise


def _coverage_retry_packet_path(packet_path, defects, run_dir, label):
    """Build the coverage-retry packet: the original packet plus a defects
    note naming exactly what's missing/unbacked, so the re-dispatched
    reviewer fixes only its coverage array. Written to a distinct path so the
    original packet (and its receipt) are untouched."""
    with open(packet_path, "r", encoding="utf-8") as f:
        packet = f.read()
    lines = [
        "",
        "",
        "## Coverage retry — your prior verdict's coverage array was invalid",
        "",
        "Fix these defects and resubmit your full verdict (unchanged apart "
        "from the corrected coverage array, unless a defect requires a real "
        "correction):",
        "",
    ]
    lines.extend("- {}".format(d) for d in defects)
    packet = packet.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    path = os.path.join(run_dir, "{}-coverage-retry.md".format(label))
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path


def _review_with_coverage(dispatch_call, packet_path, checklist, run_dir, label):
    """Dispatch a review and validate its verdict's coverage against
    ``checklist``. ``checklist`` falsy (None or empty — the empty-checklist
    skip case) skips validation entirely: no retry, coverage untouched
    (Contract checklist spec). Otherwise: validate the first verdict; on
    defects, re-dispatch **exactly once** with the defects named in the
    retry packet's prompt; a second invalid verdict is a contract error
    (raised, uncaught — same class as an unparseable verdict, never a halt).
    This is not a rework attempt: the caller must not advance the
    convergence attempt counter or touch ConvergenceState for the retry.
    Returns ``(verdict, coverage_retried)``."""
    verdict = dispatch_call(packet_path)
    if not checklist:
        return verdict, False
    defects = forge_dispose.validate_coverage(verdict, checklist)
    if not defects:
        return verdict, False
    retry_path = _coverage_retry_packet_path(packet_path, defects, run_dir, label)
    verdict = dispatch_call(retry_path)
    defects = forge_dispose.validate_coverage(verdict, checklist)
    if defects:
        raise RuntimeError(
            "reviewer verdict coverage still invalid after one retry: {}".format(
                "; ".join(defects)
            )
        )
    return verdict, True


# --- per-task execution & plan loop -----------------------------------------


def _brief_for(task, plan_path, spec_path, run_dir, attempt, findings):
    """Write the worker brief for one attempt and return its path + SHA-256. On a
    rework attempt (findings non-empty) the outstanding findings are appended so
    the re-dispatched worker sees exactly what to fix; the SHA covers that text."""
    brief = eb.build_brief(plan_path, task.number, spec_path)
    if findings:
        lines = ["", "", "## Rework — address these findings before resubmitting", ""]
        lines.extend("- {}".format(f) for f in findings)
        brief = brief.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    brief_path = os.path.join(
        run_dir, "task-{}-attempt-{}-brief.md".format(task.number, attempt)
    )
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief)
    sha = hashlib.sha256(brief.encode("utf-8")).hexdigest()
    return brief_path, sha


def _resume_findings_prompt(findings, run_dir, task, attempt):
    """Rework-resume prompt: the outstanding findings **only** — no brief
    re-paste, no spec (Session continuity spec). The resumed worker already
    holds the full brief and contract preamble from its cold-spawn attempt; a
    fixer with memory fixes the cause instead of patching the symptom, and
    re-pasting the brief here would defeat that. Overwritten fresh each
    resumed attempt, like ``_brief_for``'s brief."""
    lines = ["## Rework — address these findings before resubmitting", ""]
    lines.extend("- {}".format(f) for f in findings)
    prompt = "\n".join(lines) + "\n"
    path = os.path.join(
        run_dir, "task-{}-attempt-{}-resume-prompt.md".format(task.number, attempt)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt)
    return path


def _execution_failure_finding(detail):
    """An execution failure (worker crash/timeout, acceptance non-zero) as an
    implicit fix-retry finding: disposition ``fix`` so the attempt cannot converge
    to pass, but no provenance/impact so it never defers, scope-halts, or counts as
    a carried (stuck) review finding — only the regression (green->red) and
    backstop rules apply (Rework loop & convergence). ``detail`` is the reattempt
    guidance appended to the next brief and surfaced as the outstanding finding."""
    return Finding(
        id="exec-failure", summary=detail, file=None, lines=None,
        provenance=None, impact=None, disposition="fix",
    )


def execute_task(task, plan_path, spec_path, run_dir, codex_bin, cwd, threads,
                  effort_override=None, timeout=DEFAULT_TIMEOUT, autofix_mode="auto"):
    """Run one task through the convergence rework loop: worker -> acceptance ->
    (standard/complex) reviewer -> classify -> convergence_decision, backed by a
    per-task ConvergenceState. Each attempt yields ``pass`` (done), ``rework``
    (re-dispatch the worker with the outstanding fix findings appended to the
    brief and carried into the re-review packet), or ``halt`` (escalate with a
    halt reason + any drafted repair task). An execution failure (worker
    crash/timeout, acceptance non-zero) preempts the reviewer and is treated as an
    implicit fix-retry finding — never defers or scope-halts, but counts for
    regression (green->red) and the backstop. ``autofix_mode`` ``gate``
    short-circuits any reviewer finding to a halt. ``effort_override`` (from a
    per-task ``--effort N=LEVEL`` CLI flag) replaces only this task's worker
    effort, never the reviewer's. ``threads`` is the run-wide per-role
    ``codex exec`` session-id map (Codex mechanics spec); this task's worker
    and reviewer dispatches write their captured thread ids into it in
    place."""
    model, effort = TIER_MAP[task.tier]
    if effort_override is not None:
        effort = effort_override
    # Per-task review base = HEAD at task start (the prior task's commit; the
    # run-start commit for task 1). Each passed task commits, so the tree is clean
    # here and `git diff <review_base>` isolates this task's own changes. Taken
    # once (trivial tiers need no reviewer).
    review_base = _git_head(cwd) if task.tier != "trivial" else None
    state = ConvergenceState()
    findings_carry = []     # outstanding fix-finding summaries -> next brief
    prior_findings = []     # outstanding fix findings (dicts) -> next re-review packet
    live_path = os.path.join(run_dir, "task-{}-live.log".format(task.number))
    worker_role = "task-{}-worker".format(task.number)
    reviewer_role = "task-{}-reviewer".format(task.number)
    review_attempts = 0     # count of REVIEWS actually dispatched (may lag `attempt`
                             # when an execution failure preempted a review) — this,
                             # not `attempt`, decides discovery (0) vs verification (>0)

    attempt = 0
    while True:
        attempt += 1
        brief_path, brief_sha = _brief_for(
            task, plan_path, spec_path, run_dir, attempt, findings_carry
        )
        update_run_progress(run_dir, task.number, "worker")
        # Rework laps (attempt > 1) resume the worker with the outstanding
        # findings alone (Session continuity spec) — a fixer with memory fixes
        # the cause instead of patching the symptom. A missing thread id (the
        # cold spawn never captured one) or a resume that fails outright
        # (non-zero exit, missing session, context overflow) falls back to
        # exactly one cold spawn with the full brief; the fallback is recorded
        # on this attempt's receipt, never fatal (Continuity scope and failure).
        worker_resume_thread = threads.get(worker_role) if attempt > 1 else None
        worker_resume_fallback = attempt > 1 and worker_resume_thread is None
        # Pre-repair snapshot (Delta-scoped verification packets spec): every
        # rework lap dispatches a repair (resumed worker, or its cold
        # fallback below) — snapshot the tree just before that dispatch so a
        # later verification packet's delta is `git diff <repair_snapshot>`,
        # scoped to this repair alone rather than the task's whole
        # accumulated diff. Never taken on attempt 1 (nothing has repaired
        # anything yet); never mutates the working tree.
        repair_snapshot = forge_git.snapshot_tree(cwd) if attempt > 1 else None
        if worker_resume_thread:
            resume_prompt_path = _resume_findings_prompt(
                findings_carry, run_dir, task, attempt
            )
            worker = dispatch_worker(
                task, resume_prompt_path, codex_bin, run_dir, threads,
                effort_override=effort_override, timeout=timeout,
                resume_thread=worker_resume_thread,
            )
            if worker.timed_out or worker.exit_code != 0:
                worker_resume_fallback = True
                threads.pop(worker_role, None)  # stale session; don't retry it later
                worker = dispatch_worker(
                    task, brief_path, codex_bin, run_dir, threads,
                    effort_override=effort_override, timeout=timeout,
                )
        else:
            worker = dispatch_worker(
                task, brief_path, codex_bin, run_dir, threads,
                effort_override=effort_override, timeout=timeout,
            )
        update_run_progress(run_dir, task.number, "acceptance")
        acceptance = run_acceptance(task, cwd, live_path)

        worker_ok = worker.exit_code == 0 and not worker.timed_out
        acc_ok = all(r.exit_code == 0 for r in acceptance)

        review_verdict = None
        findings = []       # classified Finding objects for this attempt
        cause = None        # short human-readable cause for an execution failure
        coverage_skipped = None  # None: no review this attempt (unchanged)
        coverage_retry = False
        reviewer_resume_fallback = False

        if worker.timed_out:
            cause = "worker timed out after {}s".format(timeout)
            findings = [_execution_failure_finding(
                "Prior worker attempt timed out after {}s with no usable result — "
                "reattempt the task.".format(timeout))]
        elif not worker_ok:
            cause = "worker exited {}".format(worker.exit_code)
            findings = [_execution_failure_finding(
                "Prior worker attempt exited {} with no usable result — reattempt "
                "the task.".format(worker.exit_code))]
        elif not acc_ok:
            failed = next(r for r in acceptance if r.exit_code != 0)
            cause = "acceptance failed: {}".format(failed.command)
            findings = [_execution_failure_finding(
                "Acceptance command `{}` failed (exit {}). Output tail:\n{}".format(
                    failed.command, failed.exit_code, failed.output_tail))]
        elif task.tier != "trivial":
            # Trivial tier: acceptance is the whole verification. Standard/complex:
            # a reviewer judges the diff against the spec, and the runner verifies
            # provenance against that same diff and derives each finding's
            # disposition (the matrix) — the reviewer proposes, the runner decides.
            if review_base is None:
                raise RuntimeError(
                    "cannot generate review packet for task {}: cwd is not a git "
                    "repository".format(task.number)
                )
            update_run_progress(run_dir, task.number, "review")
            checklist, coverage_skipped = _checklist_or_skip(
                forge_checklist.build_task_checklist, plan_path, spec_path,
                task.number,
            )
            # Discovery (this task's first review) is always cold — an
            # independent first read is the entire justification for a
            # separate reviewer (DECISIONS 2026-07-16, 2026-07-17); resuming it
            # would hand the review to an agent that already holds the
            # worker's reasoning. Verification (every review after) resumes
            # the reviewer thread, with the same missing-id/failed-resume
            # cold fallback as the worker (Session continuity /
            # Continuity scope and failure specs). Once a fallback happens
            # within this attempt, the coverage-retry call (if any) also
            # stays cold rather than re-attempting a session already known bad.
            is_verification = review_attempts > 0
            if is_verification and repair_snapshot is not None:
                # Delta-scoped verification packet (Delta-scoped verification
                # packets spec): the resumed reviewer already holds the task
                # block, the full spec, and the whole-plan diff in session —
                # re-sending them is exactly the waste this phase removes.
                # Packet = outstanding findings + this repair's own delta
                # (`git diff <repair_snapshot>`) + the reduced checklist
                # (only items an outstanding finding's contract_ref names).
                # `checklist` is reassigned to that same reduced list so the
                # coverage validation below judges the verdict against
                # exactly what the reviewer was shown — validating against
                # the full checklist here would demand coverage of items the
                # packet never mentioned, forcing a spurious retry-then-error
                # on every verification lap of any multi-item checklist
                # (fixed 2026-08-21; the spec's own words: "Verification laps
                # carry the reduced checklist ... not the full one").
                delta_diff = _git_diff(cwd, repair_snapshot)
                checklist = (
                    forge_checklist.reduce_checklist(checklist, prior_findings)
                    if checklist else checklist
                )
                packet_text = rp.build_verification_packet(
                    prior_findings, delta_diff, checklist
                )
                packet_path = os.path.join(
                    run_dir, "task-{}-review.md".format(task.number)
                )
                with open(packet_path, "w", encoding="utf-8") as f:
                    f.write(packet_text)
            else:
                packet_path = _packet_for(
                    task, plan_path, run_dir, review_base, cwd,
                    prior_findings=prior_findings or None, checklist=checklist,
                )
            review_resume_state = {
                "thread": threads.get(reviewer_role) if is_verification else None,
            }
            reviewer_resume_fallback = is_verification and review_resume_state["thread"] is None

            def _reviewer_dispatch_call(p):
                nonlocal reviewer_resume_fallback
                resume_thread = review_resume_state["thread"]
                if resume_thread:
                    try:
                        return dispatch_reviewer(
                            task, p, codex_bin, run_dir, threads, timeout=timeout,
                            resume_thread=resume_thread,
                        )
                    except RuntimeError:
                        reviewer_resume_fallback = True
                        threads.pop(reviewer_role, None)
                        review_resume_state["thread"] = None
                return dispatch_reviewer(
                    task, p, codex_bin, run_dir, threads, timeout=timeout,
                )

            verdict, coverage_retry = _review_with_coverage(
                _reviewer_dispatch_call,
                packet_path, checklist, run_dir, "task-{}".format(task.number),
            )
            review_attempts += 1
            classify_findings(verdict, _git_diff(cwd, review_base))
            review_verdict = verdict_to_dict(verdict)
            findings = verdict.findings

        action, halt_reason = convergence_decision(
            findings, state, acc_ok, attempt, autofix_mode
        )
        advance_state(state, findings, acc_ok)

        fix_findings = [f for f in findings if f.disposition == "fix"]
        deferrals = [finding_to_dict(f) for f in findings if f.disposition == "defer"]
        halted = [f for f in findings if f.disposition == "halt"]
        repair_task = halted[0].repair_task if halted else None
        status = {"pass": "passed", "rework": "rework", "halt": "escalated"}[action]
        outstanding = [f.summary for f in findings] if action == "halt" else []

        receipt = {
            "task_number": task.number,
            "title": task.title,
            "tier": task.tier,
            "model": model,
            "effort": effort,
            "brief_path": os.path.abspath(brief_path),
            "brief_sha256": brief_sha,
            "worker_exit_code": worker.exit_code,
            "acceptance_results": [asdict(r) for r in acceptance],
            "review_verdict": review_verdict,
            "attempt": attempt,
            "status": status,
            "halt_reason": halt_reason,
            "outstanding_findings": outstanding,
            "repair_task": repair_task,
            "coverage_skipped": coverage_skipped,
            "coverage_retry": coverage_retry,
            "resume_fallback": worker_resume_fallback or reviewer_resume_fallback,
        }
        write_receipt(run_dir, task, attempt, receipt)

        if action == "pass":
            return TaskOutcome(status="passed", attempts=attempt, summary="",
                               deferrals=deferrals)
        if action == "halt":
            return TaskOutcome(
                status="escalated",
                attempts=attempt,
                summary=cause or "{}: {}".format(
                    halt_reason, "; ".join(outstanding) or "(unspecified)"),
                findings=outstanding,
                halt_reason=halt_reason,
                deferrals=deferrals,
                repair_task=repair_task,
            )
        # rework: carry the outstanding fix findings into the next brief (an
        # execution-failure retry finding included — its summary is reattempt
        # guidance) and the real reviewer fix findings into the next re-review
        # packet (the implicit crash marker carries no review identity).
        findings_carry = [f.summary for f in fix_findings]
        prior_findings = [
            finding_to_dict(f) for f in fix_findings if f.impact is not None
        ]


# --- final review: whole-plan review through the same convergence loop -----


def _final_review_fix_brief(spec_path, diff, findings, run_dir, attempt):
    """Write the final-review fix-dispatch prompt: spec + whole-plan diff (for
    context) + the outstanding ``fix`` findings to resolve. Never a full task
    brief — there is no task to reissue, only what the reviewer flagged as
    in-diff and contract-breaking. Overwritten fresh each attempt, like the
    per-task rework brief."""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_text = f.read()
    lines = ["## Final-review fix — resolve these findings", ""]
    for finding in findings:
        loc = " ({}:{})".format(finding.file, finding.lines) if finding.file else ""
        lines.append("- {}{}".format(finding.summary, loc))
    diff_body = diff if diff.strip() else "(no changes)\n"
    if not diff_body.endswith("\n"):
        diff_body += "\n"
    # Fence must outrun any backtick run in the diff so a diffed line like
    # " ```" (context-prefixed fence, ≤3-space indent) can't close it early
    # — same problem, same fix as review-packet.py's build_packet.
    longest_run = max(
        (len(m.group(0)) for m in re.finditer(r"`+", diff_body)), default=0
    )
    fence = "`" * max(3, longest_run + 1)
    diff_section = fence + "diff\n" + diff_body + fence + "\n"
    brief = (
        spec_text.rstrip("\n") + "\n\n"
        + "\n".join(lines) + "\n\n"
        + "## Whole-plan diff\n\n" + diff_section
    )
    brief_path = os.path.join(
        run_dir, "final-review-fix-attempt-{}-brief.md".format(attempt)
    )
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief)
    return brief_path


def dispatch_final_review_fix(brief_path, codex_bin, run_dir, tier, attempt, threads=None,
                              timeout=DEFAULT_TIMEOUT):
    """Final-review fix dispatch: one ``codex exec`` call scoped to the
    outstanding ``fix`` findings against the whole-plan diff — the final-review
    "worker" (Final review spec), never a full task brief re-dispatch. Same call
    shape as dispatch_worker (contract preamble + prompt, --output-last-message,
    timeout-bounded); tier = the plan's highest task tier. Dispatched with
    ``--json`` (never ``--ephemeral``); the captured ``thread_id`` is written
    into ``threads`` under the ``final-fixer`` role (shared across every
    fix-dispatch attempt in this run, like the live/events files below).
    ``threads`` is optional — omitted (or None), a fresh dict is used and the
    captured id is not persisted anywhere the caller can see."""
    if threads is None:
        threads = {}
    model, effort = TIER_MAP[tier]
    preamble = contract_preamble(tier)
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = f.read()
    prompt = preamble + "\n\n" + brief
    last_msg_path = os.path.join(
        run_dir, "final-review-fix-attempt-{}-last.txt".format(attempt)
    )
    argv = [
        codex_bin,
        "exec",
        "--json",
        "-m",
        model,
        "-c",
        "model_reasoning_effort={}".format(effort),
        "--output-last-message",
        last_msg_path,
        prompt,
    ]
    live_path = os.path.join(run_dir, "final-review-live.log")
    events_path = os.path.join(run_dir, "final-fixer-events.jsonl")
    header = "── final-review fix · codex exec · {} · {} ──".format(model, effort)
    result = run_json_teed(
        argv, timeout=timeout, live_path=live_path, events_path=events_path,
        header=header, render_line=_render_event_line,
    )
    if result.thread_id:
        threads["final-fixer"] = result.thread_id
    if result.timed_out:
        return WorkerResult(
            exit_code=None, last_message="", argv=argv, timed_out=True,
            thread_id=result.thread_id,
        )
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return WorkerResult(
        exit_code=result.exit_code, last_message=last_message, argv=argv,
        thread_id=result.thread_id,
    )


def _git_commit_final_review_fixes(cwd):
    """Commit every final-review fix-dispatch edit as one ``fix: final-review``
    commit, once the loop converges to pass — regardless of how many fix-dispatch
    attempts it took (Commit discipline: a single commit, not one per attempt).
    Returns the new HEAD SHA, or None when nothing was staged (the fix
    dispatch(es) made no net change) or ``cwd`` is not a git repo. Mirrors
    ``_git_commit_task``'s empty-commit guard; kept local rather than added to
    forge_git.py since there is no Task to key a per-task message off — the
    commit message here is fixed, not derived."""
    if _git_head(cwd) is None:
        return None
    add = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
    if add.returncode != 0:
        raise RuntimeError(
            "git add -A for final-review fix failed in {}: {}".format(
                cwd, add.stderr.strip()
            )
        )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=cwd, capture_output=True, text=True
    )
    if staged.returncode == 0:
        return None  # nothing staged -> skip, no empty commit
    commit = subprocess.run(
        ["git", "commit", "-m", "fix: final-review"], cwd=cwd,
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        raise RuntimeError(
            "git commit for final-review fix failed in {}: {}".format(
                cwd, commit.stderr.strip()
            )
        )
    return _git_head(cwd)


def run_final_review_loop(spec_path, run_base, run_dir, codex_bin, cwd, tier,
                          autofix_mode, threads=None, timeout=DEFAULT_TIMEOUT,
                          plan_path=None):
    """Whole-plan final review through the same convergence loop as
    ``execute_task`` (Final review spec: "now runs the same loop"). Diff base is
    always ``run_base`` (run-start HEAD) across every attempt — a fix dispatch's
    edits are left uncommitted, so each re-review's diff simply accumulates them;
    a single ``fix: final-review`` commit lands once the loop converges to pass,
    only if a fix was ever applied (Commit discipline). ``threads`` is the
    run-wide per-role session-id map; the final reviewer and fix dispatches
    write their captured thread ids into it in place. Optional — omitted (or
    None), a fresh dict is used and no caller can see the captured ids. Attempt
    1 is review-only (nothing to fix yet); from attempt 2 on, the "worker" is a
    dispatch_final_review_fix call scoped to the outstanding ``fix`` findings — a
    fix-dispatch crash/timeout preempts the re-review as an implicit
    execution-failure finding, exactly like ``execute_task``. Halt carries the
    drafted ``repair_task``. ``plan_path`` (optional; omitted -> no checklist
    generated, coverage validation skipped) is required to build the final
    contract checklist (the union of every task's Spec/acceptance clauses +
    integration items) — a caller with no plan handy (e.g. a unit test
    exercising the loop in isolation) still gets the loop's pre-Task-4
    behavior unchanged."""
    if threads is None:
        threads = {}
    state = ConvergenceState()
    fix_findings = []  # outstanding fix findings -> next attempt's fix dispatch
    prior_findings = []  # outstanding reviewer fix findings (dicts) -> next packet
    applied_fix = False
    coverage_skipped = None  # carries forward across a fix-dispatch-only attempt
    coverage_retry = False
    attempt = 0
    while True:
        attempt += 1
        exec_ok = True
        findings = []

        if fix_findings:
            update_run_progress(run_dir, None, "final-review-fix")
            diff = _git_diff(cwd, run_base)
            brief_path = _final_review_fix_brief(
                spec_path, diff, fix_findings, run_dir, attempt
            )
            worker = dispatch_final_review_fix(
                brief_path, codex_bin, run_dir, tier, attempt, threads, timeout=timeout
            )
            applied_fix = True
            if worker.timed_out:
                exec_ok = False
                findings = [_execution_failure_finding(
                    "Prior final-review fix dispatch timed out after {}s with no "
                    "usable result — reattempt.".format(timeout))]
            elif worker.exit_code != 0:
                exec_ok = False
                findings = [_execution_failure_finding(
                    "Prior final-review fix dispatch exited {} with no usable "
                    "result — reattempt.".format(worker.exit_code))]

        if exec_ok:
            update_run_progress(run_dir, None, "final-review")
            diff = _git_diff(cwd, run_base)
            checklist = None
            if plan_path is not None:
                checklist, coverage_skipped = _checklist_or_skip(
                    forge_checklist.build_final_checklist, plan_path, spec_path,
                )
            packet_path = _final_packet(
                spec_path, run_base, diff, run_dir,
                prior_findings=prior_findings or None, checklist=checklist,
            )
            verdict, coverage_retry = _review_with_coverage(
                lambda p: dispatch_final_review(
                    p, codex_bin, run_dir, tier, threads, timeout=timeout
                ),
                packet_path, checklist, run_dir, "final",
            )
            classify_findings(verdict, diff)
            write_final_review_receipt(
                run_dir, verdict, coverage_skipped=coverage_skipped,
                coverage_retry=coverage_retry,
            )
            findings = verdict.findings

        # The final review has no acceptance command that can regress, so the
        # acceptance signal is always green: a fix-dispatch crash is an implicit
        # fix-retry finding (rework to backstop), exactly like execute_task's
        # worker crash — never a spurious green->red regression (regression here
        # is only a resolved reviewer finding reappearing).
        action, halt_reason = convergence_decision(
            findings, state, True, attempt, autofix_mode
        )
        advance_state(state, findings, True)

        fix_findings = [f for f in findings if f.disposition == "fix"]
        # Carry only real reviewer fix findings (not the execution-failure
        # marker, which has no review identity) into the next re-review packet.
        prior_findings = [
            finding_to_dict(f) for f in fix_findings if f.impact is not None
        ]
        deferrals = [finding_to_dict(f) for f in findings if f.disposition == "defer"]
        halted = [f for f in findings if f.disposition == "halt"]
        repair_task = halted[0].repair_task if halted else None
        outstanding = [f.summary for f in findings] if action == "halt" else []

        if action == "pass":
            if applied_fix:
                _git_commit_final_review_fixes(cwd)
            return TaskOutcome(
                status="passed", attempts=attempt, summary="", deferrals=deferrals
            )
        if action == "halt":
            # Persist the halt-reason class onto the final-review receipt itself
            # (not just this in-memory TaskOutcome) — `--status` reads run.json +
            # receipts only, exactly like a per-task halt's receipt-carried
            # `halt_reason` (Receipts spec). `verdict` is always bound by here:
            # attempt 1 never has fix_findings so it always runs the `exec_ok`
            # branch that sets it; only a later fix-dispatch attempt can hit
            # `exec_ok=False`, and it carries the prior attempt's verdict forward.
            write_final_review_receipt(
                run_dir, verdict, halt_reason=halt_reason,
                coverage_skipped=coverage_skipped, coverage_retry=coverage_retry,
            )
            return TaskOutcome(
                status="escalated",
                attempts=attempt,
                summary="{}: {}".format(
                    halt_reason, "; ".join(outstanding) or "(unspecified)"),
                findings=outstanding,
                halt_reason=halt_reason,
                deferrals=deferrals,
                repair_task=repair_task,
            )
        # rework: fix_findings (set above) drives the next attempt's fix dispatch.


# --- terminal doc-sync stage ------------------------------------------------


# Reconcile-only instruction for the doc-sync worker. Kept here (not in the
# agents/*.md contract) because the doc_sync verdict shape is a runner concern,
# mirroring REVIEW_VERDICT_INSTRUCTION's split.
DOC_SYNC_INSTRUCTION = (
    "Reconcile EXISTING documentation to the shipped whole-plan diff below: "
    "update stale references, changed signatures/behavior, spec changelog "
    "entries, and ROADMAP status that the diff made inaccurate. Edit only docs "
    "that already exist and that the diff affects — never author new "
    "documentation, and never touch code. If you find a documentation/contract "
    "contradiction you cannot mechanically reconcile (a doc asserts something "
    "the shipped code now contradicts, and choosing the correct side is a human "
    "decision), make no edit and end your message with exactly one JSON object "
    'and nothing after it: {"doc_sync": "contradiction", "contradiction": '
    '"<what conflicts>"}. Otherwise make the edits directly and end with '
    '{"doc_sync": "reconciled"} (or {"doc_sync": "clean"} when nothing needed '
    "changing). Emit nothing parseable as JSON after that object."
)


@dataclass
class DocSyncResult:
    """Outcome of the terminal doc-sync stage. ``status`` is ``reconciled`` (docs
    were stale; the edits landed as one ``docs: sync`` commit), ``clean`` (no doc
    drift, no commit), or ``halt`` (an unreconcilable doc/contract contradiction —
    or a dispatch that produced no usable result — so the run stops for a human,
    no commit). ``commit`` is the ``docs: sync`` SHA when one landed; ``reconciled``
    the doc paths it touched; ``contradiction`` the named conflict/cause on a
    halt."""

    status: str
    commit: str | None = None
    reconciled: list = field(default_factory=list)
    contradiction: str | None = None


def _doc_sync_brief(spec_path, diff, run_dir):
    """Write the doc-sync prompt: the spec + reconcile-only instruction + the
    shipped whole-plan ``diff`` fenced with a dynamic-length fence (like
    _final_review_fix_brief, so a diff line that is itself a ``` fence can't close
    the block early). Overwritten fresh; there is exactly one doc-sync stage."""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_text = f.read()
    diff_body = diff if diff.strip() else "(no changes)\n"
    if not diff_body.endswith("\n"):
        diff_body += "\n"
    longest_run = max(
        (len(m.group(0)) for m in re.finditer(r"`+", diff_body)), default=0
    )
    fence = "`" * max(3, longest_run + 1)
    diff_section = fence + "diff\n" + diff_body + fence + "\n"
    brief = (
        spec_text.rstrip("\n") + "\n\n"
        + DOC_SYNC_INSTRUCTION + "\n\n"
        + "## Whole-plan diff\n\n" + diff_section
    )
    brief_path = os.path.join(run_dir, "doc-sync-brief.md")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief)
    return brief_path


def _parse_doc_sync(last_message):
    """The doc-sync worker's verdict: the last parseable JSON object carrying a
    ``doc_sync`` key (``reconciled`` | ``clean`` | ``contradiction``), or None when
    it emitted none. Same last-object-wins scan as parse_verdict, keyed on a
    different field. A missing verdict is *not* a contract error here — git is the
    authoritative record of what changed (the commit decision reads the staged
    tree, not this verdict); the verdict only signals an unreconcilable
    contradiction that must halt."""
    decoder = json.JSONDecoder()
    found = None
    i = 0
    n = len(last_message)
    while i < n:
        if last_message[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(last_message, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and "doc_sync" in obj:
            found = obj
        i = end
    return found


def _git_commit_doc_sync(cwd):
    """Stage and commit the doc-sync worker's edits as one ``docs: sync`` commit
    (Commit discipline). Returns ``(sha, reconciled_paths)``, or ``(None, [])``
    when nothing was staged (the worker changed no doc) or ``cwd`` is not a git
    repo. Mirrors _git_commit_final_review_fixes' empty-stage guard; the
    reconciled list is the staged path set (``git diff --cached --name-only``) —
    authoritative over any worker self-report."""
    if _git_head(cwd) is None:
        return None, []
    add = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
    if add.returncode != 0:
        raise RuntimeError(
            "git add -A for doc-sync failed in {}: {}".format(cwd, add.stderr.strip())
        )
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=cwd,
        capture_output=True, text=True,
    )
    reconciled = [p for p in names.stdout.splitlines() if p.strip()]
    if not reconciled:
        return None, []  # nothing staged -> skip, no empty commit
    commit = subprocess.run(
        ["git", "commit", "-m", "docs: sync"], cwd=cwd, capture_output=True, text=True,
    )
    if commit.returncode != 0:
        raise RuntimeError(
            "git commit for doc-sync failed in {}: {}".format(cwd, commit.stderr.strip())
        )
    return _git_head(cwd), reconciled


def dispatch_doc_sync(spec_path, run_base, diff, run_dir, tier, codex_bin, cwd,
                      timeout=DEFAULT_TIMEOUT):
    """Terminal doc-sync stage (Terminal doc-sync stage spec): one ``codex exec``
    dispatch that reconciles EXISTING documentation to the shipped whole-plan
    ``diff`` — stale references, changed signatures/behavior, spec changelog,
    ROADMAP status — never authoring new docs and never touching code. Runs only
    after final review passes (the caller's guard). Returns a DocSyncResult:

    - the worker names an unreconcilable doc/contract contradiction -> ``halt``
      (no commit; the run stops for a human, the conflict named);
    - the worker edited an existing doc -> ``reconciled``, committed ``docs: sync``;
    - nothing changed -> ``clean``, no commit.

    A dispatch that crashes or times out also halts with the cause named — it
    produced no usable result, and silently treating that as "no drift" would
    mask the failure (fail-loud, like a reviewer crash). Dispatched with
    ``--json`` (never ``--ephemeral``) like every other stage; the doc-sync
    stage has no role in ``run.json``'s ``threads`` map (Interface: worker |
    reviewer | final-reviewer | final-fixer only) — it is terminal and never
    resumed, so its captured thread id is discarded rather than persisted."""
    model, effort = TIER_MAP[tier]
    preamble = contract_preamble(tier)
    brief_path = _doc_sync_brief(spec_path, diff, run_dir)
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = f.read()
    prompt = preamble + "\n\n" + brief
    last_msg_path = os.path.join(run_dir, "doc-sync-last.txt")
    argv = [
        codex_bin,
        "exec",
        "--json",
        "-m",
        model,
        "-c",
        "model_reasoning_effort={}".format(effort),
        "--output-last-message",
        last_msg_path,
        prompt,
    ]
    if os.path.exists(last_msg_path):
        os.remove(last_msg_path)  # never re-read a prior stage's message
    live_path = os.path.join(run_dir, "doc-sync-live.log")
    events_path = os.path.join(run_dir, "doc-sync-events.jsonl")
    header = "── doc-sync · codex exec · {} · {} ──".format(model, effort)
    update_run_progress(run_dir, None, "doc-sync")
    result = run_json_teed(
        argv, timeout=timeout, live_path=live_path, events_path=events_path,
        header=header, render_line=_render_event_line,
    )
    if result.timed_out:
        return DocSyncResult(
            status="halt",
            contradiction="doc-sync dispatch timed out after {}s without a "
            "usable result".format(timeout),
        )
    if result.exit_code != 0:
        tail = (result.tail or "").strip()[:300]
        return DocSyncResult(
            status="halt",
            contradiction="doc-sync dispatch exited {}{}".format(
                result.exit_code, ": " + tail if tail else ""),
        )
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    verdict = _parse_doc_sync(last_message)
    if verdict is not None and verdict.get("doc_sync") == "contradiction":
        return DocSyncResult(
            status="halt",
            contradiction=verdict.get("contradiction") or "(unspecified contradiction)",
        )
    sha, reconciled = _git_commit_doc_sync(cwd)
    if sha is None:
        return DocSyncResult(status="clean")
    return DocSyncResult(status="reconciled", commit=sha, reconciled=reconciled)


def run_plan(plan_path, spec_path, run_dir, codex_bin, cwd, effort_overrides=None,
             timeout=DEFAULT_TIMEOUT, autofix_mode="auto"):
    """Sequential whole-plan loop. Tasks already ``passed`` in this run-dir (a
    resume) are skipped; the rest run through execute_task in dependency order.
    Halts on the first escalation. After every task passes, one plan-level final
    review runs against the whole-plan diff + spec (git repo required), and — once
    that passes — a terminal doc-sync stage reconciles existing docs to the
    shipped diff. ``autofix_mode`` (``auto`` | ``gate``, chosen at the execution
    offer) threads into every per-task and final-review convergence decision:
    ``auto`` runs the disposition matrix, ``gate`` halts on any finding.
    Defer-disposition findings from every task and the final review aggregate into
    ``run.json`` under ``deferrals`` (the runner never writes DEFERRALS.md — the
    orchestrator does, at completion). ``effort_overrides`` (``{task_number:
    level}``, from repeatable ``--effort N=LEVEL``) must reference only task
    numbers present in the plan — an unknown number raises naming the cause.
    ``timeout`` bounds every worker and reviewer ``codex exec`` call.
    ``run.json``'s ``threads`` map (Codex mechanics / Receipts spec) is reset to
    empty at the start of every invocation — including a resume — since a
    persisted session may be stale once a halt lets a human hand-edit code; a
    thread id is never carried across runner invocations, only within one."""
    # Clean-tree precondition (every invocation, first run and resume): commit
    # discipline only yields clean per-task/whole-plan boundaries if the tree
    # starts clean. Checked before creating the run dir or `.forge/` gitignore so
    # neither perturbs the check. A non-repo cwd (None) skips the precondition.
    dirty = _working_tree_dirty(cwd)
    if dirty:
        raise RuntimeError(
            "working tree not clean at run start — commit or discard before "
            "re-invoking:\n{}".format("\n".join(dirty))
        )
    # Parse and validate the plan BEFORE creating the run dir: an unparseable
    # plan (or an --effort pointing at a missing task) is a contract error that
    # must leave no run.json — the spec surfaces it via stderr only.
    # Neither call depends on the run dir.
    tasks = parse_plan_tasks(plan_path)
    order = order_tasks(tasks)
    effort_overrides = effort_overrides or {}
    unknown = sorted(set(effort_overrides) - {t.number for t in tasks})
    if unknown:
        raise RuntimeError(
            "--effort references unknown task number(s) {} — plan has task(s) "
            "{}".format(
                ", ".join(str(n) for n in unknown),
                ", ".join(str(t.number) for t in tasks),
            )
        )
    os.makedirs(run_dir, exist_ok=True)
    ensure_forge_gitignore(cwd)
    # Whole-plan final-review diff base: the run-start HEAD, captured once and
    # persisted in run.json so a resume reuses it rather than a HEAD that has
    # advanced past already-committed tasks.
    run_base = _read_base_commit(run_dir) or _git_head(cwd)
    prior_task_list = _read_run_tasks(run_dir) or []
    prior_commits = {t.get("number"): t.get("commit") for t in prior_task_list}
    prior_tasks = {t.get("number"): t for t in prior_task_list}
    # Run start time survives a resume so the monitor's elapsed doesn't reset; pid
    # is the current process (a liveness hint the monitor/--status can probe).
    run_started = _read_started_at(run_dir) or utc_iso()
    run_pid = os.getpid()

    # Seed the full task roster (dependency order) as `queued` so run.json is a
    # complete, self-contained record the monitor renders — queued tasks included —
    # then update each entry in place as it runs. summary_by_num indexes the same
    # dict objects for O(1) update.
    task_summaries = [
        {
            "number": t.number,
            "title": t.title,
            "tier": t.tier,
            "status": "queued",
            "attempts": 0,
            "commit": None,
            "started_at": None,
            "ended_at": None,
        }
        for t in order
    ]
    summary_by_num = {s["number"]: s for s in task_summaries}
    overall = "passed"
    escalated = False
    # Defer-disposition findings aggregate here across every task and the final
    # review; surfaced in the terminal run.json (the orchestrator writes them into
    # DEFERRALS.md at completion — the runner never touches that curated doc).
    deferrals = []
    doc_sync_record = None
    # Per-role codex exec session-id map (Codex mechanics spec) — cleared here at
    # invocation start (never read back from a prior run.json), populated in
    # place by dispatch_worker/_dispatch_review_call/dispatch_final_review_fix as
    # the run proceeds, and rewritten into run.json alongside every other field.
    threads = {}

    # Incremental run.json: write `running` before the first task so --status/the
    # monitor can distinguish an in-progress run from a dead one, and rewrite after
    # each passed task so live per-task progress is visible. base_commit rides
    # along so a resume still reads it; started_at/pid feed the monitor.
    write_run_json(run_dir, plan_path, spec_path, "running", task_summaries, run_base,
                   started_at=run_started, pid=run_pid, threads=threads)
    # Drop a short launcher for the standing monitor and print a one-token command
    # (a long absolute path line-wraps in the session and is hard to run).
    write_watch_launcher(cwd, os.path.join(SCRIPTS_DIR, "forge-monitor.py"))
    print("monitor: sh .forge/watch   (if not already watching)", flush=True)

    # order_tasks yields dependency order (each dependency before its dependents)
    # and the loop breaks on the first escalation, so a dependent is never reached
    # unless every dependency already passed — no separate depends-on guard needed.
    for task in order:
        summary = summary_by_num[task.number]
        if latest_status(run_dir, task.number) == "passed":
            # Resume: a prior invocation already completed this task.
            prior = _read_latest_receipt(run_dir, task.number) or {}
            prior_summary = prior_tasks.get(task.number, {})
            summary.update({
                "status": "passed",
                "attempts": prior.get("attempt", 1),
                "commit": prior_commits.get(task.number),
                "started_at": prior_summary.get("started_at"),
                "ended_at": prior_summary.get("ended_at"),
            })
            continue

        _clear_task_receipts(run_dir, task.number)
        print("task {}: {} — starting".format(task.number, task.title), flush=True)
        task_started = utc_iso()
        # Mark the task `running` with its start time so the monitor lights the row
        # and shows live per-task elapsed (its summary is otherwise `queued` until
        # it completes).
        summary.update({"status": "running", "started_at": task_started})
        write_run_json(run_dir, plan_path, spec_path, "running", task_summaries,
                       run_base, started_at=run_started, pid=run_pid, threads=threads)
        outcome = execute_task(
            task, plan_path, spec_path, run_dir, codex_bin, cwd, threads,
            effort_override=effort_overrides.get(task.number), timeout=timeout,
            autofix_mode=autofix_mode,
        )
        deferrals.extend(outcome.deferrals)
        print("task {}: {} ({} attempt(s))".format(
            task.number, outcome.status, outcome.attempts), flush=True)
        summary.update({
            "status": outcome.status,
            "attempts": outcome.attempts,
            "ended_at": utc_iso(),
        })
        if outcome.status == "passed":
            annotate_ledger(
                plan_path, task, "passed, {} attempt(s)".format(outcome.attempts)
            )
            # Commit this task's slice (ledger annotation included); records the
            # SHA, or None when the task changed nothing (no empty commit).
            summary["commit"] = _git_commit_task(cwd, task)
            write_run_json(
                run_dir, plan_path, spec_path, "running", task_summaries, run_base,
                started_at=run_started, pid=run_pid, threads=threads,
            )
        else:
            annotate_ledger(plan_path, task, "escalated: {}".format(outcome.summary))
            overall = "escalated"
            escalated = True
            break

    # Resuming a run where every remaining task was already `passed` skips the
    # loop's own write_run_json calls entirely (each iteration just `continue`s),
    # leaving run.json's tasks stamped `queued` from the seed write above for the
    # whole final-review phase. Flush the corrected summaries before entering it.
    if not escalated:
        write_run_json(run_dir, plan_path, spec_path, "running", task_summaries,
                       run_base, started_at=run_started, pid=run_pid, threads=threads)

    if not escalated and run_base is not None:
        # Final broad review: whole-plan diff + spec, one reviewer at the plan's
        # highest task tier (not a pinned ceiling), now run through the same
        # convergence loop as a per-task review (Final review spec) — a fix
        # dispatch reworks in-diff/contract-breaking findings in-loop, and only a
        # genuine scope decision (or --gate, wired in Task 7) halts. Skipped when
        # the diff is empty (nothing to review) or cwd is not a git repo (no
        # baseline).
        diff = _git_diff(cwd, run_base)
        if diff.strip():
            final_tier = max(tasks, key=lambda t: TIER_ORDER.index(t.tier)).tier
            final_outcome = run_final_review_loop(
                spec_path, run_base, run_dir, codex_bin, cwd, final_tier,
                autofix_mode, threads, timeout=timeout, plan_path=plan_path,
            )
            deferrals.extend(final_outcome.deferrals)
            if final_outcome.status == "escalated":
                overall = "escalated-final-review"
            else:
                # Terminal doc-sync: reconcile existing docs to the shipped diff,
                # only now that every code gate is green (never masks a code
                # defect as drift). The diff is recomputed so it includes any
                # `fix: final-review` commit the loop just landed. A doc/contract
                # contradiction it cannot mechanically reconcile halts the run.
                doc_sync = dispatch_doc_sync(
                    spec_path, run_base, _git_diff(cwd, run_base), run_dir,
                    final_tier, codex_bin, cwd, timeout=timeout,
                )
                doc_sync_record = {
                    "status": doc_sync.status,
                    "commit": doc_sync.commit,
                    "reconciled": doc_sync.reconciled,
                }
                if doc_sync.contradiction:
                    doc_sync_record["contradiction"] = doc_sync.contradiction
                if doc_sync.status == "halt":
                    overall = "escalated-doc-sync"

    # Terminal write: no current_task/current_phase, so the pointer is cleared —
    # the monitor stops the spinner and paints the terminal-state banner. The
    # autonomy record (autofix_mode always; deferrals/doc_sync when present) rides
    # the final write for --status and the orchestrator's completion summary.
    write_run_json(run_dir, plan_path, spec_path, overall, task_summaries, run_base,
                   started_at=run_started, pid=run_pid,
                   deferrals=deferrals or None, autofix_mode=autofix_mode,
                   doc_sync=doc_sync_record, threads=threads)
    return 0 if overall == "passed" else 2


def resume(plan_path, spec_path, run_dir):
    """Re-invoke over an existing ``run_dir``: tasks whose latest receipt status is
    ``passed`` are skipped (not re-dispatched); execution resumes at the first
    incomplete/escalated task. Receipts + plan checkboxes are the only resume
    state (Resume spec). A thin, documented alias over ``run_plan`` — whose
    skip-passed logic already makes every invocation resumable — using the
    production defaults: ``codex`` on PATH and the current working directory."""
    return run_plan(plan_path, spec_path, run_dir, "codex", os.getcwd())


def _default_run_dir():
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    return os.path.join(".forge", "runs", stamp)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="forge-run.py",
        description="Deterministic whole-plan task runner over `codex exec`.",
    )
    parser.add_argument("plan", nargs="?", help="approved plan markdown file")
    parser.add_argument("--spec", help="design spec markdown file")
    parser.add_argument(
        "--status",
        action="store_true",
        help="read-only: print the run summary for --run-dir and exit; "
        "dispatches nothing (plan/--spec not required)",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="receipt directory (default: .forge/runs/<timestamp>/)",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="path to the codex executable (test seam; default: codex on PATH)",
    )
    parser.add_argument(
        "--effort",
        action="append",
        default=[],
        metavar="N=LEVEL",
        help="per-task worker reasoning-effort override (repeatable); LEVEL in "
        "{}; applies to that task's worker dispatch only, never the "
        "reviewer".format(", ".join(ALLOWED_EFFORTS)),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds before a worker/reviewer codex subprocess is killed "
        "(default: {})".format(DEFAULT_TIMEOUT),
    )
    parser.add_argument(
        "--autofix",
        choices=AUTOFIX_MODES,
        default="auto",
        help="review-finding autonomy (chosen at the execution offer): `auto` "
        "runs the disposition matrix (fix in-diff contract-breaking, defer "
        "improvements, halt a pre-existing scope decision); `gate` halts on any "
        "finding (default: auto)",
    )
    args = parser.parse_args(argv)

    # Read-only status mode: print the run summary from run.json + receipts and
    # exit. Dispatches nothing; plan/--spec are not required.
    if args.status:
        if not args.run_dir:
            parser.error("--status requires --run-dir")
        state = forge_status.read_run_state(args.run_dir)
        if state is None:
            print("no run at {}".format(args.run_dir))
        else:
            print(forge_status.render_status(state))
        return 0

    if not args.plan or not args.spec:
        parser.error("plan and --spec are required (or use --status --run-dir)")

    run_dir = args.run_dir or _default_run_dir()
    try:
        effort_overrides = parse_effort_overrides(args.effort)
        return run_plan(
            args.plan, args.spec, run_dir, args.codex_bin, os.getcwd(),
            effort_overrides=effort_overrides, timeout=args.timeout,
            autofix_mode=args.autofix,
        )
    except RuntimeError as e:
        print("error: {}".format(e), file=sys.stderr)
        # Persist a contract-error marker when the run dir already exists, so
        # --status reports it. Errors before the run dir exists (dirty tree,
        # unparseable plan) leave no run.json — stderr is the only signal.
        # Preserve any base_commit/tasks already persisted so a later resume is
        # unaffected (the error may have struck mid-run, after tasks committed).
        if os.path.isdir(run_dir):
            try:
                # Preserve the run's started_at (monitor elapsed) and record the
                # current pid; omitting current_task/current_phase clears the live
                # pointer so the monitor paints the contract-error banner and stops
                # the spinner rather than freezing a task mid-flight.
                write_run_json(
                    run_dir, args.plan, args.spec, "contract-error",
                    _read_run_tasks(run_dir) or [], _read_base_commit(run_dir),
                    contract_error=str(e),
                    started_at=_read_started_at(run_dir), pid=os.getpid(),
                )
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
