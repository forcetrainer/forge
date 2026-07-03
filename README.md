<div align="center">

# ⚒ forge

**Where code gets worked.**

A development process for working with coding agents,
packaged as a plugin for Claude Code and Codex CLI.

[![version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fforcetrainer%2Fforge%2Fmain%2F.claude-plugin%2Fplugin.json&query=%24.version&label=version&color=b45309)](https://github.com/forcetrainer/forge/blob/main/.claude-plugin/plugin.json)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-d97757)](https://code.claude.com/docs/en/plugins)
[![Codex CLI](https://img.shields.io/badge/Codex%20CLI-plugin-111111)](https://developers.openai.com/codex)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Why it exists](#why-it-exists) ·
[The flow](#the-flow) ·
[What it costs](#what-it-costs) ·
[Anatomy](#anatomy) ·
[Install](#install) ·
[Developing](#developing-editing-skills) ·
[Lineage](#lineage)

</div>

---

This is how I work with coding agents. It borrows from other tools and
methods, keeps what worked for me, and drops what didn't. I'm not claiming
it's the best way to do this. It's the way that fits how I build things.
Take whatever is useful.

## Why it exists

Coding agents write good code and make bad judgment calls. Working with
them, I kept hitting the same problems:

- **They make design decisions on their own.** Mid-task, the agent picks a
  dependency or a data model — a call I wanted to make — and it's buried in
  a diff I'm skimming.
- **They waste tokens.** Every session re-explores the codebase, workers
  report back with full transcripts, and plans stuffed with code get
  rewritten at execution time anyway.
- **They forget.** Decisions from one session get relitigated the next.
  Deferred work just disappears.
- **They can't scale the process down.** I once watched an agent spend 1.5
  hours setting up a test harness for a one-line edit to an HTML file.

forge puts a gate at each of those failure points and stays out of the way
otherwise.

## The flow

**Brainstorm → spec → plan → TDD execution**, with approval gates between
stages:

```mermaid
flowchart LR
    I([idea]) --> B[brainstorm]
    B -- "✋ design approved" --> S[["spec<br/>docs/forge/specs/"]]
    S --> P[["plan<br/>docs/forge/plans/"]]
    P -- "✋ routing approved" --> E[TDD execution]
    E -- "review + full suite" --> D([shipped])
```

Not everything gets the full flow. The routing question is whether a change
creates new structure or works within existing structure — size doesn't
matter:

```mermaid
flowchart TD
    Q{"creates new architecture?"}
    Q -- "no — mechanical edit" --> G1["⚙ gear 1 · just do it<br/>no process at all"]
    Q -- "no — delta to a spec'd system" --> G2["⚙⚙ gear 2 · conversational gate → TDD<br/>owning spec amended afterward"]
    Q -- "yes" --> G3["⚙⚙⚙ gear 3 · the full flow"]
```

One more boundary worth stating: the flow starts when I'm ready to build,
not when I'm thinking. Ideas get kicked around in free-form conversation
first — research, comparisons, half-formed notes — with no gates and no
process. The keepers land in `docs/forge/ideas/`, and when one is ready to
become a build, that doc feeds the brainstorm as its starting input. The
flow is for building, not ideating, and I don't force everything through it.

**Brainstorming** turns an idea into a spec through conversation. The agent
asks questions in small batches, proposes a few approaches with a
recommendation, and walks through the design section by section for
approval. The point is to make design decisions in conversation, where
changing course is cheap. Specs stay short and get amended in place as the
system changes.

**Planning** turns the spec into tasks: which files, what interfaces, what
tests, what counts as done. Plans never contain implementation code — code
written before there's compiler and test feedback gets written twice. Each
task is tagged trivial, standard, or complex based on what it actually
requires.

**Execution** sends each task to a worker agent matched to its tier:
`forge-light` (cheap model), `forge-standard`, or `forge-deep` (strongest
model). The model is pinned in the agent definition, so switching models at
the session level can't quietly downgrade a build. Workers do strict TDD
(failing test first, then code) and get their instructions from generated
brief files, so plan and spec content doesn't pile up in the orchestrator.
Review scales with risk: trivial tasks just have to pass their acceptance
commands, bigger tasks get a real review, a second reviewer only steps in if
the first finds problems, and after two fix rounds it comes back to me. A
final review covers the whole diff.

**Project memory** is three markdown files. `docs/forge/DECISIONS.md` holds
what was decided and why — it's read before new work, and logged decisions
are constraints. `DEFERRALS.md` holds work that was skipped on purpose.
`ROADMAP.md` tracks phases. Workers can skip nice-to-haves if they log it;
they can't skip anything the spec requires.

## What it costs

- **Approval gates mean waiting on me.** For throwaway code or prototyping
  that's pure overhead — skip the flow.
- **Repos fill up with docs.** The flow's memory is files in `docs/forge/`.
  If you don't want documentation as a working habit, this will feel like
  clutter.
- **It's built around how I work:** solo, long-lived projects, making the
  architecture calls myself. A team would draw the lines differently.

## Anatomy

| Piece | What it is |
|---|---|
| `skills/brainstorming` | Gear routing, then idea → design → spec through dialogue. Includes a browser-based visual companion for mockups. |
| `skills/planning` | Spec → plan (what/where, no code) → tiered execution. Codex execution notes in `codex-execution.md`. |
| `skills/tdd` | Red-green-refactor cut to its operational core. Test-harness creation is plan-level work, never a drive-by. |
| `skills/project-memory` | Formats and rules for ROADMAP / DECISIONS / DEFERRALS. |
| `agents/` + `codex/agents/` | The three tier workers, model pinned per harness (Claude: haiku/sonnet/opus; Codex: gpt-5.4-mini/gpt-5.4/gpt-5.5). |
| `scripts/` | `extract-brief.py` (plan+spec → worker brief), `review-packet.py` (task block + diff → review packet). Stdlib Python. |
| `hooks/session-start` | Injects ~60 words of flow context, only in repos with `docs/forge/` (or legacy `docs/theforge/`, with a rename nudge). Silent everywhere else. |

## Install

### Claude Code

```bash
claude plugin marketplace add forcetrainer/forge
claude plugin install forge@forge
```

To update later: `claude plugin update forge@forge` (or `git pull` in a
local clone).

### Codex CLI

```bash
codex plugin marketplace add /path/to/forge
codex plugin install forge@forge
```

The `SessionStart` hook works on Codex without extra wiring — the shared
`hooks/hooks.json` schema is compatible and Codex sets `CLAUDE_PLUGIN_ROOT`
for plugin-hook compatibility.

Then copy the tier agents to your Codex config (they can't ship inside the
plugin):

```bash
cp codex/agents/*.toml ~/.codex/agents/
```

**On plugin update:** re-run the copy command; the `.toml` files are synced
manually by design.

<details>
<summary><strong>Known Codex caveats</strong></summary>

- Subagent selection has known regressions (custom-agent selection broke in
  v0.137.0 and spawned agents silently inherited the parent model). If
  spawned agents run the wrong model, check acceptance-command output rather
  than trusting the spawn.
- Spawned subagents pile up in the CLI's agent list, and completed workers
  keep counting against the thread limit
  ([openai/codex#19197](https://github.com/openai/codex/issues/19197),
  [openai/codex#22779](https://github.com/openai/codex/issues/22779)).
  Sequential dispatch (`skills/planning/codex-execution.md`) keeps the pile
  small; forge doesn't build cleanup machinery for a harness bug.

</details>

## Developing (editing skills)

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

This repo uses its own conventions: decisions live in
`docs/forge/DECISIONS.md` (read it before changing skill behavior), skipped
work in `docs/forge/DEFERRALS.md`. The `docs/forge/` directory also opts
this repo into its own session hook.

## Lineage

forge started as a fork of [superpowers](https://github.com/obra/superpowers)
v5.1.0, and the skeleton is still visible: discipline packaged as skills,
brainstorm → plan → execute, TDD throughout. It has diverged a long way
since. Plans no longer embed implementation code, review is proportional
instead of three reviewers per task, the 800-word every-session hook is
gone, and the gears, tier routing, project memory, brief/packet scripts,
and Codex packaging don't exist upstream. The full accounting of what was
kept and cut is in `docs/notes/superpowers-assessment.md`.

Before superpowers, [BMAD](https://github.com/bmadcode/BMAD-METHOD) taught
me what deliberate agentic coding looks like. The mechanics here are
different, but the habits stuck: documentation as working memory, markdown
files as the record of in-flight work, and TDD as the baseline.

The rest is pragmatism about the harness itself: when Claude Code grew a
native feature (worktrees, code review, verification, subagents), the
matching skill got deleted instead of maintained in parallel.

---

<div align="center">
<sub>Built for me, shared in case it's useful to you. <a href="LICENSE">MIT</a>.</sub>
</div>
