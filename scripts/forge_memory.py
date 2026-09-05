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
  opt-in. A deferral IS an open issue nobody is working on, so the record
  carries no disposition field: ``title``, ``why``, ``from``. Who noticed
  it is a GitHub label (``by:human``/``by:agent``), not a record field,
  and what KIND of work it is (feature/defect/debt/risk) is a human
  judgment this engine never makes.

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
import sys

if __name__ == "__main__":
    # This file is both an importable module and the executable entry point
    # (the pre-commit hook and the CI workflow run `forge_memory.py ...`).
    # Executing the body under the name ``__main__`` would make
    # forge_memory_store's ``import forge_memory`` load a SECOND copy of it:
    # SchemaError and Record would then exist as two distinct classes, and
    # the CLI's ``except SchemaError`` could not catch what the store
    # raises — the user got a raw traceback instead of the named error.
    # So ``__main__`` defines nothing of its own; it imports the one
    # canonical module and delegates. One ``sys.modules`` object, one class
    # identity (DECISIONS 2026-07-14, the same rule ``forge_common`` and
    # ``forge_lint`` follow), and the import cycle is broken at the entry
    # point rather than papered over inside it.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import forge_memory

    sys.exit(forge_memory.main(sys.argv[1:]))

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

import forge_memory_store as fms  # noqa: E402


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
        FieldSpec("scope", 80, True, None),
        FieldSpec("because", 300, True, None),
        FieldSpec("source", 120, True, None),
    ],
    "deferral": [
        FieldSpec("title", 80, True, None),
        FieldSpec("why", 300, True, None),
        FieldSpec("from", None, True, None),
    ],
}


class SchemaError(Exception):
    """A parse or format operation failed loud. Message names the cause —
    the offending line, field, or record — never a guess at intent.

    ``line``, when set, is the 1-based line number ``parse`` failed at,
    carried as structured data so a caller (``FileStore.scan``) can read
    it directly instead of scraping it back out of the message text —
    which would silently break the moment a message was reworded. The
    message itself keeps naming the line for humans; ``line`` is only
    where that data comes from for code."""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.line = line


@dataclass
class Record:
    # A record is its type and its fields — there is no store-assigned
    # metadata on it. A ``ref`` field once carried the issue number of a
    # record read back from GitHub, which is why it was excluded from
    # equality and repr; the GitHub read path is gone, so nothing assigns
    # a ref and there is no metadata to keep out of equality.
    type: str
    fields: dict[str, str] = field(default_factory=dict)


# Every field in every record type is a single rendered line: the heading, or
# one ``**Label:** value``. A newline or other control character in a value
# therefore produces text ``render`` writes and ``parse`` cannot read back —
# the file is bricked for every later add/list/retire/fmt, and layer 1 denies
# the direct edit needed to repair it. So this is a validate-time defect,
# reported in the same pass and the same class as a budget overrun.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
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
    """Field name -> rendered label. ``because`` -> ``Because``, ``id``
    -> ``Id``. Used identically by ``render`` and ``parse`` so the two stay
    exact inverses of each other."""
    return field_name[0].upper() + field_name[1:]


def validate(record):
    """Every defect in ``record`` against its type's SCHEMA, in one pass —
    never just the first. Checks required/non-empty, per-field character
    budgets, and each field's ``form`` (kebab-case id, ISO date)."""
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

        control = _CONTROL_RE.search(value)
        if control:
            defects.append(
                "field '{}' must be a single line: it contains the control "
                "character {!r} at position {} — {} renders as one line and "
                "parse would not read it back: {!r}".format(
                    spec.name, control.group(0), control.start(),
                    "the heading" if spec is fields[0] else "each field",
                    value,
                )
            )

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
                    ),
                    line=lineno,
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
                    ),
                    line=lineno,
                )
            if name in current.fields:
                raise SchemaError(
                    "line {}: duplicate field '{}' in this record".format(
                        lineno, name,
                    ),
                    line=lineno,
                )
            current.fields[name] = value
            continue

        raise SchemaError(
            "line {}: unparsable — expected '## <{}>' or '**Label:** value', "
            "got {!r}".format(lineno, heading_field.name, line),
            line=lineno,
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


def _read_managed_file(path):
    """Text of ``path``, or a named ``SchemaError`` if it does not exist. A
    typo'd path is bad input like any other here — never an uncaught
    FileNotFoundError."""
    if not os.path.exists(path):
        raise SchemaError(
            "{}: no such file — fmt takes paths to existing project-memory "
            "files (a 'constraint' or 'deferral' basename under "
            "docs/forge/).".format(path)
        )
    with open(path, encoding="utf-8") as f:
        return f.read()


def fmt_check(paths):
    """Every defect across ``paths`` in one pass, never just the first:
    unparsable files (one message, naming the line), then every
    ``validate`` defect on every record in every file that does parse."""
    defects = []
    for path in paths:
        record_type = _infer_type(path)
        text = _read_managed_file(path)

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
        text = _read_managed_file(path)

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


# --- CLI ---------------------------------------------------------------
#
# The composition contract: every subcommand below builds a ``Record`` from
# its own typed flags. There is no free-form body/text argument anywhere,
# and no subcommand reads an existing entry to learn its shape — ``--help``
# is the only template that exists. This is the project's primary anti-
# drift mechanism (validation is the backstop, not the mechanism): an agent
# that could pass raw record text could still imitate a drifted entry, so
# that path is simply not offered.


def _print_lines(lines, out):
    for line in lines:
        print(line, file=out)


def _record_to_dict(record):
    return dict(record.fields)


# constraints.md is a snapshot of what's true RIGHT NOW, not a log — its
# value comes from staying short enough to re-read every session, which is
# exactly what lets a rule that stopped being true get noticed. Twelve is a
# soft cap: crossing it never refuses (the whole engine composes and merges
# typed records; whether one is still true is a human read, not something
# this script can judge), it only prints a notice so a human decides whether
# to retire something.
_CONSTRAINT_SOFT_CAP = 12


def _constraint_cap_notice(count):
    if count <= _CONSTRAINT_SOFT_CAP:
        return None
    return (
        "note: constraints.md now holds {} constraints, past the soft cap "
        "of {}. This is not refused, but a file this long stops being one "
        "you re-read at every session start — consider whether one of "
        "these has stopped being true and retiring it.".format(
            count, _CONSTRAINT_SOFT_CAP,
        )
    )


def _print_records(records, json_out):
    if json_out:
        print(json.dumps([_record_to_dict(r) for r in records], indent=2))
    else:
        for r in records:
            print(render(r))


def cmd_add_constraint(args, repo_root):
    record = Record(type="constraint", fields={
        "id": args.id,
        "rule": args.rule,
        "scope": args.scope,
        "because": args.because,
        "source": args.source,
    })

    defects = validate(record)
    if defects:
        _print_lines(defects, sys.stderr)
        return 1

    try:
        store = fms.select_store(repo_root, "constraint")
        store.create(record)
        count = len(store.list("constraint"))
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1

    notice = _constraint_cap_notice(count)
    if notice is not None:
        print(notice)
    return 0


def cmd_update_constraint(args, repo_root):
    updates = {
        name: value for name, value in (
            ("rule", args.rule),
            ("because", args.because),
            ("scope", args.scope),
            ("source", args.source),
        )
        if value is not None
    }
    if not updates:
        print(
            "update-constraint: pass at least one of --rule/--because/"
            "--scope/--source — an update that changes nothing is a "
            "mistake, not a no-op.",
            file=sys.stderr,
        )
        return 1

    try:
        store = fms.select_store(repo_root, "constraint")
        records = store.list("constraint")
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1

    match_index = next(
        (i for i, r in enumerate(records) if r.fields.get("id") == args.id),
        None,
    )
    if match_index is None:
        print(
            "update-constraint: no constraint with id {!r} to update.".format(
                args.id,
            ),
            file=sys.stderr,
        )
        return 1

    # Validate the WHOLE merged record before touching the file: a budget
    # overrun (or any other defect) introduced by the update must leave
    # constraints.md byte-identical, never a half-applied edit.
    merged_fields = dict(records[match_index].fields)
    merged_fields.update(updates)
    merged = Record(type="constraint", fields=merged_fields)

    defects = validate(merged)
    if defects:
        _print_lines(defects, sys.stderr)
        return 1

    records[match_index] = merged
    # ``store`` is always a FileStore for "constraint" (select_store hard-
    # wires it), so ``_write`` — the same serializer fmt_write and create
    # use — is available here. There is no public replace-in-place method
    # on Store because create/retire are the only operations GitHubStore
    # could ever honour for a deferral; this in-place edit is specific to
    # the constraint file.
    store._write(records)
    return 0


def cmd_retire_constraint(args, repo_root):
    try:
        store = fms.select_store(repo_root, "constraint")
        store.retire(args.id)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_list_constraints(args, repo_root):
    try:
        store = fms.select_store(repo_root, "constraint")
        records = store.list("constraint")
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.scope:
        records = [
            r for r in records
            if fnmatch.fnmatch(r.fields.get("scope", ""), args.scope)
        ]
    _print_records(records, args.json)
    return 0


def _load_run_json_entry(run_path, finding_id, occurrence=None):
    """Load ``run_path`` and return ``(data, entry)`` where ``entry`` is the
    ``deferrals`` list item whose ``id`` matches ``finding_id``. Raises
    ``SchemaError`` naming ``run_path`` (missing or malformed) or naming
    ``finding_id`` (no matching entry) — never a guess. Read-only; the
    caller decides whether and what to write back.

    Finding ids are reviewer-authored per review and are never namespaced
    across a run, so two staged deferrals can legitimately share the id
    ``F1``. Returning the first match would then write the issue number
    onto the wrong entry, and permanently strand the other (it would read
    as "already filed" forever). ``occurrence`` — the 1-based ordinal among
    the entries sharing ``finding_id`` — is what disambiguates them, and it
    is always available — but NOT because the runner writes the list once
    at close-out; it rewrites ``run.json`` on every invocation, resume
    included. What actually holds the list still is two rules. First, the
    runner READS THE PRIOR ``deferrals`` LIST BACK at the start of each
    invocation and only ever appends to it (``forge-run.py``'s
    ``stage_deferrals`` over ``forge_receipts._read_deferrals``), so a
    resume never rebuilds the list from scratch, never drops an entry an
    earlier invocation staged, and never inserts ahead of one. Second,
    filing only ever ADDS an ``issue`` key to an entry it already found.
    Positions are therefore append-only, which is what makes an ordinal
    name the same entry on every re-run.
    That lets BOTH colliding deferrals be filed, each carrying its own
    issue. Without it, an ambiguous id is refused loudly, naming every
    candidate — a wrong match is never made silently."""
    if not os.path.exists(run_path):
        raise SchemaError(
            "{}: no such file — --run takes the path to an existing run's "
            "run.json.".format(run_path)
        )
    try:
        with open(run_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        # Permission-denied, a directory where a file was expected, or any
        # other reason the path exists but can't be read — same class as
        # "no such file" (the file cannot be read), so the same named,
        # non-zero-exit contract applies: never a raw traceback.
        raise SchemaError("{}: could not read run.json ({})".format(run_path, e))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SchemaError("{}: malformed run.json ({})".format(run_path, e))
    if not isinstance(data, dict):
        raise SchemaError(
            "{}: malformed run.json (expected a top-level JSON "
            "object)".format(run_path)
        )
    matches = [
        entry for entry in data.get("deferrals") or []
        if isinstance(entry, dict) and entry.get("id") == finding_id
    ]
    if not matches:
        raise SchemaError(
            "{}: no staged deferral with finding-id {!r}".format(run_path, finding_id)
        )
    if occurrence is not None:
        if not 1 <= occurrence <= len(matches):
            raise SchemaError(
                "{}: --occurrence {} is out of range for finding-id {!r} — {} "
                "staged deferral(s) carry that id (occurrences 1-{}).".format(
                    run_path, occurrence, finding_id, len(matches), len(matches),
                )
            )
        return data, matches[occurrence - 1]
    if len(matches) > 1:
        listing = "\n".join(
            "  --occurrence {}: {}{}".format(
                n,
                (entry.get("summary") or "(no summary)").strip(),
                " [already filed as issue #{}]".format(entry["issue"])
                if entry.get("issue") is not None else "",
            )
            for n, entry in enumerate(matches, start=1)
        )
        raise SchemaError(
            "{}: finding-id {!r} is ambiguous — {} staged deferrals carry it "
            "(reviewer finding ids are per-review and are not unique across a "
            "run). Filing without saying which one would attribute the issue "
            "to the wrong finding, so re-run with --occurrence N:\n{}".format(
                run_path, finding_id, len(matches), listing,
            )
        )
    return data, matches[0]


def _atomic_write_json(path, data):
    """Write ``data`` to ``path`` as JSON without ever leaving a
    truncated/partial file for a concurrent reader to see. ``run.json`` is
    written by ``forge-run.py`` and read by ``forge-monitor.py``/
    ``forge_status.py`` while a run may be in flight; this defer write-back
    is a second, independent writer, so it writes to a temp file in the
    same directory first and ``os.replace``s it onto ``path`` — atomic on
    POSIX, so a reader ever sees either the old content or the new, never
    a half-written file. Same shape as ``write_run_json`` (``indent=2``, no
    ``sort_keys``) so this write-back is indistinguishable from a runner
    write."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".forge_memory_run_json_", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def cmd_defer(args, repo_root):
    if bool(args.run) != bool(args.finding_id):
        print(
            "defer: --run and --finding-id must be given together (one "
            "without the other names no deferral entry to record into).",
            file=sys.stderr,
        )
        return 1
    if args.occurrence is not None and not args.run:
        print(
            "defer: --occurrence selects among the staged deferrals in a "
            "run.json that share one finding id — it means nothing without "
            "--run/--finding-id.",
            file=sys.stderr,
        )
        return 1

    run_data = None
    run_entry = None
    if args.run:
        try:
            run_data, run_entry = _load_run_json_entry(
                args.run, args.finding_id, args.occurrence,
            )
        except SchemaError as e:
            print(str(e), file=sys.stderr)
            return 1
        existing_issue = run_entry.get("issue")
        if existing_issue is not None:
            print(
                "defer: finding-id {!r} was already filed as issue #{} — "
                "not re-filing.".format(args.finding_id, existing_issue),
                file=sys.stderr,
            )
            return 1
        # Filing is a close-out step: run.json is a live runner artifact
        # while a run is in progress (forge-run.py/forge_receipts.py write
        # it, forge-monitor.py/forge_status.py read it), and writing
        # another process's live artifact is not something to allow by
        # convention alone. ``forge_status`` owns BOTH runner-facing
        # definitions this function needs, so it is imported once, here:
        # ``is_terminal``, the one definition of the status vocabulary's
        # terminal/non-terminal split (built on its own ``_STATE_MAP``),
        # and ``deferral_provenance``, the one definition of what a
        # runner-staged deferral's ``from`` records — shared with the
        # template ``--status`` emits, so the value that template
        # advertises and the value a filing actually records cannot drift.
        # Inside the function rather than at module level so this runner
        # concern is pulled in only on the ``--run`` path and never adds
        # to every other forge_memory invocation's (or forge_lint's)
        # startup cost.
        import forge_status
        status = run_data.get("status")
        if not forge_status.is_terminal(status):
            print(
                "defer: {} is for a run that is not in a recognized "
                "terminal state (status {!r}) — filing is a close-out "
                "step, run this once the run has finished.".format(
                    args.run, status,
                ),
                file=sys.stderr,
            )
            return 1

    if args.from_:
        provenance = args.from_
    elif run_entry is not None:
        # A --run filing is by definition a runner-staged deferral that
        # came through the close-out review gate. Defaulting it to "user"
        # — the marker the spec reserves for a deferral a human asked for
        # directly, which SKIPS that gate — would misrecord the one field
        # that says where the deferral came from. ``forge_status`` is
        # already imported above, on this same ``--run`` path.
        provenance = forge_status.deferral_provenance(
            run_data.get("plan"), run_entry, args.run,
        )
    else:
        provenance = "user"

    record = Record(type="deferral", fields={
        "title": args.title,
        "why": args.why,
        "from": provenance,
    })

    defects = validate(record)
    if defects:
        _print_lines(defects, sys.stderr)
        return 1

    try:
        store = fms.select_store(repo_root, "deferral")
        # ``by`` is not a record field: who noticed a deferral is an
        # ISSUE LABEL (``by:human``/``by:agent``), so it travels beside
        # the record rather than inside it. The file backend has no label
        # facility and drops it — that backend is an explicit opt-out from
        # issues, and inventing a schema field to mirror a GitHub
        # affordance would put the two stores' records out of sync.
        ref = store.create(record, by=args.by)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.run:
        # ``issue`` is always a string: GitHubStore returns a numeric
        # string, FileStore returns the record's title — a key that is
        # sometimes an int and sometimes an arbitrary string is a trap for
        # a caller (the close-out gate's idempotency check, and
        # ``forge_status.render_staged_deferrals``) that only needs to know
        # "is this key present", not what type it holds.
        run_entry["issue"] = str(ref)
        try:
            _atomic_write_json(args.run, run_data)
        except OSError as e:
            # The issue now exists — it must never be lost to a
            # traceback. Losing it here and blindly retrying `defer`
            # would create a SECOND issue for the same finding, exactly
            # the duplicate the idempotency check above exists to
            # prevent, reached from this side instead. So: name the
            # created issue loudly, and the fix is a manual edit, not a
            # re-run (re-running with these same args would create that
            # duplicate, since run.json still has no recorded ``issue``).
            print(
                "defer: issue #{} was created on GitHub, but recording it "
                "into {} failed ({}). The issue exists — do NOT re-run "
                "this command with the same arguments, it would file a "
                "duplicate. Instead, manually add \"issue\": \"{}\" to the "
                "deferral with finding-id {!r} in that file once the "
                "underlying write problem is fixed.".format(
                    ref, args.run, e, ref, args.finding_id,
                ),
                file=sys.stderr,
            )
            return 1

    return 0


def cmd_resolve_deferral(args, repo_root):
    try:
        store = fms.select_store(repo_root, "deferral")
        store.retire(args.ref, reason=args.reason)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_fmt(args, repo_root):
    """Two branches: explicit paths, or no PATH (every managed local file).

    There is no third, network-touching branch and no flag to opt out of
    one. ``fmt`` reads managed FILES; it never reads back the issues this
    engine filed. Re-finding forge's own records duplicated the GitHub UI,
    and the threat read-back validation guarded — a human editing an issue
    body — does not propagate, because the next record is composed from
    CLI arguments rather than read from the last one. The validation that
    matters runs at WRITE time in ``GitHubStore.create``, before anything
    reaches the network.

    That is also why ``--local-only`` is gone rather than kept as a
    synonym for the default: it existed to name the branch that skipped
    the open-issue check, and with that check gone the two branches it
    distinguished do byte-for-byte identical work. A flag whose presence
    and absence mean the same thing is a claim the code no longer backs.
    """
    if args.paths:
        # Explicit paths restrict fmt to exactly those files.
        if args.check:
            try:
                defects = fmt_check(args.paths)
            except SchemaError as e:
                print(str(e), file=sys.stderr)
                return 1
            _print_lines(defects, sys.stdout)
            return 1 if defects else 0
        try:
            fmt_write(args.paths)
        except SchemaError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    # No PATH argument: every managed local file. Never calls gh —
    # ``managed_paths`` only CONSTRUCTS a store to learn which files are
    # managed, so this is safe offline (the pre-commit hook runs it).
    try:
        managed = fms.managed_paths(repo_root)
    except fms.ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.check:
        defects = fmt_check(managed)
        _print_lines(defects, sys.stdout)
        return 1 if defects else 0
    try:
        fmt_write(managed)
    except SchemaError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


_PRE_COMMIT_MARKER = (
    "# forge-memory-guard: installed by scripts/forge_memory.py "
    "install-guards --pre-commit"
)

_PRE_COMMIT_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Do not hand-edit — re-run `install-guards --pre-commit` to update this hook.
exec python3 "{script}" fmt --check
""".format(marker=_PRE_COMMIT_MARKER, script=os.path.abspath(__file__))

_CI_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "templates",
    "forge-memory-check.yml",
)
_CI_TEMPLATE_PATH = os.path.normpath(_CI_TEMPLATE_PATH)


def _install_pre_commit_hook(repo_root):
    """Write ``.git/hooks/pre-commit`` running ``fmt --check`` on managed
    paths. Refuses to clobber a pre-commit hook it did not install itself —
    detected by ``_PRE_COMMIT_MARKER``, a line only this installer writes —
    but re-running over its own previously installed hook is idempotent."""
    git_dir = os.path.join(repo_root, ".git")
    if not os.path.isdir(git_dir):
        raise SchemaError(
            "install-guards --pre-commit: no .git directory at {!r} — run "
            "this from the root of a git repository.".format(repo_root)
        )

    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, "pre-commit")

    if os.path.exists(hook_path):
        with open(hook_path, encoding="utf-8") as f:
            existing = f.read()
        if _PRE_COMMIT_MARKER not in existing:
            raise SchemaError(
                "install-guards --pre-commit: {!r} already exists and was "
                "not installed by forge_memory.py — refusing to overwrite "
                "it. Remove or back up the existing hook, then "
                "re-run.".format(hook_path)
            )

    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(_PRE_COMMIT_HOOK_TEMPLATE)
    os.chmod(
        hook_path,
        os.stat(hook_path).st_mode | 0o111,
    )


def _repo_runs_this_engine(repo_root):
    """True when ``repo_root``'s own ``scripts/forge_memory.py`` *is* this
    module — the exact precondition the CI workflow's repo-relative command
    needs. ``samefile`` rather than a name or remote-URL check: a stale copy
    at that path is not this engine and would not behave like it."""
    candidate = os.path.join(repo_root, "scripts", "forge_memory.py")
    if not os.path.exists(candidate):
        return False
    try:
        return os.path.samefile(candidate, os.path.abspath(__file__))
    except OSError:
        return False


def _install_ci_workflow(repo_root):
    """Copy ``templates/forge-memory-check.yml`` into
    ``.github/workflows/``, creating the directory if absent. Idempotent —
    re-running writes the same template content again.

    Refuses outside the forge plugin repo. The workflow runs ``python3
    scripts/forge_memory.py fmt --check``, a repo-relative path that exists
    only where this engine is committed; installed into a downstream repo it
    could only ever fail "No such file", and a permanently red required
    check is worse than no check. The alternative — having the workflow
    check out the forge plugin repo at a pinned ref — is rejected here: no
    release tags exist to pin to, an unpinned ref is a supply-chain edge no
    installer should open on a user's behalf, and the plugin repo may not be
    readable by a downstream repo's token, so that workflow would fail too,
    just later and less legibly. Layer 2 (``--pre-commit``, which bakes in
    the absolute plugin path) is the enforcement guarantee for downstream
    repos; layer 3 is enabled on the forge plugin repo itself, exactly as
    the spec's enforcement table says."""
    if not _repo_runs_this_engine(repo_root):
        raise SchemaError(
            "install-guards --ci: the CI workflow runs `python3 "
            "scripts/forge_memory.py fmt --check`, but {!r} has no "
            "scripts/forge_memory.py of its own — the installed workflow "
            "could only ever fail 'No such file'. Refusing to install a "
            "workflow that cannot pass. Use `install-guards --pre-commit` "
            "instead: the layer-2 hook runs the same check and bakes in the "
            "absolute path to this engine ({}), so it works in any "
            "repo.".format(repo_root, os.path.abspath(__file__))
        )
    if not os.path.exists(_CI_TEMPLATE_PATH):
        raise SchemaError(
            "install-guards --ci: template not found at {!r}.".format(
                _CI_TEMPLATE_PATH,
            )
        )
    with open(_CI_TEMPLATE_PATH, encoding="utf-8") as f:
        content = f.read()

    workflows_dir = os.path.join(repo_root, ".github", "workflows")
    os.makedirs(workflows_dir, exist_ok=True)
    dest = os.path.join(workflows_dir, "forge-memory-check.yml")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)


def cmd_install_guards(args, repo_root):
    if not args.pre_commit and not args.ci:
        print(
            "install-guards: pass --pre-commit and/or --ci — installing "
            "nothing is not a valid choice. Installation is always an "
            "explicit, human-initiated act; no forge stage runs this on "
            "its own.",
            file=sys.stderr,
        )
        return 1

    ok = True
    if args.pre_commit:
        try:
            _install_pre_commit_hook(repo_root)
        except SchemaError as e:
            print(str(e), file=sys.stderr)
            ok = False
    if args.ci:
        try:
            _install_ci_workflow(repo_root)
        except SchemaError as e:
            print(str(e), file=sys.stderr)
            ok = False
    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="forge_memory.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-constraint")
    p.add_argument("--id", required=True)
    p.add_argument("--rule", required=True)
    p.add_argument("--because", required=True)
    p.add_argument("--scope", default="repo")
    p.add_argument(
        "--source", required=True,
        help="Where this rule comes from — an issue, PR, or spec path. "
             "Read-time material: a constraint written in one phase may be "
             "applied in another and need this to be understood. Required, "
             "with no default — a placeholder value would point nowhere.",
    )
    p.set_defaults(func=cmd_add_constraint)

    p = sub.add_parser("update-constraint")
    p.add_argument(
        "--id", required=True,
        help="The constraint to update. This is the record's key and is "
             "never itself a settable field — a renamed constraint is a "
             "retire plus an add, since every citation pointing at the old "
             "id would otherwise break silently.",
    )
    p.add_argument("--rule")
    p.add_argument("--because")
    p.add_argument("--scope")
    p.add_argument("--source")
    p.set_defaults(func=cmd_update_constraint)

    p = sub.add_parser("retire-constraint")
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_retire_constraint)

    p = sub.add_parser("list-constraints")
    p.add_argument("--scope")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list_constraints)

    p = sub.add_parser("defer")
    p.add_argument("--title", required=True)
    p.add_argument("--why", required=True)
    p.add_argument(
        "--by", required=True, choices=("human", "agent"),
        help="Who noticed this: applied to the filed issue as the "
             "'by:human' or 'by:agent' origin label. Required and never "
             "defaulted — the engine cannot infer it, and a guess would "
             "put a wrong label on a real issue. The KIND label "
             "(feature/defect/debt/risk) is never set here: that is a "
             "human judgment, made in the GitHub UI.",
    )
    p.add_argument("--from", dest="from_")
    p.add_argument(
        "--run", dest="run",
        help="Path to a run's run.json. Requires --finding-id. On "
             "successful issue creation, the resolved issue number is "
             "recorded into the matching deferral entry's 'issue' key — "
             "idempotent: an entry that already carries 'issue' is not "
             "re-filed.",
    )
    p.add_argument(
        "--finding-id", dest="finding_id",
        help="Id of the staged deferral in --run's run.json to record the "
             "filed issue number into. Requires --run.",
    )
    p.add_argument(
        "--occurrence", dest="occurrence", type=int,
        help="1-based ordinal among the staged deferrals sharing "
             "--finding-id. Reviewer finding ids are per-review and are not "
             "unique across a run; when two staged deferrals carry the same "
             "id, this says which one is being filed (both can be). "
             "Requires --run/--finding-id; an ambiguous id without it is "
             "refused, never guessed at.",
    )
    p.set_defaults(func=cmd_defer)

    p = sub.add_parser("resolve-deferral")
    p.add_argument("--ref", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_resolve_deferral)

    p = sub.add_parser("fmt")
    fg = p.add_mutually_exclusive_group(required=True)
    fg.add_argument("--check", action="store_true")
    fg.add_argument("--write", action="store_true")
    p.add_argument("paths", nargs="*", metavar="PATH")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("install-guards")
    p.add_argument("--pre-commit", action="store_true", dest="pre_commit")
    p.add_argument("--ci", action="store_true")
    p.set_defaults(func=cmd_install_guards)

    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args, os.getcwd())

