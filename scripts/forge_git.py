"""forge_git — git helpers and review-packet assembly for forge-run.py.

The diff base for review packets: HEAD lookup, working-tree cleanliness, the
per-task commit (one vertical slice per passed task), ``git diff``, and the
per-task / whole-plan review packets built by review-packet.py. Git failures
raise loudly naming the cause (a packet-generation error — halt per the Halt
spec).
"""
import os
import subprocess
import tempfile

from forge_common import eb, rp
from forge_receipts import strip_ledger_annotations


def _git_head(cwd):
    """HEAD SHA of the repo at ``cwd``, or None when ``cwd`` is not a git repo
    (git unavailable / no commits). Reviews require a repo; callers that must have
    one raise loudly, and the plan-level final review is skipped without one."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _working_tree_dirty(cwd):
    """The working tree's dirty paths (``git status --porcelain`` lines), or ``[]``
    when clean, or ``None`` when ``cwd`` is not a git repo. The self-ignored
    ``.forge/`` never appears (its ``*`` gitignore). Commit discipline requires a
    clean tree at invocation start, so ``run_plan`` halts on a non-empty list."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def snapshot_tree(cwd):
    """Pre-repair working-tree snapshot for a later ``git diff <ref>`` (Delta-
    scoped verification packets spec): taken just before a repair dispatch so
    the next verification packet's delta is scoped to that repair alone.
    ``git stash create`` records a commit-ish of the current index + tracked
    working-tree state without touching either (unlike plain ``git stash``,
    it never updates the stash ref or the working tree, and — like ``git
    diff`` — it never looks at untracked files) — the usual path. When there
    is nothing tracked to stash, ``stash create`` prints nothing; that means
    the tracked tree already equals HEAD, so the snapshot ref is simply
    ``HEAD`` itself — no ``git add``, no index mutation, not even for an
    untracked file sitting in the tree (a prior fallback staged those via
    ``git add -A``, which a later ``git add -A && git commit`` would then
    sweep into the task or final-review commit — fixed 2026-08-21). Never
    mutates the working tree or the index either way. Returns ``None``
    outside a git repo (mirrors ``_git_head``); raises RuntimeError naming the
    cause on a git failure (a packet-generation error — halt per the Halt
    spec)."""
    head = _git_head(cwd)
    if head is None:
        return None
    try:
        stash = subprocess.run(
            ["git", "stash", "create"], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if stash.returncode != 0:
        raise RuntimeError(
            "git stash create failed in {}: {}".format(cwd, stash.stderr.strip())
        )
    ref = stash.stdout.strip()
    return ref if ref else head


def _git_commit_task(cwd, task):
    """Commit this passed task's work as one slice: ``git add -A`` then
    ``git commit -m "forge: task <N> — <title>"``. Returns the new HEAD SHA, or
    ``None`` when nothing was staged (empty ``git diff --cached`` — e.g. a task
    that changed no files, or a human pre-fixed the work on resume) or ``cwd`` is
    not a git repo. Never creates an empty commit. ``.forge/`` is ignored, never
    staged; the ledger annotation (written before this call) rides in the commit."""
    if _git_head(cwd) is None:
        return None
    try:
        add = subprocess.run(
            ["git", "add", "-A"], cwd=cwd, capture_output=True, text=True
        )
        if add.returncode != 0:
            raise RuntimeError(
                "git add -A for task {} failed in {}: {}".format(
                    task.number, cwd, add.stderr.strip()
                )
            )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=cwd,
            capture_output=True, text=True,
        )
        if staged.returncode == 0:
            return None  # nothing staged -> skip, no empty commit
        msg = "forge: task {} — {}".format(task.number, task.title)
        commit = subprocess.run(
            ["git", "commit", "-m", msg], cwd=cwd, capture_output=True, text=True
        )
    except OSError:
        return None
    if commit.returncode != 0:
        raise RuntimeError(
            "git commit for task {} failed in {}: {}".format(
                task.number, cwd, commit.stderr.strip()
            )
        )
    return _git_head(cwd)


def _git_diff(cwd, base):
    """The review diff: ``git diff <base>`` plus new-file hunks for untracked,
    non-ignored files (review-packet.py's ``git_diff`` — one implementation
    behind every packet, the finding classifier, and the standalone CLIs, so
    "in-diff" means the same thing everywhere). Raises RuntimeError naming the
    cause on a git failure (a packet-generation error — halt per the Halt
    spec)."""
    return rp.git_diff(cwd, base)


def _packet_for(task, plan_path, run_dir, base, cwd, prior_findings=None,
                 checklist=None, spec_path=None):
    """Per-task review packet via review-packet.py: the task block + ``git diff
    <base>``. Missing task block raises (fail-loud). On a rework attempt
    ``prior_findings`` (a persisted finding_to_dict() list) carries the prior
    attempt's outstanding findings into the packet so the re-reviewer labels each
    current finding resolved/carried/new against them (Rework loop & convergence:
    carry the finding set into the next re-review packet). ``checklist`` (a list
    of forge_checklist.ChecklistItem, or None) threads the contract checklist
    into the packet, rendered after the diff and before the prior-findings
    section — omitted (None, the empty-checklist skip case) leaves the packet
    unchanged (Contract checklist spec).

    ``spec_path`` (optional), when the task declares a ``**Spec:**`` line,
    resolves that line's names via ``find_spec_sections`` and passes the
    resulting ``(heading, body)`` pairs into ``build_packet`` as
    ``spec_sections`` — context the reviewer reads for understanding, not a
    checklist item (Contract checklist spec). A task declaring no
    ``**Spec:**`` gets no spec-context section, ``spec_path`` or not."""
    with open(plan_path, "r", encoding="utf-8") as f:
        plan_text = f.read()
    block = rp.extract_task_block(plan_text, task.number)
    if block is None:
        raise RuntimeError(
            "review packet: " + rp.diagnose_missing_task(plan_text, task.number, plan_path)
        )
    # The reviewer boundary is where the ledger stops. A task block carries
    # the runner's own outcome annotation on its checkbox line, and on a
    # resumed task that annotation reads `escalated: <reason>: <findings>` —
    # telling a discovery reviewer that the work it is judging was frozen and
    # what the last reviewer said about it. Stripped here, once, for every
    # review packet rather than only the resumed path, because this is the
    # structural guard (`discovery-review-is-cold`) and not a special case.
    # The worker's brief is untouched: the worker may know it was paused.
    block = strip_ledger_annotations(block)
    spec_sections = None
    spec_names = eb.parse_spec_names(block)
    if spec_names:
        spec_lines = eb.read_lines(spec_path)
        spec_sections = eb.find_spec_sections(spec_lines, spec_names)
    diff = _git_diff(cwd, base)
    packet = rp.build_packet(
        block, base, diff, prior_findings=prior_findings, checklist=checklist,
        review_kind="discovery", spec_sections=spec_sections,
    )
    path = os.path.join(run_dir, "task-{}-review.md".format(task.number))
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path


def _final_packet(spec_path, base, diff, run_dir, prior_findings=None,
                   checklist=None):
    """Whole-plan final-review packet: the spec + the whole-plan ``git diff
    <base>``, assembled by review-packet.py's fence-safe builder. On a re-review
    ``prior_findings`` (a persisted finding_to_dict() list) carries the prior
    attempt's outstanding fix findings into the packet so the fresh-context final
    reviewer labels each current finding resolved/carried/new against them —
    identical to the per-task path (Final review spec: "the same loop").
    ``checklist`` mirrors ``_packet_for``'s: the final contract checklist (or
    None, the empty-checklist skip case), rendered after the diff and before
    the prior-findings section."""
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_text = f.read()
    packet = rp.build_packet(
        spec_text, base, diff, prior_findings=prior_findings, checklist=checklist,
        review_kind="discovery",
    )
    path = os.path.join(run_dir, "final-review.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path


def freeze_ref_name(run_id, task_number):
    """The forge-owned ref a halted task's freeze is parked under:
    ``refs/forge/freeze/<run_id>/task-<N>``. Under ``refs/forge/`` rather than
    ``refs/heads/``, so the freeze is never a branch, never pushed by default,
    and — being a ref — never garbage-collected (Halt resolution: retained
    under a forge-owned ref)."""
    return "refs/forge/freeze/{}/task-{}".format(run_id, task_number)


def freeze_stage_ref_name(run_id, stage):
    """The forge-owned ref a halted whole-run STAGE's freeze is parked under:
    ``refs/forge/freeze/<run_id>/<stage>`` (``final-review`` | ``doc-sync``).
    The same shape and the same guarantees as ``freeze_ref_name``'s per-task
    ref — a task number and a stage name can never collide, since the task
    form is always ``task-<N>``."""
    return "refs/forge/freeze/{}/{}".format(run_id, stage)


def _git(cwd, args, what, env=None, stdin=None):
    """Run a git command, raising RuntimeError naming ``what`` and git's own
    stderr on failure (parsers-fail-loud: never guess, never default)."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        env=env, input=stdin,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "{} failed in {}: {}".format(what, cwd, proc.stderr.strip())
        )
    return proc.stdout


def _resolve_commit(cwd, sha, what):
    """``sha`` as a resolvable commit object, or RuntimeError naming the sha.
    A freeze sha comes from run state written by an earlier process; a missing
    object means the ref was deleted or the repo pruned, and silently treating
    that as "nothing to restore" would drop a human's paused work without a
    word (parsers-fail-loud)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "{}^{{commit}}".format(sha)],
        cwd=cwd, capture_output=True, text=True,
    )
    resolved = proc.stdout.strip()
    if proc.returncode != 0 or not resolved:
        raise RuntimeError(
            "{}: freeze commit {} is not a resolvable object in {}".format(
                what, sha, cwd
            )
        )
    return resolved


def _untracked_nested_repos(cwd):
    """Untracked paths under ``cwd`` that are themselves git repositories.

    ``git add -A`` records such a directory as a 160000 gitlink pointing at a
    commit the outer object store does not have (and errors outright when the
    nested repo has no commit yet), so the nested repo's actual files are
    never captured — the freeze that results cannot be replayed. ``git clean
    -fd`` will not remove it either (that needs ``-ff``), so the tree would
    not be back at HEAD. Detected before anything is written; see
    ``freeze_attempt``."""
    listing = _git(
        cwd, ["ls-files", "--others", "--exclude-standard", "--directory"],
        "git ls-files --others for freeze",
    )
    found = []
    for entry in listing.splitlines():
        entry = entry.strip()
        if not entry.endswith("/"):
            continue
        for dirpath, dirnames, filenames in os.walk(os.path.join(cwd, entry)):
            if ".git" in dirnames or ".git" in filenames:
                found.append(os.path.relpath(dirpath, cwd))
                dirnames[:] = []  # a repo's own contents are not scanned
    return found


def freeze_attempt(cwd, ref_name):
    """Capture the in-progress attempt as a commit parked at ``ref_name``,
    then return the working tree to HEAD. Returns the freeze SHA, or ``None``
    when there is nothing to freeze (the tree already equals HEAD) or ``cwd``
    is not a git repo.

    Tracked *and* untracked non-ignored changes are captured: a task built
    from new files is otherwise frozen as empty, which is exactly what ``git
    stash create`` (tracked-only, like ``git diff``) would do here. The
    capture stages into a **temporary index** (``GIT_INDEX_FILE``), seeded
    from HEAD so tracked-but-ignored paths survive, so the repo's real index
    is never touched — the recorded prior failure was a ``git add -A``
    capture whose staged leftovers a later ``git add -A && git commit`` swept
    into the next task's slice (see ``snapshot_tree``, fixed 2026-08-21).

    The commit is written with ``commit-tree`` (parent HEAD) and published
    with ``update-ref`` on ``ref_name`` alone: HEAD and the current branch ref
    never move, so the checkpoint stays the review base for both the human's
    fix and the resumed task (Commit discipline: freeze commits are parked off
    the mainline, never stacked on it).

    The working tree is then reset to HEAD — ``git reset --hard`` plus ``git
    clean -fd`` for the untracked files the reset leaves behind. ``clean``
    runs without ``-x``, so ignored paths (``.forge/`` among them) survive.
    Raises RuntimeError naming the cause on any git failure."""
    head = _git_head(cwd)
    if head is None:
        return None
    nested = _untracked_nested_repos(cwd)
    if nested:
        raise RuntimeError(
            "freeze_attempt: untracked nested git repository at {} in {} — its "
            "files cannot be captured in a freeze (git records only a gitlink, "
            "to a commit this repo does not have), and removing it would "
            "destroy work that is not ours to discard. Move, remove, or commit "
            "it, then resume.".format(", ".join(nested), cwd)
        )
    fd, index_path = tempfile.mkstemp(prefix="forge-freeze-index-")
    os.close(fd)
    os.remove(index_path)  # git wants to create it itself
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    try:
        _git(cwd, ["read-tree", "HEAD"], "git read-tree for freeze", env=env)
        _git(cwd, ["add", "-A"], "git add -A for freeze", env=env)
        tree = _git(cwd, ["write-tree"], "git write-tree for freeze", env=env).strip()
    finally:
        if os.path.exists(index_path):
            os.remove(index_path)
    head_tree = _git(cwd, ["rev-parse", "HEAD^{tree}"],
                     "git rev-parse HEAD^{tree} for freeze").strip()
    if tree == head_tree:
        return None  # nothing to freeze; no ref written
    sha = _git(
        cwd, ["commit-tree", tree, "-p", head, "-m", "forge: freeze " + ref_name],
        "git commit-tree for freeze",
    ).strip()
    _git(cwd, ["update-ref", ref_name, sha], "git update-ref for freeze")
    _git(cwd, ["reset", "--hard", "HEAD"], "git reset --hard for freeze")
    _git(cwd, ["clean", "-fd"], "git clean -fd for freeze")
    return sha


def restore_freeze(cwd, freeze_sha):
    """Replay the freeze's change onto the current HEAD. Returns ``True`` when
    it applies (the working tree now carries the frozen change, unstaged, so
    the resumed task's own commit sweeps it normally), ``False`` on conflict —
    in which case the working tree is left clean at HEAD and the task restarts
    from the checkpoint with ``freeze_diff`` as reference text (Halt
    resolution: a conflict is the signal that the fix invalidated the work,
    and the runner never resolves one).

    ``git apply --3way`` does the replay, which implies ``--index``; the
    following mixed ``git reset`` unstages, so a restored untracked file is
    untracked again. Raises RuntimeError naming ``freeze_sha`` when it is not
    a resolvable object."""
    resolved = _resolve_commit(cwd, freeze_sha, "restore_freeze")
    patch = _git(
        cwd,
        ["diff", "--no-textconv", "--no-ext-diff", "--binary",
         resolved + "^", resolved],
        "git diff for freeze restore",
    )
    if not patch.strip():
        return True  # an empty freeze restores to exactly the current tree
    proc = subprocess.run(
        ["git", "apply", "--3way", "-"], cwd=cwd,
        capture_output=True, text=True, input=patch,
    )
    if proc.returncode != 0:
        _git(cwd, ["reset", "--hard", "HEAD"], "git reset after conflicted replay")
        _git(cwd, ["clean", "-fd"], "git clean after conflicted replay")
        return False
    _git(cwd, ["reset"], "git reset after freeze restore")
    return True


def freeze_diff(cwd, freeze_sha):
    """The frozen change as patch text — supplied to a resumed task as
    reference material when the replay conflicts. Raises RuntimeError naming
    ``freeze_sha`` when it is not a resolvable object."""
    resolved = _resolve_commit(cwd, freeze_sha, "freeze_diff")
    return _git(
        cwd,
        ["diff", "--no-textconv", "--no-ext-diff", resolved + "^", resolved],
        "git diff for freeze_diff",
    )
