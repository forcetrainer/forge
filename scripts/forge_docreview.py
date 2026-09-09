"""forge_docreview — spec review's reference table (mechanical half) and the
verdict validation / disposition (judgment half's contract enforcement).

Extracts every backticked span from a spec's prose, classifies each as
path-shaped, symbol-shaped, or neither, and resolves path/symbol references
against the repo. Fenced code is excluded via ``extract-brief.fence_mask`` —
a backticked span inside a fenced block is content, never a reference.

The table is a **checklist, not a rule** (spec: Spec review): an unresolved
reference is legal — a spec for a system not yet built names files that do
not exist. This module only enumerates and resolves; it never decides.

``validate_verdict``/``validate_citation``/``dispose`` enforce the document
review contract (spec: `execution` "Document review contract"), separate from
the diff-shaped reviewer verdict contract `forge_dispose.py` validates — a
document review has no diff, so this schema is its own.
"""
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass

import forge_common


REPO_ROOT = forge_common.REPO_ROOT

BACKTICK_RE = re.compile(r'`([^`\n]+)`')
# identifier, dotted name (a.b.c), or name()/a.b.c() form.
SYMBOL_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\))?$')


@dataclass
class Reference:
    ref: str
    shape: str  # "path" | "symbol" | "other"
    resolved: bool
    found_at: "str | None"


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _extension_set(tracked_files):
    """Extensions carried by some file in the repo, derived from
    ``git ls-files`` at call time — never a hardcoded literal (spec: Spec
    review)."""
    exts = set()
    for f in tracked_files:
        _, ext = os.path.splitext(f)
        if ext:
            exts.add(ext)
    return exts


def _classify(ref, extensions):
    if "/" in ref:
        return "path"
    if any(ref.endswith(ext) for ext in extensions):
        return "path"
    if SYMBOL_RE.match(ref):
        return "symbol"
    return "other"


def _resolve_path(ref, tracked_set):
    """A path resolves against git-tracked state only — never raw filesystem
    existence, which would make output depend on untracked local litter
    (build artifacts, `__pycache__`, ...) and vary across checkouts."""
    norm = ref.strip("/")
    if not norm:
        return False, None
    if norm in tracked_set:
        return True, norm
    prefix = norm + "/"
    if any(f.startswith(prefix) for f in tracked_set):
        return True, norm
    return False, None


def _resolve_symbol(ref):
    """Exit 0 is a match; exit 1 is a legitimate no-match (legal, unresolved).
    Anything else is a tool failure — `git grep` couldn't even run — and must
    raise naming the cause, never collapse into a legal negative result
    (constraint: `parsers-fail-loud`)."""
    search_text = ref[:-2] if ref.endswith("()") else ref
    result = subprocess.run(
        ["git", "grep", "-n", "-F", "--", search_text],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode == 0:
        path, line, _ = result.stdout.splitlines()[0].split(":", 2)
        return True, f"{path}:{line}"
    if result.returncode == 1:
        return False, None
    raise RuntimeError(
        f"git grep failed while resolving `{ref}` (exit {result.returncode}): "
        f"{result.stderr.strip()}"
    )


def extract_references(spec_text):
    """Ordered, de-duplicated list of ``Reference`` for every backticked span
    in ``spec_text`` outside fenced regions. Includes ``"other"``-shaped
    spans (flags, enum values, constraint ids) unresolved — callers wanting
    only the checklist use ``reference_table``."""
    lines = spec_text.splitlines()
    mask = forge_common.eb.fence_mask(lines)

    seen = []
    seen_set = set()
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        for m in BACKTICK_RE.finditer(line):
            ref = m.group(1)
            if ref not in seen_set:
                seen_set.add(ref)
                seen.append(ref)

    tracked_files = _tracked_files()
    tracked_set = set(tracked_files)
    extensions = _extension_set(tracked_files)

    references = []
    for ref in seen:
        shape = _classify(ref, extensions)
        if shape == "path":
            resolved, found_at = _resolve_path(ref, tracked_set)
        elif shape == "symbol":
            resolved, found_at = _resolve_symbol(ref)
        else:
            resolved, found_at = False, None
        references.append(
            Reference(ref=ref, shape=shape, resolved=resolved, found_at=found_at)
        )
    return references


def reference_table(spec_text):
    """The checklist: only ``path`` and ``symbol`` entries — ``other`` (flags,
    enum values, constraint ids) is noise and is dropped."""
    return [r for r in extract_references(spec_text) if r.shape in ("path", "symbol")]


# --- verdict validation and disposition (Document review contract) ----------


_REFERENCE_DISPOSITIONS = {"intended-new", "wrong", "unverifiable"}
_FINDING_KINDS = {"groundedness", "sufficiency", "contradiction"}

# The declared schema for required-field checking (Document review contract).
# One uniform rule reads this table; a field nobody enumerated is not a hole
# by construction — adding a required field here is the entire change needed
# to enforce it, no new checking code. ``label_field`` names the entry's own
# field to identify it by in a defect message (falling back to a positional
# index when that field is itself blank) — never the field under test, so a
# defect always names *both* the field and the entry it belongs to.
_REQUIRED_FIELDS_SCHEMA = {
    "references": {
        "fields": ("ref", "disposition", "evidence"),
        "label_field": "ref",
    },
    "dependencies_read": {
        "fields": ("symbol", "file", "behavior"),
        "label_field": "symbol",
    },
    "findings": {
        "fields": ("id", "summary", "kind", "section", "evidence", "proposed_amendment"),
        "label_field": "id",
    },
}


_BLANK_CATEGORIES = frozenset({
    "Cf",  # format: zero-width space U+200B, BOM U+FEFF, word joiner U+2060...
    "Cc",  # control: NUL U+0000, U+0001... (tab/newline are also Cc, but
           # `ch.isspace()` already strips those — they only reach this
           # category test when NOT whitespace, e.g. non-whitespace C0/C1
           # controls, so a legitimate embedded newline/tab is unaffected).
})


def _is_blank(value):
    """True when ``value`` is not a string, or is nothing but whitespace and
    invisible/meaningless Unicode content (``_BLANK_CATEGORIES``) once
    stripped. The one emptiness test every required-field check uses — fixed
    once, here, so a field satisfied by invisible content is caught in every
    entry type, not special-cased per field or per call site. ``str.strip()``
    alone is not enough: it strips whitespace (category Zs/Zl/Zp and some
    Cc) but not the rest of Cf/Cc, so e.g. a ZWSP-only or NUL-only string
    survives it.

    Deliberately excludes: Co (private-use) and Cn (unassigned) — both
    occupy real character cells and are visually present (a private-use
    glyph or the ``.notdef``/"tofu" box a renderer shows for an unassigned
    codepoint), so treating them as blank would hide a rendered but
    unintelligible field rather than catch an invisible one; and Cs
    (surrogate halves), which cannot occur in a well-formed Python ``str``
    on its own, so there is nothing there to guard against.

    KNOWN LIMIT, deliberate. Unicode has no category meaning "renders as
    nothing", so a category test cannot catch every visually blank value.
    U+2800 BRAILLE PATTERN BLANK is category So and U+3164/U+115F/U+1160
    HANGUL FILLER are Lo, yet all render empty; a lone combining mark (Mn)
    does too. These still satisfy a required field. Not closed on purpose:
    the threat this test exists for is a reviewer under-filling a form —
    an omitted key, an empty string, stray whitespace, a pasted zero-width
    space — and those are all caught. Deliberate evasion is not reachable
    here at all, since ``evidence: "checked it"`` is visible, well-formed
    and equally empty of meaning. What defeats *that* is verifying the
    claims a field makes about the repository, not inspecting its
    characters. See the structural checks on ``dependencies_read`` and
    ``findings[].section``."""
    if not isinstance(value, str):
        return True
    visible = "".join(
        ch for ch in value
        if not ch.isspace() and unicodedata.category(ch) not in _BLANK_CATEGORIES
    )
    return not visible


def _entry_label(entry, entry_type, label_field, index):
    """The entry's own ``label_field`` value when it is meaningfully
    non-empty, else a positional fallback — so a defect can still name the
    entry even when the label field itself is the missing one."""
    value = entry.get(label_field)
    if not _is_blank(value):
        return value
    return "{}[{}]".format(entry_type, index)


def _collect_entries(verdict, entry_type, defects):
    """(index, entry) pairs for every well-formed dict entry in
    ``verdict[entry_type]``. A non-list value for the array itself, or a
    non-dict element within it, is reported as one defect naming the field
    (or the field and position) and skipped — never raised, so a malformed
    entry is one defect among others and validation continues through the
    rest (the Interface's ``validate_verdict`` returns rather than throws so
    every defect can be reported in one pass; ``parsers-fail-loud`` is
    honored at the CLI boundary that exits non-zero on ``valid=False``, not
    by throwing from the middle of this pass)."""
    raw = verdict.get(entry_type)
    if raw is None:
        return []
    if not isinstance(raw, list):
        defects.append(
            "{} is not a list: {!r}".format(entry_type, raw)
        )
        return []
    entries = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            defects.append(
                "{}[{}] is not an object: {!r}".format(entry_type, i, entry)
            )
            continue
        entries.append((i, entry))
    return entries


def _validate_required_fields(entries_by_type, defects):
    """The one uniform required-field rule, table-driven from
    ``_REQUIRED_FIELDS_SCHEMA``: for every declared entry type, every
    well-formed entry (``entries_by_type`` — already filtered by
    ``_collect_entries``), every declared field — present and meaningfully
    non-empty (``_is_blank``), or it's a defect naming both the field and
    the entry. Appends to ``defects`` in place."""
    for entry_type, spec in _REQUIRED_FIELDS_SCHEMA.items():
        for i, entry in entries_by_type[entry_type]:
            label = _entry_label(entry, entry_type, spec["label_field"], i)
            for field_name in spec["fields"]:
                value = entry.get(field_name)
                if _is_blank(value):
                    defects.append(
                        "{} entry {!r} is missing required field {!r}".format(
                            entry_type, label, field_name
                        )
                    )


@dataclass
class VerdictResult:
    valid: bool
    defects: "list[str]"
    findings: "list[dict]"


@dataclass
class Disposition:
    amend: "list[dict]"
    surface: "list[dict]"


def validate_verdict(verdict, unresolved_refs, repo_root):
    """Validate a document review verdict against the Document review
    contract (spec: `execution` "Document review contract"). Returns a
    ``VerdictResult`` — never raises on a malformed verdict, because every
    defect must be reported in one pass; a raise would surface only the
    first (constraint: `parsers-fail-loud` is honored at the CLI boundary —
    the caller that finds this function's ``valid`` false is the one that
    exits non-zero naming every defect, not this function).

    ``unresolved_refs`` is the set of ref strings the reviewer owes a
    disposition on (the packet's reference table, filtered to unresolved).
    ``repo_root`` is accepted for interface symmetry with ``dispose`` and
    ``validate_citation``; this function's checks are schema-only and never
    touch the filesystem — citation resolution is `dispose`'s job."""
    del repo_root  # schema-only checks; citation resolution happens in dispose()
    defects = []

    # Parse each declared entry array once, skipping (and reporting, never
    # raising) any malformed shape — a non-list array or a non-dict element
    # — so every check below sees only well-formed entries and a malformed
    # one still yields a defect among others rather than aborting the pass.
    entries_by_type = {
        entry_type: _collect_entries(verdict, entry_type, defects)
        for entry_type in _REQUIRED_FIELDS_SCHEMA
    }

    # The one uniform required-field rule (Document review contract), driven
    # by the declared schema — covers references[].{ref,disposition,evidence},
    # dependencies_read[].{symbol,file,behavior}, and findings[].{id,summary,
    # kind,section,evidence,proposed_amendment} in one pass. Everything below
    # is deliberately NOT a presence check: enumeration membership, coverage
    # of the unresolved-ref set, and the replaced_system boolean/guarantees
    # relationship.
    _validate_required_fields(entries_by_type, defects)

    unresolved_set = set(unresolved_refs)
    covered = set()
    for _, entry in entries_by_type["references"]:
        ref = entry.get("ref")
        covered.add(ref)
        disposition = entry.get("disposition")
        if disposition not in _REFERENCE_DISPOSITIONS:
            defects.append(
                "references entry for {!r} has unknown disposition {!r}".format(
                    ref, disposition
                )
            )
    missing = unresolved_set - covered
    for ref in sorted(missing):
        defects.append(
            "missing references entry for unresolved ref {!r}".format(ref)
        )
    extra = covered - unresolved_set
    for ref in sorted(r for r in extra if r is not None):
        defects.append(
            "references entry names ref {!r} outside the unresolved set".format(ref)
        )

    dependencies_read = verdict.get("dependencies_read") or []
    dependencies_waiver = verdict.get("dependencies_waiver")
    if not dependencies_read and dependencies_waiver is None:
        defects.append(
            "dependencies_read is empty and dependencies_waiver is null"
        )

    replaced_system = verdict.get("replaced_system") or {}
    applies = replaced_system.get("applies")
    guarantees = replaced_system.get("guarantees") or []
    if not isinstance(applies, bool):
        # Covers a missing replaced_system, one present but lacking `applies`,
        # and a non-boolean `applies` — all three leave `applies` failing the
        # isinstance check, so a required field cannot go unasked simply by
        # omitting it (execution.md: "guidance alone is ignorable, a required
        # field is not").
        defects.append(
            "replaced_system.applies is missing or not a boolean: {!r}".format(
                applies
            )
        )
    elif applies is False and guarantees:
        defects.append(
            "replaced_system.applies is false but guarantees is non-empty"
        )
    elif applies is True and not guarantees:
        defects.append(
            "replaced_system.applies is true but guarantees is empty"
        )

    for i, finding in entries_by_type["findings"]:
        label = _entry_label(finding, "findings", "id", i)
        kind = finding.get("kind")
        if kind is not None and kind not in _FINDING_KINDS:
            defects.append(
                "finding {!r} has unknown kind {!r}".format(label, kind)
            )

    findings = [entry for _, entry in entries_by_type["findings"]]
    return VerdictResult(valid=not defects, defects=defects, findings=findings)


def validate_citation(citation, repo_root):
    """True when ``citation`` is a ``<file>:<line>`` string naming an
    existing file under ``repo_root`` and a line number within it
    (1-indexed, inclusive of the last line). False for any malformed form, a
    missing file, or a line past end of file — all legal negative results,
    mirroring `_resolve_path`'s existence-check contract. A file that exists
    but cannot be read raises (constraint: `parsers-fail-loud`) — that is a
    tool failure, not a legal negative."""
    if not citation or ":" not in citation:
        return False
    file_part, _, line_part = citation.rpartition(":")
    if not file_part or not line_part.isdigit():
        return False
    line_num = int(line_part)
    if line_num < 1:
        return False
    full_path = os.path.join(repo_root, file_part)
    if not os.path.isfile(full_path):
        return False
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
    except OSError as e:
        raise RuntimeError(
            "citation names an existing file that could not be read: "
            "{} ({})".format(full_path, e)
        )
    return line_num <= line_count


def dispose(findings, repo_root):
    """Disposition matrix for a document review's findings (spec: `execution`
    "Document review contract"). Returns ``Disposition(amend, surface)``.

    A ``groundedness`` finding with a citation that resolves (`validate_
    citation`) goes to ``amend`` — the author amends the spec, scoped
    re-review. A ``groundedness`` finding whose citation is absent or
    unresolvable is **rewritten** to ``kind: "sufficiency"`` and routed to
    ``surface`` — a real downgrade, not advisory, so a reviewer cannot route
    a design opinion into the auto-amend path by labelling it a fact.
    ``sufficiency`` and ``contradiction`` always surface; nothing in those
    kinds is auto-applied."""
    amend = []
    surface = []
    for finding in findings:
        finding = dict(finding)
        if finding.get("kind") == "groundedness":
            citation = finding.get("citation")
            if validate_citation(citation, repo_root):
                amend.append(finding)
            else:
                finding["kind"] = "sufficiency"
                surface.append(finding)
        else:
            surface.append(finding)
    return Disposition(amend=amend, surface=surface)
