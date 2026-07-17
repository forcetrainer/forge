"""forge_dispose — the shared cross-harness review-finding decision helper.

Pure decision logic, extracted from forge-run.py (Phase 12b): verdict parsing,
provenance verification against the actual diff, the disposition matrix
(fix/defer/halt), and convergence-based rework/halt/pass decisions across
attempts. Contains no dispatch, no fixing, no committing, no file writes to
project docs — it takes reviewer output and diff text in, returns a decision.
The Codex runner (forge-run.py) calls these functions in-process; the Claude
dispatch path calls the same logic via this module's CLI (``main`` below) —
one tested implementation, two callers, so the decision is identical on both
harnesses regardless of who acts on it.

Imported as a plain module (not via importlib) so ``sys.modules`` caches one
instance and ``Finding``/``Verdict`` keep a single class identity across
forge-run.py and this module — the same discipline forge_git/forge_plan/
forge_receipts follow (DECISIONS 2026-07-14).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

from forge_common import (
    AUTOFIX_MODES,
    MAX_ATTEMPTS_BACKSTOP,
    Finding,
    Verdict,
    finding_to_dict,
)


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

    def to_dict(self):
        """Serialize for the CLI's ``--state`` round-trip (sets -> sorted lists,
        for a stable/diffable JSON file)."""
        return {
            "resolved_ids": sorted(self.resolved_ids),
            "carried_ids": sorted(self.carried_ids),
            "prev_acceptance_ok": self.prev_acceptance_ok,
        }

    @classmethod
    def from_dict(cls, d):
        """Deserialize a prior ``--state`` file (lists -> sets). ``None``/``{}``
        yields a fresh state — the CLI's attempt-1 case, before any state file
        exists."""
        d = d or {}
        return cls(
            resolved_ids=set(d.get("resolved_ids") or []),
            carried_ids=set(d.get("carried_ids") or []),
            prev_acceptance_ok=d.get("prev_acceptance_ok"),
        )


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


# --- CLI ---------------------------------------------------------------------


def execution_failure_finding(detail):
    """An execution failure (worker crash/timeout, acceptance non-zero) as an
    implicit fix-retry finding: disposition ``fix`` so the attempt cannot converge
    to pass, but no provenance/impact so it never defers, scope-halts, or counts as
    a carried (stuck) review finding — only the regression (green->red) and
    backstop rules apply (Rework loop & convergence). ``detail`` is the reattempt
    guidance surfaced as the outstanding finding. Mirrors forge-run.py's own
    ``_execution_failure_finding`` (same shape, same contract) — the CLI's
    ``--execution-failure`` path bypasses classify_findings entirely, exactly as
    the runner's in-process call does."""
    return Finding(
        id="exec-failure", summary=detail, file=None, lines=None,
        provenance=None, impact=None, disposition="fix",
    )


def _run_git_diff(base):
    """``git diff <base>`` in the caller's cwd — the CLI computes its own
    authoritative diff (never trusts a caller-supplied diff), exactly as
    classify_findings/verify_provenance require. Fails loud naming the cause on
    a bad ref or a git invocation failure (DECISIONS 2026-07-11)."""
    try:
        result = subprocess.run(
            ["git", "diff", base], capture_output=True, text=True
        )
    except OSError as e:
        raise RuntimeError("failed to invoke git: {}".format(e))
    if result.returncode != 0:
        raise RuntimeError(
            "git diff {} failed: {}".format(base, result.stderr.strip())
        )
    return result.stdout


def _findings_by_disposition(findings):
    """Group classified findings by disposition for decision.json, each
    serialized via forge_common.finding_to_dict — the same wire shape the
    reviewer emits and the runner already persists to receipts/run.json."""
    grouped = {"fix": [], "defer": [], "halt": []}
    for f in findings:
        grouped.setdefault(f.disposition, []).append(finding_to_dict(f))
    return grouped


def _build_decision(action, halt_reason, findings, state):
    return {
        "action": action,
        "halt_reason": halt_reason,
        "findings": _findings_by_disposition(findings),
        "state": state.to_dict(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_dispose.py")
    parser.add_argument(
        "--verdict", default=None,
        help="path to the reviewer's verdict JSON; required unless "
             "--execution-failure is set",
    )
    parser.add_argument(
        "--base", required=True,
        help="diff base: the task's prior commit, or run-start HEAD for the "
             "final review",
    )
    parser.add_argument(
        "--state", default=None,
        help="path to the prior ConvergenceState JSON; omitted/absent means a "
             "fresh state (attempt 1)",
    )
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--acceptance-ok", required=True, choices=["true", "false"])
    parser.add_argument("--autofix", required=True, choices=list(AUTOFIX_MODES))
    parser.add_argument(
        "--execution-failure", action="store_true",
        help="synthesize the implicit fix-retry finding for a worker crash/"
             "timeout/acceptance failure instead of reading --verdict",
    )
    parser.add_argument(
        "--execution-detail", default="execution failure",
        help="reattempt guidance surfaced as the execution-failure finding's "
             "summary",
    )
    args = parser.parse_args(argv)

    if args.execution_failure and args.verdict:
        print(
            "error: --verdict and --execution-failure are mutually exclusive",
            file=sys.stderr,
        )
        return 1
    if not args.execution_failure and not args.verdict:
        print(
            "error: --verdict is required unless --execution-failure is set",
            file=sys.stderr,
        )
        return 1

    if args.state and os.path.exists(args.state):
        try:
            with open(args.state, "r", encoding="utf-8") as f:
                state = ConvergenceState.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            print(
                "error: cannot read state file {}: {}".format(args.state, e),
                file=sys.stderr,
            )
            return 1
    else:
        state = ConvergenceState()

    acceptance_ok = args.acceptance_ok == "true"

    try:
        if args.execution_failure:
            findings = [execution_failure_finding(args.execution_detail)]
        else:
            try:
                with open(args.verdict, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(
                    "error: cannot read verdict file {}: {}".format(
                        args.verdict, e
                    ),
                    file=sys.stderr,
                )
                return 1
            verdict = _verdict_from_obj(raw)
            if verdict.kind == "findings":
                diff_text = _run_git_diff(args.base)
                verdict = classify_findings(verdict, diff_text)
            findings = verdict.findings
    except RuntimeError as e:
        print("error: {}".format(e), file=sys.stderr)
        return 1

    action, halt_reason = convergence_decision(
        findings, state, acceptance_ok, args.attempt, args.autofix
    )
    advance_state(state, findings, acceptance_ok)

    print(json.dumps(_build_decision(action, halt_reason, findings, state)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
