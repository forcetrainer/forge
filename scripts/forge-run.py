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

Reuses ``extract-brief.py`` for all plan/spec parsing — no duplicated heading
grammar. Tier -> model/effort mapping lives in exactly one table (``TIER_MAP``).
All parse failures raise loudly naming the cause (DECISIONS 2026-07-11);
``ultra`` reasoning effort is never emitted (DECISIONS 2026-07-13).
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)

# Reuse extract-brief.py (hyphenated filename -> importlib) for plan/spec parsing.
_eb_spec = importlib.util.spec_from_file_location(
    "forge_run_extract_brief", os.path.join(SCRIPTS_DIR, "extract-brief.py")
)
eb = importlib.util.module_from_spec(_eb_spec)
_eb_spec.loader.exec_module(eb)

# Reuse review-packet.py for the reviewer packet (task block / spec + git diff) —
# no duplicated packet assembly or heading grammar.
_rp_spec = importlib.util.spec_from_file_location(
    "forge_run_review_packet", os.path.join(SCRIPTS_DIR, "review-packet.py")
)
rp = importlib.util.module_from_spec(_rp_spec)
_rp_spec.loader.exec_module(rp)


# Tier -> (model, model_reasoning_effort). Single update point on model churn.
TIER_MAP = {
    "trivial": ("gpt-5.6-luna", "medium"),
    "standard": ("gpt-5.6-terra", "high"),
    "complex": ("gpt-5.6-sol", "high"),
}
# Reviewer routing (used in Task 3); trivial tier has no reviewer.
REVIEW_MAP = {
    "standard": ("gpt-5.6-terra", "high"),
    "complex": ("gpt-5.6-sol", "high"),
}
# Worker contract source per tier — agents/*.md body (frontmatter stripped),
# single source shared with the Claude Code harness.
CONTRACT_AGENT = {
    "trivial": "forge-light",
    "standard": "forge-standard",
    "complex": "forge-deep",
}

_ACC_TAIL_CHARS = 2000

# Rework cap: initial attempt + one rework, enforced as a loop counter
# (DECISIONS 2026-07-13 — the prose cap proved unenforceable).
MAX_ATTEMPTS = 2

# Default subprocess timeout (seconds) for worker and reviewer `codex exec`
# calls; overridable via --timeout. A hung worker/reviewer must not hang the
# runner forever (final-review finding).
DEFAULT_TIMEOUT = 3600

# Allowed --effort override levels (per-task worker dispatch only). `ultra` is
# deliberately excluded — it is prohibited at every tier (DECISIONS 2026-07-13).
ALLOWED_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Reviewer verdict contract — the machine-readable half the runner parses. Kept
# in the runner (not agents/*.md) because the JSON shape is a runner concern; the
# reviewer's judgement rules live in the agents/*.md review paragraph (preamble).
REVIEW_VERDICT_INSTRUCTION = (
    "End your message with your verdict as exactly one JSON object and nothing "
    "after it: {\"verdict\": \"pass\"} when the diff satisfies the spec and the "
    "task, or {\"verdict\": \"findings\", \"findings\": [\"<file:line - issue>\", "
    "...]} listing every blocking issue as strings. The runner parses the last "
    "JSON object in your message; emit nothing parseable as JSON after it."
)


@dataclass
class Task:
    number: int
    title: str
    tier: str
    depends_on: list = field(default_factory=list)
    acceptance_commands: list = field(default_factory=list)
    checkbox_line: int = -1


@dataclass
class AcceptanceResult:
    command: str
    exit_code: int
    output_tail: str


@dataclass
class WorkerResult:
    exit_code: int
    last_message: str
    argv: list
    timed_out: bool = False


@dataclass
class Verdict:
    kind: str  # "pass" | "findings"
    findings: list = field(default_factory=list)


@dataclass
class TaskOutcome:
    status: str  # "passed" | "escalated"
    attempts: int
    summary: str
    findings: list = field(default_factory=list)


# --- plan parsing (reuses extract-brief heading grammar) --------------------


def _field_value(block_lines, block_mask, name):
    """First-line value of a single-line ``**Name:**`` field, or None."""
    prefix = "**{}:**".format(name)
    for i, ln in enumerate(block_lines):
        if not block_mask[i] and ln.startswith(prefix):
            return ln[len(prefix):].strip()
    return None


def _field_text(block_lines, block_mask, name):
    """Full text of a ``**Name:**`` field: its line plus any wrapped
    continuation, joined with spaces. A blank line, a new field, a heading, or a
    fence ends it."""
    prefix = "**{}:**".format(name)
    for i, ln in enumerate(block_lines):
        if block_mask[i] or not ln.startswith(prefix):
            continue
        parts = [ln[len(prefix):]]
        j = i + 1
        while j < len(block_lines):
            if block_mask[j]:
                break
            nxt = block_lines[j]
            if (
                nxt.strip() == ""
                or eb.FIELD_LINE_RE.match(nxt)
                or re.match(r"^#{1,6}\s", nxt)
            ):
                break
            parts.append(nxt)
            j += 1
        return " ".join(p.strip() for p in parts).strip()
    return ""


def _parse_commands(text):
    """Inline-code spans on an ``**Acceptance:**`` line are the commands."""
    return [
        m.group(1).strip()
        for m in re.finditer(r"`([^`]+)`", text)
        if m.group(1).strip()
    ]


def _parse_depends(text):
    return [int(n) for n in re.findall(r"Task\s+(\d+)", text)]


def parse_plan_tasks(plan_path):
    """Parse every ``### Task N:`` block into a Task. Raises RuntimeError naming
    the cause on a wrong-level task heading or a duplicate task number — never
    guesses (DECISIONS 2026-07-11)."""
    lines = eb.read_lines(plan_path)
    mask = eb.fence_mask(lines)

    starts = []  # (number, line_index)
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = eb.TASK_HEADING_RE.match(line)
        if m:
            starts.append((int(m.group(1)), i))
            continue
        wl = eb.ANY_LEVEL_TASK_HEADING_RE.match(line)
        if wl and len(wl.group(1)) != 3:
            raise RuntimeError(
                "task {n} heading must be '### Task {n}:' (three #), found "
                "'{lvl} Task {n}:' at line {ln} in {p}".format(
                    n=int(wl.group(2)), lvl=wl.group(1), ln=i + 1, p=plan_path
                )
            )
    if not starts:
        raise RuntimeError("no '### Task N:' headings found in {}".format(plan_path))

    nums = [n for n, _ in starts]
    dups = sorted({n for n in nums if nums.count(n) > 1})
    if dups:
        raise RuntimeError(
            "duplicate task number(s) {} — '### Task N:' headings must be unique "
            "in {}".format(", ".join(str(d) for d in dups), plan_path)
        )

    tasks = []
    for num, start in starts:
        block = eb.extract_task_block(lines, num)
        heading = lines[start]
        tm = re.match(r"^###\s+Task\s+\d+:\s*(.*)$", heading)
        title = tm.group(1).strip() if tm else ""

        block_lines = block.splitlines()
        block_mask = eb.fence_mask(block_lines)

        tier = _field_value(block_lines, block_mask, "Tier")
        if tier is None:
            raise RuntimeError("task {} is missing the **Tier:** line".format(num))
        tier = tier.strip().lower()
        if tier not in TIER_MAP:
            raise RuntimeError(
                "task {} has unknown tier {!r} — expected one of {}".format(
                    num, tier, ", ".join(sorted(TIER_MAP))
                )
            )

        depends_on = _parse_depends(_field_value(block_lines, block_mask, "Depends on") or "")
        acceptance = _parse_commands(_field_text(block_lines, block_mask, "Acceptance"))

        checkbox_line = -1
        for offset, bl in enumerate(block_lines):
            if block_mask[offset]:
                continue
            if re.match(r"^\s*[-*]\s*\[[ xX]\]", bl):
                checkbox_line = start + offset
                break

        tasks.append(
            Task(
                number=num,
                title=title,
                tier=tier,
                depends_on=depends_on,
                acceptance_commands=acceptance,
                checkbox_line=checkbox_line,
            )
        )
    return tasks


def order_tasks(tasks):
    """Return tasks in dependency order (each dependency before its dependents).
    Raises on an unknown dependency or a cycle."""
    by_num = {t.number: t for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            if d not in by_num:
                raise RuntimeError(
                    "task {} depends on unknown task {}".format(t.number, d)
                )
    order = []
    state = {}  # number -> 0 visiting, 1 done

    def visit(n):
        s = state.get(n)
        if s == 1:
            return
        if s == 0:
            raise RuntimeError("dependency cycle involving task {}".format(n))
        state[n] = 0
        for d in by_num[n].depends_on:
            visit(d)
        state[n] = 1
        order.append(by_num[n])

    for t in tasks:
        visit(t.number)
    return order


_EFFORT_OVERRIDE_RE = re.compile(r"^(\d+)=(.+)$")


def parse_effort_overrides(raw_list):
    """Parse repeatable ``--effort N=LEVEL`` CLI entries into ``{task_number:
    level}``. Malformed entries (not ``N=LEVEL``) or a level outside
    ALLOWED_EFFORTS (including ``ultra``, which is prohibited at every tier)
    raise RuntimeError naming the cause. Task-number existence against the plan
    is validated separately by the caller, once the plan is parsed."""
    overrides = {}
    for item in raw_list or []:
        m = _EFFORT_OVERRIDE_RE.match(item.strip())
        if not m:
            raise RuntimeError(
                "--effort {!r} must be in the form N=LEVEL (task number and "
                "one of {})".format(item, ", ".join(ALLOWED_EFFORTS))
            )
        number = int(m.group(1))
        level = m.group(2).strip()
        if level not in ALLOWED_EFFORTS:
            raise RuntimeError(
                "--effort {!r}: unknown level {!r} — expected one of {}".format(
                    item, level, ", ".join(ALLOWED_EFFORTS)
                )
            )
        overrides[number] = level
    return overrides


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


def verdict_to_dict(verdict):
    if verdict.kind == "pass":
        return {"verdict": "pass"}
    return {"verdict": "findings", "findings": list(verdict.findings)}


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


# --- git helpers (diff base for review packets) -----------------------------


def _git_head(cwd):
    """HEAD SHA of the repo at ``cwd``, or None when ``cwd`` is not a git repo
    (git unavailable / no commits). Reviews require a repo; callers that must have
    one raise loudly, and the plan-level final review is skipped without one."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _snapshot_worktree(cwd):
    """Per-task review base: a tree-ish capturing the working tree *before* the
    task runs, so ``git diff <snapshot>`` afterwards shows only this task's own
    changes. Nothing commits between tasks (HEAD never advances), so a HEAD base
    would fold every prior task's still-uncommitted change into this task's packet;
    ``git stash create`` snapshots the current tracked working tree as a dangling
    commit without touching the working tree, index, or refs. Returns that commit
    SHA, or HEAD when the tree is clean (``stash create`` emits nothing), or None
    when ``cwd`` is not a git repo (callers that reach the reviewer raise loudly).
    Untracked files are invisible to this base, consistent with ``git diff``
    (DEFERRALS 2026-07-11)."""
    head = _git_head(cwd)
    if head is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "stash", "create"], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or head


def _git_diff(cwd, base):
    """``git diff <base>`` in ``cwd``. Raises RuntimeError naming the cause on a
    git failure (a packet-generation error — halt per the Halt spec)."""
    proc = subprocess.run(
        ["git", "diff", base], cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git diff {} failed in {}: {}".format(base, cwd, proc.stderr.strip())
        )
    return proc.stdout


def _packet_for(task, plan_path, run_dir, base, cwd):
    """Per-task review packet via review-packet.py: the task block + ``git diff
    <base>``. Missing task block raises (fail-loud)."""
    with open(plan_path, "r", encoding="utf-8") as f:
        plan_text = f.read()
    block = rp.extract_task_block(plan_text, task.number)
    if block is None:
        raise RuntimeError(
            "review packet: " + rp.diagnose_missing_task(plan_text, task.number, plan_path)
        )
    diff = _git_diff(cwd, base)
    packet = rp.build_packet(block, base, diff)
    path = os.path.join(run_dir, "task-{}-review.md".format(task.number))
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path


def _final_packet(spec_path, base, diff, run_dir):
    """Whole-plan final-review packet: the spec + the whole-plan ``git diff
    <base>``, assembled by review-packet.py's fence-safe builder."""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_text = f.read()
    packet = rp.build_packet(spec_text, base, diff)
    path = os.path.join(run_dir, "final-review.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path


# --- receipts & ledger ------------------------------------------------------


def write_receipt(run_dir, task, attempt, receipt_dict):
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "task-{}-attempt-{}.json".format(task.number, attempt))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(receipt_dict, f, indent=2)
    return path


def write_run_json(run_dir, plan_path, spec_path, status, task_summaries):
    os.makedirs(run_dir, exist_ok=True)
    data = {
        "plan": os.path.abspath(plan_path),
        "spec": os.path.abspath(spec_path),
        "status": status,
        "tasks": task_summaries,
    }
    path = os.path.join(run_dir, "run.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def write_final_review_receipt(run_dir, verdict):
    """Persist the plan-level final-review verdict alongside the task receipts."""
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "final-review.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(verdict_to_dict(verdict), f, indent=2)
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
    # Snapshot the working tree before this task runs — the per-task review base.
    # HEAD does not advance between tasks (nothing commits), so a HEAD base would
    # include prior tasks' uncommitted changes; the snapshot isolates `git diff`
    # to this task's own changes. Taken once (trivial tiers need no reviewer).
    review_base = _snapshot_worktree(cwd) if task.tier != "trivial" else None
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
    os.makedirs(run_dir, exist_ok=True)
    ensure_forge_gitignore(cwd)
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
    run_base = _git_head(cwd)  # whole-plan diff base for the final review

    task_summaries = []
    overall = "passed"
    escalated = False

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
                }
            )
            continue

        _clear_task_receipts(run_dir, task.number)
        outcome = execute_task(
            task, plan_path, spec_path, run_dir, codex_bin, cwd,
            effort_override=effort_overrides.get(task.number), timeout=timeout,
        )
        task_summaries.append(
            {
                "number": task.number,
                "title": task.title,
                "tier": task.tier,
                "status": outcome.status,
                "attempts": outcome.attempts,
            }
        )
        if outcome.status == "passed":
            annotate_ledger(
                plan_path, task, "passed, {} attempt(s)".format(outcome.attempts)
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

    write_run_json(run_dir, plan_path, spec_path, overall, task_summaries)
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
    parser.add_argument("plan", help="approved plan markdown file")
    parser.add_argument("--spec", required=True, help="design spec markdown file")
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

    run_dir = args.run_dir or _default_run_dir()
    try:
        effort_overrides = parse_effort_overrides(args.effort)
        return run_plan(
            args.plan, args.spec, run_dir, args.codex_bin, os.getcwd(),
            effort_overrides=effort_overrides, timeout=args.timeout,
        )
    except RuntimeError as e:
        print("error: {}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
