# Developing forge

On the machine where you edit the plugin, point the marketplace at your
working copy so edits are picked up locally:

```bash
claude plugin marketplace add ~/development/forge
```

The plugin cache only re-syncs on a **version bump**. After editing anything
under `skills/`, `agents/`, or `hooks/`:

```bash
# 1. bump "version" in BOTH .claude-plugin/plugin.json and .codex-plugin/plugin.json (lockstep — a test enforces it)
# 2. then:
claude plugin update forge@forge
# 3. restart the session to apply
```

This repo uses its own conventions: binding rules live in
`docs/forge/constraints.md` (CLI-authored, user-approved — see the
project-memory skill; read it before changing skill behavior), skipped
work is filed as a GitHub issue via `scripts/forge_memory.py defer` (see the
project-memory skill), not a markdown file. The `docs/forge/` directory also
opts this repo into its own session hook.
