"""forge_planreview — plan review's verdict validation and disposition (spec:
`pipeline` "Plan review"; `execution` "Plan review verdict").

A plan review has no diff and no codebase references, so neither the spec
review verdict (``forge_docreview``) nor the diff-shaped reviewer verdict fits;
this schema is its own. ``validate_verdict`` enforces that every section in the
packet's section table is answered and every answer is well-formed — not that a
reviewer's requirement list is complete, nor that a cited promise really covers
its requirement (spec: Known limit).
"""
from dataclasses import dataclass

from forge_docreview import _is_blank


_FINDING_KINDS = ("uncovered", "contradiction", "spec-defect")
_VERDICT_VALUES = ("pass", "findings")
_FINDING_REQUIRED_FIELDS = ("id", "summary", "kind", "section", "evidence", "proposed_amendment")


@dataclass
class PlanVerdictResult:
    valid: bool
    defects: "list[str]"
    findings: "list[dict]"


@dataclass
class PlanDisposition:
    amend: "list[dict]"
    surface: "list[dict]"


def _is_task_number(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _list_field(container, key, defects):
    """``container[key]`` as a list, or ``[]`` after reporting why it is not
    one. Never raises, so one malformed field is one defect among others."""
    raw = container.get(key)
    if isinstance(raw, list):
        return raw
    defects.append("{} is not a list: {!r}".format(key, raw))
    return []


def _check_requirement(section, i, req, promise_ids, defects):
    """Validate one requirement; returns True when it is uncovered (empty
    ``covered_by`` and a null ``na``)."""
    label = "section {!r} requirement[{}]".format(section, i)
    if not isinstance(req, dict):
        defects.append("{} is not an object: {!r}".format(label, req))
        return False
    if _is_blank(req.get("requirement")):
        defects.append("{} is missing required field 'requirement'".format(label))
    else:
        label = "section {!r} requirement {!r}".format(section, req["requirement"])

    covered_by = req.get("covered_by")
    covered_ok = isinstance(covered_by, list)
    if not covered_ok:
        defects.append("{} covered_by is not a list: {!r}".format(label, covered_by))
        covered_by = []
    for pid in covered_by:
        if pid not in promise_ids:
            defects.append(
                "{} covered_by names id {!r} which is not in the promise table".format(
                    label, pid
                )
            )

    if "na" not in req:
        defects.append("{} is missing required field 'na'".format(label))
        return False
    na = req["na"]
    if na is None:
        return covered_ok and not covered_by
    if _is_blank(na):
        defects.append("{} has an empty na reason: {!r}".format(label, na))
    if covered_by:
        defects.append("{} has na set but covered_by is non-empty".format(label))
    return False


def _check_coverage(entries, section_table, promise_ids, defects):
    """Validate every coverage entry against the section table; returns the
    set of section headings holding an uncovered requirement."""
    headings = [e.heading for e in section_table]
    seen = []
    uncovered_sections = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            defects.append("coverage[{}] is not an object: {!r}".format(i, entry))
            continue
        section = entry.get("section")
        if not isinstance(section, str) or section not in headings:
            defects.append(
                "coverage entry names section {!r} which is not in the section "
                "table".format(section)
            )
            continue
        if section in seen:
            defects.append(
                "duplicate coverage entry for section {!r}".format(section)
            )
        seen.append(section)
        requirements = entry.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            defects.append(
                "coverage entry for section {!r} has empty or non-list "
                "requirements: {!r}".format(section, requirements)
            )
            continue
        for j, req in enumerate(requirements):
            if _check_requirement(section, j, req, promise_ids, defects):
                uncovered_sections.add(section)
    for heading in headings:
        if heading not in seen:
            defects.append(
                "coverage is missing section {!r} from the section table".format(heading)
            )
    return uncovered_sections


def _finding_label(finding, index):
    value = finding.get("id")
    if not _is_blank(value):
        return value
    return "findings[{}]".format(index)


def _check_finding(label, finding, section_table, spec_headings, task_numbers, defects):
    for field_name in _FINDING_REQUIRED_FIELDS:
        if _is_blank(finding.get(field_name)):
            defects.append(
                "finding {!r} is missing required field {!r}".format(label, field_name)
            )

    kind = finding.get("kind")
    if not _is_blank(kind) and kind not in _FINDING_KINDS:
        defects.append(
            "finding {!r} has unknown kind {!r}; must be one of {}".format(
                label, kind, list(_FINDING_KINDS)
            )
        )

    section = finding.get("section")
    table_entry = None
    if isinstance(section, str) and not _is_blank(section):
        table_entry = next((e for e in section_table if e.heading == section), None)
        if section not in spec_headings:
            defects.append(
                "finding {!r} names section {!r} which matches no spec heading".format(
                    label, section
                )
            )
        elif kind == "uncovered" and table_entry is None:
            defects.append(
                "finding {!r} is 'uncovered' but section {!r} is not in the "
                "section table".format(label, section)
            )

    if "task" not in finding:
        defects.append("finding {!r} is missing required field 'task'".format(label))
        return
    task = finding["task"]
    if task is None:
        return
    if not _is_task_number(task) or task not in task_numbers:
        defects.append(
            "finding {!r} names task {!r} which is not a task in the plan".format(
                label, task
            )
        )
    elif kind == "uncovered" and table_entry is not None and task not in table_entry.tasks:
        defects.append(
            "finding {!r} names task {} which does not name section {!r} "
            "(tasks: {})".format(label, task, section, list(table_entry.tasks))
        )


def validate_verdict(verdict, section_table, promise_ids, spec_headings, task_numbers):
    """Validate a plan review verdict against the Plan review verdict contract.
    Returns a ``PlanVerdictResult`` — never raises on a malformed verdict,
    because every defect must be reported in one pass; the caller that finds
    ``valid`` false names every defect and exits non-zero (constraint:
    `parsers-fail-loud`). Nothing is normalized: an id or section that differs
    from a legal value, even only in case, is a defect, and an unknown ``kind``
    is never reclassified."""
    if not isinstance(verdict, dict):
        return PlanVerdictResult(
            valid=False,
            defects=["verdict is not an object: {!r}".format(verdict)],
            findings=[],
        )
    defects = []
    promise_ids = list(promise_ids)
    spec_headings = list(spec_headings)
    task_numbers = list(task_numbers)

    verdict_value = verdict.get("verdict")
    if verdict_value not in _VERDICT_VALUES:
        defects.append(
            "'verdict' has unknown value {!r}; must be one of {}".format(
                verdict_value, list(_VERDICT_VALUES)
            )
        )

    uncovered_sections = _check_coverage(
        _list_field(verdict, "coverage", defects),
        section_table, promise_ids, defects,
    )

    raw_findings = _list_field(verdict, "findings", defects)
    findings = []
    seen_ids = []
    uncovered_finding_sections = set()
    for i, finding in enumerate(raw_findings):
        if not isinstance(finding, dict):
            defects.append("findings[{}] is not an object: {!r}".format(i, finding))
            continue
        findings.append(finding)
        label = _finding_label(finding, i)
        fid = finding.get("id")
        if isinstance(fid, str) and not _is_blank(fid):
            if fid in seen_ids:
                defects.append("duplicate finding id {!r}".format(fid))
            seen_ids.append(fid)
        _check_finding(label, finding, section_table, spec_headings, task_numbers, defects)
        if finding.get("kind") == "uncovered" and isinstance(finding.get("section"), str):
            section = finding["section"]
            uncovered_finding_sections.add(section)
            if section not in uncovered_sections:
                defects.append(
                    "finding {!r} is 'uncovered' but section {!r} has no uncovered "
                    "requirement".format(label, section)
                )

    for section in sorted(uncovered_sections - uncovered_finding_sections):
        defects.append(
            "section {!r} has an uncovered requirement but no 'uncovered' finding "
            "names it".format(section)
        )

    if verdict_value == "pass":
        if raw_findings:
            defects.append(
                "verdict is 'pass' but findings is non-empty ({} entries)".format(
                    len(raw_findings)
                )
            )
        if uncovered_sections:
            defects.append(
                "verdict is 'pass' but sections have uncovered requirements: "
                "{}".format(sorted(uncovered_sections))
            )
    elif verdict_value == "findings" and not raw_findings:
        defects.append("verdict is 'findings' but findings is empty")

    return PlanVerdictResult(valid=not defects, defects=defects, findings=findings)


def dispose(findings):
    """Disposition matrix for a valid plan verdict's findings: ``uncovered``
    and ``contradiction`` go to ``amend`` (the plan's author repairs the plan),
    ``spec-defect`` goes to ``surface`` (the spec is wrong or silent; the user
    decides, never auto-applied). Called only on a valid verdict's findings."""
    amend = []
    surface = []
    for finding in findings:
        if finding.get("kind") == "spec-defect":
            surface.append(finding)
        else:
            amend.append(finding)
    return PlanDisposition(amend=amend, surface=surface)
