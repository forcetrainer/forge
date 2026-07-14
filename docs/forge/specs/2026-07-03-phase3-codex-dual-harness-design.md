# Phase 3: Codex dual-harness packaging — design

Goal: forge installable on Codex CLI from this repo, alongside Claude Code. Skills, scripts, and the session-start script are shared verbatim; harness divergence is isolated to manifests, agent definitions, hook wiring, and one execution reference file.

## Verified harness contracts (July 2026)

- Codex plugin manifest: `.codex-plugin/plugin.json` — required `name` (kebab-case), `version` (semver), `description`. Plugin root convention: `skills/`, `hooks/hooks.json`, optional `.mcp.json`.
- Marketplace: `.agents/plugins/marketplace.json` at repo root — `name`, `interface.displayName`, `plugins[]` with `source: {source: "local", path: "./"}`.
- Custom agents: TOML, one file per agent, project scope `.codex/agents/`, personal scope `~/.codex/agents/`. Required: `name`, `description`, `developer_instructions`. Optional: `model`, `model_reasoning_effort` (`minimal`–`xhigh`), `nickname_candidates` (unique, ASCII letters/digits/spaces/hyphens/underscores). **Plugins cannot bundle agents** — install is a copy step.
- Hooks: `SessionStart` exists; output contract identical to Claude Code (`hookSpecificOutput.additionalContext` JSON on stdout). Config via `hooks.json` or `[hooks]` in `config.toml`, user or project layer; plugins carry `hooks/hooks.json`.
- Invocation model: no Workflow tool, no auto-delegation — subagents spawn only when named explicitly in a prompt.
- Current models: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`. Excluded: `gpt-5.3-codex-spark` (Pro-only preview), `gpt-5.2`/`gpt-5.3-codex` (deprecated).

## Deliverables

### 1. Codex manifests
- `.codex-plugin/plugin.json`: name `forge`, description matching `.claude-plugin/plugin.json`, version **kept in lockstep** with the Claude manifest — every version bump touches both.
- `.agents/plugins/marketplace.json`: local-source marketplace exposing this repo as the plugin root.
- Test: manifest JSON validity + version equality across both plugin manifests (pytest, stdlib only).

### 2. Tier agents — `codex/agents/*.toml`
- Three files: `forge-light.toml`, `forge-standard.toml`, `forge-deep.toml`.
- `developer_instructions` = the body text of the corresponding `agents/*.md` (same worker/reviewer contract, kept in sync manually; divergence is a bug).
- Mapping:

| Agent | model | model_reasoning_effort |
|---|---|---|
| forge-light | gpt-5.4-mini | low |
| forge-standard | gpt-5.4 | high |
| forge-deep | gpt-5.5 | xhigh |

- `nickname_candidates`: tier-prefixed pools (`forge-light-1`…`-5`, likewise standard/deep) so every spawned worker self-identifies its tier in the agent list.
- Install: documented one-line copy to `~/.codex/agents/` in README (no install script). Re-copy on plugin update; README states this.

### 3. Hook wiring
- `hooks/session-start` unchanged — output contract already matches Codex.
- Open mechanical item (plan-time verification, first task): whether Codex's plugin `hooks/hooks.json` schema and env-var (Claude uses `${CLAUDE_PLUGIN_ROOT}`) can coexist with the Claude schema in the same file.
  - Compatible → single shared `hooks/hooks.json`.
  - Collision → Codex wiring in a separate file (`hooks/codex-hooks.json` or documented `[hooks]` `config.toml` snippet); Claude file untouched.
- Acceptance either way: hook fires on Codex in a repo containing `docs/forge/`, emits the flow context; emits nothing elsewhere.

### 4. Harness-conditional execution
- `skills/planning/SKILL.md` Execution section: add one branch line — Workflow tool unavailable → read `codex-execution.md` in the skill directory. No other Execution-section changes.
- New `skills/planning/codex-execution.md`, contract:
  - **Sequential dispatch only**: one worker at a time, spawned by naming the tier agent (e.g. "Have forge-standard implement task N"), `Depends on` order enforced serially. No pipelining, no worktree isolation.
  - Briefs via `extract-brief.py`, review packets via `review-packet.py` — unchanged.
  - **Orchestrator no-work rule** (hard): during dispatched execution the orchestrator never opens or edits implementation files — dispatch, read one-paragraph reports, run acceptance commands, update the ledger. Catching yourself about to edit a source file = you owed a dispatch. A worker failing the 2-iteration rework cap escalates to the user with findings; the orchestrator never absorbs the work inline.
  - **Dispatch ledger**: plan-file checkboxes double as the worker tracker — on dispatch, annotate the task line with the worker nickname (`dispatched: forge-standard-2`); on completion, review outcome. Agent-list rows resolve to plan lines via the tier-prefixed nicknames.
  - **No lifecycle machinery**: worker accumulation/quota bugs are harness-side (openai/codex #19197, #22779); sequential dispatch minimizes accumulation; nothing else is built.
  - Review flow, proportional review, deferral rule, final review: same as the Execution section, executed sequentially.

### 5. README
- Codex install section: marketplace add, agent copy line, re-copy-on-update note, known harness caveats (subagent selection regression history, worker-list accumulation).

## Acceptance criteria

- Existing pytest suite passes; new manifest test passes.
- Claude Code behavior unchanged: plugin updates and loads, hook and skills work as before.
- Codex: plugin installs from the local marketplace; skills discoverable; SessionStart hook emits flow context only in forge-signal repos; after agent copy, all three tier agents spawnable by name with pinned model/effort and tier-prefixed nicknames.

## Risks / constraints

- Codex subagent surface is young: selection regressed in CLI v0.137.0 (fixed), `.codex/agents` visibility from tool-backed sessions has open issues. codex-execution.md wording must not assume spawn reliability — acceptance-command verification catches silent inheritance of wrong model.
- Model IDs will churn; mapping table is the single place to update.

## Changelog

2026-07-03: plugin renamed theforge → forge; manifest names, signal dir (docs/forge/, legacy accepted), and namespace refs updated throughout.
2026-07-13: execution substrate superseded by the codex exec runner spec (2026-07-13-codex-exec-runner-design.md) — deliverable 2 (TOML tier agents) retired, deliverable 4's in-session sequential dispatch, nickname pools, and checkbox-as-dispatch-ledger replaced by runner receipts; manifests, hook wiring, and README structure still owned here.
