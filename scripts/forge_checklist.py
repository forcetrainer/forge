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
| ``t<N>.t<M>``  | each test case listed on task N's ``**Tests:**`` line         |
| ``t<N>.a<M>``  | each field-clause-grammar clause of task N's ``**Acceptance:**`` |
|                | field that isn't solely an inline-code command                |
| ``t<N>``       | final review only: task N's title, as an integration item     |

The *citable* set a review's findings may name as ``contract_ref`` is
wider than the checklist it must render coverage on: ``citable_refs`` adds a
task's declared ``spec:<slug>`` sections, and ``final_citable_refs`` adds
every task's ``t<N>.t<M>`` ids to the final checklist. Both are emitted by
the CLI's ``--citable`` modifier as the JSON id array ``forge_dispose.py
--citable`` consumes.

Fail-loud, matching the packet contract: an unresolvable or ambiguous
``**Spec:**`` name raises (reusing extract-brief.py's existing raise), and an
empty checklist raises naming the absent source — never a silently thin
checklist.

Imported as a plain module (``import forge_common``, not importlib) so
``sys.modules`` caches one instance and ``Finding``/``Verdict`` keep a single
class identity across the runner and this module.
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


def _collapse_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()


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
    clauses = eb.parse_field_clauses(gc_block, "Global Constraints") if gc_block else []
    return [
        ChecklistItem(id="g{}".format(i), source="global", text=clause)
        for i, clause in enumerate(clauses, start=1)
    ]


def _test_items(task_block, task_number):
    cases = eb.parse_test_cases(task_block)
    return [
        ChecklistItem(
            id="t{}.t{}".format(task_number, i),
            source="tests",
            text=case,
        )
        for i, case in enumerate(cases, start=1)
    ]


def _acceptance_items(task_block, task_number):
    clauses = eb.parse_field_clauses(task_block, "Acceptance")
    kept = [c for c in clauses if not _INLINE_CODE_ONLY_RE.match(c)]
    return [
        ChecklistItem(
            id="t{}.a{}".format(task_number, i),
            source="acceptance",
            text=clause,
        )
        for i, clause in enumerate(kept, start=1)
    ]


def _require_spec_path(task_number, spec_names, spec_path):
    if spec_names and not spec_path:
        raise RuntimeError(
            "task {} declares **Spec:** but --spec was not given".format(
                task_number
            )
        )


def build_task_checklist(plan_path, spec_path, task_number):
    """That task's own promises: plan **Global Constraints:** clauses + the
    task's **Tests:** cases + the task's **Acceptance:** prose clauses.

    Spec sections are a final-review-only source (see ``build_final_checklist``)
    — a task is allocated only a slice of a spec section by the plan, and
    nothing here says which slice, so a per-task checklist never asks a
    reviewer to render a verdict on the whole section. The task's
    ``**Spec:**`` line is still validated (an unresolvable name still raises)
    since it continues to pull context into the worker brief and review
    packet — it just contributes no checklist item here.
    """
    lines = eb.read_lines(plan_path)
    task_block = eb.extract_task_block(lines, task_number)
    if task_block is None:
        raise RuntimeError(eb.diagnose_missing_task(lines, task_number, plan_path))
    _, gc_block = eb.extract_header(lines)

    spec_names = eb.parse_spec_names(task_block)
    _require_spec_path(task_number, spec_names, spec_path)
    if spec_names:
        # The task's **Spec:** line still pulls context into the worker
        # brief and review packet, so an unresolvable/ambiguous name is
        # still a defect to surface here — even though it contributes no
        # checklist item (spec: items are a final-review-only source).
        spec_lines = eb.read_lines(spec_path)
        eb.find_spec_sections(spec_lines, spec_names)

    items = []
    items.extend(_global_constraint_items(gc_block))
    items.extend(_test_items(task_block, task_number))
    items.extend(_acceptance_items(task_block, task_number))

    if not items:
        raise RuntimeError(
            "checklist for task {} is empty — no global constraints, test "
            "cases, or acceptance clauses were found".format(task_number)
        )
    return items


def citable_refs(plan_path, spec_path, task_number):
    """The set of ids a per-task review's findings may cite as
    ``contract_ref``: this task's own coverage item ids (``build_task_
    checklist``'s ``g<N>``/``t<N>.t<M>``/``t<N>.a<M>``) plus the
    ``spec:<slug>`` id of every section this task's ``**Spec:**`` line names
    (Contract checklist: covering and citing are different acts). Coverage
    items are what the reviewer must render a verdict on; the citable set is
    deliberately wider, so a finding against code that contradicts the spec
    can still name which section it breaks without being asked to certify
    the whole section as covered.

    Unlike ``build_task_checklist``, an empty result here is never a defect
    to raise on — the citable set is a wider superset used only to validate
    ``contract_ref`` membership, and ``validate_contract_refs`` already
    treats a falsy citable set as nothing to check.
    """
    lines = eb.read_lines(plan_path)
    task_block = eb.extract_task_block(lines, task_number)
    if task_block is None:
        raise RuntimeError(eb.diagnose_missing_task(lines, task_number, plan_path))
    _, gc_block = eb.extract_header(lines)

    spec_names = eb.parse_spec_names(task_block)
    _require_spec_path(task_number, spec_names, spec_path)

    refs = set()
    refs.update(item.id for item in _global_constraint_items(gc_block))
    refs.update(item.id for item in _test_items(task_block, task_number))
    refs.update(item.id for item in _acceptance_items(task_block, task_number))
    if spec_names:
        spec_lines = eb.read_lines(spec_path)
        refs.update(item.id for item in _spec_items(spec_lines, spec_names))
    return refs


def build_final_checklist(plan_path, spec_path):
    """Union of every task's **Spec:** sections + global constraints + every
    task's acceptance prose clauses + one t<N> integration item per task."""
    items = _final_items(plan_path, spec_path)
    if not items:
        raise RuntimeError(
            "final checklist is empty — no spec sections, global constraints, "
            "acceptance clauses, or tasks were found"
        )
    return items


def _final_items(plan_path, spec_path):
    """``build_final_checklist``'s items without its empty-checklist raise —
    shared with ``final_citable_refs``, which (like ``citable_refs``) must
    never raise on an empty set: a falsy citable set is "nothing to check
    membership against", not a defect."""
    lines = eb.read_lines(plan_path)
    _, gc_block = eb.extract_header(lines)
    tasks = forge_plan.parse_plan_tasks(plan_path)

    items = list(_global_constraint_items(gc_block))
    seen_spec_ids = set()
    spec_lines = None

    for task in tasks:
        task_number, title = task.number, task.title
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

    return items


def final_citable_refs(plan_path, spec_path):
    """The set of ids a FINAL review's findings may cite as ``contract_ref``:
    every final-checklist item id (spec sections, global constraints,
    acceptance clauses, and the ``t<N>`` integration items) **plus** every
    task's ``t<N>.t<M>`` test-case ids — the one coverage source that is
    task-only (Contract checklist: a task's checklist is its own promises).

    Widening, rather than exempting replayed findings, is deliberate. A
    per-task finding dispositioned ``seed`` is replayed verbatim into the
    final discovery packet with its ``contract_ref`` intact, so a finding
    raised against ``t3.t2`` re-cites ``t3.t2`` at the final review; with a
    checklist-derived citable set that correct finding fails membership and
    costs the run a contract error on a pointer technicality. Exempting
    seeded findings from membership instead would open the hole membership
    exists to close — "contract-breaking" claimed against an invented
    reference — since the exemption would ride on a disposition the reviewer
    can influence. Every id here is still derived from the plan's own
    grammar: nothing invented becomes citable.

    Like ``citable_refs``, an empty result is never a defect to raise on.
    """
    refs = {item.id for item in _final_items(plan_path, spec_path)}
    lines = eb.read_lines(plan_path)
    for task in forge_plan.parse_plan_tasks(plan_path):
        task_block = eb.extract_task_block(lines, task.number)
        if task_block is None:
            continue
        refs.update(item.id for item in _test_items(task_block, task.number))
    return refs


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
    # --citable is a modifier on the scope flags, not a third scope: --task N
    # and --final already say WHICH review this is, and the citable set is
    # scoped exactly the same way (a task's own, or the whole plan's). A
    # separate --citable-task/--citable-final pair would restate that choice,
    # and a bare --citable would have no scope to compute against. It emits
    # the JSON array of id strings `forge_dispose.py --citable` reads, so
    # --format md is meaningless with it and is rejected rather than ignored
    # (parsers fail loud).
    parser.add_argument("--citable", action="store_true")
    args = parser.parse_args(argv)

    if args.citable and args.format == "md":
        parser.error("--citable emits a JSON id array; --format md is not valid with it")

    try:
        if args.citable:
            refs = (
                final_citable_refs(args.plan, args.spec) if args.final
                else citable_refs(args.plan, args.spec, args.task)
            )
        elif args.final:
            items = build_final_checklist(args.plan, args.spec)
        else:
            items = build_task_checklist(args.plan, args.spec, args.task)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.citable:
        output = json.dumps(sorted(refs), indent=2)
    elif args.format == "json":
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
