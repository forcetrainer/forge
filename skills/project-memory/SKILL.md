---
name: project-memory
description: Use when authoring a constraint, recording deferred work, or updating the project roadmap — and as the format reference for docs/forge/constraints.md, ROADMAP.md, and deferral issues (via scripts/forge_memory.py).
---

# Project Memory

One append-friendly file under `docs/forge/` (ROADMAP.md), one CLI-managed file (constraints.md), plus GitHub issues for deferred work give the project durable memory across sessions. Entries are terse, newest first where order applies. Create each markdown file on its first entry — no empty scaffolds. Commit memory updates with the work that produced them.

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

## ROADMAP.md — phases of larger systems

Created **only** when brainstorming decomposes work into multiple sub-projects or phases. One line each:

```markdown
- [in-progress] Phase 2: Sync engine — bidirectional sync with conflict log ([spec](specs/...), [plan](plans/...))
- [planned] Phase 3: Sharing — read-only share links
```

Statuses: `planned | in-progress | done | deferred`. Planning marks a phase `in-progress` at kickoff and `done` at completion.

## Legacy

A repo with `docs/superpowers/` and no `docs/forge/`: offer a one-time `git mv docs/superpowers docs/forge` rather than reading both paths forever.
