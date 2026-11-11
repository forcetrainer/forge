# tools

Analysis tools for forge itself. Not part of the plugin runtime: no skill, hook
or runner calls anything here.

## `forge_runs_audit.py` — where Codex runs spend their time

Audits every `forge-run.py` run in a repo. It joins each run directory's receipts
and per-role `*-events.jsonl` to the `~/.codex/sessions` rollouts (per-turn
timing, model, effort, tokens), labels each dispatch turn as necessary work or a
repeat (validation retry, rework, re-review, cold respawn…), and prints a waste
ledger.

```bash
python3 tools/forge_runs_audit.py <repo> [--codex-home ~/.codex] [--json out.json]
python3 tools/forge_runs_audit.py --run-dir <one run dir>
```

Read-only. Needs the repo's `.forge/runs/` and the matching Codex sessions on the
same machine, so run it where the runs happened. Standard library only.

The `--json` output quotes the audited repo's task titles and review findings;
treat it as that repo's data.

## `forge_runs_bundle.py` — carry run records to another machine

Zips what an analysis needs from one or more repos' `.forge/runs/`: `run.json`,
attempt receipts, review packets, briefs, resume and retry prompts. It leaves out
the Codex event streams and live logs, which are most of a run directory's size,
and anything over the size cap. It adds each run's plan and spec files, any
`.forge/audits/` JSON, and a `git log --stat` since the earliest bundled run.

```bash
python3 tools/forge_runs_bundle.py <repo> [<repo> ...] [--run NAME ...] \
    [--audits DIR ...] [--out bundle.zip] [--max-file-kb 512]
```

Read-only. `MANIFEST.json` in the zip lists every file seen, bundled or skipped,
with its size and modification time. Standard library only.

The zip quotes the bundled repos' plans, diffs and review findings; treat it as
those repos' data.
