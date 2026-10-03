#!/usr/bin/env python3
"""Extract a self-contained worker brief from a plan (+ optional spec) for one task.

Usage:
    extract-brief.py <plan.md> <task-number> [--spec <spec.md>] [--out <dir>]

Writes ``<out>/task-<N>-brief.md`` containing, in order: the plan header
contracts (``**Goal:**`` and ``**Global Constraints:**``, the latter omitted
when absent), the full Task <N> block, and any spec sections named on the
task's ``**Spec:**`` line. Prints the absolute output path to stdout.

Exits nonzero with a message on stderr for any degraded-output condition:
unreadable plan or spec, task number not found, missing/empty/wrapped
``**Goal:**``, a ``**Spec:**`` that is wrapped across lines or carries a
parenthetical or ``;``, task declares ``**Spec:**`` but ``--spec`` was not
given, or a named spec-section heading is unmatched or ambiguous. Never emits
a silently thin brief.

``**Goal:**`` and ``**Spec:**`` are each a single line; ``**Spec:**`` is bare,
comma-separated heading names only.

Fenced code blocks (``` or ~~~) are content, never structure: no heading,
field, or task matcher fires on a fenced line, and a fenced ``## …`` never
terminates a block.

Self-contained by design (Global Constraints: no shared module between
scripts) — the task-block parser here is intentionally duplicated in
review-packet.py.
"""
import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional


HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
TASK_HEADING_RE = re.compile(r'^###\s+Task\s+(\d+):')
# Lenient matcher used ONLY to diagnose a wrong-level heading (e.g. '## Task 1:')
# after the strict match above fails — never for extraction.
ANY_LEVEL_TASK_HEADING_RE = re.compile(r'^(#{1,6})\s+Task\s+(\d+):')
# A new '**Field:**' line — distinct from bold prose like '**quickly** and…',
# which is wrapped continuation, never a field. The name may itself contain
# backticked bold (e.g. '**Note on `**Spec:**` lines below:**'), so the test
# is ':**' anywhere after the opening '**', not a clean [^*]+ name.
FIELD_LINE_RE = re.compile(r'^\*\*.*:\*\*')
FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
# A heading line terminating a field clause block: '#' through '######' at
# column 0 (spec: Plan documents, Field clause grammar) — any heading level.
# Anchored to the start of the line, so a '#' inside inline code or mid-line
# prose never matches.
CLAUSE_BLOCK_HEADING_RE = re.compile(r'^#{1,6}\s')


def fence_mask(lines):
    """Per-line booleans: True when the line is fenced code (``` or ~~~,
    delimiters included). Fenced lines are content, never structure — no
    heading, field, or task matcher may fire on them.
    """
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


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines(keepends=True)
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}")


def extract_task_block(lines, task_number):
    """Task block = '### Task <N>:' heading through the next h1–h3 heading or
    EOF (h4+ is intra-task structure; an h1 like '# Appendix' ends the block —
    otherwise the brief silently swells with everything through EOF).

    Fenced lines are skipped for both the start match and the terminator — a
    fenced example containing '## …' must not end the block early. A duplicate
    '### Task <N>:' heading raises: silently picking one is a guess.
    """
    mask = fence_mask(lines)
    starts = [
        i
        for i, line in enumerate(lines)
        if not mask[i]
        and (m := TASK_HEADING_RE.match(line))
        and int(m.group(1)) == task_number
    ]
    if not starts:
        return None
    if len(starts) > 1:
        raise RuntimeError(
            f"'### Task {task_number}:' appears more than once (lines "
            + " and ".join(str(i + 1) for i in starts)
            + ") — task numbers must be unique"
        )
    start = starts[0]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if not mask[j] and re.match(r'^#{1,3}\s', lines[j]):
            end = j
            break
    return "".join(lines[start:end]).rstrip("\n")


def diagnose_missing_task(lines, task_number, plan_path):
    """Explain why a strict '### Task N:' match failed.

    If the task heading exists at the wrong level (e.g. '## Task 1:'), name the
    real cause and point at it — the convention is exactly three '#'. Otherwise
    fall back to the honest 'not found'.
    """
    mask = fence_mask(lines)
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = ANY_LEVEL_TASK_HEADING_RE.match(line)
        if m and int(m.group(2)) == task_number:
            level = m.group(1)
            return (
                f"task {task_number} heading must be '### Task {task_number}:' "
                f"(three #), found '{level} Task {task_number}:' at line {i + 1} "
                f"in {plan_path}"
            )
    return f"task {task_number} not found in {plan_path}"


def is_wrapped_continuation(lines, idx, mask=None):
    """True if the line after ``idx`` is a wrapped continuation of a
    single-line field. A blank line, a new ``**Field:**``, a heading, or the
    start of a fenced block ends the field legitimately; anything else —
    including bold prose like ``**quickly** and…`` — is prose that wrapped
    onto a second source line and would be silently dropped by
    first-line-only parsing.
    """
    nxt = idx + 1
    if nxt >= len(lines):
        return False
    if mask is not None and mask[nxt]:
        return False
    after = lines[nxt].strip()
    if after == "" or FIELD_LINE_RE.match(after) or re.match(r'^#{1,6}\s', after):
        return False
    return True


def extract_header(lines):
    """Return (goal_line, global_constraints_block_or_None).

    ``**Goal:**`` is a required header contract: raise if it is absent, empty,
    or wrapped across two source lines — a silently truncated goal violates the
    module's "never a silently thin brief" guarantee.

    The header ends at the first task heading: a ``**Goal:**`` or
    ``**Global Constraints:**`` line inside a task block is task content,
    never a header field. A duplicate ``**Global Constraints:**`` raises —
    silently letting one win is a guess.
    """
    mask = fence_mask(lines)
    header_end = len(lines)
    for i, line in enumerate(lines):
        if not mask[i] and ANY_LEVEL_TASK_HEADING_RE.match(line):
            header_end = i
            break
    goal_line = None
    gc_block = None
    for i, line in enumerate(lines[:header_end]):
        if mask[i]:
            continue
        if goal_line is None and line.startswith("**Goal:**"):
            if is_wrapped_continuation(lines, i, mask):
                raise RuntimeError(
                    "**Goal:** must be a single line; found a wrapped "
                    f"continuation: {lines[i + 1].strip()!r}"
                )
            if not line[len("**Goal:**"):].strip():
                raise RuntimeError("**Goal:** is declared but empty")
            goal_line = line.rstrip("\n")
        if line.startswith("**Global Constraints:**"):
            if gc_block is not None:
                raise RuntimeError(
                    "**Global Constraints:** appears more than once in the "
                    "plan header"
                )
            block_lines = [line.rstrip("\n")]
            j = i + 1
            while j < len(lines) and (
                mask[j]
                or not (
                    re.match(r'^#{1,6}\s', lines[j])
                    or FIELD_LINE_RE.match(lines[j])
                )
            ):
                block_lines.append(lines[j].rstrip("\n"))
                j += 1
            while block_lines and block_lines[-1].strip() == "":
                block_lines.pop()
            gc_block = "\n".join(block_lines)
    if goal_line is None:
        raise RuntimeError("plan header is missing the required **Goal:** line")
    return goal_line, gc_block


def parse_spec_names(task_block):
    """Parse a task's ``**Spec:**`` line into a list of heading names.

    The line must be a single line of bare, comma-separated heading names.
    Raise on a wrapped continuation, an empty declaration, or a parenthetical
    or ';' — each of these otherwise mis-splits or truncates the section list
    and yields a silently thin brief.
    """
    lines = task_block.splitlines()
    mask = fence_mask(lines)
    idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if not mask[i] and ln.startswith("**Spec:**")
        ),
        None,
    )
    if idx is None:
        return []
    if is_wrapped_continuation(lines, idx, mask):
        raise RuntimeError(
            "**Spec:** must be a single line of comma-separated heading names; "
            f"found a wrapped continuation: {lines[idx + 1].strip()!r}"
        )
    content = lines[idx][len("**Spec:**"):].strip()
    if not content:
        raise RuntimeError("**Spec:** is declared but names no spec sections")
    for bad in ("(", ")", ";"):
        if bad in content:
            raise RuntimeError(
                "**Spec:** takes bare comma-separated heading names — no "
                f"parentheticals or ';' (use one --spec file); found {bad!r} "
                f"in: {content!r}"
            )
    return [name.strip() for name in content.split(",") if name.strip()]


NONE_TESTS_RE = re.compile(r'^none\b')
BULLET_RE = re.compile(r'^-\s+(.*)$')


def parse_field_clause_lines(block, field_name):
    """Parse a machine-read multi-clause field (``**Tests:**``,
    ``**Acceptance:**``, ``**Global Constraints:**``) into its list of
    clauses, per the field clause grammar (spec: Plan documents).

    Exactly two forms are legal: the marker alone on its line followed by
    ``-`` bullets — one clause per bullet, in document order, the block
    ending at the first blank line, the next ``**Field:**`` marker, or any
    heading line (``#`` through ``######`` at column 0); or the
    marker with a value on the same line, which is exactly one clause. Below
    the bullet level ``;`` and ``.`` are literal — no separator splits a
    clause. Inside a bulleted block, a line whose first non-space character
    is ``-`` starts a new clause; any other non-blank, non-fenced line
    continues the preceding clause, joined with a single space. Returns
    ``[]`` when the field is absent. Raises on a marker alone followed by
    neither a bullet nor a value — a silently empty list would be
    indistinguishable from "no field at all". Fence-masked like
    ``parse_spec_names``: a fenced marker line doesn't match, and a fenced
    line inside a bulleted block ends it.

    ``block`` may be a task block or the plan header block, since
    ``**Global Constraints:**`` lives in the header, not a task.

    Returns ``[(index, clause)]`` — each clause with the 0-based index,
    within ``block``, of its first line.
    """
    lines = block.splitlines()
    mask = fence_mask(lines)
    prefix = f"**{field_name}:**"
    idx = next(
        (i for i, ln in enumerate(lines) if not mask[i] and ln.startswith(prefix)),
        None,
    )
    if idx is None:
        return []
    content = lines[idx][len(prefix):].strip()
    if content:
        return [(idx, content)]
    clauses = []
    for j in range(idx + 1, len(lines)):
        line = lines[j]
        if (
            mask[j]
            or line.strip() == ""
            or FIELD_LINE_RE.match(line)
            or CLAUSE_BLOCK_HEADING_RE.match(line)
        ):
            break
        m = BULLET_RE.match(line)
        if m:
            clauses.append((j, m.group(1).strip()))
        elif clauses:
            first, text = clauses[-1]
            clauses[-1] = (first, (text + " " + line.strip()).strip())
        else:
            break
    if not clauses:
        raise RuntimeError(
            f"{prefix} is declared but lists no '-' bullets: {lines[idx]!r}"
        )
    return clauses


def parse_field_clauses(block, field_name):
    """The clauses of ``parse_field_clause_lines``, without their line
    indices."""
    return [clause for _, clause in parse_field_clause_lines(block, field_name)]


def parse_test_cases(task_block):
    """Parse a task's ``**Tests:**`` field into a list of test case names.

    Thin caller over ``parse_field_clauses``, preserving its own signature
    and the ``**Tests:** none — <reason>`` zero-clause form: unlike
    ``**Acceptance:**``/``**Global Constraints:**``, a single-line
    ``**Tests:**`` value is never a legal one-clause form (a test
    description is prose that may itself contain ``;``, so splitting on one
    would guess intent) — ``none — <reason>`` is the sole single-line
    exception, and any other single-line value raises.
    """
    lines = task_block.splitlines()
    mask = fence_mask(lines)
    idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if not mask[i] and ln.startswith("**Tests:**")
        ),
        None,
    )
    if idx is None:
        return []
    content = lines[idx][len("**Tests:**"):].strip()
    if content:
        if NONE_TESTS_RE.match(content):
            return []
        raise RuntimeError(
            "**Tests:** must be the marker alone on its line followed by "
            "'-' bullets, or 'none — <reason>' on one line — inline joined "
            f"cases are not legal; found: {lines[idx]!r}"
        )
    return parse_field_clauses(task_block, "Tests")


def collapse_ws(text):
    return re.sub(r'\s+', ' ', text).strip()


def strip_heading_text(text):
    """Drop a leading numbering token (e.g. '1.', '2.3') before matching and
    collapse whitespace."""
    return collapse_ws(re.sub(r'^\d+(\.\d+)*\.?\s+', '', text.strip()))


def match_heading_names(name, headings):
    """Match ``name`` against ``headings`` — ``(level, raw_text,
    stripped_text, start_index)`` tuples as extracted by
    ``find_spec_sections``. Returns the headings whose text equals the name
    when any do, else those the name prefixes. Both comparisons are
    case-insensitive with whitespace collapsed on both sides. Numbering is
    stripped only from the candidate side (already baked into
    ``stripped_text`` by the time it reaches here), never from ``name``
    itself — a query that legitimately starts with a digit must not have it
    silently eaten.

    The single predicate ``find_spec_sections`` and
    ``forge_docreview._resolve_section`` (spec: Spec review, Structural
    verification) both use, so the two cannot silently drift apart on what
    counts as a match — `tests/test_forge_docreview.py`'s
    ``SectionMatcherParityTests`` is the tripwire that would catch it if
    they ever did."""
    needle = collapse_ws(name).lower()
    exact = [h for h in headings if collapse_ws(h[2]).lower() == needle]
    if exact:
        return exact
    return [h for h in headings if collapse_ws(h[2]).lower().startswith(needle)]


def find_spec_sections(spec_lines, names):
    mask = fence_mask(spec_lines)
    headings = []  # (level, raw_text, stripped_text, start_index)
    for i, line in enumerate(spec_lines):
        if mask[i]:
            continue
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            raw_text = collapse_ws(m.group(2))
            headings.append((level, raw_text, strip_heading_text(raw_text), i))

    sections = []
    for name in names:
        matches = match_heading_names(name, headings)
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
            if mask[j]:
                continue
            hm = HEADING_RE.match(spec_lines[j])
            if hm and len(hm.group(1)) <= level:
                end = j
                break
        content = "".join(spec_lines[start:end]).rstrip("\n")
        sections.append((raw_text, content))
    return sections


@dataclass
class SpecFile:
    path: str
    spec_id: Optional[str]
    # False for a legacy plan's ``--spec`` file: bracketed entries are not
    # legal against it, whatever its ``system`` value.
    declared: bool = True


@dataclass
class ResolvedSection:
    spec: SpecFile
    heading: str
    lines: List[str]
    label: str


def parse_spec_files(plan_lines):
    """The paths in the plan header's ``**Spec files:**`` field, in order,
    one pair of wrapping backticks removed; ``[]`` when the field is absent.
    Field clause grammar, read by ``parse_field_clauses``; the header ends at
    the first task heading."""
    mask = fence_mask(plan_lines)
    header_end = len(plan_lines)
    for i, line in enumerate(plan_lines):
        if not mask[i] and ANY_LEVEL_TASK_HEADING_RE.match(line):
            header_end = i
            break
    header = "".join(plan_lines[:header_end])
    paths = []
    for clause in parse_field_clauses(header, "Spec files"):
        if len(clause) >= 2 and clause.startswith("`") and clause.endswith("`"):
            clause = clause[1:-1].strip()
        paths.append(clause)
    return paths


def _spec_id_of(path):
    """The frontmatter ``system`` value of a spec file, or ``None`` when the
    file has no frontmatter. A frontmatter block that is opened but never
    closed raises."""
    lines = read_lines(path)
    if not lines or lines[0].rstrip("\n") != "---":
        return None
    system = None
    for i in range(1, len(lines)):
        raw = lines[i].rstrip("\n")
        if raw == "---":
            return system
        m = re.match(r'^system:\s*(.*)$', raw)
        if m:
            system = m.group(1).strip() or None
    raise RuntimeError(f"{path}: unterminated frontmatter block")


def _find_repo_root(start):
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            raise RuntimeError(
                f"no repository root (a directory holding .git) found above {start}"
            )
        cur = parent


def load_spec_set(plan_path, legacy_spec_path=None, repo_root=None):
    """The plan's spec set. A plan declaring ``**Spec files:**`` reads each
    path against the repository root; a legacy plan's set is its ``--spec``
    file, or empty."""
    declared = parse_spec_files(read_lines(plan_path))
    if declared and legacy_spec_path:
        raise RuntimeError(
            f"{plan_path} declares **Spec files:** and --spec {legacy_spec_path} "
            "was also given — a plan with the header is run without --spec"
        )
    if not declared:
        if not legacy_spec_path:
            return []
        # Never opened here: a legacy spec is read only when an entry
        # resolves against it, and it needs no id (bracketed ids are illegal
        # in a legacy plan).
        return [SpecFile(legacy_spec_path, None, False)]
    if repo_root is None:
        repo_root = _find_repo_root(os.path.dirname(os.path.abspath(plan_path)))
    spec_set = []
    seen = {}
    for rel in declared:
        path = os.path.join(repo_root, rel)
        if not os.path.isfile(path):
            raise RuntimeError(f"**Spec files:** path {rel} names no file ({path})")
        spec_id = _spec_id_of(path)
        if spec_id is None:
            raise RuntimeError(
                f"**Spec files:** path {rel} has no frontmatter 'system:' value "
                "to serve as its spec id"
            )
        if spec_id in seen:
            raise RuntimeError(
                f"**Spec files:** {seen[spec_id]} and {rel} share the spec id "
                f"{spec_id!r}"
            )
        seen[spec_id] = rel
        spec_set.append(SpecFile(path, spec_id))
    return spec_set


def parse_spec_entries(task_block):
    """Each ``**Spec:**`` entry as ``(spec id or None, heading name)``. An
    entry is ``[<id>] <name>`` or a bare ``<name>``; it splits at its first
    ``]`` and whitespace inside the brackets raises."""
    entries = []
    for entry in parse_spec_names(task_block):
        if not entry.startswith("["):
            entries.append((None, entry))
            continue
        close = entry.find("]")
        if close < 0:
            raise RuntimeError(f"**Spec:** entry {entry!r} has no closing ']'")
        spec_id = entry[1:close]
        name = entry[close + 1:].strip()
        if not spec_id or re.search(r'\s', spec_id):
            raise RuntimeError(
                f"**Spec:** entry {entry!r}: the spec id inside the brackets "
                "must be non-empty with no whitespace"
            )
        if not name:
            raise RuntimeError(f"**Spec:** entry {entry!r} names no heading")
        entries.append((spec_id, name))
    return entries


def resolve_entries(entries, spec_set, task_number):
    """Resolve each entry to its section within the spec file it names."""
    multi = len(spec_set) > 1
    declared_ids = ", ".join(str(f.spec_id) for f in spec_set)
    resolved = []
    for spec_id, name in entries:
        shown = f"[{spec_id}] {name}" if spec_id is not None else name
        where = f"task {task_number} **Spec:** entry {shown!r}"
        if not spec_set:
            raise RuntimeError(f"{where}: the plan has no spec")
        if spec_id is None:
            if multi:
                raise RuntimeError(
                    f"{where}: a plan declaring more than one spec file needs "
                    f"[<spec id>] on every entry; declared ids: {declared_ids}"
                )
            spec = spec_set[0]
        else:
            if not spec_set[0].declared:
                raise RuntimeError(
                    f"{where}: a legacy plan (no **Spec files:**) takes bare "
                    "entries only"
                )
            spec = next((f for f in spec_set if f.spec_id == spec_id), None)
            if spec is None:
                raise RuntimeError(
                    f"{where}: id {spec_id!r} names no declared spec "
                    f"(matched exactly, case-sensitively); declared ids: "
                    f"{declared_ids}"
                )
        try:
            heading, content = find_spec_sections(read_lines(spec.path), [name])[0]
        except RuntimeError as e:
            raise RuntimeError(f"{where}: {e}")
        label = f"[{spec.spec_id}] {heading}" if multi else heading
        resolved.append(
            ResolvedSection(spec, heading, content.splitlines(keepends=True), label)
        )
    return resolved


def build_brief(plan_path, task_number, spec_path=None):
    lines = read_lines(plan_path)
    task_block = extract_task_block(lines, task_number)
    if task_block is None:
        raise RuntimeError(diagnose_missing_task(lines, task_number, plan_path))

    goal_line, gc_block = extract_header(lines)
    entries = parse_spec_entries(task_block)
    spec_set = load_spec_set(plan_path, spec_path)

    if entries and not spec_set:
        raise RuntimeError(
            f"task {task_number} declares **Spec:** but the plan has no spec "
            "(no **Spec files:** header and --spec was not given)"
        )

    sections = resolve_entries(entries, spec_set, task_number)

    parts = ["# Plan header\n\n"]
    if goal_line:
        parts.append(goal_line + "\n")
    if gc_block:
        parts.append(gc_block + "\n")
    parts.append("\n")
    parts.append(f"# Task {task_number}\n\n")
    parts.append(task_block + "\n")
    for section in sections:
        parts.append(f"\n\n# Spec: {section.label}\n\n")
        parts.append("".join(section.lines).rstrip("\n") + "\n")
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
