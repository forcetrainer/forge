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
import subprocess
import sys
from dataclasses import asdict


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)

# The helper modules are plain (underscore-named) siblings. Put SCRIPTS_DIR on
# the path so they import as normal modules — ``sys.modules`` then caches one
# instance of each (notably forge_common), so the shared dataclasses keep a
# single identity across every module and the test suite. (extract-brief.py /
# review-packet.py stay importlib-loaded inside forge_common: hyphenated names.)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import forge_common
import forge_git
import forge_plan
import forge_receipts
import forge_status

# Re-export the sibling API into this namespace: the runner's own code below
# calls these by bare name, and tests/docs address them as ``forge_run.<name>``.
from forge_common import (  # noqa: F401
    ALLOWED_EFFORTS,
    CONTRACT_AGENT,
    DEFAULT_TIMEOUT,
    MAX_ATTEMPTS,
    REVIEW_MAP,
    REVIEW_VERDICT_INSTRUCTION,
    TIER_MAP,
    AcceptanceResult,
    Task,
    TaskOutcome,
    Verdict,
    WorkerResult,
    eb,
    rp,
    verdict_to_dict,
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
    annotate_ledger,
    ensure_forge_gitignore,
    latest_status,
    write_final_review_receipt,
    write_receipt,
    write_run_json,
)

_ACC_TAIL_CHARS = forge_common._ACC_TAIL_CHARS


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


def dispatch_worker(task, brief_path, codex_bin, run_dir, effort_override=None,
                     timeout=DEFAULT_TIMEOUT):
    """One ``codex exec`` worker process, tier-pinned model/effort (``effort_
    override`` replaces only the effort, never the model, for a per-task
    ``--effort N=LEVEL`` bump). Prompt = contract preamble + brief. Returns the
    exit code, last message, and the exact argv emitted. A hung process is
    killed at ``timeout`` seconds and reported as ``timed_out=True`` — the
    caller treats that exactly like a failed iteration, never hangs the run."""
    model, effort = TIER_MAP[task.tier]
    if effort_override is not None:
        effort = effort_override
    preamble = contract_preamble(task.tier)
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = f.read()
    prompt = preamble + "\n\n" + brief
    last_msg_path = os.path.join(run_dir, "task-{}-worker-last.txt".format(task.number))
    argv = [
        codex_bin,
        "exec",
        "-m",
        model,
        "-c",
        "model_reasoning_effort={}".format(effort),
        "--output-last-message",
        last_msg_path,
        prompt,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return WorkerResult(exit_code=None, last_message="", argv=argv, timed_out=True)
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return WorkerResult(exit_code=proc.returncode, last_message=last_message, argv=argv)


def run_acceptance(task, cwd):
    """Run each acceptance command directly (shell) in ``cwd``; capture exit code
    and an output tail."""
    results = []
    for cmd in task.acceptance_commands:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        combined = (proc.stdout or "") + (proc.stderr or "")
        results.append(
            AcceptanceResult(
                command=cmd,
                exit_code=proc.returncode,
                output_tail=combined[-_ACC_TAIL_CHARS:],
            )
        )
    return results


# --- reviewer dispatch & verdict --------------------------------------------


def _verdict_from_obj(obj):
    """Map a decoded JSON value to a Verdict if it matches a verdict shape, else
    None. ``{"verdict": "pass"}`` -> pass; ``{"verdict": "findings", "findings":
    [str, ...]}`` -> findings; anything else is not a verdict."""
    if not isinstance(obj, dict) or obj.get("verdict") is None:
        return None
    if obj["verdict"] == "pass":
        return Verdict(kind="pass")
    if obj["verdict"] == "findings":
        findings = obj.get("findings")
        if isinstance(findings, list) and all(isinstance(x, str) for x in findings):
            return Verdict(kind="findings", findings=list(findings))
    return None


def parse_verdict(last_message):
    """Extract the reviewer verdict: the last parseable JSON object in the
    message (fenced or bare) matching a verdict shape. Anything else raises
    RuntimeError naming the cause — never guessed, never retried silently
    (DECISIONS 2026-07-11)."""
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
        verdict = _verdict_from_obj(obj)
        if verdict is not None:
            found = verdict
        i = end  # skip past the parsed object
    if found is None:
        raise RuntimeError(
            "reviewer produced no parseable verdict JSON "
            '({"verdict": "pass"} or {"verdict": "findings", "findings": [...]}); '
            "got: " + repr(last_message.strip()[:300])
        )
    return found


def _dispatch_review_call(model, effort, preamble, packet_path, codex_bin, last_msg_path,
                           timeout=DEFAULT_TIMEOUT):
    """Shared plumbing for per-task and final reviewers: one ``codex exec`` call,
    prompt = review preamble + verdict instruction + packet; returns the parsed
    Verdict. Fail-loud on a crashed reviewer, a timed-out reviewer, or an
    unparseable verdict — never silently trusts or reuses stale output (Halt
    spec; ``parse_verdict`` never retries silently). The last-message file is
    cleared before the call so a prior attempt's verdict can never be re-read,
    and the reviewer's own exit code is checked (unlike a worker crash, a
    reviewer crash yields no verdict to judge, so it halts the run rather than
    consuming a rework iteration). A reviewer that hangs past ``timeout`` is
    handled the same way — it also yields no verdict to judge."""
    with open(packet_path, "r", encoding="utf-8") as f:
        packet = f.read()
    prompt = preamble + "\n\n" + REVIEW_VERDICT_INSTRUCTION + "\n\n" + packet
    argv = [
        codex_bin,
        "exec",
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
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "reviewer process ({} at effort {}) timed out after {}s without a "
            "usable verdict".format(model, effort, timeout)
        )
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip()[:300]
        raise RuntimeError(
            "reviewer process ({} at effort {}) exited {} without a usable "
            "verdict{}".format(
                model,
                effort,
                proc.returncode,
                ": " + stderr_tail if stderr_tail else "",
            )
        )
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return parse_verdict(last_message)


def dispatch_reviewer(task, packet_path, codex_bin, run_dir, timeout=DEFAULT_TIMEOUT):
    """Per-task reviewer via ``codex exec`` routed by REVIEW_MAP[tier] (standard ->
    terra/high, complex -> sol/high). Preamble = the tier agent's review paragraph.
    Returns the parsed Verdict."""
    model, effort = REVIEW_MAP[task.tier]
    preamble = contract_preamble(task.tier)
    last_msg_path = os.path.join(run_dir, "task-{}-review-last.txt".format(task.number))
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path, timeout=timeout
    )


def dispatch_final_review(packet_path, codex_bin, run_dir, timeout=DEFAULT_TIMEOUT):
    """Whole-plan final review: one sol/high ``codex exec`` call (REVIEW_MAP[
    'complex']) with the forge-deep final-integration-review preamble against the
    whole-plan diff + spec. Returns the parsed Verdict."""
    model, effort = REVIEW_MAP["complex"]
    preamble = contract_preamble("complex")
    last_msg_path = os.path.join(run_dir, "final-review-last.txt")
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path, timeout=timeout
    )


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


def execute_task(task, plan_path, spec_path, run_dir, codex_bin, cwd,
                  effort_override=None, timeout=DEFAULT_TIMEOUT):
    """Run one task through the rework loop: worker -> acceptance -> (standard/
    complex) reviewer, capped at MAX_ATTEMPTS. A worker crash, a worker timeout,
    a failed acceptance command, or a findings verdict is a failed iteration;
    the next iteration re-dispatches the worker with the outstanding findings
    appended to the brief. Hitting the cap yields status ``escalated`` with the
    outstanding findings on the final receipt. ``effort_override`` (from a
    per-task ``--effort N=LEVEL`` CLI flag) replaces only this task's worker
    effort, never the reviewer's."""
    model, effort = TIER_MAP[task.tier]
    if effort_override is not None:
        effort = effort_override
    # Per-task review base = HEAD at task start (the prior task's commit; the
    # run-start commit for task 1). Each passed task commits, so the tree is clean
    # here and `git diff <review_base>` isolates this task's own changes. Taken
    # once (trivial tiers need no reviewer).
    review_base = _git_head(cwd) if task.tier != "trivial" else None
    findings_carry = []

    attempt = 0
    while True:
        attempt += 1
        brief_path, brief_sha = _brief_for(
            task, plan_path, spec_path, run_dir, attempt, findings_carry
        )
        worker = dispatch_worker(
            task, brief_path, codex_bin, run_dir,
            effort_override=effort_override, timeout=timeout,
        )
        acceptance = run_acceptance(task, cwd)

        worker_ok = worker.exit_code == 0 and not worker.timed_out
        acc_ok = all(r.exit_code == 0 for r in acceptance)

        review_verdict = None
        iteration_findings = []
        failure_summary = None

        if worker.timed_out:
            failure_summary = "worker timed out after {}s".format(timeout)
            iteration_findings = [
                "Prior worker attempt timed out after {}s with no usable "
                "result — reattempt the task.".format(timeout)
            ]
        elif not worker_ok:
            failure_summary = "worker exited {}".format(worker.exit_code)
            iteration_findings = [
                "Prior worker attempt exited {} with no usable result — "
                "reattempt the task.".format(worker.exit_code)
            ]
        elif not acc_ok:
            failed = next(r for r in acceptance if r.exit_code != 0)
            failure_summary = "acceptance failed: {}".format(failed.command)
            iteration_findings = [
                "Acceptance command `{}` failed (exit {}). Output tail:\n{}".format(
                    failed.command, failed.exit_code, failed.output_tail
                )
            ]
        elif task.tier != "trivial":
            # Trivial tier: acceptance is the whole verification. Standard/complex:
            # a reviewer judges the diff against the spec.
            if review_base is None:
                raise RuntimeError(
                    "cannot generate review packet for task {}: cwd is not a git "
                    "repository".format(task.number)
                )
            packet_path = _packet_for(task, plan_path, run_dir, review_base, cwd)
            verdict = dispatch_reviewer(task, packet_path, codex_bin, run_dir, timeout=timeout)
            review_verdict = verdict_to_dict(verdict)
            if verdict.kind == "findings":
                iteration_findings = list(verdict.findings)
                failure_summary = "review findings: {}".format(
                    "; ".join(verdict.findings) if verdict.findings else "(unspecified)"
                )

        passed = failure_summary is None
        if passed:
            status = "passed"
        elif attempt >= MAX_ATTEMPTS:
            status = "escalated"
        else:
            status = "rework"

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
            "outstanding_findings": iteration_findings if status == "escalated" else [],
        }
        write_receipt(run_dir, task, attempt, receipt)

        if passed:
            return TaskOutcome(status="passed", attempts=attempt, summary="")
        if status == "escalated":
            return TaskOutcome(
                status="escalated",
                attempts=attempt,
                summary=failure_summary,
                findings=iteration_findings,
            )
        findings_carry = iteration_findings  # rework: carry into the next brief


def run_plan(plan_path, spec_path, run_dir, codex_bin, cwd, effort_overrides=None,
             timeout=DEFAULT_TIMEOUT):
    """Sequential whole-plan loop. Tasks already ``passed`` in this run-dir (a
    resume) are skipped; the rest run through execute_task in dependency order.
    Halts on the first escalation. After every task passes, one plan-level final
    review runs against the whole-plan diff + spec (git repo required).
    ``effort_overrides`` (``{task_number: level}``, from repeatable ``--effort
    N=LEVEL``) must reference only task numbers present in the plan — an
    unknown number raises naming the cause. ``timeout`` bounds every worker and
    reviewer ``codex exec`` call."""
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
    prior_commits = {
        t.get("number"): t.get("commit")
        for t in (_read_run_tasks(run_dir) or [])
    }

    task_summaries = []
    overall = "passed"
    escalated = False

    # Incremental run.json: write `running` before the first task so --status/the
    # hook can distinguish an in-progress run from a dead one, and rewrite after
    # each passed task so live per-task progress is visible. base_commit rides
    # along so a resume still reads it.
    write_run_json(run_dir, plan_path, spec_path, "running", task_summaries, run_base)

    # order_tasks yields dependency order (each dependency before its dependents)
    # and the loop breaks on the first escalation, so a dependent is never reached
    # unless every dependency already passed — no separate depends-on guard needed.
    for task in order:
        if latest_status(run_dir, task.number) == "passed":
            # Resume: a prior invocation already completed this task.
            prior = _read_latest_receipt(run_dir, task.number) or {}
            task_summaries.append(
                {
                    "number": task.number,
                    "title": task.title,
                    "tier": task.tier,
                    "status": "passed",
                    "attempts": prior.get("attempt", 1),
                    "commit": prior_commits.get(task.number),
                }
            )
            continue

        _clear_task_receipts(run_dir, task.number)
        print("task {}: {} — starting".format(task.number, task.title), flush=True)
        outcome = execute_task(
            task, plan_path, spec_path, run_dir, codex_bin, cwd,
            effort_override=effort_overrides.get(task.number), timeout=timeout,
        )
        print("task {}: {} ({} attempt(s))".format(
            task.number, outcome.status, outcome.attempts), flush=True)
        summary = {
            "number": task.number,
            "title": task.title,
            "tier": task.tier,
            "status": outcome.status,
            "attempts": outcome.attempts,
            "commit": None,
        }
        task_summaries.append(summary)
        if outcome.status == "passed":
            annotate_ledger(
                plan_path, task, "passed, {} attempt(s)".format(outcome.attempts)
            )
            # Commit this task's slice (ledger annotation included); records the
            # SHA, or None when the task changed nothing (no empty commit).
            summary["commit"] = _git_commit_task(cwd, task)
            write_run_json(
                run_dir, plan_path, spec_path, "running", task_summaries, run_base
            )
        else:
            annotate_ledger(plan_path, task, "escalated: {}".format(outcome.summary))
            overall = "escalated"
            escalated = True
            break

    if not escalated and run_base is not None:
        # Final broad review: whole-plan diff + spec, one sol/high reviewer. No
        # rework loop at plan level — findings are a human gate. Skipped when the
        # diff is empty (nothing to review) or cwd is not a git repo (no baseline).
        diff = _git_diff(cwd, run_base)
        if diff.strip():
            packet_path = _final_packet(spec_path, run_base, diff, run_dir)
            verdict = dispatch_final_review(packet_path, codex_bin, run_dir, timeout=timeout)
            write_final_review_receipt(run_dir, verdict)
            if verdict.kind == "findings":
                overall = "escalated-final-review"

    write_run_json(run_dir, plan_path, spec_path, overall, task_summaries, run_base)
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
                write_run_json(
                    run_dir, args.plan, args.spec, "contract-error",
                    _read_run_tasks(run_dir) or [], _read_base_commit(run_dir),
                    contract_error=str(e),
                )
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
