---
name: project-memory
description: Use when authoring a constraint, recording deferred work, or filing a program or phase — and as the format reference for docs/forge/constraints.md and GitHub issues (via scripts/forge_memory.py).
---

# Project Memory

One CLI-managed file (constraints.md) plus GitHub issues — for deferred work and for programs/phases — give the project durable memory across sessions. Entries are terse, newest first where order applies. Create constraints.md on its first entry — no empty scaffold. Commit memory updates with the work that produced them.

## constraints.md — what must not be broken, right now

A constraint is a rule that, if broken, produces a **defect** — not a preference, not a record of a choice made. `docs/forge/constraints.md` is a snapshot of what is true **now**, never a log: a rule that stops being true is deleted, not annotated. A stale constraint is worse than a missing one, because an agent obeys it into a conflict.

A decision — "we chose X over Y because Z" — is **not** a constraint. Rationale lives in PR bodies and spec changelogs; there is no decision log.

The file is authored **only** through `scripts/forge_memory.py`'s `add-constraint` / `update-constraint` / `retire-constraint` / `list-constraints` subcommands — direct edits are denied by a PreToolUse hook. Every subcommand builds a record from typed flags; there is no free-form body argument.

Fields, all required unless noted: `id` (kebab-case, ≤40 chars), `rule` (≤200 chars, imperative), `scope` (≤80 chars, defaults to `repo`), `because` (≤300 chars), `source` (≤120 chars — an issue, a PR, a spec path, or, for a rule that predates this file, an archive entry such as `docs/forge/archive/DECISIONS.md 2026-07-11`. An archive entry is **historical provenance, not a live authority** — it says where the rule was first written down; the archive is explicitly non-authoritative and takes no new entries). Budget overrun is a hard error, never a truncation.

**Operator rule — not enforced by anything:** creation and update require user approval. Nothing in the system checks this; the deny hook above blocks a direct file edit and points the agent straight at the CLI, which is ungated. Agents may propose a constraint at an approval gate; no forge stage writes one unattended.

There is a soft cap of 12 constraints: crossing it prints a notice listing the current ids and never refuses — the value of the file comes from staying short enough to re-read every session.

`hooks/session-start` reads `constraints.md` and supplies its rules as session context automatically — no skill needs to read the file itself to surface it to the user.

## Deferrals — what we consciously didn't do

Deferrals are **GitHub issues**, not a file. `docs/forge/DEFERRALS.md` is
retired as a write target and as a live read path — nothing writes it, and
nothing reads it as the record of deferred work. File deferrals through
`scripts/forge_memory.py`; **read** them in the GitHub issue list, which is
what it is for — the CLI files and closes, it never lists issues back:

```bash
forge_memory.py defer --title "Skipped retry backoff on the sync client" \
  --why "Single-user, local network; failures are rare and manual retry is fine." \
  --from "docs/forge/plans/2026-06-10-sync.md, Task 3" --by agent
forge_memory.py resolve --ref 123 --reason "fixed in Phase 4"
```

`title` (≤80 chars), `why` (≤300 chars), and `from` (plan path plus the
stage that produced it — `, Task N`, or `, final review` for a finding the
plan-level final review raised — or `user`) are the record's whole schema;
budget overrun is a hard error, not a truncation. There is no disposition
field: a deferral **is** an open issue nobody is working on, so "backlog"
would only restate that, "drop" means don't file it, and "revisit when X"
is a comment on the issue.

**Labels.** Six, every one meaningful to a person:

- **Kind** — exactly one, required: `feature` | `defect` | `debt` | `risk`.
  Set by a **human**, in the GitHub UI. `defer` never applies one and has
  no flag for it — whether something is a defect or debt is a judgment the
  engine cannot make, and a guess reads as a judgment somebody made.
- **Origin** — exactly one, required: `by:human` | `by:agent`. This is
  `--by`, which is required on every `defer` and never defaulted. The
  label is created in the repo on first use if it is missing.

Which phase is deliberately **not** a label: `from:` already names the plan
and the task, which is finer and cannot drift from it.

**Agency rule:** during execution, agents may defer **non-spec scope only**
— nice-to-haves, refactors, edge polish they judge out of scope. Anything
the spec requires is never silently deferred; it surfaces at the review gate
instead. Findings an agent judges deferrable are **staged**, not filed,
until the **close-out review gate** — after the final review passes and the
suite is green, every staged deferral is presented to the user, who accepts,
edits, or drops each; only accepted ones are filed. A deferral the user asks
for mid-session files **immediately**, with `from: user`, no gate. List
filed deferrals' issue numbers in the end-of-plan summary.

## Programs and phases

Filed **only** when brainstorming decomposes work into multiple sub-projects or
phases. A program is a GitHub epic; each phase is one of its sub-issues, linked
by two native edges — sub-issue (phase belongs to program) and blocked-by
(phase N+1 is blocked by phase N). Both edges are real GitHub links, not text.

```bash
forge_memory.py add-program --name "Sync engine" \
  --why "Bidirectional sync needs staged rollout." --kind feature
forge_memory.py add-phase --epic 210 --seq 1 --of 3 \
  --title "Conflict log" --why "Foundation the rest of sync builds on." \
  --kind feature
```

`add-program` takes `--name`, `--why` (≤300 chars), and `--kind` — one of
`feature | defect | debt | risk`, required, no default: the engine never
infers what kind of work something is. `add-phase` takes `--epic` (the
program's issue number), `--seq` (1-based, must equal the epic's current
sub-issue count plus one — out-of-order filing is a caller error, never
reordered silently), `--of` (total phase count, for the title only), `--title`,
`--why`, and the same required `--kind`. `add-phase` only files new phases; it
never adopts issues that already exist.

A phase's issue title renders as `<program name> <seq>/<of>: <title>` —
nothing parses the sequence back out of it; the render happens once, at
filing. Origin is unconditionally `by:human` — no flag, because filing a
program or phase is a human decision, unlike a `defer`, which either harness
can call.

**Status is not forge's.** Open/closed is the only status forge reads or
writes — no `planned | in-progress | done`. Ordering and priority live on the
human's GitHub Projects board, which forge never calls. Planning closes the
phase issue with a reason at plan completion; it never marks a phase
`in-progress` at kickoff.

## Legacy

A repo with `docs/superpowers/` and no `docs/forge/`: offer a one-time `git mv docs/superpowers docs/forge` rather than reading both paths forever. A `ROADMAP.md` found in an existing repo is an unread file — its phases belong in issues, not a migration script.
