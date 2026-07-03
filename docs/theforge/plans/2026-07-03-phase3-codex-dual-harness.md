# Phase 3: Codex Dual-Harness Packaging Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Make theforge installable on Codex CLI from this repo alongside Claude Code, sharing skills/scripts/hook-script verbatim.
**Architecture:** Harness divergence isolated to four surfaces: Codex manifests (`.codex-plugin/`, `.agents/plugins/`), TOML tier agents (`codex/agents/`), hook wiring config, and one execution reference file (`skills/planning/codex-execution.md`) loaded only when the Workflow tool is absent. Everything else is shared.
**Tech stack:** JSON manifests, TOML agent definitions, bash hook script (existing), pytest + Python stdlib (`json`, `tomllib`) for validation tests.
**Global Constraints:** Python ≥3.11 (tomllib), stdlib only — no new dependencies. Plugin versions in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` stay in lockstep. No Claude Code behavior changes: existing files untouched except one branch line in `skills/planning/SKILL.md`, README additions, and the lockstep version bump. Spec: docs/theforge/specs/2026-07-03-phase3-codex-dual-harness-design.md.

### Task 1: Codex plugin + marketplace manifests, manifest tests
- [ ] Done

**Files:**
- Create: `.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`
- Test: `tests/test_manifests.py`

**Spec:** Verified harness contracts, Codex manifests

**Interface:** `.codex-plugin/plugin.json`: `{"name": "theforge", "version": <lockstep semver>, "description": <match .claude-plugin description>}`. `.agents/plugins/marketplace.json`: `{"name": "theforge", "interface": {"displayName": "theforge"}, "plugins": [{"name": "theforge", "source": {"source": "local", "path": "./"}}]}`.

**Tests:** all four manifest files (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`) parse as JSON; both plugin manifests carry identical `version`; Codex plugin manifest has required `name`/`version`/`description` with kebab-case name and semver version; marketplace `source.path` is `./`-prefixed.

**Acceptance:** `python3 -m pytest tests/test_manifests.py -v` — all pass. `python3 -m pytest tests/ -v` — no regressions.

**Tier:** standard

**Depends on:** nothing

### Task 2: TOML tier agents
- [ ] Done

**Files:**
- Create: `codex/agents/forge-light.toml`
- Create: `codex/agents/forge-standard.toml`
- Create: `codex/agents/forge-deep.toml`
- Test: `tests/test_codex_agents.py`

**Spec:** Verified harness contracts, Tier agents

**Interface:** each TOML: `name` (matches filename stem), `description` (from corresponding `agents/*.md` frontmatter description), `developer_instructions` (body text of corresponding `agents/*.md`, below frontmatter, verbatim), `model` + `model_reasoning_effort` per spec mapping table (light: gpt-5.4-mini/low, standard: gpt-5.4/high, deep: gpt-5.5/xhigh), `nickname_candidates = ["forge-<tier>-1" … "forge-<tier>-5"]`.

**Tests:** each TOML parses via `tomllib`; required fields present; model/effort match the spec mapping exactly; nickname candidates are unique, non-empty, tier-prefixed, and ASCII letters/digits/spaces/hyphens/underscores only; `developer_instructions` equals the corresponding `agents/*.md` body (frontmatter stripped, whitespace-normalized) — the sync-divergence-is-a-bug rule as a test.

**Acceptance:** `python3 -m pytest tests/test_codex_agents.py -v` — all pass. `python3 -m pytest tests/ -v` — no regressions.

**Tier:** standard

**Depends on:** nothing

### Task 3: Codex hook wiring
- [ ] Done

**Files:**
- Modify: `hooks/hooks.json` (only if Codex schema verified compatible — else untouched)
- Create: `hooks/codex-hooks.json` (only if schemas collide)
- Test: `tests/test_manifests.py` (extend: hook config JSON validity)

**Spec:** Verified harness contracts, Hook wiring

**Interface:** verification first — fetch https://developers.openai.com/codex/hooks and https://developers.openai.com/codex/plugins/build; determine (a) Codex plugin hook-config schema at `hooks/hooks.json`, (b) Codex's plugin-root env var (Claude uses `${CLAUDE_PLUGIN_ROOT}`). Compatible schema and env var → single shared `hooks/hooks.json`; any collision → `hooks/codex-hooks.json` with Codex SessionStart wiring pointing at the shared `hooks/session-start` script, Claude file byte-identical to current. `hooks/session-start` is not modified in either outcome. Record which outcome held in the task report.

**Tests:** every `hooks/*.json` parses as JSON; Claude wiring retains `SessionStart` → `session-start` command path; if `codex-hooks.json` exists it references the same script.

**Acceptance:** `python3 -m pytest tests/test_manifests.py -v` — all pass. `git diff hooks/session-start` — empty.

**Tier:** standard

**Depends on:** Task 1 (extends `tests/test_manifests.py`)

### Task 4: Harness-conditional execution reference
- [ ] Done

**Files:**
- Create: `skills/planning/codex-execution.md`
- Modify: `skills/planning/SKILL.md` (one line added to Execution section, immediately after the routing table paragraph: Workflow tool unavailable → read `codex-execution.md` in this skill directory; no other changes)

**Spec:** Harness-conditional execution

**Interface:** `codex-execution.md` covers, in telegraphic skill style: sequential dispatch only (one worker at a time, spawn by naming the tier agent, `Depends on` serial, no pipelining/worktree isolation); briefs via `extract-brief.py` and review packets via `review-packet.py` unchanged; orchestrator no-work hard rule (never opens/edits implementation files; about-to-edit = owed dispatch; 2-iteration rework cap escalates to user, never absorbs inline); dispatch ledger (plan checkboxes annotated `dispatched: <nickname>` on spawn, review outcome on completion); no lifecycle machinery (harness-side bugs, sequential dispatch is the mitigation); review flow/proportional review/deferral rule/final review same as Execution section, sequentially.

**Tests:** none (skill prose — no runtime surface).

**Acceptance:** `grep -c "codex-execution.md" skills/planning/SKILL.md` returns 1; `git diff skills/planning/SKILL.md` shows exactly one added line; every contract item above present in `codex-execution.md` (reviewer checklist against spec §4).

**Tier:** standard

**Depends on:** nothing

### Task 5: README Codex section + lockstep version bump
- [ ] Done

**Files:**
- Modify: `README.md` (new Codex install section)
- Modify: `.claude-plugin/plugin.json` (version → 0.5.0)
- Modify: `.codex-plugin/plugin.json` (version → 0.5.0)

**Spec:** README

**Interface:** README section covers: marketplace add from this repo, one-line agent copy (`cp codex/agents/*.toml ~/.codex/agents/`), re-copy on plugin update note, known Codex caveats (subagent-selection regression history, worker-list accumulation — issues #19197/#22779), hook wiring outcome from Task 3 (including `[hooks]` config.toml snippet only if Task 3 landed on the collision path).

**Tests:** version-lockstep test from Task 1 covers the bump.

**Acceptance:** `python3 -m pytest tests/ -v` — full suite passes.

**Tier:** trivial

**Depends on:** Task 1, Task 2, Task 3, Task 4
