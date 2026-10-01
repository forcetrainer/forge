# Running plan execution on Codex CLI

Claude Code executes a plan natively — in-session tier-worker dispatch, native
worktrees, code review, and verification to lean on. Codex CLI has none of
those, so on Codex forge *supplies the execution layer itself*: a
deterministic runner in place of in-session dispatch, foreground halt-relay in
place of native session awareness, explicit per-task commits, and a live
monitor. The flow, gates, and tiers are identical across both harnesses —
only the execution substrate differs. See
[The execution loop](execution-loop.md) for the review/classify/fix/defer/halt
model both harnesses implement, and
[`skills/planning/codex-execution.md`](../../skills/planning/codex-execution.md)
for the full invocation contract, halt/resume semantics, and the
orchestrator's reduced role.

## Invocation

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" <plan.md> --spec <spec.md> --timeout 900
```

That single call is whole-plan scope. The runner owns the task loop, brief
generation, worker dispatch, acceptance-command execution, review dispatch,
the convergence-based rework loop, receipts, and plan-checkbox ledger
annotations.

**Precondition:** requires a clean working tree at start —
`git status --porcelain` empty, with `.forge/` self-ignored. A dirty tree
causes a contract error (exit 1) naming the dirty paths; commit or discard
before re-invoking.

**`--autofix auto | gate`** (default `auto`): `auto` runs the disposition
matrix — fixes in-diff contract-breaking findings in-loop, defers
improvements, halts only genuine scope decisions. `gate` is the conservative
escape hatch — any finding halts for a human, no auto-fix.

**Per-task commits:** after each task passes, the runner stages all changes
and commits with message `forge: task N — <title>`, giving a clean checkpoint
after every passed task. `.forge/` is never staged. Escalated tasks commit
nothing — uncommitted work stays for human resolution.

## Session awareness — run in the foreground

Foreground is what makes a halt visible: the orchestrator is blocked on the
command, so when the runner exits non-zero (escalation exit 2, contract error
exit 1) it reads the receipt and relays the halt into the conversation —
"task N escalated, needs your decision." `--timeout 900` bounds every
`codex exec` call, so a genuinely hung task is killed and escalated rather
than sitting forever. Don't background-and-walk-away — that reintroduces the
blindness this mechanism exists to prevent.

Peek at any run on demand (dispatches nothing, exits 0):

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/forge-run.py" --status --run-dir .forge/runs/<name>
```

## Live monitor (optional)

For a live view, run the monitor in a second terminal — a full-screen `rich`
TUI showing the plan ledger with the in-flight task lit, that task's
`codex exec` output scrolling, and a full-width banner when the run completes
or halts. The recommended shape is a **standing monitor**: leave it open once
and it attaches to every run.

```bash
sh .forge/watch      # standing monitor (forge-monitor.py --follow); the runner prints this at start
```

`--follow` watches the newest run and auto-flips to each new run as it
starts. One-shot forms also exist: `forge-monitor.py --latest` (newest run,
then exit) or `--run-dir .forge/runs/<name>`. It only reads the run dir
(dispatches nothing) and needs `rich` (`pip install rich`; on a
PEP-668-managed Python use `--break-system-packages` or a venv); a killed
runner shows as `stalled?` rather than a stuck spinner. `--status` above
stays the zero-dependency peek.

Receipts land in `.forge/runs/<timestamp>/`, uncommitted — the runner writes
a self-ignoring `.forge/.gitignore` (`*`) on first run, so there's no
target-repo setup.

## Hook trust after a forge update

Codex runs a plugin's hooks only after you trust them. Trust is keyed per
`hooks/hooks.json` entry — plugin, event, and position — and hashes the
entry itself (event, matcher, command, `async`, timeout), not the script it
runs. So:

- Upgrading forge, or upgrading Codex, does not by itself revoke trust, and
  edits to the hook scripts never do.
- A forge release that **changes `hooks/hooks.json`** does: an edited entry
  reads as modified, an added one as untrusted, and inserting or reordering
  entries shifts the positions of the ones after it, untrusting them too.

The interactive TUI then opens **"Hooks need review"** at startup. Choose
**Trust all and continue** (or review first). **Continue without trusting
(hooks won't run)** leaves forge's hooks off for that session — no
session-start context, no `constraints.md` write guard — and records nothing,
so the prompt returns at the next startup. `codex exec` never prompts; an
untrusted hook simply does not run there.

## Known Codex caveats

These apply to ad-hoc in-session Codex subagents (exploration, one-off
review) — the only place forge still spawns them. Plan execution goes
through `forge-run.py`'s one-`codex exec`-process-per-task instead, which
sidesteps both issues by construction (no parent-model inheritance, no
completed-worker accumulation).

- Subagent selection has known regressions (custom-agent selection broke in
  v0.137.0 and spawned agents silently inherited the parent model). If
  spawned agents run the wrong model, check acceptance-command output rather
  than trusting the spawn.
- Spawned subagents pile up in the CLI's agent list, and completed workers
  keep counting against the thread limit
  ([openai/codex#19197](https://github.com/openai/codex/issues/19197),
  [openai/codex#22779](https://github.com/openai/codex/issues/22779)).
