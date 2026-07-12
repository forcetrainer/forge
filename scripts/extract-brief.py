#!/usr/bin/env python3
"""Extract a self-contained worker brief from a plan (+ optional spec) for one task.

Usage:
    extract-brief.py <plan.md> <task-number> [--spec <spec.md>] [--out <dir>]

Writes ``<out>/task-<N>-brief.md`` containing, in order: the plan header
contracts (``**Goal:**`` and ``**Global Constraints:**``, the latter omitted
when absent), the full Task <N> block, and any spec sections named on the
task's ``**Spec:**`` line. Prints the absolute output path to stdout.

Exits nonzero with a message on stderr for any degraded-output condition:
unreadable plan or spec, task number not found, task declares ``**Spec:**``
but ``--spec`` was not given, or a named spec-section heading is unmatched
or ambiguous. Never emits a silently thin brief.

Self-contained by design (Global Constraints: no shared module between
scripts) — the task-block parser here is intentionally duplicated in
review-packet.py.
"""
import argparse
import os
import re
import sys
import tempfile


HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
TASK_HEADING_RE = re.compile(r'^###\s+Task\s+(\d+):')
# Lenient matcher used ONLY to diagnose a wrong-level heading (e.g. '## Task 1:')
# after the strict match above fails — never for extraction.
ANY_LEVEL_TASK_HEADING_RE = re.compile(r'^(#{1,6})\s+Task\s+(\d+):')


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines(keepends=True)
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}")


def extract_task_block(lines, task_number):
    """Task block = '### Task <N>:' heading through next '###'/'##' heading or EOF."""
    start = None
    for i, line in enumerate(lines):
        m = TASK_HEADING_RE.match(line)
        if m and int(m.group(1)) == task_number:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^#{2,3}\s', lines[j]):
            end = j
            break
    return "".join(lines[start:end]).rstrip("\n")


def diagnose_missing_task(lines, task_number, plan_path):
    """Explain why a strict '### Task N:' match failed.

    If the task heading exists at the wrong level (e.g. '## Task 1:'), name the
    real cause and point at it — the convention is exactly three '#'. Otherwise
    fall back to the honest 'not found'.
    """
    for i, line in enumerate(lines):
        m = ANY_LEVEL_TASK_HEADING_RE.match(line)
        if m and int(m.group(2)) == task_number:
            level = m.group(1)
            return (
                f"task {task_number} heading must be '### Task {task_number}:' "
                f"(three #), found '{level} Task {task_number}:' at line {i + 1} "
                f"in {plan_path}"
            )
    return f"task {task_number} not found in {plan_path}"


def extract_header(lines):
    """Return (goal_line, global_constraints_block_or_None)."""
    goal_line = None
    gc_block = None
    for i, line in enumerate(lines):
        if goal_line is None and line.startswith("**Goal:**"):
            goal_line = line.rstrip("\n")
        if line.startswith("**Global Constraints:**"):
            block_lines = [line.rstrip("\n")]
            j = i + 1
            while j < len(lines) and not re.match(r'^(#{1,6}\s|\*\*)', lines[j]):
                block_lines.append(lines[j].rstrip("\n"))
                j += 1
            while block_lines and block_lines[-1].strip() == "":
                block_lines.pop()
            gc_block = "\n".join(block_lines)
    return goal_line, gc_block


def parse_spec_names(task_block):
    m = re.search(r'^\*\*Spec:\*\*\s*(.+)$', task_block, re.MULTILINE)
    if not m:
        return []
    return [name.strip() for name in m.group(1).split(",") if name.strip()]


def strip_heading_text(text):
    """Drop a leading numbering token (e.g. '1.', '2.3') before prefix matching."""
    return re.sub(r'^\d+(\.\d+)*\.?\s+', '', text).strip()


def find_spec_sections(spec_lines, names):
    headings = []  # (level, raw_text, stripped_text, start_index)
    for i, line in enumerate(spec_lines):
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            raw_text = m.group(2).strip()
            headings.append((level, raw_text, strip_heading_text(raw_text), i))

    sections = []
    for name in names:
        matches = [h for h in headings if h[2].lower().startswith(name.lower())]
        if not matches:
            raise RuntimeError(f'spec section not found for "{name}"')
        if len(matches) > 1:
            raise RuntimeError(
                f'spec section "{name}" is ambiguous: matches '
                + ", ".join(h[1] for h in matches)
            )
        level, raw_text, _, start = matches[0]
        end = len(spec_lines)
        for j in range(start + 1, len(spec_lines)):
            hm = HEADING_RE.match(spec_lines[j])
            if hm and len(hm.group(1)) <= level:
                end = j
                break
        content = "".join(spec_lines[start:end]).rstrip("\n")
        sections.append((raw_text, content))
    return sections


def build_brief(plan_path, task_number, spec_path):
    lines = read_lines(plan_path)
    task_block = extract_task_block(lines, task_number)
    if task_block is None:
        raise RuntimeError(diagnose_missing_task(lines, task_number, plan_path))

    goal_line, gc_block = extract_header(lines)
    spec_names = parse_spec_names(task_block)

    if spec_names and not spec_path:
        raise RuntimeError(
            f"task {task_number} declares **Spec:** but --spec was not given"
        )

    sections = []
    if spec_names:
        spec_lines = read_lines(spec_path)
        sections = find_spec_sections(spec_lines, spec_names)

    parts = ["# Plan header\n\n"]
    if goal_line:
        parts.append(goal_line + "\n")
    if gc_block:
        parts.append(gc_block + "\n")
    parts.append("\n")
    parts.append(f"# Task {task_number}\n\n")
    parts.append(task_block + "\n")
    for heading_text, content in sections:
        parts.append(f"\n\n# Spec: {heading_text}\n\n")
        parts.append(content + "\n")
    return "".join(parts)


def main(argv):
    parser = argparse.ArgumentParser(prog="extract-brief.py")
    parser.add_argument("plan")
    parser.add_argument("task_number", type=int)
    parser.add_argument("--spec")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    try:
        brief = build_brief(args.plan, args.task_number, args.spec)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    out_dir = args.out or tempfile.mkdtemp()
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"task-{args.task_number}-brief.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(brief)
    except OSError as e:
        print(f"cannot write brief to {out_dir}: {e}", file=sys.stderr)
        return 1

    print(os.path.abspath(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
