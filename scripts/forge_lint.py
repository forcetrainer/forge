#!/usr/bin/env python3
"""forge_lint — plan/spec grammar lint, run before any dispatch.

A defect in a plan or spec document can never be classified ``in-diff`` (a
task's diff contains code, not the document specifying it), so before this
module existed every such defect surfaced mid-run as a ``pre-existing x
contract-breaking`` halt — a human round-trip for what is really a syntax
error. Lint converts that into a two-second failure before anything
dispatches: Codex's ``forge-run.py`` calls it in-process after the clean-tree
precondition and before the first task; the Claude orchestrator invokes the
CLI at the same point.

Pure functions + a CLI, mirroring the ``forge_dispose.py`` / ``forge_checklist.py``
pattern (one implementation, two harness callers). Checks are validated
against **documented grammar only** — never taste, never style — by reusing
``forge_plan``, ``forge_checklist``, and ``extract-brief.py`` (``eb``) for
every parse rather than reimplementing heading, tier, or spec-name grammar.
``forge_plan.parse_plan_tasks`` itself is fail-loud (raises on the first
problem across the whole plan), which is right for a runner that must stop
immediately but wrong for a linter that must report **every** defect in one
run. To reconcile the two, tier/justification grammar is re-validated one
task at a time by feeding just that task's block back through
``forge_plan.parse_plan_tasks`` in isolation (see ``_parse_task_tier``) — so
one task's bad tier never hides a defect in another task.

Imported as a plain module (``import forge_common``, not importlib) so
``sys.modules`` caches one instance and ``Finding``/``Verdict`` keep a single
class identity across the runner and this module (DECISIONS 2026-07-14).
"""
import argparse
import os
import re
import sys
import tempfile
import types
from dataclasses import dataclass

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import forge_common  # noqa: E402
import forge_plan  # noqa: E402
import forge_checklist  # noqa: E402

eb = forge_common.eb


@dataclass
class LintDefect:
    severity: str  # "error" | "warning"
    where: str  # file/line or task number
    message: str


def _error(where, message):
    return LintDefect(severity="error", where=where, message=message)


def _warning(where, message):
    return LintDefect(severity="warning", where=where, message=message)


def _slice_block(lines, mask, start):
    """Block text from ``start`` through the next h1–h3 heading or EOF — the
    same terminator rule as ``eb.extract_task_block``, needed here because
    that helper only locates a block via a strict, unique ``### Task N:``
    match and so can't be used for a wrong-level or duplicated heading. A
    structural defect in one task's heading must never suppress checks on
    that task's own otherwise-valid fields, or on any other task."""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if not mask[j] and re.match(r"^#{1,3}\s", lines[j]):
            end = j
            break
    return "".join(lines[start:end]).rstrip("\n")


def _lint_heading_structure(lines, mask):
    """Every ``### Task N:`` heading at level 3, numbers unique. Returns
    ``(defects, task_numbers, blocks)``: ``task_numbers`` is the canonical
    set — each number with exactly one correctly-leveled heading, safe to
    hand to ``eb.extract_task_block``/``forge_checklist`` — while ``blocks``
    is every locatable task block worth field-checking, canonical entries
    plus each duplicate or wrong-level occurrence under its own ``where``
    label (line-qualified, since its task number alone is ambiguous)."""
    defects = []
    valid_starts = []  # (number, line_index) — level-3 'Task N:' matches
    wrong_level = []  # (number, line_index, level_marker)
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = eb.TASK_HEADING_RE.match(line)
        if m:
            valid_starts.append((int(m.group(1)), i))
            continue
        wl = eb.ANY_LEVEL_TASK_HEADING_RE.match(line)
        if wl and len(wl.group(1)) != 3:
            wrong_level.append((int(wl.group(2)), i, wl.group(1)))

    for num, i, lvl in wrong_level:
        defects.append(_error(
            "line {}".format(i + 1),
            "task {n} heading must be '### Task {n}:' (three #), found "
            "'{lvl} Task {n}:'".format(n=num, lvl=lvl),
        ))

    by_num = {}
    for num, idx in valid_starts:
        by_num.setdefault(num, []).append(idx)

    dups = {n: idxs for n, idxs in by_num.items() if len(idxs) > 1}
    for n in sorted(dups):
        defects.append(_error(
            "task {}".format(n),
            "duplicate task number {n} — '### Task {n}:' headings appear at "
            "lines {lines}".format(n=n, lines=", ".join(str(i + 1) for i in dups[n])),
        ))

    task_numbers = sorted(n for n in by_num if n not in dups)

    blocks = []  # (where, num, block_text) — every locatable block
    for num in task_numbers:
        idx = by_num[num][0]
        blocks.append(("task {}".format(num), num, _slice_block(lines, mask, idx)))
    for n in sorted(dups):
        for idx in dups[n]:
            blocks.append((
                "task {} (line {})".format(n, idx + 1), n,
                _slice_block(lines, mask, idx),
            ))
    for num, i, lvl in wrong_level:
        blocks.append((
            "task {} (line {}, wrong heading level)".format(num, i + 1), num,
            _slice_block(lines, mask, i),
        ))

    return defects, task_numbers, blocks


def _parse_task_tier(block, where, num):
    """Re-parse just this task's block through ``forge_plan.parse_plan_tasks``
    in isolation, so its Tier/justification grammar (present, valid after
    normalization, justification required for non-standard tiers) is fully
    reused rather than re-derived — while a defect here never blocks the
    Tier check on any other task, since each task gets its own isolated
    parse.

    The block's own heading line is normalized to canonical ``### Task N:``
    form first: a wrong-level heading is already reported once by the
    structural check, so re-parsing it verbatim here would just trip that
    same rule again (under a throwaway temp-file path) instead of actually
    checking this task's Tier grammar."""
    block_lines = block.splitlines()
    if block_lines:
        m = eb.ANY_LEVEL_TASK_HEADING_RE.match(block_lines[0])
        if m:
            block_lines[0] = "### Task {}:{}".format(num, block_lines[0][m.end():])
    normalized = "\n".join(block_lines)

    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="forge-lint-task-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(normalized + "\n")
        try:
            forge_plan.parse_plan_tasks(tmp_path)
        except RuntimeError as e:
            return [_error(where, str(e))]
        return []
    finally:
        os.remove(tmp_path)


def _lint_task_fields(blocks, spec_lines):
    """Per-task Tier, Acceptance-present, and Spec checks against every
    locatable block (canonical, duplicate, or wrong-level — see
    ``_lint_heading_structure``); also returns each canonical task's parsed
    ``Depends on`` numbers (via ``forge_plan``'s regex reader, which never
    raises) for the cross-task check in ``_lint_depends``."""
    defects = []
    depends_map = {}
    for where, num, block in blocks:
        block_lines = block.splitlines()
        block_mask = eb.fence_mask(block_lines)

        defects.extend(_parse_task_tier(block, where, num))

        if forge_plan._field_value(block_lines, block_mask, "Acceptance") is None:
            defects.append(_error(where, "missing **Acceptance:** line"))

        try:
            spec_names = eb.parse_spec_names(block)
        except RuntimeError as e:
            defects.append(_error(where, str(e)))
            spec_names = []
        if spec_names and spec_lines is not None:
            try:
                eb.find_spec_sections(spec_lines, spec_names)
            except RuntimeError as e:
                defects.append(_error(where, str(e)))

        depends_map[(where, num)] = forge_plan._parse_depends(
            forge_plan._field_value(block_lines, block_mask, "Depends on") or ""
        )
    return defects, depends_map


def _lint_depends(task_numbers, depends_map, canonical_depends):
    """``**Depends on:**`` references existing task numbers, with no cycles.

    Existence is fully decidable no matter which block is asking — whether
    Task 7 exists doesn't depend on the asking block's own heading being
    unambiguous — so it's checked over *every* located block's depends data
    (``depends_map``, keyed by the same ``where`` as the other per-block
    checks), never just the canonical ones; every missing reference is
    reported, not just the first.

    Cycle detection is different: ``forge_plan.order_tasks`` genuinely needs
    an unambiguous node set to walk, so only the canonical (unique,
    correctly-leveled) tasks participate there, via lightweight stand-ins
    carrying only the references that do exist — a missing-task defect
    never masks a genuine cycle among the rest."""
    defects = []
    valid = set(task_numbers)
    for (where, num), deps in depends_map.items():
        for d in deps:
            if d not in valid:
                defects.append(_error(
                    where, "depends on unknown task {}".format(d),
                ))

    stand_ins = [
        types.SimpleNamespace(number=n, depends_on=[d for d in canonical_depends[n] if d in valid])
        for n in task_numbers
    ]
    try:
        forge_plan.order_tasks(stand_ins)
    except RuntimeError as e:
        defects.append(_error("plan", str(e)))
    return defects


_TASK_SCOPED_MESSAGE_RE = re.compile(r"^task (\d+)\b")


def _lint_checklists(plan_path, spec_path, task_numbers, structural_clean):
    """A checklist generates for every task and for ``--final``. An empty
    checklist is a legal plan (Phase 13 spec) — a warning, never an error.

    ``--final`` re-derives the whole plan (``build_final_checklist`` calls
    ``forge_plan.parse_plan_tasks`` on the full file), so any grammar defect
    it hits — a bad tier, an unresolvable **Spec:** name, a task declaring
    **Spec:** with no ``--spec`` given — is one already surfaced by a direct,
    per-task check above under ``where="task N"``. Its own message already
    names the task ("task N ..."), so it's remapped to that same ``where``
    and left for the caller's global (where, message) dedup to collapse —
    never a second, separately-worded line for one real defect. Skipped
    entirely when the heading structure itself is broken elsewhere in the
    plan: the whole-plan reparse would just fail on that (already reported,
    differently worded) structural defect instead of telling us anything
    about checklist generation."""
    defects = []
    for num in task_numbers:
        try:
            forge_checklist.build_task_checklist(plan_path, spec_path, num)
        except RuntimeError as e:
            msg = str(e)
            where = "task {}".format(num)
            defects.append(_warning(where, msg) if "is empty" in msg else _error(where, msg))

    if task_numbers and structural_clean:
        try:
            forge_checklist.build_final_checklist(plan_path, spec_path)
        except RuntimeError as e:
            msg = str(e)
            m = _TASK_SCOPED_MESSAGE_RE.match(msg)
            where = "task {}".format(m.group(1)) if m else "--final"
            defects.append(_warning(where, msg) if "is empty" in msg else _error(where, msg))
    return defects


def _dedup(defects):
    """Collapse defects that are the same real problem surfaced twice under
    identical (where, severity, message) — e.g. an unresolvable **Spec:**
    name found by the direct Spec check and again by checklist generation.
    Every distinct real defect still appears at least once; order (first
    occurrence wins) is preserved."""
    seen = set()
    deduped = []
    for d in defects:
        key = (d.where, d.severity, d.message)
        if key not in seen:
            seen.add(key)
            deduped.append(d)
    return deduped


def lint_plan(plan_path, spec_path=None):
    """Every documented-grammar defect in ``plan_path`` (and ``spec_path``
    when given), never short-circuiting on the first — including when the
    heading structure itself is broken: a wrong-level or duplicated task
    heading is reported, but every other task (and that task's own
    otherwise-valid fields) is still checked, never silently dropped. Never
    rejects a legal plan: ``**Spec:**``, ``**Global Constraints:**``, and
    prose acceptance are all optional per the planning skill, so their
    absence is never an error — and an empty checklist is a warning, not an
    error."""
    lines = eb.read_lines(plan_path)
    mask = eb.fence_mask(lines)
    defects = []

    heading_defects, task_numbers, blocks = _lint_heading_structure(lines, mask)
    defects.extend(heading_defects)
    if not blocks:
        defects.append(_error("plan", "no '### Task N:' headings found in {}".format(plan_path)))
        return _dedup(defects)

    try:
        eb.extract_header(lines)
    except RuntimeError as e:
        defects.append(_error("plan header", str(e)))

    spec_lines = eb.read_lines(spec_path) if spec_path else None

    field_defects, depends_map = _lint_task_fields(blocks, spec_lines)
    defects.extend(field_defects)

    canonical_depends = {
        num: deps for (where, num), deps in depends_map.items()
        if where == "task {}".format(num)
    }
    defects.extend(_lint_depends(task_numbers, depends_map, canonical_depends))
    defects.extend(_lint_checklists(
        plan_path, spec_path, task_numbers, structural_clean=not heading_defects,
    ))

    return _dedup(defects)


def main(argv):
    parser = argparse.ArgumentParser(prog="forge_lint.py")
    parser.add_argument("plan")
    parser.add_argument("--spec")
    args = parser.parse_args(argv)

    try:
        defects = lint_plan(args.plan, args.spec)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    for d in defects:
        print("[{}] {}: {}".format(d.severity, d.where, d.message))

    return 1 if any(d.severity == "error" for d in defects) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
