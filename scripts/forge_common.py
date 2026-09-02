"""forge_common — shared foundation for forge-run.py and its helper modules.

Holds the runner's dataclasses, the tier/effort/contract constants (each with a
single update point), and the two hyphenated sibling scripts (``extract-brief``,
``review-packet``) loaded once via importlib and re-exported as ``eb``/``rp`` so
every module shares one instance. Imported as a plain module (``import
forge_common``) so ``sys.modules`` caches a single object — dataclass identity is
preserved across forge_plan/forge_git/forge_receipts and the test suite.
"""
import importlib.util
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)


def _load_sibling(mod_name, filename):
    """Load a sibling script by path (its filename is hyphenated, so it cannot be
    a normal import). One instance, shared via re-export."""
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(SCRIPTS_DIR, filename)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse extract-brief.py for plan/spec parsing and review-packet.py for reviewer
# packets — no duplicated heading grammar or packet assembly.
eb = _load_sibling("forge_run_extract_brief", "extract-brief.py")
rp = _load_sibling("forge_run_review_packet", "review-packet.py")


# Tier -> (model, model_reasoning_effort). Single update point on model churn.
TIER_MAP = {
    "trivial": ("gpt-5.6-luna", "low"),
    "standard": ("gpt-5.6-terra", "medium"),
    "complex": ("gpt-5.6-sol", "medium"),
}
TIER_ORDER = ("trivial", "standard", "complex")  # ascending; index gives rank
# Reviewer routing reads TIER_MAP directly (reviewer tier = task tier; the
# once-separate reviewer table is retired to remove the stale-drift hazard of
# two tier tables silently diverging on a model-churn edit).
# Worker contract source per tier — agents/*.md body (frontmatter stripped),
# single source shared with the Claude Code harness.
CONTRACT_AGENT = {
    "trivial": "forge-light",
    "standard": "forge-standard",
    "complex": "forge-deep",
}

_ACC_TAIL_CHARS = 2000

# Backstop against slow non-convergence, not a target cap — the convergence
# loop decides pass/rework/halt on per-attempt progress; this is only a
# seatbelt, raised from the old 2-iteration cap (DECISIONS 2026-07-16: every
# prior halt at 2 resolved on one more manual run, i.e. the cap was tripping
# on converging work, not a considered gate).
MAX_ATTEMPTS_BACKSTOP = 5

# --autofix modes: "auto" applies the disposition matrix (default, fixes the
# in-diff x contract-breaking quadrant in-loop); "gate" halts on any finding
# regardless of quadrant — the conservative escape hatch (DECISIONS 2026-07-16).
AUTOFIX_MODES = ("auto", "gate")

# Halt reasons surfaced on an escalated TaskOutcome / final review (Disposition
# matrix + Rework loop & convergence specs).
HALT_REASONS = ("scope-decision", "regression", "stuck", "backstop", "gate")

# Default subprocess timeout (seconds) for worker and reviewer `codex exec`
# calls; overridable via --timeout. A hung worker/reviewer must not hang the
# runner forever (final-review finding).
DEFAULT_TIMEOUT = 3600

# Allowed --effort override levels (per-task worker dispatch only). `ultra` is
# deliberately excluded — it is prohibited at every tier (DECISIONS 2026-07-13).
ALLOWED_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Reviewer verdict contract — the machine-readable half the runner parses. Kept
# here (not agents/*.md) because the JSON shape is a runner concern; the
# reviewer's judgement rules live in the agents/*.md review paragraph (preamble).
REVIEW_VERDICT_INSTRUCTION = (
    "End your message with your verdict as exactly one JSON object and nothing "
    "after it: {\"verdict\": \"pass\", \"coverage\": [ ... ]} when the diff "
    "satisfies the spec and the task, or {\"verdict\": \"findings\", "
    "\"coverage\": [ ... ], \"findings\": [ ... ]} listing every issue as a "
    "finding object: {\"id\": \"f1\", \"summary\": \"one line\", "
    "\"location\": {\"file\": \"path\", \"lines\": \"12-20\"}, \"provenance\": "
    "\"in-diff\" | \"pre-existing\", \"impact\": \"contract-breaking\" | "
    "\"improvement\", \"contract_ref\": \"acceptance criterion or spec "
    "section it violates\" | null, \"convergence\": \"resolved\" | \"carried\" "
    "| \"new\" | null, \"carried_from\": \"prior finding id\" | null, "
    "\"repair_task\": {\"title\": ..., \"files\": [...], \"spec\": ..., "
    "\"tests\": [...], \"acceptance\": [...], \"tier\": ...} | null}. "
    "coverage is required on every DISCOVERY verdict, pass included, and "
    "OMITTED on VERIFICATION verdicts — the packet's '## Review kind' marker "
    "names which one this review is; a verification verdict that includes "
    "coverage anyway is also accepted. On a discovery verdict: one entry per "
    "checklist id supplied in the packet, each {\"id\": \"<checklist id>\", "
    "\"status\": \"satisfied\" | \"violated\" | \"n/a\", \"evidence\": "
    "\"file:line, hunk, or reason\"} — evidence must be non-empty on every "
    "entry, and \"n/a\" requires a reason in evidence (why the diff cannot "
    "touch that item), never a rubber-stamped satisfied. "
    "location.lines accepts a single line (\"12\"), a single range "
    "(\"12-20\"), or a comma-separated list of either (\"12-20,45,60-62\") — "
    "a finding is in-diff when any one of those ranges falls inside the "
    "diff. "
    "Classification rules: label impact \"contract-breaking\" only when "
    "contract_ref names the acceptance criterion or spec section it violates — "
    "a contract-breaking finding with no contract_ref is treated as "
    "improvement; a contract-breaking finding must carry a parseable "
    "location.lines — an absent or unparseable location on a contract-"
    "breaking finding is rejected and re-dispatched, never silently treated "
    "as pre-existing; a finding whose location falls outside this review's "
    "diff is pre-existing, never in-diff, regardless of how it reads; "
    "repair_task is "
    "required only when the finding is pre-existing and contract-breaking, "
    "optional otherwise. Set convergence and carried_from only on a re-review, "
    "labeling each current finding resolved, carried, or new against the prior "
    "attempt's findings supplied in the packet — omit both on a first review. "
    "The runner parses the last JSON object in your message; emit nothing "
    "parseable as JSON after it."
)


@dataclass
class Task:
    number: int
    title: str
    tier: str
    tier_justification: str | None = None
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
    thread_id: "str | None" = None
    # The dispatched prompt. It is deliberately NOT in ``argv`` — it travels on
    # the child's stdin so it is not bounded by ARG_MAX — but callers and tests
    # still need to see exactly what was sent.
    prompt: str = ""


@dataclass
class Finding:
    id: str
    summary: str
    file: str
    lines: str  # "12-20" or "12"
    provenance: str  # "in-diff" | "pre-existing" (reviewer-proposed)
    impact: str  # "contract-breaking" | "improvement"
    contract_ref: str | None = None
    convergence: str | None = None  # "resolved" | "carried" | "new" | None
    carried_from: str | None = None
    repair_task: dict | None = None
    disposition: str | None = None  # "fix" | "defer" | "halt" — set by the runner


@dataclass
class CoverageEntry:
    id: str
    status: str  # "satisfied" | "violated" | "n/a"
    evidence: str


@dataclass
class Verdict:
    kind: str  # "pass" | "findings"
    findings: list = field(default_factory=list)  # list[Finding]
    coverage: list = field(default_factory=list)  # list[CoverageEntry]


@dataclass
class TaskOutcome:
    status: str  # "passed" | "escalated"
    attempts: int
    summary: str
    findings: list = field(default_factory=list)
    halt_reason: str | None = None
    deferrals: list = field(default_factory=list)
    repair_task: dict | None = None


def finding_to_dict(finding):
    """Serialize one Finding for a receipt / run.json — mirrors the reviewer's
    own wire schema (nested ``location``) so a persisted finding can also be
    replayed straight into review-packet.py's ``--prior-findings`` input.
    Passes a bare string through unchanged: forge-run.py's verdict parser
    still emits the pre-Task-2 ``list[str]`` shape until the classification
    engine (Task 2) rewrites it to build Finding objects."""
    if not isinstance(finding, Finding):
        return finding
    return {
        "id": finding.id,
        "summary": finding.summary,
        "location": {"file": finding.file, "lines": finding.lines},
        "provenance": finding.provenance,
        "impact": finding.impact,
        "contract_ref": finding.contract_ref,
        "convergence": finding.convergence,
        "carried_from": finding.carried_from,
        "repair_task": finding.repair_task,
        "disposition": finding.disposition,
    }


def verdict_to_dict(verdict):
    if verdict.kind == "pass":
        return {"verdict": "pass"}
    return {
        "verdict": "findings",
        "findings": [finding_to_dict(f) for f in verdict.findings],
    }


@dataclass
class TeeResult:
    exit_code: "int | None"  # None when timed out
    timed_out: bool
    tail: str  # last _ACC_TAIL_CHARS of merged stdout+stderr


@dataclass
class JsonTeeResult:
    exit_code: "int | None"  # None when timed out
    timed_out: bool
    tail: str  # last _ACC_TAIL_CHARS of merged stdout+stderr (raw, pre-render)
    thread_id: "str | None"  # from the first `thread.started` event, or None


def _spawn_and_pump(argv, *, cwd, shell, timeout, pump_line, stdin_text=None):
    """Spawn ``argv``, calling ``pump_line(line)`` for each merged stdout+stderr
    line as it arrives, and return ``(exit_code_or_None, timed_out)``. Shared
    plumbing between ``run_teed`` and ``run_json_teed`` — the process
    spawn/kill/timeout handling is identical; only what happens per line differs.
    A child that outlives ``timeout`` is killed (whole process group) and
    reported as ``timed_out`` — never hangs the run. ``start_new_session`` puts
    the child in its own group so ``shell=True`` command trees die with it.

    ``stdin_text``, when set, is written to the child's stdin and the pipe is
    then closed (signalling EOF). This is how prompts reach ``codex exec``:
    argv is capped by ``ARG_MAX`` (1 MiB on darwin, shared with the environment
    block), which is far below the model's usable context, so a large review
    packet passed as an argument fails the *spawn* with ``E2BIG``. The write
    runs on its own daemon thread — a child that never drains stdin would
    otherwise block the caller once the ~64 KiB pipe buffer filled, before
    ``proc.wait(timeout)`` could bound it."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        shell=shell,
        # DEVNULL, never inherited: a dispatched child that reads stdin must
        # get immediate EOF rather than block on the runner's terminal.
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    writer = None
    if stdin_text is not None:
        def _feed():
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass  # child exited or closed stdin early; its exit code decides
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

        writer = threading.Thread(target=_feed, daemon=True)
        writer.start()

    def _pump():
        for line in proc.stdout:
            pump_line(line)

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
    reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)
    # Close the pipe once the reader is done with it. Popen only closes it on
    # GC, so without this every dispatch leaks a file object and CPython emits
    # a ResourceWarning under `-W error` / unittest's warning capture. Guarded:
    # a killed child can leave the reader blocked past its join, and closing
    # under it would raise inside the pump thread.
    if not reader.is_alive():
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass

    return (None if timed_out else proc.returncode), timed_out


def run_teed(argv, *, cwd=None, shell=False, timeout, live_path, header,
             stdin_text=None):
    """Run a subprocess, streaming its merged stdout+stderr line-by-line into
    ``live_path`` (append) under a ``header`` line, while returning the exit code,
    a timed-out flag, and the output tail the runner loop needs.

    A behavior-preserving replacement for ``subprocess.run(capture_output=True)``:
    the returned ``tail`` matches the old ``combined[-_ACC_TAIL_CHARS:]``. The
    live file is flushed per line so a tailing monitor sees output as it
    happens."""
    with open(live_path, "a", encoding="utf-8") as live:
        live.write(header + "\n")
        live.flush()
        buf = []

        def _pump_line(line):
            live.write(line)
            live.flush()
            buf.append(line)

        exit_code, timed_out = _spawn_and_pump(
            argv, cwd=cwd, shell=shell, timeout=timeout, pump_line=_pump_line,
            stdin_text=stdin_text,
        )

    merged = "".join(buf)
    return TeeResult(
        exit_code=exit_code, timed_out=timed_out, tail=merged[-_ACC_TAIL_CHARS:]
    )


def run_json_teed(argv, *, cwd=None, timeout, live_path, events_path, header,
                   render_line, stdin_text=None):
    """Run a ``codex exec --json`` child: each stdout line is one JSONL event.
    Raw JSONL is appended to ``events_path`` (thread capture + debugging); each
    event is translated via ``render_line(event) -> str | None`` into a plain-
    text line appended to ``live_path`` under ``header`` — ``None`` emits nothing,
    so control/progress events produce no live-log noise (parity with the old
    human-readable stream, never raw JSON). A merged-stream line that fails to
    parse as JSON (stray stderr text, not an event) is teed to ``live_path``
    verbatim instead — fail-open, matching ``run_teed``'s behavior for that line
    — and is not written to ``events_path``. The child's ``thread_id`` is
    captured from the first ``{"type": "thread.started", "thread_id": ...}``
    event seen; absent from the stream, it stays ``None`` without raising."""
    with open(live_path, "a", encoding="utf-8") as live, \
         open(events_path, "a", encoding="utf-8") as events:
        live.write(header + "\n")
        live.flush()
        buf = []
        thread_id = None

        def _pump_line(line):
            nonlocal thread_id
            buf.append(line)
            stripped = line.rstrip("\n")
            if not stripped:
                return
            try:
                event = json.loads(stripped)
            except ValueError:
                live.write(line)
                live.flush()
                return
            events.write(stripped + "\n")
            events.flush()
            if (
                thread_id is None
                and isinstance(event, dict)
                and event.get("type") == "thread.started"
            ):
                thread_id = event.get("thread_id")
            rendered = render_line(event) if isinstance(event, dict) else None
            if rendered is not None:
                live.write(rendered if rendered.endswith("\n") else rendered + "\n")
                live.flush()

        exit_code, timed_out = _spawn_and_pump(
            argv, cwd=cwd, shell=False, timeout=timeout, pump_line=_pump_line,
            stdin_text=stdin_text,
        )

    merged = "".join(buf)
    return JsonTeeResult(
        exit_code=exit_code,
        timed_out=timed_out,
        tail=merged[-_ACC_TAIL_CHARS:],
        thread_id=thread_id,
    )
