#!/usr/bin/env python3
"""forge_memory — schema-driven record engine for project memory (constraints,
deferrals).

Motivating defect: entries in ``DECISIONS.md`` are structurally valid (right
number of lines, right field names) but prose-expanded — a ``**Why:**`` field
that should be a sentence balloons into a paragraph. Agents author new
entries by copying the format of the entry above, so each drifted entry
raises the next one's baseline; structural validation alone never catches
this because drift lives in *word count inside a valid field*, not in shape.
The fix is per-field character budgets enforced as hard errors, and those
budgets — like the field lists themselves — live in ``SCHEMA`` **in code**,
never in a config file: a repo that can widen its own budget can disable the
mechanism entirely.

Two record types share one engine:

- ``constraint`` — always file-backed at ``docs/forge/constraints.md``, read
  at session start; must be local and free of network calls.
- ``deferral`` — GitHub issue by default; file-backed only by explicit
  opt-in. ``roadmap`` is retired as a follow-up value along with
  ``ROADMAP.md``; ``backlog`` means the issue stays open.

``render`` is the single source of record text; ``parse`` is its exact
inverse — ``render(parse(render(r))) == render(r)`` for both types. A file a
human hand-edited and that still parses is normalized back to canonical form
on the next ``fmt_write`` (structural drift has no stable state to
accumulate in); one that does not parse, or that parses but overruns a
budget, fails loud, naming the offending line or field — no guessing, no
silent fallback (DECISIONS.md 2026-07-11: scripts that parse project-memory
text accept exactly one documented form and raise at the first deviation,
naming the real cause). ``validate`` and ``fmt_check`` report **every**
defect they find in one pass, never just the first — the same rule
``forge_lint.py`` follows for plan/spec grammar.

Pure functions here; any CLI wiring (``fmt --check``/``--write`` as a
subcommand of the forge CLI) is a separate task.
"""
import os
import re
from dataclasses import dataclass, field


@dataclass
class FieldSpec:
    name: str
    budget: int | None
    required: bool
    form: str | None = None


SCHEMA: dict[str, list[FieldSpec]] = {
    # First field in each list is the record's identifying/heading field.
    "constraint": [
        FieldSpec("id", 40, True, "kebab-id"),
        FieldSpec("rule", 200, True, None),
        FieldSpec("because", 300, True, None),
        FieldSpec("scope", None, False, None),
        FieldSpec("added", None, True, "iso-date"),
        FieldSpec("source", None, True, None),
    ],
    "deferral": [
        FieldSpec("title", 80, True, None),
        FieldSpec("why", 300, True, None),
        FieldSpec("from", None, True, None),
        FieldSpec("follow-up", None, True, "follow-up"),
    ],
}


class SchemaError(Exception):
    """A parse or format operation failed loud. Message names the cause —
    the offending line, field, or record — never a guess at intent."""


@dataclass
class Record:
    type: str
    fields: dict[str, str] = field(default_factory=dict)


_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FOLLOWUP_RE = re.compile(r"^(backlog|drop|revisit-when:.+)$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_HEADING_RE = re.compile(r"^## (.+)$")
_FIELD_RE = re.compile(r"^\*\*([^:*]+):\*\* (.*)$")


def _schema_for(record_type):
    fields = SCHEMA.get(record_type)
    if fields is None:
        raise SchemaError(
            "unknown record type {!r}: expected one of {}".format(
                record_type, sorted(SCHEMA),
            )
        )
    return fields


def _label(field_name):
    """Field name -> rendered label. ``follow-up`` -> ``Follow-up``, ``id``
    -> ``Id``. Used identically by ``render`` and ``parse`` so the two stay
    exact inverses of each other."""
    return field_name[0].upper() + field_name[1:]


def validate(record):
    """Every defect in ``record`` against its type's SCHEMA, in one pass —
    never just the first. Checks required/non-empty, per-field character
    budgets, and each field's ``form`` (kebab-case id, ISO date, the
    deferral follow-up enum)."""
    fields = _schema_for(record.type)
    defects = []
    for spec in fields:
        value = record.fields.get(spec.name)
        if value is None or value == "":
            if spec.required:
                defects.append(
                    "field '{}' is required but missing or empty".format(spec.name)
                )
            continue

        if spec.budget is not None and len(value) > spec.budget:
            defects.append(
                "field '{}' exceeds its {}-char budget ({} chars): {!r}".format(
                    spec.name, spec.budget, len(value), value,
                )
            )

        if spec.form == "kebab-id":
            if not _KEBAB_RE.match(value):
                defects.append(
                    "field '{}' must be kebab-case (lowercase, digits, "
                    "single hyphens, no leading/trailing hyphen): {!r}".format(
                        spec.name, value,
                    )
                )
        elif spec.form == "follow-up":
            if not _FOLLOWUP_RE.match(value):
                defects.append(
                    "field '{}' must be 'backlog', 'drop', or "
                    "'revisit-when:<condition>' (not {!r}) — 'roadmap' is "
                    "retired with ROADMAP.md".format(spec.name, value)
                )
        elif spec.form == "iso-date":
            if not _ISO_DATE_RE.match(value):
                defects.append(
                    "field '{}' must be an ISO-8601 date (YYYY-MM-DD), "
                    "not {!r}".format(spec.name, value)
                )

    return defects


def render(record):
    """The single source of record text. Field order is SCHEMA order for
    ``record.type``; the first field renders as the ``## `` heading, every
    other field as a ``**Label:** value`` line. ``parse`` is this
    function's exact inverse."""
    fields = _schema_for(record.type)
    heading_field = fields[0]
    lines = ["## {}".format(record.fields.get(heading_field.name, ""))]
    for spec in fields[1:]:
        lines.append(
            "**{}:** {}".format(_label(spec.name), record.fields.get(spec.name, ""))
        )
    return "\n".join(lines) + "\n"


def parse(text, type):
    """Parse ``text`` into a list of ``Record`` of ``type``. Structural
    only — line syntax, known field labels, and per-file uniqueness of the
    identifying (heading) field; budgets and form are ``validate``'s job.
    Fails loud on the first unrecognized line or duplicate id, naming the
    line number — never guesses at what a malformed line meant."""
    fields = _schema_for(type)
    heading_field = fields[0]
    label_to_name = {_label(spec.name): spec.name for spec in fields}

    records = []
    seen_headings = {}
    current = None

    def flush():
        if current is not None:
            records.append(current)

    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "":
            continue

        m = _HEADING_RE.match(line)
        if m:
            flush()
            heading_value = m.group(1)
            if heading_value in seen_headings:
                raise SchemaError(
                    "line {}: duplicate {} {!r} (first seen at line {})".format(
                        lineno, heading_field.name, heading_value,
                        seen_headings[heading_value],
                    )
                )
            seen_headings[heading_value] = lineno
            current = Record(type=type, fields={heading_field.name: heading_value})
            continue

        fm = _FIELD_RE.match(line)
        if fm and current is not None:
            label, value = fm.group(1), fm.group(2)
            name = label_to_name.get(label)
            if name is None:
                raise SchemaError(
                    "line {}: unknown field '{}' for record type {!r}".format(
                        lineno, label, type,
                    )
                )
            if name in current.fields:
                raise SchemaError(
                    "line {}: duplicate field '{}' in this record".format(
                        lineno, name,
                    )
                )
            current.fields[name] = value
            continue

        raise SchemaError(
            "line {}: unparsable — expected '## <{}>' or '**Label:** value', "
            "got {!r}".format(lineno, heading_field.name, line)
        )

    flush()
    return records


def _render_all(records):
    """Canonical whole-file text for a list of records: each rendered by
    ``render``, separated by exactly one blank line."""
    return "\n".join(render(r).rstrip("\n") for r in records) + "\n" if records else ""


def _infer_type(path):
    """Record type is inferred from the filename, since ``fmt_check`` and
    ``fmt_write`` take bare paths. 'constraint' or 'deferral' must appear in
    the basename — anything else fails loud rather than guessing."""
    base = os.path.basename(path)
    if "constraint" in base:
        return "constraint"
    if "deferral" in base:
        return "deferral"
    raise SchemaError(
        "cannot infer record type from filename {!r}: expected 'constraint' "
        "or 'deferral' in the basename".format(base)
    )


def fmt_check(paths):
    """Every defect across ``paths`` in one pass, never just the first:
    unparsable files (one message, naming the line), then every
    ``validate`` defect on every record in every file that does parse."""
    defects = []
    for path in paths:
        record_type = _infer_type(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()

        try:
            records = parse(text, record_type)
        except SchemaError as e:
            defects.append("{}: {}".format(path, e))
            continue

        heading_name = SCHEMA[record_type][0].name
        for record in records:
            ident = record.fields.get(heading_name, "?")
            for msg in validate(record):
                defects.append(
                    "{}: {} {!r}: {}".format(path, record_type, ident, msg)
                )

    return defects


def fmt_write(paths):
    """``gofmt`` for project memory: parse each file, then rewrite it as the
    canonical render of its records. Fails loud instead of writing anything
    if a file doesn't parse, or if any record overruns a budget or fails
    its field form — those are content problems ``fmt_write`` must never
    paper over by reformatting around them. Idempotent: a second run on
    already-canonical content produces byte-identical output."""
    for path in paths:
        record_type = _infer_type(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()

        records = parse(text, record_type)

        heading_name = SCHEMA[record_type][0].name
        for record in records:
            defects = validate(record)
            if defects:
                ident = record.fields.get(heading_name, "?")
                raise SchemaError(
                    "{}: {} {!r} fails validation, refusing to format: {}".format(
                        path, record_type, ident, "; ".join(defects),
                    )
                )

        with open(path, "w", encoding="utf-8") as f:
            f.write(_render_all(records))
