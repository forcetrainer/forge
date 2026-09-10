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
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass

import forge_common


REPO_ROOT = forge_common.REPO_ROOT

BACKTICK_RE = re.compile(r'`([^`\n]+)`')
# identifier, dotted name (a.b.c), or name()/a.b.c() form.
SYMBOL_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\))?$')
# angle-bracketed placeholder text such as `<system>` or `<N>` — a template
# metavariable, never a reference (spec: Spec review, Classification
# precision).
METAVAR_RE = re.compile(r'<[^<>]+>')


@dataclass
class Reference:
    ref: str
    shape: str  # "path" | "symbol" | "other"
    resolved: bool
    found_at: "str | None"
    # Populated only when a path-shaped ref matched more than one tracked
    # path by unique-suffix resolution — `resolved` stays False (never a
    # silent pick among candidates) but this names what it's ambiguous
    # against, for reporting (spec: Spec review, Classification precision).
    ambiguous_matches: "list[str] | None" = None


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
    if METAVAR_RE.search(ref):
        # A template metavariable is dropped like a flag or an enum value —
        # never a path/symbol claim, regardless of what else the span looks
        # like (e.g. `docs/forge/specs/<system>.md` still contains "/").
        return "other"
    if "/" in ref:
        return "path"
    if any(ref.endswith(ext) for ext in extensions):
        return "path"
    if SYMBOL_RE.match(ref):
        return "symbol"
    return "other"


def _tracked_dirs(tracked_files):
    """Every directory path implied by some tracked file, e.g. `a/b/c.py`
    contributes `a` and `a/b`."""
    dirs = set()
    for f in tracked_files:
        parts = f.split("/")[:-1]
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}" if cur else part
            dirs.add(cur)
    return dirs


def _suffix_candidates(tracked_files):
    """Every tracked file path plus every directory path implied by one —
    the set a bare basename or relative directory fragment resolves against
    by unique trailing match (spec: Spec review, Classification precision)."""
    return set(tracked_files) | _tracked_dirs(tracked_files)


def _suffix_matches(norm, candidates):
    return sorted(p for p in candidates if p == norm or p.endswith("/" + norm))


def _resolve_path(ref, tracked_set, suffix_candidates):
    """A path resolves against git-tracked state only — never raw filesystem
    existence, which would make output depend on untracked local litter
    (build artifacts, `__pycache__`, ...) and vary across checkouts.

    Three tiers, in order: exact tracked-file match; a tracked-directory
    prefix match (``ref`` names a directory, not a file); and, failing both,
    a bare basename or relative directory fragment resolves when exactly one
    tracked path (file or implied directory) ends with it — reported
    ambiguous, never a silent pick, when more than one does (spec: Spec
    review, Classification precision). Returns ``(resolved, found_at,
    ambiguous_matches)``; the last is non-None only on an ambiguous suffix
    match."""
    norm = ref.strip("/")
    if not norm:
        return False, None, None
    if norm in tracked_set:
        return True, norm, None
    prefix = norm + "/"
    if any(f.startswith(prefix) for f in tracked_set):
        return True, norm, None
    matches = _suffix_matches(norm, suffix_candidates)
    if len(matches) == 1:
        return True, matches[0], None
    if len(matches) > 1:
        return False, None, matches
    return False, None, None


def _resolve_symbol(ref, pathspec=None):
    """Exit 0 is a match; exit 1 is a legitimate no-match (legal, unresolved).
    Anything else is a tool failure — `git grep` couldn't even run — and must
    raise naming the cause, never collapse into a legal negative result
    (constraint: `parsers-fail-loud`).

    ``pathspec``, when given, scopes the search to that one tracked file —
    the same grep, reused (never duplicated) by the structural check on
    ``dependencies_read[].symbol`` (spec: Spec review, Structural
    verification), which must confirm the symbol appears in the specific
    file claimed, not merely somewhere in the repo."""
    search_text = ref[:-2] if ref.endswith("()") else ref
    argv = ["git", "grep", "-n", "-F", "-e", search_text]
    if pathspec is not None:
        argv += ["--", pathspec]
    result = subprocess.run(
        argv, cwd=REPO_ROOT, capture_output=True, text=True,
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
    suffix_candidates = _suffix_candidates(tracked_files)

    references = []
    for ref in seen:
        shape = _classify(ref, extensions)
        ambiguous_matches = None
        if shape == "path":
            resolved, found_at, ambiguous_matches = _resolve_path(
                ref, tracked_set, suffix_candidates
            )
        elif shape == "symbol":
            resolved, found_at = _resolve_symbol(ref)
        else:
            resolved, found_at = False, None
        references.append(
            Reference(
                ref=ref, shape=shape, resolved=resolved, found_at=found_at,
                ambiguous_matches=ambiguous_matches,
            )
        )
    return references


def reference_table(spec_text):
    """The checklist: only ``path`` and ``symbol`` entries — ``other`` (flags,
    enum values, constraint ids) is noise and is dropped. A span with no
    alphanumeric character (a bare `/` from a sentence about path syntax, a
    stray punctuation token) is dropped too — it is not a reference, even
    though slash-detection alone would classify it path-shaped (spec: Spec
    review, Classification precision)."""
    return [
        r for r in extract_references(spec_text)
        if r.shape in ("path", "symbol") and any(ch.isalnum() for ch in r.ref)
    ]


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


def _validate_dependencies_read(entries, defects):
    """Structural verification of ``dependencies_read`` — a field carrying a
    claim about the repository is verified, not merely non-blank (spec: Spec
    review, Structural verification). ``file`` must name a tracked file;
    ``symbol`` must actually appear in that file, checked by reusing
    ``_resolve_symbol`` (Task 1's symbol resolution) scoped to the one file,
    never a second grep implementation. Skips a field already reported blank
    by ``_validate_required_fields`` — this is a second, independent check on
    top of that one, not a replacement for it."""
    tracked_set = set(_tracked_files())
    for i, entry in entries:
        label = _entry_label(entry, "dependencies_read", "symbol", i)
        file_val = entry.get("file")
        if _is_blank(file_val):
            continue
        if not isinstance(file_val, str) or file_val not in tracked_set:
            defects.append(
                "dependencies_read entry {!r} names file {!r} which is not "
                "a tracked file".format(label, file_val)
            )
            continue
        symbol_val = entry.get("symbol")
        if _is_blank(symbol_val) or not isinstance(symbol_val, str):
            continue
        found, _found_at = _resolve_symbol(symbol_val, pathspec=file_val)
        if not found:
            defects.append(
                "dependencies_read entry {!r} names symbol {!r} which does "
                "not appear in {!r}".format(label, symbol_val, file_val)
            )


def _resolve_section(name, spec_sections):
    """Case-insensitive unique-prefix match of ``name`` against
    ``spec_sections`` (the real heading texts of the spec under review).
    Returns the list of matching section names — empty means unresolved,
    more than one means ambiguous; the caller decides which, never a silent
    pick.

    Calls ``extract-brief.match_heading_names`` — the exact predicate
    ``find_spec_sections`` itself uses — rather than reimplementing it, so
    the two cannot silently drift apart on what counts as a match (this is
    the fix for rework finding f1: an earlier version of this function
    stripped numbering from the query too, which `find_spec_sections` never
    does). Not a call to ``find_spec_sections`` itself: that function takes
    raw ``spec_lines`` (`validate_verdict`'s required ``spec_sections``
    argument is contractually a flat list of heading-name strings, not
    lines — see its acceptance command) and also walks headings to compute
    each section's content boundary, which this check has no use for; it
    also raises fail-fast on the first non-match/ambiguity, whereas every
    finding here must be checked and every defect collected in one
    non-raising pass — telling "not found" apart from "ambiguous" by
    catching ``RuntimeError`` and parsing its message text would be the
    meaning-vs-spelling anti-pattern this plan's Acceptance bar forbids
    elsewhere. Pseudo-heading tuples are built here only to match
    ``match_heading_names``'s ``(level, raw_text, stripped_text,
    start_index)`` shape; level and index are unused by the predicate and
    are placeholders. `tests/test_forge_docreview.py`'s
    ``SectionMatcherParityTests`` is the tripwire that would still catch a
    future drift (e.g. if `match_heading_names` itself grew a second
    caller-specific branch)."""
    strip = forge_common.eb.strip_heading_text
    headings = [(0, s, strip(s), i) for i, s in enumerate(spec_sections)]
    return [h[1] for h in forge_common.eb.match_heading_names(name, headings)]


def _validate_findings_sections(entries, spec_sections, defects):
    """``findings[].section`` must name a real section of the spec under
    review — a claim about the document, verified the same way
    ``dependencies_read`` is, not left as a blankness-only check (spec: Spec
    review, Structural verification). Skips a blank ``section`` — already
    reported by ``_validate_required_fields``."""
    for i, entry in entries:
        label = _entry_label(entry, "findings", "id", i)
        section_val = entry.get("section")
        if _is_blank(section_val) or not isinstance(section_val, str):
            continue
        matches = _resolve_section(section_val, spec_sections)
        if not matches:
            defects.append(
                "finding {!r} names section {!r} which does not match any "
                "section of the spec under review".format(label, section_val)
            )
        elif len(matches) > 1:
            defects.append(
                "finding {!r} names section {!r} which is ambiguous: "
                "matches {}".format(label, section_val, ", ".join(matches))
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


def validate_verdict(verdict, unresolved_refs, repo_root, spec_sections):
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
    ``validate_citation``; citation resolution itself is still `dispose`'s
    job. ``spec_sections`` is the **required** list of the spec under
    review's real section heading texts, checked against
    ``findings[].section`` (spec: Spec review, Structural verification) — a
    caller that does not supply it is a ``TypeError``, not a silently
    skipped check (issue #68: an enforcement input a caller may omit is
    enforcement that silently vanishes).

    Beyond schema shape, this function now also verifies two fields whose
    value is a claim about the repository rather than free prose:
    ``dependencies_read[].file``/``.symbol`` (against git-tracked state) and
    ``findings[].section`` (against ``spec_sections``) — both touch the
    filesystem/git, unlike the purely structural checks below."""
    del repo_root  # accepted for interface symmetry; citation resolution happens in dispose()
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

    # Structural verification: a required field carrying a claim about the
    # repository is verified, not merely non-blank (spec: Spec review,
    # Structural verification) — the mechanical backstop against designing
    # against a function or section never actually read.
    _validate_dependencies_read(entries_by_type["dependencies_read"], defects)
    _validate_findings_sections(entries_by_type["findings"], spec_sections, defects)

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

    replaced_system = verdict.get("replaced_system")
    if replaced_system is not None and not isinstance(replaced_system, dict):
        # A present-but-wrong-shaped replaced_system (a string, int, bool,
        # or a falsy non-dict like []) must report a defect and let the
        # rest of the pass continue, never raise — the same treatment
        # `_collect_entries` already gives the three entry arrays, extended
        # to this one structured (non-array) field.
        defects.append(
            "replaced_system is not an object: {!r}".format(replaced_system)
        )
    else:
        replaced_system = replaced_system or {}
        applies = replaced_system.get("applies")
        guarantees = replaced_system.get("guarantees") or []
        if not isinstance(applies, bool):
            # Covers an absent replaced_system, one present but lacking
            # `applies`, and a non-boolean `applies` — all three leave
            # `applies` failing the isinstance check, so a required field
            # cannot go unasked simply by omitting it (execution.md:
            # "guidance alone is ignorable, a required field is not").
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
        if "citation" in finding:
            citation = finding["citation"]
            if citation is not None and not isinstance(citation, str):
                # citation is optional (null/absent are legal — the
                # groundedness guard downgrades those to sufficiency at
                # disposition) so it is never in the required-field schema
                # and is never type-checked there; a wrong-typed value
                # (e.g. an int) must still be caught here, before it can
                # reach `dispose`/`validate_citation`, which assume a
                # string or None (constraint: parsers-fail-loud — the CLI
                # must name the cause, not crash downstream).
                defects.append(
                    "finding {!r} has non-string citation {!r}".format(
                        label, citation
                    )
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


# --- packet build (spec: Spec review, reviewer packet) -----------------------


_ANTI_PATTERNS_DOC = "skills/brainstorming/design-anti-patterns.md"


def _required_verdict_fields_text():
    """The required-field list rendered from the same declared schema
    ``validate_verdict`` checks against (``_REQUIRED_FIELDS_SCHEMA``) plus the
    enums and structural rules that schema alone doesn't capture — one
    source, so a schema change (e.g. a new required field) shows up here
    with no separate prose to keep in sync."""
    lines = []
    for entry_type, schema in _REQUIRED_FIELDS_SCHEMA.items():
        fields = ", ".join("`{}`".format(f) for f in schema["fields"])
        lines.append("- `{}[]` — each entry needs: {}".format(entry_type, fields))
    lines.append(
        "- `references[].disposition` — one of: {}".format(
            ", ".join(sorted(_REFERENCE_DISPOSITIONS))
        )
    )
    lines.append(
        "- `findings[].kind` — one of: {}".format(
            ", ".join(sorted(_FINDING_KINDS))
        )
    )
    lines.append(
        "- `dependencies_read` may be empty only when `dependencies_waiver` "
        "is non-null."
    )
    lines.append(
        "- `replaced_system.applies` (boolean) is required; when true, "
        "`replaced_system.guarantees` must be non-empty, and when false it "
        "must be empty."
    )
    return "\n".join(lines) + "\n"


def _reference_state_text(r):
    """The one place a ``Reference``'s state becomes reviewer-facing text —
    shared by both renderers so an ambiguous entry (more than one tracked
    path matched a bare basename/directory fragment by unique-suffix
    resolution — never a silent pick, see `_resolve_path`) is always
    visibly distinguishable from a plain unresolved one and always names
    its candidates, not just reported as "unresolved" and left
    indistinguishable (spec: Spec review, Classification precision — f2)."""
    if r.resolved:
        return "resolved at {}".format(r.found_at)
    if r.ambiguous_matches:
        return "ambiguous: matches {}".format(", ".join(r.ambiguous_matches))
    return "unresolved"


def _render_reference_table(table):
    if not table:
        return "(no path/symbol references)\n"
    lines = []
    for r in table:
        lines.append("- `{}` ({}, {})".format(r.ref, r.shape, _reference_state_text(r)))
    return "\n".join(lines) + "\n"


def _render_unresolved_list(unresolved):
    if not unresolved:
        return "(none)\n"
    lines = []
    for r in unresolved:
        if r.ambiguous_matches:
            lines.append("- `{}` — {}".format(r.ref, _reference_state_text(r)))
        else:
            lines.append("- `{}`".format(r.ref))
    return "\n".join(lines) + "\n"


def _scoped_reference_table(lines, sections):
    """(scoped_sections, table) for ``lines`` given ``sections`` (``None`` or
    empty means unscoped): scoped means the table covers only the named
    sections' own content (the disposition obligation an amendment actually
    owes — spec: Spec review, "Amendments re-enter, scoped to the changed
    sections plus their references"); unscoped means the whole document.
    Shared by ``build_packet`` and the CLI's ``--verdict`` path so the
    verdict is checked against exactly the reference set the packet
    displayed, never a wider or narrower one."""
    if sections:
        scoped_sections = forge_common.eb.find_spec_sections(lines, sections)
        table = reference_table(
            "\n\n".join(content for _, content in scoped_sections)
        )
        return scoped_sections, table
    return None, reference_table("".join(lines))


def _all_spec_section_names(spec_lines):
    """Every heading's raw text in ``spec_lines`` — the section names
    ``findings[].section`` is checked against (`validate_verdict`'s required
    ``spec_sections`` argument). Always the *whole* document's headings, not
    just a ``--section``-scoped subset: a finding may legitimately cite any
    section of the spec under review, and the whole-document contradiction
    question already applies regardless of scope (spec: Spec review)."""
    eb = forge_common.eb
    mask = eb.fence_mask(spec_lines)
    names = []
    for i, line in enumerate(spec_lines):
        if mask[i]:
            continue
        m = eb.HEADING_RE.match(line)
        if m:
            names.append(m.group(2).strip())
    return names


def build_packet(spec_path, sections=None):
    """Assemble the reviewer's packet as text (spec: Spec review).

    Without ``sections``, the packet carries the whole spec. With
    ``sections`` (an amendment re-entering, scoped to the changed sections
    plus their references), the packet carries just the named sections' own
    reference table — the disposition obligation an amendment actually
    owes — while still carrying the *full* document as context, and states
    that the whole-document contradiction question applies regardless of
    scope: an amendment can contradict a section it never touched.

    Raises (never returns a legal-negative) on a missing/unreadable spec or
    an unresolvable/ambiguous section name — both `extract-brief.py`
    failures this function propagates unchanged (constraint:
    `parsers-fail-loud`).
    """
    lines = forge_common.eb.read_lines(spec_path)
    full_text = "".join(lines)

    scoped_sections, table = _scoped_reference_table(lines, sections)
    unresolved = [r for r in table if not r.resolved]

    parts = []
    if scoped_sections is not None:
        parts.append("# Scoped sections\n\n")
        for _, content in scoped_sections:
            parts.append(content.rstrip("\n") + "\n\n")
        parts.append(
            "The whole-document contradiction question applies regardless "
            "of scope, to sections not named above as well — an amendment "
            "can contradict a section it did not touch. The full document "
            "follows as context.\n\n"
        )
        parts.append("# Full document (context)\n\n")
        parts.append(full_text.rstrip("\n") + "\n\n")
    else:
        parts.append("# Spec\n\n")
        parts.append(full_text.rstrip("\n") + "\n\n")

    parts.append("# Hunting list\n\n")
    parts.append(
        "Reviewer guidance: @{doc} — load {doc} by reference. Its Trigger / "
        "Gate / Instead entries are the single tuning surface for what "
        "this review hunts; this packet never restates them.\n\n".format(
            doc=_ANTI_PATTERNS_DOC
        )
    )

    parts.append("# Reference table\n\n")
    parts.append(_render_reference_table(table))
    parts.append("\n")

    parts.append("# Unresolved references requiring disposition\n\n")
    parts.append(
        "The reviewer owes a disposition — `intended-new`, `wrong`, or "
        "`unverifiable`, each with evidence — on every reference below. A "
        "missing disposition invalidates the verdict.\n\n"
    )
    parts.append(_render_unresolved_list(unresolved))
    parts.append("\n")

    parts.append("# Required verdict fields\n\n")
    parts.append(_required_verdict_fields_text())

    return "".join(parts)


# --- CLI ---------------------------------------------------------------------


def _emit(text, out_path):
    """Write ``text`` to ``out_path``, or print it, on both the packet-emit
    and decision-write call sites — the single place either guards an
    unwritable ``--out`` (missing directory, permissions, ...): a legal-
    negative-shaped failure named on stderr, never an uncaught OSError
    (constraint: `parsers-fail-loud`, enforced at this CLI boundary since
    everything upstream returns rather than raises). Returns ``True`` on
    success, ``False`` on a reported failure — the caller's cue to exit
    non-zero instead of returning 0."""
    if not out_path:
        print(text)
        return True
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        print("error: cannot write to {}: {}".format(out_path, e), file=sys.stderr)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_docreview.py")
    parser.add_argument("--spec", required=True)
    parser.add_argument(
        "--section", action="append", default=[],
        help="repeatable; a named section scopes the packet's reference "
             "table to that section (plus its own references) while the "
             "full document still rides along as context. Omitted: the "
             "whole document.",
    )
    parser.add_argument(
        "--verdict",
        help="path to the reviewer's verdict JSON; when given, validates "
             "and disposes instead of emitting a packet.",
    )
    parser.add_argument("--repo-root", default=REPO_ROOT)
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    try:
        packet = build_packet(args.spec, sections=args.section or None)
    except RuntimeError as e:
        print("error: {}".format(e), file=sys.stderr)
        return 1

    if not args.verdict:
        return 0 if _emit(packet, args.out) else 1

    try:
        with open(args.verdict, "r", encoding="utf-8") as f:
            verdict = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(
            "error: cannot read verdict file {}: {}".format(args.verdict, e),
            file=sys.stderr,
        )
        return 1

    # A verdict file can be valid JSON and still be the wrong shape (a list,
    # a string, null, a number, a boolean) — legal-negative-shaped input,
    # not a tool crash. validate_verdict/_collect_entries assume a dict
    # (verdict.get(...)); everything downstream of this module deliberately
    # returns defects rather than raising, so this CLI boundary is the only
    # place a malformed shape can be turned into a named, non-zero exit
    # instead of an uncaught AttributeError (constraint: `parsers-fail-loud`).
    if not isinstance(verdict, dict):
        print(
            "error: verdict file {} does not contain a JSON object "
            "(found {}): {!r}".format(
                args.verdict, type(verdict).__name__, verdict
            ),
            file=sys.stderr,
        )
        return 1

    # The same reference set the packet displayed — scoped to --section when
    # given, whole-document otherwise (`_scoped_reference_table`) — so the
    # verdict is checked against exactly what the reviewer was asked to
    # dispose of, never a wider or narrower set.
    spec_lines = forge_common.eb.read_lines(args.spec)
    _, table = _scoped_reference_table(spec_lines, args.section or None)
    unresolved_refs = [r.ref for r in table if not r.resolved]
    # Always the whole spec's section names, never scoped to --section — see
    # `_all_spec_section_names`.
    spec_sections = _all_spec_section_names(spec_lines)

    result = validate_verdict(verdict, unresolved_refs, args.repo_root, spec_sections)
    if not result.valid:
        # Disposition is only meaningful for a verdict that already passed
        # validation (dispose()/validate_citation assume a validated shape
        # and can themselves crash on exactly the field validate_verdict
        # just flagged, e.g. a non-string citation) — so an invalid verdict
        # is reported without ever calling dispose. No disposition ran, so
        # the decision omits amend/surface entirely rather than inventing
        # empty lists that would claim a disposition took place.
        decision = {"valid": False, "defects": result.defects}
        if not _emit(json.dumps(decision, indent=2), args.out):
            return 1
        print(
            "error: invalid verdict: " + "; ".join(result.defects),
            file=sys.stderr,
        )
        return 1

    disposition = dispose(result.findings, args.repo_root)
    decision = {
        "valid": True,
        "defects": [],
        "amend": disposition.amend,
        "surface": disposition.surface,
    }
    if not _emit(json.dumps(decision, indent=2), args.out):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
