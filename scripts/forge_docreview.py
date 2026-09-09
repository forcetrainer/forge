"""forge_docreview — spec review's reference table (mechanical half).

Extracts every backticked span from a spec's prose, classifies each as
path-shaped, symbol-shaped, or neither, and resolves path/symbol references
against the repo. Fenced code is excluded via ``extract-brief.fence_mask`` —
a backticked span inside a fenced block is content, never a reference.

The table is a **checklist, not a rule** (spec: Spec review): an unresolved
reference is legal — a spec for a system not yet built names files that do
not exist. This module only enumerates and resolves; it never decides.
"""
import os
import re
import subprocess
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
