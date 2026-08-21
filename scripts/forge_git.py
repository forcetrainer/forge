"""forge_git — git helpers and review-packet assembly for forge-run.py.

The diff base for review packets: HEAD lookup, working-tree cleanliness, the
per-task commit (one vertical slice per passed task), ``git diff``, and the
per-task / whole-plan review packets built by review-packet.py. Git failures
raise loudly naming the cause (a packet-generation error — halt per the Halt
spec).
"""
import os
import subprocess

from forge_common import rp


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
    """``git diff <base>`` in ``cwd``. Raises RuntimeError naming the cause on a
    git failure (a packet-generation error — halt per the Halt spec)."""
    proc = subprocess.run(
        ["git", "diff", base], cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git diff {} failed in {}: {}".format(base, cwd, proc.stderr.strip())
        )
    return proc.stdout


def _packet_for(task, plan_path, run_dir, base, cwd, prior_findings=None,
                 checklist=None):
    """Per-task review packet via review-packet.py: the task block + ``git diff
    <base>``. Missing task block raises (fail-loud). On a rework attempt
    ``prior_findings`` (a persisted finding_to_dict() list) carries the prior
    attempt's outstanding findings into the packet so the re-reviewer labels each
    current finding resolved/carried/new against them (Rework loop & convergence:
    carry the finding set into the next re-review packet). ``checklist`` (a list
    of forge_checklist.ChecklistItem, or None) threads the contract checklist
    into the packet, rendered after the diff and before the prior-findings
    section — omitted (None, the empty-checklist skip case) leaves the packet
    unchanged (Contract checklist spec)."""
    with open(plan_path, "r", encoding="utf-8") as f:
        plan_text = f.read()
    block = rp.extract_task_block(plan_text, task.number)
    if block is None:
        raise RuntimeError(
            "review packet: " + rp.diagnose_missing_task(plan_text, task.number, plan_path)
        )
    diff = _git_diff(cwd, base)
    packet = rp.build_packet(
        block, base, diff, prior_findings=prior_findings, checklist=checklist
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
        spec_text, base, diff, prior_findings=prior_findings, checklist=checklist
    )
    path = os.path.join(run_dir, "final-review.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(packet)
    return path
