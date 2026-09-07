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
[Claude Code vs. Codex CLI](#claude-code-vs-codex-cli) ·
[What it costs](#what-it-costs) ·
[Anatomy](#anatomy) ·
[Install](#install) ·
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

**Execution** sends each task to a worker agent matched to its tier —
cheapest model for trivial work, strongest for complex — pinned per agent so
a session-level model switch can't quietly downgrade a build. Workers do
strict TDD (failing test first, then code). Review scales with risk: trivial
tasks just pass their acceptance commands; bigger tasks get a real review
that **classifies** each finding — **fix** it, **defer** it (logged), or
**halt** for a human decision — and reworks until findings *converge* rather
than for a fixed number of rounds. A final review runs the same loop over the
whole diff. [**The execution loop**](docs/forge/execution-loop.md) is the
full explanation of this cycle, including why it's the same decision logic on
both harnesses.

**Why forge runs implementation serially.** Tasks dispatch one at a time on
both harnesses, even though fanning out independent tasks is technically
possible. Parallelism would only buy wall-clock time — forge's commit
discipline depends on a **linear** history, since each task's review base is
`git diff <prior commit>`, meaningful only across a clean chain of vertical
slices. Parallel writers break that chain and reintroduce the integration
mess per-task commits exist to prevent. Fan-out stays for **read-only** work
(research, independent review lenses); coding writes, and that stays serial.

**Project memory** is `docs/forge/constraints.md` plus GitHub issues.
`constraints.md` holds the rules that must not be broken right now —
CLI-authored, user-approved, and a snapshot rather than a log: a rule that
stops being true is deleted, not annotated. Phases of larger systems are
issues too — an epic per program, a sub-issue per phase, linked with native
sub-issue and blocked-by edges; open/closed is the only status forge reads.
Work that was skipped on purpose is also a GitHub issue, filed at a reviewed
close-out gate rather than a markdown entry — workers can defer nice-to-haves
if they stage the reason, they can't skip anything the spec requires.

## Claude Code vs. Codex CLI

The flow, gates, and tiers are identical on both harnesses — only the
execution substrate differs. Claude Code has native session infrastructure to
lean on; Codex CLI doesn't, so forge supplies it.

| | Claude Code | Codex CLI |
|---|---|---|
| Task dispatch | In-session, via the Workflow tool | External deterministic runner, `scripts/forge-run.py` — one `codex exec` process per task |
| Isolation between tasks | Native worktrees | Process boundary (no worktree; sequential on the working tree) |
| Halt visibility | Native session awareness | Foreground-only invocation — a halt relays into the conversation on non-zero exit |
| Commits | Native review/commit flow | Explicit per-task commits (`forge: task N — <title>`) written by the runner |
| Progress view | In-session | Optional live `rich` TUI (`sh .forge/watch`) reading run receipts |

Full Codex operational detail — invocation, `--status`, the monitor, resume
after a halt, and known Codex caveats — is in
[**Running on Codex**](docs/forge/running-on-codex.md).

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
| `skills/project-memory` | Formats and rules for constraints and GitHub issues (deferrals, programs, phases). |
| `agents/` | The three tier workers (forge-light, forge-standard, forge-deep), model pinned per harness. |
| `scripts/` — briefs & packets | `extract-brief.py` (plan+spec → worker brief) and `review-packet.py` (task block + diff → review packet). Stdlib; used by both harnesses. |
| `scripts/` — Codex runtime | `forge-run.py` (the deterministic plan runner — one `codex exec`/task, receipts, per-task commits), `forge-monitor.py` (the live `rich` TUI), and the `forge_*` / `forge_status` helper modules. Stdlib except the monitor, which needs `rich`. |
| `hooks/session-start` | Injects ~60 words of flow context, only in repos with `docs/forge/` (or legacy `docs/theforge/`, with a rename nudge). Silent everywhere else. |

## Install

### Claude Code

```bash
claude plugin marketplace add forcetrainer/forge
claude plugin install forge@forge
```

To update later: `claude plugin update forge@forge`.

**Channels.** One marketplace offers two, each pinned to a commit rather
than to whatever `main` happens to be:

| plugin | what you get |
| --- | --- |
| `forge` | the current release — install this one |
| `forge-beta` | the next version, feature-complete and being proven |

Install a different channel by name — `claude plugin install forge-beta@forge`.
A channel moves only when its pin is updated, so `main` advancing never
changes what you have installed. A prerelease says so in its version
(`0.12.1-beta`); the suffix goes away when the line is ready. To run `main`
itself, add your own checkout as a marketplace — `claude plugin marketplace
add /path/to/forge` — which follows the branch as a pinned channel never
will. The current pre-release is
[0.12.1-beta](https://github.com/forcetrainer/forge/releases/tag/forge--v0.12.1-beta),
which adds the review contract and halted-run resume on top of the beta
line's rework of project memory and specs; see its release notes for the
breaking changes.

### Codex CLI

```bash
codex plugin marketplace add /path/to/forge
codex plugin install forge@forge
```

The `SessionStart` hook works on Codex without extra wiring — the shared
`hooks/hooks.json` schema is compatible and Codex sets `CLAUDE_PLUGIN_ROOT`.
Plan execution then runs through forge's own runner, not in-session dispatch
— see [Claude Code vs. Codex CLI](#claude-code-vs-codex-cli) and
[Running on Codex](docs/forge/running-on-codex.md).

Contributing to forge itself (editing skills/agents/hooks) is covered in
[CONTRIBUTING.md](CONTRIBUTING.md).

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
