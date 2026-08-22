#!/usr/bin/env python3
"""review-packet.py: extract a plan's Task N block plus its git diff into a
self-contained review packet for a reviewer.

Usage:
    review-packet.py <plan.md> <task-number> --base <git-ref> [--out <dir>]
        [--prior-findings <path>] [--checklist <path>]

Never emits a silently thin packet: any failure to locate the task block or
to run git exits nonzero with a message on stderr.

``--prior-findings`` points at a JSON file of the prior attempt's findings
(a persisted finding_to_dict() list replays straight through); when given,
the packet gains a section instructing the reviewer to label each current
finding resolved/carried/new against them. Omitted, the packet is unchanged.

``--checklist`` points at a JSON file of checklist items (forge_checklist.py's
``--format json`` output: a list of ``{"id", "source", "text"}``); when given,
the packet gains a ``## Contract checklist`` section, appended after the diff
and before any prior-findings section. Omitted, the packet is unchanged.

The diff is ``git diff <base>``: committed, staged, and unstaged *tracked*
changes — untracked files are invisible to it. Commit (or at least ``git
add``) the task's work before generating a packet.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def fence_mask(lines):
    """Per-line booleans: True when the line is fenced code (``` or ~~~,
    delimiters included). Fenced lines are content, never structure —
    duplicated from extract-brief.py by design (no shared module)."""
    mask = [False] * len(lines)
    fence_char = None
    fence_len = 0
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if fence_char is None:
            if m:
                mask[i] = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
        else:
            mask[i] = True
            if (
                m
                and m.group(1)[0] == fence_char
                and len(m.group(1)) >= fence_len
                and line.strip() == m.group(1)
            ):
                fence_char = None
    return mask


# Lenient matcher used ONLY to diagnose a wrong-level heading (e.g. '## Task 1:')
# after the strict match fails — never for extraction.
ANY_LEVEL_TASK_HEADING_RE = re.compile(r"^(#{1,6})\s+Task\s+(\d+):")


def extract_task_block(text, task_number):
    """Return the ``### Task <task_number>:`` block (through the next h1–h3
    heading or EOF; h4+ is intra-task structure), or None if the task isn't
    found.

    Fenced lines are skipped for both the start match and the terminator — a
    fenced example containing '## …' must not end the block early. A duplicate
    ``### Task <N>:`` heading raises ValueError: silently picking one is a
    guess."""
    lines = text.splitlines(keepends=True)
    mask = fence_mask(lines)
    start_pattern = re.compile(r"^###\s+Task\s+" + re.escape(str(task_number)) + r":")
    starts = [
        i for i, line in enumerate(lines) if not mask[i] and start_pattern.match(line)
    ]
    if not starts:
        return None
    if len(starts) > 1:
        raise ValueError(
            "'### Task {}:' appears more than once (lines {}) — task numbers "
            "must be unique".format(
                task_number, " and ".join(str(i + 1) for i in starts)
            )
        )
    start_idx = starts[0]

    end_pattern = re.compile(r"^#{1,3}\s")
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if not mask[i] and end_pattern.match(lines[i]):
            end_idx = i
            break
    return "".join(lines[start_idx:end_idx])


def diagnose_missing_task(text, task_number, plan_path):
    """Explain why a strict '### Task N:' match failed — duplicated from
    extract-brief.py by design (no shared module). If the heading exists at
    the wrong level (e.g. '## Task 1:'), name the real cause and point at it;
    otherwise fall back to the honest 'not found'."""
    lines = text.splitlines(keepends=True)
    mask = fence_mask(lines)
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = ANY_LEVEL_TASK_HEADING_RE.match(line)
        if m and int(m.group(2)) == task_number:
            return (
                "Task {n} heading must be '### Task {n}:' (three #), found "
                "'{level} Task {n}:' at line {line} in {path}".format(
                    n=task_number, level=m.group(1), line=i + 1, path=plan_path
                )
            )
    return "Task {} not found in {}".format(task_number, plan_path)


def build_review_kind_section(review_kind):
    """Render the '## Review kind' marker that tells the reviewer which
    verdict contract applies: 'discovery' (full contract sweep, coverage
    required) or 'verification' (prior findings + repair delta, coverage
    omitted). Placed first in the packet so it's the first thing the
    reviewer reads (Coverage on discovery only spec)."""
    return "## Review kind\n\n{}\n".format(review_kind)


def build_checklist_section(checklist):
    """Render a '## Contract checklist' markdown section, one line per item
    as '- <id> — <text>' — mirrors forge_checklist.render_section's format.
    ``checklist`` items only need attribute access to ``id``/``text``; the
    CLI (main(), below) constructs actual forge_checklist.ChecklistItem
    instances from ``--checklist``'s JSON via a deferred import, and an
    in-process caller (forge-run.py) passes forge_checklist.ChecklistItem
    instances directly — one implementation, imported by both callers
    (Contract checklist spec: "Codex's review-packet.py imports it")."""
    lines = ["## Contract checklist", ""]
    lines.extend("- {} — {}".format(it.id, it.text) for it in checklist)
    return "\n".join(lines) + "\n"


def build_prior_findings_section(prior_findings):
    """Render the prior attempt's findings (as loaded from --prior-findings)
    into a packet section instructing the reviewer to label each current
    finding against them, echoing the prior id in carried_from. The findings
    are embedded verbatim as JSON — a persisted finding_to_dict() list
    replays straight through, ids and all."""
    body = json.dumps(prior_findings, indent=2)
    if not body.endswith("\n"):
        body += "\n"
    return (
        "## Prior findings — label each current finding resolved/carried/new "
        "against these; echo the prior id in carried_from\n\n"
        "```json\n" + body + "```\n"
    )


def build_packet(task_block, base, diff_output, prior_findings=None,
                  checklist=None, review_kind=None):
    """``review_kind`` (None by default) opts into the '## Review kind'
    marker — the CLI (main(), below) never passes it, so its output stays
    byte-identical to pre-Task-6 behavior; forge_git.py's in-process callers
    (the runner's real discovery packets) pass ``review_kind="discovery"``
    explicitly (Coverage on discovery only spec)."""
    if diff_output.strip() == "":
        diff_body = "no changes vs {}\n".format(base)
    else:
        diff_body = diff_output
        if not diff_body.endswith("\n"):
            diff_body += "\n"
    # Fence must outrun any backtick run in the diff so a diffed line like
    # " ```" (context-prefixed fence, ≤3-space indent) can't close it early.
    longest_run = max(
        (len(m.group(0)) for m in re.finditer(r"`+", diff_body)), default=0
    )
    fence = "`" * max(3, longest_run + 1)
    diff_section = fence + "diff\n" + diff_body + fence + "\n"
    packet = task_block.rstrip("\n") + "\n\n" + diff_section
    if checklist is not None:
        packet += "\n" + build_checklist_section(checklist)
    if prior_findings is not None:
        packet += "\n" + build_prior_findings_section(prior_findings)
    if review_kind is not None:
        packet = build_review_kind_section(review_kind) + "\n" + packet
    return packet


def build_verification_packet(findings, delta_diff, checklist, review_kind="verification"):
    """Delta-scoped verification packet for a resumed reviewer (Delta-scoped
    verification packets spec): the outstanding findings + the repair delta +
    the reduced checklist — never the task block, the full spec, or the
    whole-plan diff, all of which the resumed reviewer already holds in
    session. ``findings`` is a list of finding dicts (a persisted
    finding_to_dict() list, same shape as ``build_prior_findings_section``'s
    input) embedded verbatim as JSON. ``delta_diff`` is ``git diff
    <pre-repair snapshot>`` — just this repair's changes. ``checklist`` is
    the reduced item list (forge_checklist.reduce_checklist output, or an
    object exposing ``id``/``text`` like ``build_checklist_section``
    expects); ``None`` (the empty-checklist skip case, or a reduction with no
    matching items to render) omits the section entirely, mirroring
    ``build_packet``'s own None-skips-section handling. Reuses
    ``build_packet``'s fence-safety: a dynamic-length fence sized past the
    longest backtick run in the delta, so a delta line containing ``` can't
    close the block early. ``review_kind`` defaults to "verification" (this
    builder's only real use) and prepends the '## Review kind' marker;
    pass None to omit it."""
    body = json.dumps(findings, indent=2)
    if not body.endswith("\n"):
        body += "\n"
    findings_section = (
        "## Outstanding findings\n\n```json\n" + body + "```\n"
    )

    if delta_diff.strip() == "":
        delta_body = "no changes\n"
    else:
        delta_body = delta_diff
        if not delta_body.endswith("\n"):
            delta_body += "\n"
    longest_run = max(
        (len(m.group(0)) for m in re.finditer(r"`+", delta_body)), default=0
    )
    fence = "`" * max(3, longest_run + 1)
    delta_section = "## Repair delta\n\n" + fence + "diff\n" + delta_body + fence + "\n"

    packet = findings_section + "\n" + delta_section
    if checklist is not None:
        packet += "\n" + build_checklist_section(checklist)
    if review_kind is not None:
        packet = build_review_kind_section(review_kind) + "\n" + packet
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(prog="review-packet.py")
    parser.add_argument("plan")
    parser.add_argument("task_number", type=int)
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--prior-findings",
        default=None,
        metavar="PATH",
        help="JSON file of the prior attempt's findings; appends a "
        "resolved/carried/new labeling section to the packet",
    )
    parser.add_argument(
        "--checklist",
        default=None,
        metavar="PATH",
        help="JSON file of checklist items (forge_checklist.py --format json "
        "output); appends a '## Contract checklist' section to the packet",
    )
    args = parser.parse_args(argv)

    prior_findings = None
    if args.prior_findings is not None:
        try:
            with open(args.prior_findings, "r", encoding="utf-8") as f:
                prior_findings = json.load(f)
        except OSError as e:
            print(
                "error: cannot read prior-findings file {}: {}".format(
                    args.prior_findings, e
                ),
                file=sys.stderr,
            )
            return 1
        except json.JSONDecodeError as e:
            print(
                "error: prior-findings file {} is not valid JSON: {}".format(
                    args.prior_findings, e
                ),
                file=sys.stderr,
            )
            return 1

    checklist = None
    if args.checklist is not None:
        try:
            with open(args.checklist, "r", encoding="utf-8") as f:
                raw_checklist = json.load(f)
        except OSError as e:
            print(
                "error: cannot read checklist file {}: {}".format(
                    args.checklist, e
                ),
                file=sys.stderr,
            )
            return 1
        except json.JSONDecodeError as e:
            print(
                "error: checklist file {} is not valid JSON: {}".format(
                    args.checklist, e
                ),
                file=sys.stderr,
            )
            return 1
        # Deferred import: main() runs only at CLI-invocation time, long
        # after forge_common has finished its sibling-load sequence (which
        # loads this module as `rp` *before* forge_common finishes defining
        # the names forge_checklist needs at import time, via forge_plan —
        # TIER_MAP/ALLOWED_EFFORTS/Task). A module-level import here would
        # hit that circular-import hazard; a deferred one never does.
        SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        import forge_checklist
        checklist = [
            forge_checklist.ChecklistItem(
                id=it["id"], source=it["source"], text=it["text"]
            )
            for it in raw_checklist
        ]

    try:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan_text = f.read()
    except OSError as e:
        print("error: cannot read plan file {}: {}".format(args.plan, e), file=sys.stderr)
        return 1

    try:
        task_block = extract_task_block(plan_text, args.task_number)
    except ValueError as e:
        print("error: {}".format(e), file=sys.stderr)
        return 1
    if task_block is None:
        print(
            "error: "
            + diagnose_missing_task(plan_text, args.task_number, args.plan),
            file=sys.stderr,
        )
        return 1

    plan_dir = os.path.dirname(os.path.abspath(args.plan)) or "."

    try:
        result = subprocess.run(
            ["git", "diff", args.base],
            cwd=plan_dir,
            capture_output=True,
            text=True,
        )
    except OSError as e:
        print("error: failed to invoke git: {}".format(e), file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(
            "error: git diff {} failed in {}:".format(args.base, plan_dir),
            file=sys.stderr,
        )
        print(result.stderr, end="", file=sys.stderr)
        return 1

    packet = build_packet(
        task_block, args.base, result.stdout, prior_findings, checklist
    )

    out_dir = args.out if args.out else tempfile.mkdtemp()
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "task-{}-review.md".format(args.task_number))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(packet)
    except OSError as e:
        print("error: cannot write output to {}: {}".format(out_dir, e), file=sys.stderr)
        return 1

    print(os.path.abspath(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
