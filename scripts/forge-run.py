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

import forge_common
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
    run_teed,
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
    live_path = os.path.join(run_dir, "task-{}-live.log".format(task.number))
    header = "── worker · codex exec · {} · {} ──".format(model, effort)
    result = run_teed(argv, timeout=timeout, live_path=live_path, header=header)
    if result.timed_out:
        return WorkerResult(exit_code=None, last_message="", argv=argv, timed_out=True)
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return WorkerResult(exit_code=result.exit_code, last_message=last_message, argv=argv)


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


# --- reviewer dispatch & verdict --------------------------------------------


def _finding_from_obj(obj):
    """Build one Finding from a reviewer finding object (the per-finding schema —
    nested ``location`` mirrored by finding_to_dict). The reviewer-proposed
    provenance/impact ride through unchanged; the runner verifies/derives them
    later (classify_findings). Raises RuntimeError when the finding is not an
    object, or when a ``contract-breaking`` finding omits its location — without a
    file/line to point at, provenance cannot be verified against the diff, so an
    unlocated contract-breaking claim is a contract error, never a silent guess
    (Reviewer verdict contract; DECISIONS 2026-07-11)."""
    if not isinstance(obj, dict):
        raise RuntimeError(
            "reviewer finding is not a JSON object (per-finding schema required); "
            "got: " + repr(obj)[:200]
        )
    location = obj.get("location") or {}
    file = location.get("file")
    lines = location.get("lines")
    impact = obj.get("impact")
    if impact == "contract-breaking" and (file is None or lines is None):
        raise RuntimeError(
            "reviewer finding {!r} is contract-breaking but omits its location "
            "(location.file/location.lines) — provenance cannot be verified "
            "against the diff".format(obj.get("id"))
        )
    return Finding(
        id=obj.get("id"),
        summary=obj.get("summary", ""),
        file=file,
        lines=lines,
        provenance=obj.get("provenance"),
        impact=impact,
        contract_ref=obj.get("contract_ref"),
        convergence=obj.get("convergence"),
        carried_from=obj.get("carried_from"),
        repair_task=obj.get("repair_task"),
    )


def _verdict_from_obj(obj):
    """Build the Verdict from a recognized verdict-shaped object (``verdict`` is
    ``pass`` or ``findings``; the authoritative last one in the stream). ``pass``
    carries no findings; ``findings`` parses each finding object into a Finding via
    _finding_from_obj — which raises loudly on a malformed or unlocated
    contract-breaking finding rather than dropping it."""
    if obj["verdict"] == "pass":
        return Verdict(kind="pass")
    raw = obj.get("findings")
    if not isinstance(raw, list):
        raise RuntimeError(
            "reviewer findings verdict has no findings list; got: "
            + repr(obj)[:300]
        )
    return Verdict(kind="findings", findings=[_finding_from_obj(f) for f in raw])


def parse_verdict(last_message):
    """Extract the reviewer verdict: the last parseable JSON object in the
    message (fenced or bare) whose ``verdict`` is ``pass`` or ``findings``. No
    such object raises RuntimeError naming the cause; a recognized findings
    verdict with a malformed / unlocated contract-breaking finding raises from
    _verdict_from_obj — never guessed, never retried silently (DECISIONS
    2026-07-11). The recognizer (last-object-wins) is deliberately separate from
    the strict build so only the authoritative verdict is parsed."""
    decoder = json.JSONDecoder()
    found = None  # last verdict-shaped raw dict in the stream
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
        if isinstance(obj, dict) and obj.get("verdict") in ("pass", "findings"):
            found = obj
        i = end  # skip past the parsed object
    if found is None:
        raise RuntimeError(
            "reviewer produced no parseable verdict JSON "
            '({"verdict": "pass"} or {"verdict": "findings", "findings": [...]}); '
            "got: " + repr(last_message.strip()[:300])
        )
    return _verdict_from_obj(found)


# --- classification: provenance verification + disposition matrix -----------


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def diff_line_ranges(diff_text):
    """Parse a unified diff into ``{file_path: [(start, end), ...]}`` — the
    new-side line ranges each hunk touches, in file order. The current file is
    taken from ``+++ b/<path>`` lines (``+++ /dev/null`` = a deletion, no new
    side); each ``@@ -a,b +c,d @@`` header contributes the range ``(c, c+d-1)``,
    with an omitted count defaulting to 1 and a zero count (pure-deletion hunk)
    contributing no new-side range. This is the diff half of provenance
    verification — a finding is in-diff iff its lines intersect one of these."""
    ranges = {}
    current = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = None
            else:
                if path.startswith("b/"):
                    path = path[2:]
                current = path
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m or current is None:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count <= 0:
                continue  # pure-deletion hunk: no new-side lines to point at
            ranges.setdefault(current, []).append((start, start + count - 1))
    return ranges


def _parse_lines(lines):
    """Parse a finding's ``lines`` field (``"12-20"`` or ``"12"``) into an
    inclusive ``(lo, hi)`` pair, or None when absent/unparseable (an improvement
    finding may carry no location — it defers regardless of provenance)."""
    if not lines:
        return None
    try:
        text = str(lines).strip()
        if "-" in text:
            lo, hi = text.split("-", 1)
            return int(lo), int(hi)
        n = int(text)
        return n, n
    except (ValueError, TypeError):
        return None


def verify_provenance(finding, ranges):
    """Runner-verified provenance: ``in-diff`` when the finding's lines intersect
    one of the changed ranges for its file, else ``pre-existing`` — overriding any
    optimistic reviewer claim (a finding outside the diff is pre-existing no
    matter how it was labeled; Disposition matrix)."""
    span = _parse_lines(finding.lines)
    if span is None:
        return "pre-existing"
    lo, hi = span
    for start, end in ranges.get(finding.file, []):
        if lo <= end and start <= hi:  # inclusive-range overlap
            return "in-diff"
    return "pre-existing"


def derive_disposition(finding):
    """Disposition matrix over (verified provenance × contract-gated impact).
    Impact is ``contract-breaking`` only when the reviewer named the violated
    acceptance criterion / spec section in ``contract_ref`` — a null contract_ref
    downgrades it to ``improvement`` (named-evidence rule, mirroring the
    tier-policy floor). Quadrants: in-diff×contract-breaking → ``fix`` (the only
    auto-fix cell); pre-existing×contract-breaking → ``halt`` (a real scope
    decision); every improvement → ``defer``."""
    contract_breaking = (
        finding.impact == "contract-breaking" and finding.contract_ref is not None
    )
    if not contract_breaking:
        return "defer"
    return "fix" if finding.provenance == "in-diff" else "halt"


def classify_findings(verdict, diff_text):
    """Set each finding's runner-verified provenance and derived disposition
    against ``diff_text`` (the review's actual diff), then return the verdict.
    A pass verdict is returned unchanged. The reviewer proposes classification;
    the runner decides — provenance is recomputed from the diff and disposition
    is derived from the matrix, never trusted from the reviewer."""
    if verdict.kind != "findings":
        return verdict
    ranges = diff_line_ranges(diff_text)
    for finding in verdict.findings:
        finding.provenance = verify_provenance(finding, ranges)
        finding.disposition = derive_disposition(finding)
    return verdict


# --- convergence: the pass/rework/halt decision + cross-attempt state -------


@dataclass
class ConvergenceState:
    """The runner's authoritative view across a task's attempts. ``resolved_ids``
    are the canonical finding ids the runner has recorded as resolved (a prior
    fix finding that later disappeared); ``carried_ids`` is the set of fix-finding
    canonical ids still outstanding as of the prior *reviewed* attempt — a fix id
    present in two consecutive reviewed attempts with nothing resolved between them
    is *stuck* (membership is all the stuck rule needs; the exact per-finding
    appearance count is not); ``prev_acceptance_ok`` is the prior attempt's
    acceptance result (for the green->red regression check). An execution-failure
    attempt yields no review signal, so it advances only ``prev_acceptance_ok`` and
    leaves both id sets untouched."""

    resolved_ids: set = field(default_factory=set)
    carried_ids: set = field(default_factory=set)
    prev_acceptance_ok: bool | None = None


def _canon(finding):
    """Canonical identity for cross-attempt matching: the original finding id,
    following ``carried_from`` when the reviewer re-issued the same issue under a
    new id. The runner matches by this — never by the reviewer's self-labeling —
    so a mislabeled reappearance is still caught."""
    return finding.carried_from or finding.id


def _real_fix_canons(findings):
    """Canonical ids of the reviewer's fix-disposition findings. The implicit
    execution-failure finding (no impact) carries no identity, so it never enters
    the resolved-id or carried-fix set — it is subject only to the regression
    (green->red) and backstop rules, never stuck/scope-halt."""
    return {
        _canon(f) for f in findings
        if f.disposition == "fix" and f.impact is not None
    }


def _is_execution_failure(findings):
    """True when this attempt is the synthesized execution-failure retry (worker
    crash/timeout, acceptance non-zero) rather than reviewer output. Its marker is
    the only ``fix``-disposition finding with no impact — a real reviewer fix is
    always contract-breaking (derive_disposition), so this test is unambiguous.
    Such an attempt produced no review signal, so it must leave the authoritative
    resolved-id and carried-fix sets untouched (Rework loop & convergence: an
    execution failure is subject only to the regression and backstop rules)."""
    return any(f.disposition == "fix" and f.impact is None for f in findings)


def convergence_decision(findings, state, acceptance_ok, attempt, autofix_mode,
                         backstop=MAX_ATTEMPTS_BACKSTOP):
    """Decide one attempt deterministically from the classified findings, the
    running state, acceptance, the attempt count, and the autofix mode. Returns
    ``(action, halt_reason)`` with ``action`` in {"pass", "rework", "halt"} and a
    halt reason (one of HALT_REASONS) only when halting. Precedence (Rework loop &
    convergence spec):

    1. ``gate`` mode + any reviewer finding -> halt/``gate`` (a transient
       execution failure is exempt: it carries no impact).
    2. any halt-disposition finding (pre-existing x contract-breaking) ->
       halt/``scope-decision``.
    3. regression -> halt/``regression``: a runner-recorded resolved id reappears,
       or acceptance went green->red since the prior attempt.
    4. stuck -> halt/``stuck``: a fix finding persists across two consecutive
       attempts with nothing resolved this round (net progress is otherwise not
       required — a round may resolve one finding and surface another).
    5. no fix findings remain and acceptance is green -> ``pass``.
    6. attempt count reaches the backstop -> halt/``backstop`` (a seatbelt against
       slow non-convergence, not a target cap); otherwise -> ``rework``.
    """
    if autofix_mode == "gate" and any(f.impact is not None for f in findings):
        return ("halt", "gate")
    if any(f.disposition == "halt" for f in findings):
        return ("halt", "scope-decision")
    reappeared = any(_canon(f) in state.resolved_ids for f in findings)
    green_to_red = state.prev_acceptance_ok is True and not acceptance_ok
    if reappeared or green_to_red:
        return ("halt", "regression")
    real_fix = _real_fix_canons(findings)
    prior_fix = state.carried_ids
    carried = real_fix & prior_fix
    resolved_this_round = prior_fix - real_fix
    if carried and not resolved_this_round:
        return ("halt", "stuck")
    if not any(f.disposition == "fix" for f in findings) and acceptance_ok:
        return ("pass", None)
    if attempt >= backstop:
        return ("halt", "backstop")
    return ("rework", None)


def advance_state(state, findings, acceptance_ok):
    """Fold one attempt's classified findings into the convergence state for the
    next attempt. An **execution-failure** attempt (worker crash/timeout,
    acceptance non-zero) produced no review signal, so it leaves *both* id sets
    exactly as the prior reviewed attempt left them — a crash neither resolves nor
    re-carries a reviewer finding — and advances only the acceptance result (the
    one input the green->red regression rule still needs). A **reviewed** attempt
    records fix findings that disappeared (outstanding before, gone now) into the
    authoritative resolved-id set and replaces the carried-fix set with this
    attempt's outstanding fix ids. Only reviewer fix findings carry identity (see
    _real_fix_canons); this attempt's acceptance result is always stored for the
    next green->red check."""
    if _is_execution_failure(findings):
        state.prev_acceptance_ok = acceptance_ok
        return
    real_fix = _real_fix_canons(findings)
    state.resolved_ids |= (state.carried_ids - real_fix)
    state.carried_ids = real_fix
    state.prev_acceptance_ok = acceptance_ok


def _dispatch_review_call(model, effort, preamble, packet_path, codex_bin, last_msg_path,
                           live_path, header, timeout=DEFAULT_TIMEOUT):
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
    result = run_teed(argv, timeout=timeout, live_path=live_path, header=header)
    if result.timed_out:
        raise RuntimeError(
            "reviewer process ({} at effort {}) timed out after {}s without a "
            "usable verdict".format(model, effort, timeout)
        )
    if result.exit_code != 0:
        # The stderr tail survives teeing (stderr is merged into the tee'd
        # stream), so a crashed reviewer still names its cause.
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


def dispatch_reviewer(task, packet_path, codex_bin, run_dir, timeout=DEFAULT_TIMEOUT):
    """Per-task reviewer via ``codex exec`` — fresh context at the *same tier as
    the task it reviews* (routed by TIER_MAP[task.tier]; reviewer strength never
    escalates past the task's own tier). Preamble = the tier agent's review
    paragraph. Returns the parsed Verdict."""
    model, effort = TIER_MAP[task.tier]
    preamble = contract_preamble(task.tier)
    last_msg_path = os.path.join(run_dir, "task-{}-review-last.txt".format(task.number))
    live_path = os.path.join(run_dir, "task-{}-live.log".format(task.number))
    header = "── review · codex exec · {} · {} ──".format(model, effort)
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path,
        live_path, header, timeout=timeout,
    )


def dispatch_final_review(packet_path, codex_bin, run_dir, tier, timeout=DEFAULT_TIMEOUT):
    """Whole-plan final review: one ``codex exec`` call at ``tier`` (TIER_MAP[tier]
    — the plan's highest task tier, not a pinned ceiling) with that tier's
    contract preamble against the whole-plan diff + spec. Returns the parsed
    Verdict; the header records the resolved model+effort."""
    model, effort = TIER_MAP[tier]
    preamble = contract_preamble(tier)
    last_msg_path = os.path.join(run_dir, "final-review-last.txt")
    live_path = os.path.join(run_dir, "final-review-live.log")
    header = "── final review · codex exec · {} · {} ──".format(model, effort)
    return _dispatch_review_call(
        model, effort, preamble, packet_path, codex_bin, last_msg_path,
        live_path, header, timeout=timeout,
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


def execute_task(task, plan_path, spec_path, run_dir, codex_bin, cwd,
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
    effort, never the reviewer's."""
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

    attempt = 0
    while True:
        attempt += 1
        brief_path, brief_sha = _brief_for(
            task, plan_path, spec_path, run_dir, attempt, findings_carry
        )
        update_run_progress(run_dir, task.number, "worker")
        worker = dispatch_worker(
            task, brief_path, codex_bin, run_dir,
            effort_override=effort_override, timeout=timeout,
        )
        update_run_progress(run_dir, task.number, "acceptance")
        acceptance = run_acceptance(task, cwd, live_path)

        worker_ok = worker.exit_code == 0 and not worker.timed_out
        acc_ok = all(r.exit_code == 0 for r in acceptance)

        review_verdict = None
        findings = []       # classified Finding objects for this attempt
        cause = None        # short human-readable cause for an execution failure

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
            packet_path = _packet_for(
                task, plan_path, run_dir, review_base, cwd,
                prior_findings=prior_findings or None,
            )
            verdict = dispatch_reviewer(task, packet_path, codex_bin, run_dir, timeout=timeout)
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


def dispatch_final_review_fix(brief_path, codex_bin, run_dir, tier, attempt,
                              timeout=DEFAULT_TIMEOUT):
    """Final-review fix dispatch: one ``codex exec`` call scoped to the
    outstanding ``fix`` findings against the whole-plan diff — the final-review
    "worker" (Final review spec), never a full task brief re-dispatch. Same call
    shape as dispatch_worker (contract preamble + prompt, --output-last-message,
    timeout-bounded); tier = the plan's highest task tier."""
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
        "-m",
        model,
        "-c",
        "model_reasoning_effort={}".format(effort),
        "--output-last-message",
        last_msg_path,
        prompt,
    ]
    live_path = os.path.join(run_dir, "final-review-live.log")
    header = "── final-review fix · codex exec · {} · {} ──".format(model, effort)
    result = run_teed(argv, timeout=timeout, live_path=live_path, header=header)
    if result.timed_out:
        return WorkerResult(exit_code=None, last_message="", argv=argv, timed_out=True)
    last_message = ""
    if os.path.exists(last_msg_path):
        with open(last_msg_path, "r", encoding="utf-8") as f:
            last_message = f.read()
    return WorkerResult(exit_code=result.exit_code, last_message=last_message, argv=argv)


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
                          autofix_mode, timeout=DEFAULT_TIMEOUT):
    """Whole-plan final review through the same convergence loop as
    ``execute_task`` (Final review spec: "now runs the same loop"). Diff base is
    always ``run_base`` (run-start HEAD) across every attempt — a fix dispatch's
    edits are left uncommitted, so each re-review's diff simply accumulates them;
    a single ``fix: final-review`` commit lands once the loop converges to pass,
    only if a fix was ever applied (Commit discipline). Attempt 1 is review-only
    (nothing to fix yet); from attempt 2 on, the "worker" is a
    dispatch_final_review_fix call scoped to the outstanding ``fix`` findings — a
    fix-dispatch crash/timeout preempts the re-review as an implicit
    execution-failure finding, exactly like ``execute_task``. Halt carries the
    drafted ``repair_task``."""
    state = ConvergenceState()
    fix_findings = []  # outstanding fix findings -> next attempt's fix dispatch
    prior_findings = []  # outstanding reviewer fix findings (dicts) -> next packet
    applied_fix = False
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
                brief_path, codex_bin, run_dir, tier, attempt, timeout=timeout
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
            packet_path = _final_packet(
                spec_path, run_base, diff, run_dir,
                prior_findings=prior_findings or None,
            )
            verdict = dispatch_final_review(
                packet_path, codex_bin, run_dir, tier, timeout=timeout
            )
            classify_findings(verdict, diff)
            write_final_review_receipt(run_dir, verdict)
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
            write_final_review_receipt(run_dir, verdict, halt_reason=halt_reason)
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
    mask the failure (fail-loud, like a reviewer crash)."""
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
    header = "── doc-sync · codex exec · {} · {} ──".format(model, effort)
    update_run_progress(run_dir, None, "doc-sync")
    result = run_teed(argv, timeout=timeout, live_path=live_path, header=header)
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
    ``timeout`` bounds every worker and reviewer ``codex exec`` call."""
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

    # Incremental run.json: write `running` before the first task so --status/the
    # monitor can distinguish an in-progress run from a dead one, and rewrite after
    # each passed task so live per-task progress is visible. base_commit rides
    # along so a resume still reads it; started_at/pid feed the monitor.
    write_run_json(run_dir, plan_path, spec_path, "running", task_summaries, run_base,
                   started_at=run_started, pid=run_pid)
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
                       run_base, started_at=run_started, pid=run_pid)
        outcome = execute_task(
            task, plan_path, spec_path, run_dir, codex_bin, cwd,
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
                started_at=run_started, pid=run_pid,
            )
        else:
            annotate_ledger(plan_path, task, "escalated: {}".format(outcome.summary))
            overall = "escalated"
            escalated = True
            break

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
                autofix_mode, timeout=timeout,
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
                   doc_sync=doc_sync_record)
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
