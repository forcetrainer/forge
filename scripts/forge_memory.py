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
import datetime  # noqa: E402
import fnmatch  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
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
    # Store-assigned external reference (e.g. a GitHub issue number) for a
    # record read back from a store that has one; ``None`` for a record
    # not yet stored, or read from a store (like FileStore) where the
    # heading field already serves as the ref. Orthogonal to SCHEMA/fields
    # — render/parse/validate never look at it.
    ref: str | None = None


# Every field in every record type is a single rendered line: the heading, or
# one ``**Label:** value``. A newline or other control character in a value
# therefore produces text ``render`` writes and ``parse`` cannot read back —
# the file is bricked for every later add/list/retire/fmt, and layer 1 denies
# the direct edit needed to repair it. So this is a validate-time defect,
# reported in the same pass and the same class as a budget overrun.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

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


def _today_iso():
    """``added`` is machine-set to today's date; it is never a settable
    flag on ``add-constraint``."""
    return datetime.date.today().isoformat()


def _print_lines(lines, out):
    for line in lines:
        print(line, file=out)


def _record_to_dict(record):
    return dict(record.fields)


def _print_records(records, json_out):
    if json_out:
        print(json.dumps([_record_to_dict(r) for r in records], indent=2))
    else:
        for r in records:
            print(render(r))


def cmd_add_constraint(args, repo_root):
    if args.issue is not None:
        source = "issue-{}".format(args.issue)
    elif args.spec is not None:
        source = "spec:{}".format(args.spec)
    else:
        source = "user"

    record = Record(type="constraint", fields={
        "id": args.id,
        "rule": args.rule,
        "because": args.because,
        "scope": args.scope,
        "added": _today_iso(),
        "source": source,
    })

    defects = validate(record)
    if defects:
        _print_lines(defects, sys.stderr)
        return 1

    try:
        store = fms.select_store(repo_root, "constraint")
        store.create(record)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
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


def cmd_defer(args, repo_root):
    record = Record(type="deferral", fields={
        "title": args.title,
        "why": args.why,
        "from": args.from_ or "user",
        "follow-up": args.follow_up,
    })

    defects = validate(record)
    if defects:
        _print_lines(defects, sys.stderr)
        return 1

    try:
        store = fms.select_store(repo_root, "deferral")
        store.create(record)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_list_deferrals(args, repo_root):
    try:
        store = fms.select_store(repo_root, "deferral")
        records = store.list("deferral")
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    _print_records(records, args.json)
    return 0


def cmd_resolve_deferral(args, repo_root):
    try:
        store = fms.select_store(repo_root, "deferral")
        store.retire(args.ref, reason=args.reason)
    except (SchemaError, fms.StoreUnavailable, fms.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _issue_fmt_defects(store):
    """Every open deferral issue's body checked through the single public
    ``GitHubStore.list`` interface — no private-name access into
    ``forge_memory_store``, and no second ``gh issue list`` call site.
    Passing ``errors=[]`` opts ``list`` into collect-all-defects mode so
    an unparsable body never stops the rest of the issues from being
    checked; every record ``list`` does return is then run through
    ``validate`` here, the same as a file's records are in ``fmt_check``,
    so a parsable-but-budget-overrunning body is reported too, not only an
    unparsable one — each defect names its issue number via the record's
    ``.ref``."""
    parse_errors = []
    records = store.list("deferral", state="open", errors=parse_errors)
    defects = [
        "issue #{}: {}".format(ref, msg) for ref, msg in parse_errors
    ]
    for record in records:
        for msg in validate(record):
            defects.append(
                "issue #{}: deferral {!r}: {}".format(
                    record.ref, record.fields.get("title", "?"), msg,
                )
            )
    return defects


def cmd_fmt(args, repo_root):
    # Three branches, mutually exclusive and exhaustive: explicit paths,
    # --local-only, and the no-PATH CI branch. --local-only and explicit
    # paths are both scope selectors, so passing both names no coherent
    # scope — argparse cannot express "this flag conflicts with a
    # positional", so it is rejected here by name rather than resolved by
    # accident. (It used to fall through to the explicit-paths branch,
    # silently ignoring the flag.)
    if args.paths and args.local_only:
        print(
            "fmt: --local-only and explicit PATH arguments are mutually "
            "exclusive — --local-only *is* a path selection (the managed "
            "local files). Pass PATHs to check exactly those files, or "
            "--local-only with no PATH.",
            file=sys.stderr,
        )
        return 1

    if args.paths:
        # Explicit paths restrict fmt to exactly those files; gh is never
        # called in this branch, regardless of which deferral store is
        # configured.
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

    if args.local_only:
        # For a local guard (a pre-commit hook) that must never touch the
        # network: managed local files only, no GitHub issue check, no
        # `gh` invocation — unlike the no-PATH branch below, which is
        # spec'd for CI and does check open forge:deferral issues.
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

    # No PATH argument: cover every managed file, plus (only when the
    # GitHub store is selected for deferrals) every open forge:deferral
    # issue body.
    try:
        managed = fms.managed_paths(repo_root)
        deferral_store = fms.select_store(repo_root, "deferral")
    except fms.ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    all_defects = []
    if args.check:
        all_defects.extend(fmt_check(managed))
    elif managed:
        try:
            fmt_write(managed)
        except SchemaError as e:
            all_defects.append(str(e))

    if isinstance(deferral_store, fms.GitHubStore):
        try:
            all_defects.extend(_issue_fmt_defects(deferral_store))
        except fms.StoreUnavailable as e:
            print(str(e), file=sys.stderr)
            return 1

    _print_lines(all_defects, sys.stdout)
    return 1 if all_defects else 0


_PRE_COMMIT_MARKER = (
    "# forge-memory-guard: installed by scripts/forge_memory.py "
    "install-guards --pre-commit"
)

_PRE_COMMIT_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Do not hand-edit — re-run `install-guards --pre-commit` to update this hook.
exec python3 "{script}" fmt --check --local-only
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
    g = p.add_mutually_exclusive_group()
    g.add_argument("--issue", type=int)
    g.add_argument("--spec")
    p.set_defaults(func=cmd_add_constraint)

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
    p.add_argument("--follow-up", required=True, dest="follow_up")
    p.add_argument("--from", dest="from_")
    p.set_defaults(func=cmd_defer)

    p = sub.add_parser("list-deferrals")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list_deferrals)

    p = sub.add_parser("resolve-deferral")
    p.add_argument("--ref", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_resolve_deferral)

    p = sub.add_parser("fmt")
    fg = p.add_mutually_exclusive_group(required=True)
    fg.add_argument("--check", action="store_true")
    fg.add_argument("--write", action="store_true")
    p.add_argument(
        "--local-only", action="store_true", dest="local_only",
        help="With no PATH: restrict to managed local files (constraints.md, "
             "and deferrals.md if file-backed) and never select the GitHub "
             "store or invoke gh. For a local guard (e.g. a pre-commit hook) "
             "that must work offline.",
    )
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

