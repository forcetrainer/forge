"""forge_plan — plan parsing and task ordering for forge-run.py.

Parses every ``### Task N:`` block into a ``Task`` (reusing extract-brief's
heading grammar), orders tasks by dependency, and parses ``--effort N=LEVEL``
overrides. All parse failures raise loudly naming the cause (DECISIONS
2026-07-11).
"""
import re

from forge_common import ALLOWED_EFFORTS, TIER_MAP, Task, eb


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
                tier_justification=tier_justification,
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
