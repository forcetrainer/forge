#!/usr/bin/env python3
"""forge_checklist — the machine-checked contract checklist generator.

Derives checklist items mechanically from existing plan/spec grammar — no new
authoring burden, no new plan fields. Pure functions + a CLI, mirroring the
forge_dispose.py pattern (one implementation, two harness callers): Codex's
review-packet.py imports this module directly; the Claude orchestrator
invokes the CLI.

| id form       | source                                                        |
|----------------|---------------------------------------------------------------|
| ``spec:<id>``  | each spec section named on a ``**Spec:**`` line (task's own,  |
|                | or union across all tasks for ``--final``)                    |
| ``g<N>``       | each clause of the plan header's ``**Global Constraints:**``  |
| ``t<N>.a<M>``  | each ``;``-separated clause of task N's ``**Acceptance:**``   |
|                | line that isn't solely an inline-code command                 |
| ``t<N>``       | final review only: task N's title, as an integration item     |

Fail-loud, matching the packet contract: an unresolvable or ambiguous
``**Spec:**`` name raises (reusing extract-brief.py's existing raise), and an
empty checklist raises naming the absent source — never a silently thin
checklist.

Imported as a plain module (``import forge_common``, not importlib) so
``sys.modules`` caches one instance and ``Finding``/``Verdict`` keep a single
class identity across the runner and this module (DECISIONS 2026-07-14).
"""
import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import forge_common  # noqa: E402
import forge_plan  # noqa: E402

eb = forge_common.eb


@dataclass
class ChecklistItem:
    id: str
    source: str
    text: str


_INLINE_CODE_ONLY_RE = re.compile(r"^`[^`]*`$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+")


def _collapse_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()


def _split_global_constraints(gc_block):
    """Split the plan header's **Global Constraints:** block into clauses —
    one per sentence (period + whitespace boundary), never inside an inline
    code span such as a filename (`foo.py` has no space after its period)."""
    if not gc_block:
        return []
    body = gc_block
    prefix = "**Global Constraints:**"
    if body.startswith(prefix):
        body = body[len(prefix):]
    body = _collapse_whitespace(body)
    if not body:
        return []
    return [p.strip() for p in _SENTENCE_SPLIT_RE.split(body) if p.strip()]


def _split_acceptance_clauses(text):
    """Split a task's **Acceptance:** text on ';', dropping any clause whose
    content is solely an inline-code command — already executed
    deterministically by the acceptance runner, dead checklist weight."""
    if not text:
        return []
    kept = []
    for clause in text.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        if _INLINE_CODE_ONLY_RE.match(clause):
            continue
        kept.append(clause)
    return kept


def _spec_items(spec_lines, spec_names):
    sections = eb.find_spec_sections(spec_lines, spec_names)
    items = []
    for raw_text, content in sections:
        heading = _collapse_whitespace(eb.strip_heading_text(raw_text))
        items.append(
            ChecklistItem(
                id="spec:{}".format(heading),
                source="spec",
                text=_collapse_whitespace(content),
            )
        )
    return items


def _global_constraint_items(gc_block):
    clauses = _split_global_constraints(gc_block)
    return [
        ChecklistItem(id="g{}".format(i), source="global", text=clause)
        for i, clause in enumerate(clauses, start=1)
    ]


def _acceptance_items(task_block, task_number):
    block_lines = task_block.splitlines()
    block_mask = eb.fence_mask(block_lines)
    text = forge_plan._field_text(block_lines, block_mask, "Acceptance")
    clauses = _split_acceptance_clauses(text)
    return [
        ChecklistItem(
            id="t{}.a{}".format(task_number, i),
            source="acceptance",
            text=clause,
        )
        for i, clause in enumerate(clauses, start=1)
    ]


def _require_spec_path(task_number, spec_names, spec_path):
    if spec_names and not spec_path:
        raise RuntimeError(
            "task {} declares **Spec:** but --spec was not given".format(
                task_number
            )
        )


def build_task_checklist(plan_path, spec_path, task_number):
    """The task's own **Spec:** sections + plan **Global Constraints:**
    clauses + that task's **Acceptance:** prose clauses."""
    lines = eb.read_lines(plan_path)
    task_block = eb.extract_task_block(lines, task_number)
    if task_block is None:
        raise RuntimeError(eb.diagnose_missing_task(lines, task_number, plan_path))
    _, gc_block = eb.extract_header(lines)

    items = []
    spec_names = eb.parse_spec_names(task_block)
    _require_spec_path(task_number, spec_names, spec_path)
    if spec_names:
        spec_lines = eb.read_lines(spec_path)
        items.extend(_spec_items(spec_lines, spec_names))
    items.extend(_global_constraint_items(gc_block))
    items.extend(_acceptance_items(task_block, task_number))

    if not items:
        raise RuntimeError(
            "checklist for task {} is empty — no spec sections, global "
            "constraints, or acceptance clauses were found".format(task_number)
        )
    return items


def _list_tasks(lines):
    """(number, title) for every '### Task N:' heading, in file order.

    Deliberately independent of forge_plan.parse_plan_tasks: that parser also
    validates **Tier:**, and this module needs only task identity — pulling in
    an unrelated field's validation would make checklist generation fail on a
    tier it has no business caring about."""
    mask = eb.fence_mask(lines)
    tasks = []
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = eb.TASK_HEADING_RE.match(line)
        if not m:
            continue
        tm = re.match(r"^###\s+Task\s+\d+:\s*(.*)$", line)
        title = tm.group(1).strip() if tm else ""
        tasks.append((int(m.group(1)), title))
    if not tasks:
        raise RuntimeError("no '### Task N:' headings found in plan")
    return tasks


def build_final_checklist(plan_path, spec_path):
    """Union of every task's **Spec:** sections + global constraints + every
    task's acceptance prose clauses + one t<N> integration item per task."""
    lines = eb.read_lines(plan_path)
    _, gc_block = eb.extract_header(lines)
    tasks = _list_tasks(lines)

    items = list(_global_constraint_items(gc_block))
    seen_spec_ids = set()
    spec_lines = None

    for task_number, title in tasks:
        task_block = eb.extract_task_block(lines, task_number)
        spec_names = eb.parse_spec_names(task_block)
        _require_spec_path(task_number, spec_names, spec_path)
        if spec_names:
            if spec_lines is None:
                spec_lines = eb.read_lines(spec_path)
            for item in _spec_items(spec_lines, spec_names):
                if item.id not in seen_spec_ids:
                    seen_spec_ids.add(item.id)
                    items.append(item)
        items.extend(_acceptance_items(task_block, task_number))
        items.append(
            ChecklistItem(
                id="t{}".format(task_number),
                source="integration",
                text="Task {}: {}".format(task_number, title),
            )
        )

    if not items:
        raise RuntimeError(
            "final checklist is empty — no spec sections, global constraints, "
            "acceptance clauses, or tasks were found"
        )
    return items


def reduce_checklist(items, findings):
    """The subset of ``items`` whose id appears as some finding's
    ``contract_ref`` — used for verification packets."""
    refs = set()
    for f in findings:
        ref = f.contract_ref if hasattr(f, "contract_ref") else f.get("contract_ref")
        if ref:
            refs.add(ref)
    return [it for it in items if it.id in refs]


def render_section(items):
    """A '## Contract checklist' markdown section, one line per item as
    '- <id> — <text>'."""
    lines = ["## Contract checklist", ""]
    lines.extend("- {} — {}".format(it.id, it.text) for it in items)
    return "\n".join(lines) + "\n"


def main(argv):
    parser = argparse.ArgumentParser(prog="forge_checklist.py")
    parser.add_argument("plan")
    parser.add_argument("--spec")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", type=int)
    group.add_argument("--final", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--format", choices=("json", "md"), default="json")
    args = parser.parse_args(argv)

    try:
        if args.final:
            items = build_final_checklist(args.plan, args.spec)
        else:
            items = build_task_checklist(args.plan, args.spec, args.task)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.format == "json":
        output = json.dumps([asdict(it) for it in items], indent=2)
    else:
        output = render_section(items)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output)
        except OSError as e:
            print("cannot write to {}: {}".format(args.out, e), file=sys.stderr)
            return 1
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
