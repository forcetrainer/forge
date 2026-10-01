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

If the release changes `hooks/hooks.json` (any entry's event, matcher,
command, `async` or timeout, or the order of entries), say so in the release
notes: Codex users get a "Hooks need review" prompt at every startup, and
forge's hooks stay off until they re-trust. Editing a hook *script* needs no note.
See `docs/forge/running-on-codex.md`, Hook trust after a forge update.

This repo uses its own conventions: binding rules live in
`docs/forge/constraints.md` (CLI-authored, user-approved — see the
project-memory skill; read it before changing skill behavior), skipped
work is filed as a GitHub issue via `scripts/forge_memory.py defer` (see the
project-memory skill), not a markdown file. The `docs/forge/` directory also
opts this repo into its own session hook.
