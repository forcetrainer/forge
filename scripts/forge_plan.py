"""forge_plan — plan parsing and task ordering for forge-run.py.

Parses every ``### Task N:`` block into a ``Task`` (reusing extract-brief's
heading grammar), orders tasks by dependency, and parses ``--effort N=LEVEL``
overrides. All parse failures raise loudly naming the cause (constraint:
parsers-fail-loud).
"""
import re

from forge_common import (
    ACCEPTANCE_OUTCOMES, ALLOWED_EFFORTS, TIER_MAP, AcceptanceCheck, Task, eb,
)


def _field_value(block_lines, block_mask, name):
    """First-line value of a single-line ``**Name:**`` field, or None."""
    prefix = "**{}:**".format(name)
    for i, ln in enumerate(block_lines):
        if not block_mask[i] and ln.startswith(prefix):
            return ln[len(prefix):].strip()
    return None


class AcceptanceClauseError(RuntimeError):
    """A command clause that does not parse (spec: pipeline, Acceptance clause
    grammar). Never defaulted to ``passes`` and never read as prose."""


_EXITS_RE = re.compile(r"exits ([0-9]+)")
_PRINTS_RE = re.compile(r"prints `([^`]+)`")


def parse_acceptance_clause(clause):
    """Parse one ``**Acceptance:**`` clause. Returns None for a prose clause
    (first character after trimming is not a backtick); an ``AcceptanceCheck``
    for a command clause (inline-code command, one space, an outcome). Raises ValueError naming
    the cause for a clause that begins with inline code and does not match."""
    text = clause.strip()
    if not text.startswith("`"):
        return None
    close = text.find("`", 1)
    if close == -1:
        raise ValueError("the command span is never closed")
    command = text[1:close].strip()
    if not command:
        raise ValueError("the command span is empty")
    rest = text[close + 1:]
    if rest == "":
        raise ValueError("the outcome is missing after the command")
    if not rest.startswith(" ") or rest.startswith("  "):
        raise ValueError(
            "the command span must be followed by exactly one space and an outcome"
        )
    stated = rest[1:]
    if stated == "passes":
        return AcceptanceCheck(command, "passes", None, stated)
    if stated == "prints nothing":
        return AcceptanceCheck(command, "prints-nothing", None, stated)
    m = _EXITS_RE.fullmatch(stated)
    if m:
        return AcceptanceCheck(command, "exits", int(m.group(1)), stated)
    m = _PRINTS_RE.fullmatch(stated)
    if m:
        return AcceptanceCheck(command, "prints", m.group(1), stated)
    raise ValueError(
        "{!r} is not a legal outcome (or has a trailing period, a second "
        "command span, or trailing text)".format(stated)
    )


def parse_acceptance_field(block, task_number, first_line=None):
    """Every ``**Acceptance:**`` clause of a task block as ``(line, clause,
    check)`` — ``check`` None for a prose clause. ``first_line`` is the 1-based
    plan line of the block's first line, so ``line`` is the plan line of the
    clause (None when unknown). Raises ``AcceptanceClauseError`` naming the
    task, the line, the clause, the cause and the four legal outcomes."""
    parsed = []
    for idx, clause in eb.parse_field_clause_lines(block, "Acceptance"):
        line = first_line + idx if first_line is not None else None
        try:
            check = parse_acceptance_clause(clause)
        except ValueError as e:
            raise AcceptanceClauseError(
                "task {}{}: **Acceptance:** clause {!r} does not parse — {}; "
                "a clause beginning with inline code must be \"`<command>` "
                "<outcome>\" with one outcome of: {}".format(
                    task_number,
                    ", line {}".format(line) if line is not None else "",
                    clause, e, "; ".join(ACCEPTANCE_OUTCOMES),
                )
            ) from e
        parsed.append((line, clause, check))
    return parsed


def _parse_depends(text):
    return [int(n) for n in re.findall(r"Task\s+(\d+)", text)]


def _normalize_tier_level(raw):
    """Strip a surrounding pair of backticks and one trailing '.' from a tier
    LEVEL string (never the justification). The template teaches the
    backtick-wrapped form (``**Tier:** `standard` ``) and either form may
    also trail a sentence-ending period; both are equivalent to the bare
    level. Period is stripped before backticks so ``` `standard`. ``` (period
    outside the backtick pair) normalizes correctly."""
    s = raw.strip()
    if s.endswith("."):
        s = s[:-1].strip()
    if s.startswith("`"):
        s = s[1:].strip()
    if s.endswith("`"):
        s = s[:-1].strip()
    return s


def parse_plan_tasks(plan_path):
    """Parse every ``### Task N:`` block into a Task. Raises RuntimeError naming
    the cause on a wrong-level task heading or a duplicate task number — never
    guesses (constraint: parsers-fail-loud)."""
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

        tier_raw = _field_value(block_lines, block_mask, "Tier")
        if tier_raw is None:
            raise RuntimeError("task {} is missing the **Tier:** line".format(num))
        if "—" in tier_raw:
            level_part, justification_part = tier_raw.split("—", 1)
            tier = _normalize_tier_level(level_part).lower()
            tier_justification = justification_part.strip() or None
        else:
            tier = _normalize_tier_level(tier_raw).lower()
            tier_justification = None
        if tier not in TIER_MAP:
            raise RuntimeError(
                "task {} has unknown tier {!r} — expected one of {}".format(
                    num, tier, ", ".join(sorted(TIER_MAP))
                )
            )
        if tier == "standard":
            # Floor tier takes no justification; any trailing text is ignored.
            tier_justification = None
        elif not tier_justification:
            raise RuntimeError(
                "task {} tier {!r} is missing a justification — {} tier "
                "requires one after '— <justification>' (contract "
                "requirement)".format(num, tier, tier)
            )

        depends_on = _parse_depends(_field_value(block_lines, block_mask, "Depends on") or "")
        acceptance = [
            check
            for _, _, check in parse_acceptance_field(block, num, start + 1)
            if check is not None
        ]

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
                tier_justification=tier_justification,
                depends_on=depends_on,
                acceptance_checks=acceptance,
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
