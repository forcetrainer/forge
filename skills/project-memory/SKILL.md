---
name: project-memory
description: Use when logging a decision made about the system, recording deferred work, or updating the project roadmap — and as the format reference for docs/forge/ROADMAP.md, DECISIONS.md, and deferral issues (via scripts/forge_memory.py).
---

# Project Memory

Two append-friendly files under `docs/forge/` (DECISIONS.md, ROADMAP.md) plus GitHub issues for deferred work give the project durable memory across sessions. Entries are terse, newest first. Create each markdown file on its first entry — no empty scaffolds. Commit memory updates with the work that produced them.

## DECISIONS.md — read before building, written when decided

One entry per decision about the system:

```markdown
## 2026-06-10 — Use SQLite for local persistence
**Why:** Single-user app, no server; simplest thing that supports the query needs.
**Where:** docs/forge/specs/2026-06-10-storage-design.md
```

Log when: an approach is chosen during brainstorming, a design decision is locked during planning, or a decision crystallizes ad-hoc mid-session ("let's log that").

**Read before any feature build.** New work must not contradict logged decisions. On conflict, surface it to the user — never silently override. Reversing a decision gets a new entry that names the one it supersedes.

## Deferrals — what we consciously didn't do

Deferrals are **GitHub issues**, not a file. `docs/forge/DEFERRALS.md` is
retired as a write target and as a live read path — nothing writes it, and
nothing reads it as the record of deferred work. Record and query deferrals
through `scripts/forge_memory.py`:

```bash
forge_memory.py defer --title "Skipped retry backoff on the sync client" \
  --why "Single-user, local network; failures are rare and manual retry is fine." \
  --from "docs/forge/plans/2026-06-10-sync.md, Task 3" --follow-up backlog
forge_memory.py list-deferrals
```

`title` (≤80 chars), `why` (≤300 chars), `from` (plan path plus the stage
that produced it — `, Task N`, or `, final review` for a finding the
plan-level final review raised — or `user`), `follow-up` (`backlog` — the issue stays open — or `drop`, or
`revisit-when:<condition>`; `roadmap` is retired along with `ROADMAP.md`'s
role as a deferral destination) are the record's fields; budget overrun is a
hard error, not a truncation.

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

Statuses: `planned | in-progress | done | deferred`. Planning marks a phase `in-progress` at kickoff and `done` at completion. Deferrals with `roadmap` follow-up add a `deferred` line.

## Legacy

A repo with `docs/superpowers/` and no `docs/forge/`: offer a one-time `git mv docs/superpowers docs/forge` rather than reading both paths forever.
