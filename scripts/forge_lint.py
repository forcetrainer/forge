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
class identity across the runner and this module.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import forge_common  # noqa: E402
import forge_plan  # noqa: E402
import forge_checklist  # noqa: E402
import forge_memory  # noqa: E402
import forge_memory_store  # noqa: E402

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


# --- Living-spec grammar (Phase 14/5) --------------------------------------
#
# A living spec's frontmatter is a fixed, two-key grammar (``system:`` a
# scalar, ``supersedes:`` a ``  - path`` list) — not general YAML, so it is
# parsed by hand rather than pulling in a third-party library
# (stdlib-only binds). A file with no frontmatter at all is simply a spec
# that hasn't been migrated yet — that's a rule-2 defect (system missing),
# reported like any other rule violation, never a raise. Once a frontmatter
# block has been *opened* (the file's first line is ``---``), anything
# inside it that isn't valid grammar — no closing ``---``, a line that is
# neither ``key: value`` nor a ``supersedes`` list item — can't be
# reinterpreted as "no frontmatter"; guessing intent there is exactly what
# fail-loud forbids, so it raises instead, naming the line.

DATED_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_FRONTMATTER_KEY_RE = re.compile(r"^(system|supersedes):\s*(.*)$")
_SUPERSEDES_ITEM_RE = re.compile(r"^  - (\S.*)$")
CHANGELOG_HEADING_RE = re.compile(r"^## Changelog\s*$")
CHANGELOG_ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}: \S.*$")
AMENDED_BY_RE = re.compile(r"amended by \[([^\]]*)\]")
ARCHIVE_PREFIX = "docs/forge/archive/"


def parse_frontmatter(lines):
    """``lines`` -> ``(dict, body_start_index)``.

    Grammar: exactly two possible keys. ``system: <value>`` is a scalar;
    ``supersedes:`` (no inline value) introduces zero or more ``  - path``
    lines. No frontmatter at all (first line isn't ``---``) is not an
    error — it returns ``({}, 0)`` so the caller can report it as a normal
    rule-2 defect. Anything else that goes wrong once a block has been
    opened raises ``RuntimeError`` naming the 1-based line number; the
    caller (which knows the path) is responsible for naming the file too.
    """
    if not lines or lines[0].rstrip("\n") != "---":
        return {}, 0

    data = {}
    n = len(lines)
    i = 1
    while i < n and lines[i].rstrip("\n") != "---":
        raw = lines[i].rstrip("\n")
        if not raw.strip():
            i += 1
            continue
        m = _FRONTMATTER_KEY_RE.match(raw)
        if not m:
            raise RuntimeError(
                "line {}: not a 'key: value' line: {!r}".format(i + 1, raw)
            )
        key, value = m.group(1), m.group(2).strip()
        if key == "system":
            data["system"] = value
            i += 1
        else:  # supersedes
            if value:
                raise RuntimeError(
                    "line {}: 'supersedes:' takes a list, not an inline "
                    "value".format(i + 1)
                )
            items = []
            i += 1
            while i < n and _SUPERSEDES_ITEM_RE.match(lines[i].rstrip("\n")):
                items.append(_SUPERSEDES_ITEM_RE.match(lines[i].rstrip("\n")).group(1).strip())
                i += 1
            data["supersedes"] = items

    if i >= n:
        raise RuntimeError("line {}: unterminated frontmatter block".format(n))

    return data, i + 1


def _is_archived(path, repo_root):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root))
    return rel.replace(os.sep, "/").startswith(ARCHIVE_PREFIX)


def lint_living_spec(path, *, repo_root):
    """The five living-spec rules against ``path``, as a list of defect
    strings — every rule broken, never just the first. A file under
    ``docs/forge/archive/`` is never linted: the archive is frozen and its
    documents are dated, frontmatter-less records by design, not living
    specs. Malformed (as opposed to absent) frontmatter still raises, via
    ``parse_frontmatter`` — see that function's docstring."""
    if _is_archived(path, repo_root):
        return []

    defects = []
    filename = os.path.basename(path)
    stem = os.path.splitext(filename)[0]

    if DATED_FILENAME_RE.match(filename):
        defects.append("{}: filename carries a YYYY-MM-DD prefix".format(path))

    lines = eb.read_lines(path)
    try:
        frontmatter, body_start = parse_frontmatter(lines)
    except RuntimeError as e:
        raise RuntimeError("{}: {}".format(path, e))

    system = frontmatter.get("system")
    if system is None:
        defects.append("{}: no frontmatter 'system:' key found".format(path))
    elif system != stem:
        defects.append(
            "{}: system '{}' disagrees with filename stem '{}'".format(
                path, system, stem
            )
        )

    docs_forge_dir = os.path.join(repo_root, "docs", "forge")
    for sup in frontmatter.get("supersedes", []):
        if not os.path.isfile(os.path.join(docs_forge_dir, sup)):
            defects.append(
                "{}: supersedes path does not resolve: {}".format(path, sup)
            )

    changelog_idx = None
    for i in range(body_start, len(lines)):
        if CHANGELOG_HEADING_RE.match(lines[i].rstrip("\n")):
            changelog_idx = i
            break

    if changelog_idx is None:
        defects.append("{}: missing '## Changelog' section".format(path))
    else:
        for i in range(changelog_idx + 1, len(lines)):
            line = lines[i].rstrip("\n")
            if line.startswith("## "):
                break
            if not line.strip():
                continue
            if not CHANGELOG_ENTRY_RE.match(line.strip()):
                defects.append(
                    "{}: line {}: malformed changelog entry: {!r}".format(
                        path, i + 1, line.strip()
                    )
                )

    systems = _existing_systems(repo_root)
    for m in AMENDED_BY_RE.finditer("".join(lines)):
        system_id = m.group(1)
        if system_id not in systems:
            defects.append(
                "{}: 'amended by [{}]' names a system that does not exist".format(
                    path, system_id
                )
            )

    return defects


def _existing_systems(repo_root):
    """Systems recognized by rule 5, derived from the filenames actually
    present in ``docs/forge/specs/`` — never a hardcoded list, so a system
    can't drift out of sync with the corpus that defines it."""
    specs_dir = os.path.join(repo_root, "docs", "forge", "specs")
    try:
        names = os.listdir(specs_dir)
    except OSError:
        return set()
    return {os.path.splitext(n)[0] for n in names if n.endswith(".md")}


def lint_spec_corpus(repo_root):
    """Every living-spec defect across every file in ``docs/forge/specs/``,
    in one run — never just the first offending spec."""
    specs_dir = os.path.join(repo_root, "docs", "forge", "specs")
    defects = []
    for name in sorted(os.listdir(specs_dir)):
        if not name.endswith(".md"):
            continue
        defects.extend(lint_living_spec(os.path.join(specs_dir, name), repo_root=repo_root))
    return defects


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
    """Per-task Tier, Acceptance-present, Tests, and Spec checks against
    every locatable block (canonical, duplicate, or wrong-level — see
    ``_lint_heading_structure``); also returns each canonical task's parsed
    ``Depends on`` numbers (via ``forge_plan``'s regex reader, which never
    raises) for the cross-task check in ``_lint_depends``.

    Tests grammar reuses ``eb.parse_test_cases`` exactly like the Spec check
    reuses ``eb.parse_spec_names``: a raise (inline joined cases, or a
    marker followed by neither bullets nor ``none``) becomes an ``error``
    defect naming the task, never a silent empty checklist. A task with no
    ``**Tests:**`` field returns ``[]`` from the parser and is legal."""
    defects = []
    depends_map = {}
    for where, num, block in blocks:
        block_lines = block.splitlines()
        block_mask = eb.fence_mask(block_lines)

        defects.extend(_parse_task_tier(block, where, num))

        if forge_plan._field_value(block_lines, block_mask, "Acceptance") is None:
            defects.append(_error(where, "missing **Acceptance:** line"))

        try:
            eb.parse_test_cases(block)
        except RuntimeError as e:
            defects.append(_error(where, str(e)))

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


def check_memory_files(repo_root):
    """Every ``forge_memory.fmt_check`` defect across this repo's managed
    project-memory files, reusing ``fmt_check``/``select_store`` rather than
    reimplementing parsing or store selection — the harness-agnostic layer
    that catches a drifted ``constraints.md``/``deferrals.md`` no matter
    which harness (Bash, Codex, a human) wrote it.

    Which files are managed is ``forge_memory_store.managed_paths``'s
    answer, not a second copy of it — the same helper ``forge_memory``'s
    ``fmt`` calls, so a third managed file can never be added to one and
    forgotten in the other. It never touches ``gh``: no network, ever. A
    managed path that does not exist is not a defect — most repos will
    never have these files, and this check must never reject a legal repo
    for lacking them."""
    try:
        paths = forge_memory_store.managed_paths(repo_root)
    except forge_memory_store.ConfigError as e:
        return [_error("memory", str(e))]

    if not paths:
        return []

    return [_error("memory", msg) for msg in forge_memory.fmt_check(paths)]


# --- Changed-section coverage ----------------------------------------------
#
# The baseline is the branch's merge base with the default branch, not
# ``HEAD`` — see ``_baseline_ref``. The comparison unit is a heading keyed
# by its stripped, lowercased text —
# the same identity ``**Spec:**`` names resolve through — mapped to that
# heading's **own** body: the lines from the heading to the next heading of
# any level, with every whitespace run collapsed to a single space. Two
# consequences, both deliberate:
#
#   * Reflow is invisible. Living specs get rewrapped constantly, and a rule
#     that fired on a rewrap would be ignored within a week.
#   * A change confined to a subsection implicates that subsection only, not
#     every ancestor up to the h1. Body text is *own* body rather than
#     subtree precisely so a one-line fix in a leaf doesn't mark half the
#     document changed.
#
# Claiming, by contrast, is subtree-aware: naming a parent claims its
# descendants, because ``eb.find_spec_sections`` — what actually builds the
# brief — hands a task naming the parent the child's text too. Resolution
# reuses that same function rather than re-deriving prefix-match grammar.
#
# A heading whose own body is empty (a pure container like ``# Spec``) is
# never reported: there is nothing in it for a task to deliver, so demanding
# a claim would be noise. A heading present in the new spec but not the
# committed one — including a *renamed* heading — reads as changed: the spec
# now asserts something under a name no committed version carried, and a
# plan that doesn't name it cannot be shown to deliver it.

_GIT_UNREADABLE = object()
# Exempt because nothing in them can be built: every other section states a
# requirement a task can deliver, while these two record history and
# judgment — no task is ever assigned to add a changelog line or a risk
# entry, so demanding they be claimed would train the reader to ignore the
# rule. That principle is the membership test, not convenience: a section
# joins this set only when it is structurally unbuildable.
_EXEMPT_KEYS = frozenset({"changelog", "risks / constraints"})


def _normalize_body(text):
    """Whitespace-insensitive body text — reflow must not read as change."""
    return " ".join(text.split())


def _spec_headings(spec_lines):
    """``[(level, raw_text, key, start_index)]`` for every unfenced heading,
    in document order — the same heading grammar ``eb.find_spec_sections``
    uses, so a section's identity here is the identity ``**Spec:**`` names."""
    mask = eb.fence_mask(spec_lines)
    headings = []
    for i, line in enumerate(spec_lines):
        if mask[i]:
            continue
        m = eb.HEADING_RE.match(line)
        if m:
            raw = m.group(2).strip()
            key = _normalize_body(eb.strip_heading_text(raw).lower())
            headings.append((len(m.group(1)), raw, key, i))
    return headings


def _section_bodies(spec_lines):
    """``{key: normalized own-body text}``. Duplicate heading texts (which
    ``**Spec:**`` resolution already calls ambiguous) are joined under the
    one key, so a change in either still registers."""
    headings = _spec_headings(spec_lines)
    bodies = {}
    for n, (_level, _raw, key, start) in enumerate(headings):
        end = headings[n + 1][3] if n + 1 < len(headings) else len(spec_lines)
        body = _normalize_body("".join(spec_lines[start + 1:end]))
        bodies[key] = (bodies[key] + " " + body).strip() if key in bodies else body
    return bodies


def _git(repo_root, *args):
    """``subprocess.run`` of a git command in ``repo_root``, or ``None`` when
    git itself could not be run."""
    try:
        return subprocess.run(
            ["git", "-C", repo_root] + list(args), capture_output=True,
        )
    except OSError:
        return None


def _ok(result):
    return result is not None and result.returncode == 0


def _default_branch(repo_root):
    """The repo's default branch ref, or ``None``.

    Asked in descending order of authority, never hardcoded to one name:
    the remote's own declaration (``refs/remotes/origin/HEAD``), then this
    repo's configured ``init.defaultBranch``, then the two conventional
    names as a last resort. A repo that answers none of these has no default
    branch to merge-base against, and the caller degrades to ``HEAD``.

    Every rung — the first included — must actually resolve to a commit
    before it is accepted. ``symbolic-ref`` reports what
    ``refs/remotes/origin/HEAD`` *points at* without checking that anything
    is there, and a dangling one is ordinary after a default-branch rename
    or a partial clone. Returning that name unverified would make
    ``merge-base`` fail and the rule go permanently and silently inert while
    a resolvable default branch sits one rung down — lint reporting clean
    because it cannot see, which is worse than crying wolf since nothing
    announces that it stopped working. So an unverified answer falls
    through to the next candidate; it never returns early."""
    candidates = []
    head = _git(repo_root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if _ok(head) and head.stdout.strip():
        candidates.append(head.stdout.decode("utf-8", "replace").strip())

    configured = _git(repo_root, "config", "--get", "init.defaultBranch")
    if _ok(configured) and configured.stdout.strip():
        name = configured.stdout.decode("utf-8", "replace").strip()
        candidates += ["origin/" + name, name]
    candidates += ["origin/main", "origin/master", "main", "master"]

    for cand in candidates:
        if _ok(_git(repo_root, "rev-parse", "--verify", "--quiet", cand + "^{commit}")):
            return cand
    return None


def _baseline_ref(repo_root):
    """The commit the spec is compared against: the branch's merge base with
    the default branch.

    ``HEAD`` is the wrong baseline for this rule and the reason it exists:
    the flow amends a spec **and commits it** before the plan is written, so
    against ``HEAD`` the amendment is already in history by the time lint
    runs, nothing reads as changed, and the check is inert in exactly the
    flow it was built for. The merge base is what the branch started from,
    which is what "changed on this branch" means.

    Where no merge base resolves — the default branch itself, an unborn or
    detached HEAD, a repo with no recognizable default branch — this falls
    back to ``HEAD``, which makes the rule *inert*, not *failing*: a plan is
    not wrong merely because lint cannot establish what the branch changed.
    That is deliberately a different outcome from ``_GIT_UNREADABLE``, which
    means git could not answer at all."""
    default = _default_branch(repo_root)
    if default:
        base = _git(repo_root, "merge-base", "HEAD", default)
        if _ok(base) and base.stdout.strip():
            return base.stdout.decode("utf-8", "replace").strip()
    return "HEAD"


def _committed_spec_lines(spec_path, repo_root):
    """The spec's baseline version as lines, ``None`` when the spec has no
    committed version at all, or ``_GIT_UNREADABLE`` when git cannot answer.

    The two failure shapes are kept apart on purpose: "absent from the
    baseline" means a genuinely new system and every section is changed,
    while "git could not answer" (no repo, unborn HEAD, path outside the
    repo, an undecodable blob, no git binary) must emit nothing. Absence is
    established by ``ls-tree`` succeeding with empty output — never by
    ``git show`` failing, which cannot tell a missing path from a broken
    repo."""
    rel = os.path.relpath(
        os.path.abspath(spec_path), os.path.abspath(repo_root)
    ).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return _GIT_UNREADABLE

    baseline = _baseline_ref(repo_root)
    listed = _git(repo_root, "ls-tree", "-z", baseline, "--", rel)
    if not _ok(listed):
        return _GIT_UNREADABLE
    if not listed.stdout.strip():
        return None  # no committed version — a genuinely new spec

    shown = _git(repo_root, "show", baseline + ":" + rel)
    if not _ok(shown):
        return _GIT_UNREADABLE
    try:
        return shown.stdout.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return _GIT_UNREADABLE


def _claimed_keys(blocks, spec_lines):
    """Every section key claimed by some task's ``**Spec:**`` line, each
    claimed name pulling in its whole subtree. A name that doesn't resolve
    (or resolves ambiguously) is skipped here — ``_lint_task_fields``
    already reports it, and swallowing the coverage check over it would let
    one typo hide every gap."""
    headings = _spec_headings(spec_lines)
    by_raw = {}
    for n, (level, raw, _key, _start) in enumerate(headings):
        by_raw.setdefault(raw, n)

    claimed = set()
    for _where, _num, block in blocks:
        try:
            names = eb.parse_spec_names(block)
        except RuntimeError:
            continue
        for name in names:
            try:
                sections = eb.find_spec_sections(spec_lines, [name])
            except RuntimeError:
                continue
            n = by_raw.get(sections[0][0])
            if n is None:
                continue
            level = headings[n][0]
            claimed.add(headings[n][2])
            for m in range(n + 1, len(headings)):
                if headings[m][0] <= level:
                    break
                claimed.add(headings[m][2])
    return claimed


def _lint_spec_coverage(spec_path, spec_lines, blocks, repo_root):
    """Every spec section changed since the branch's merge base with the
    default branch is named by some task's ``**Spec:**`` line. ``##
    Changelog`` and ``## Risks / constraints`` are exempt; a spec with no
    committed version has every section changed; a git read that cannot
    answer emits nothing. Every unclaimed section is reported, never the
    first only."""
    committed = _committed_spec_lines(spec_path, repo_root)
    if committed is _GIT_UNREADABLE:
        return []

    old_bodies = {} if committed is None else _section_bodies(committed)
    new_bodies = _section_bodies(spec_lines)
    claimed = _claimed_keys(blocks, spec_lines)

    defects = []
    seen = set()
    for _level, raw, key, _start in _spec_headings(spec_lines):
        if key in seen or key in _EXEMPT_KEYS or key in claimed:
            continue
        body = new_bodies.get(key, "")
        if not body or body == old_bodies.get(key):
            continue
        seen.add(key)
        defects.append(_error(
            "spec coverage",
            'changed spec section "{}" in {} is named by no task\'s '
            "**Spec:** line".format(raw, spec_path),
        ))
    return defects


def lint_plan(plan_path, spec_path=None, *, repo_root):
    """Every documented-grammar defect in ``plan_path`` (and ``spec_path``
    when given), never short-circuiting on the first — including when the
    heading structure itself is broken: a wrong-level or duplicated task
    heading is reported, but every other task (and that task's own
    otherwise-valid fields) is still checked, never silently dropped. Never
    rejects a legal plan: ``**Spec:**``, ``**Global Constraints:**``, and
    prose acceptance are all optional per the planning skill, so their
    absence is never an error — and an empty checklist is a warning, not an
    error.

    ``repo_root`` is a required keyword-only argument (no process-cwd
    guessing here, by design (constraint: parsers-fail-loud) — a library
    function that inferred the repo root from
    ``os.getcwd()`` would silently check the wrong directory the moment a
    caller's own tracked cwd diverges from the process cwd, exactly the
    failure this harness-agnostic check exists to prevent). Every caller
    resolves and passes its own repo root explicitly; only a CLI edge may
    default it to ``os.getcwd()``. It is where ``check_memory_files`` looks
    for managed project-memory files; a memory defect and a plan/spec defect
    are always reported together in one run, never one suppressing the
    other."""
    memory_defects = check_memory_files(repo_root)

    lines = eb.read_lines(plan_path)
    mask = eb.fence_mask(lines)
    defects = list(memory_defects)

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

    if spec_lines is not None:
        defects.extend(_lint_spec_coverage(spec_path, spec_lines, blocks, repo_root))

    return _dedup(defects)


def main(argv):
    parser = argparse.ArgumentParser(prog="forge_lint.py")
    parser.add_argument("plan", nargs="?")
    parser.add_argument("--spec")
    parser.add_argument(
        "--specs", action="store_true",
        help="lint every file in docs/forge/specs/ instead of a plan",
    )
    parser.add_argument(
        "--repo-root",
        help="repo root to check managed project-memory files under "
             "(default: cwd)",
    )
    args = parser.parse_args(argv)
    # The CLI is an edge — a human is present — so it's the one place
    # allowed to default repo_root to the process cwd; lint_plan itself
    # never guesses.
    repo_root = args.repo_root if args.repo_root is not None else os.getcwd()

    if args.specs:
        try:
            defects = lint_spec_corpus(repo_root)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
        for d in defects:
            print(d)
        return 1 if defects else 0

    if not args.plan:
        parser.error("plan is required unless --specs is given")

    try:
        defects = lint_plan(args.plan, args.spec, repo_root=repo_root)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    for d in defects:
        print("[{}] {}: {}".format(d.severity, d.where, d.message))

    return 1 if any(d.severity == "error" for d in defects) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
