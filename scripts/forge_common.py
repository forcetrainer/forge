"""forge_common — shared foundation for forge-run.py and its helper modules.

Holds the runner's dataclasses, the tier/effort/contract constants (each with a
single update point), and the two hyphenated sibling scripts (``extract-brief``,
``review-packet``) loaded once via importlib and re-exported as ``eb``/``rp`` so
every module shares one instance. Imported as a plain module (``import
forge_common``) so ``sys.modules`` caches a single object — dataclass identity is
preserved across forge_plan/forge_git/forge_receipts and the test suite.
"""
import importlib.util
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
    "trivial": ("gpt-5.6-luna", "medium"),
    "standard": ("gpt-5.6-terra", "high"),
    "complex": ("gpt-5.6-sol", "high"),
}
# Reviewer routing; trivial tier has no reviewer.
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
# here (not agents/*.md) because the JSON shape is a runner concern; the
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


def verdict_to_dict(verdict):
    if verdict.kind == "pass":
        return {"verdict": "pass"}
    return {"verdict": "findings", "findings": list(verdict.findings)}


@dataclass
class TeeResult:
    exit_code: "int | None"  # None when timed out
    timed_out: bool
    tail: str  # last _ACC_TAIL_CHARS of merged stdout+stderr


def run_teed(argv, *, cwd=None, shell=False, timeout, live_path, header):
    """Run a subprocess, streaming its merged stdout+stderr line-by-line into
    ``live_path`` (append) under a ``header`` line, while returning the exit code,
    a timed-out flag, and the output tail the runner loop needs.

    A behavior-preserving replacement for ``subprocess.run(capture_output=True)``:
    the returned ``tail`` matches the old ``combined[-_ACC_TAIL_CHARS:]``, and a
    child that outlives ``timeout`` is killed (whole process group) and reported
    as ``timed_out`` — never hangs the run. ``start_new_session`` puts the child
    in its own group so ``shell=True`` command trees die with it. The live file is
    flushed per line so a tailing monitor sees output as it happens."""
    with open(live_path, "a", encoding="utf-8") as live:
        live.write(header + "\n")
        live.flush()
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        buf = []

        def _pump():
            for line in proc.stdout:
                live.write(line)
                live.flush()
                buf.append(line)

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

    merged = "".join(buf)
    return TeeResult(
        exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out,
        tail=merged[-_ACC_TAIL_CHARS:],
    )
