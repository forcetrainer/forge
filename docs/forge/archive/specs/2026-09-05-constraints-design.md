# Constraints — ARCHIVE

**Historical. Not authoritative. No new entries.**

Superseded by [`docs/forge/specs/project-memory.md`](../../specs/project-memory.md), which describes this
system as it is now. This document is kept for provenance: it records what was
specified at the time, not what is true today.

Phase 3 of the structured-memory program (issue #45). Retires `docs/forge/DECISIONS.md`;
binding rules move to `docs/forge/constraints.md` under the Phase 1 record engine.

Depends on Phase 1 (`2026-09-05-project-memory-engine-design.md`) — the CLI, budgets,
`PreToolUse` deny, lint check and canonical re-render already exist and are unchanged.

## What a constraint is

A rule that, if broken, produces a **defect**. Not a preference, not a record of a
choice. `constraints.md` is a snapshot of what is true **now**, never a log.

A decision — "we chose X over Y because Z" — is not a constraint. The current
`hooks/session-start` asserts *"logged decisions are constraints"*; that conflation is
what this phase removes.

## Authoring tests

A candidate must pass all six. Test 0 governs.

0. **It is true right now.** If it stops being true it is deleted, not annotated. A
   stale constraint is worse than a missing one: an agent obeys it into a conflict.
1. **It is a rule, not a choice.** Phrasable as an imperative. "We chose X over Y" is
   rationale — PR body.
2. **Violating it produces a defect, not a difference.** If breaking it yields
   something merely other, it is taste.
3. **Nothing already enforces it.** If a test, linter, or type enforces the rule, the
   code is the constraint; a prose copy is a second source that drifts.
4. **It is non-obvious.** It captures what a competent agent would otherwise get wrong.
5. **Scope is honest.** A rule binding only one harness says so.

## Record

| field | form | required |
|---|---|---|
| `id` | kebab-case, ≤40, unique | yes |
| `rule` | ≤200, one sentence, imperative | yes |
| `scope` | ≤80 — path glob or subsystem | yes; CLI defaults to `repo` |
| `because` | ≤300 — what breaks if ignored | yes |
| `source` | ≤120 — issue, PR, or spec path | yes |

- `because` is not decoration: an agent that does not understand a rule routes around it
  while technically complying.
- `source` is read-time material. A constraint written in Phase 1 and applied in Phase 4
  may need its originating code or spec to be understood.
- The Phase 1 `added` field is **removed**. It serves nothing at read time and there is
  no retirement deliberation to support.

## CRUD

```
forge_memory.py add-constraint    --id <slug> --rule <text> --because <text>
                                  --scope <glob> --source <ref>
forge_memory.py update-constraint --id <slug> [--rule <text>] [--because <text>]
                                  [--scope <glob>] [--source <ref>]
forge_memory.py retire-constraint --id <slug>
forge_memory.py list-constraints  [--scope <glob>] [--json]
```

- `update-constraint` changes only the named fields and validates the whole record
  afterward. `id` is the key and cannot be changed — a renamed constraint is a retire
  plus an add, which is honest, since every citation pointing at the old id breaks. Editing in place is the normal operation, because the file holds only what
  is currently true — rewording a rule is sharpening it, not deleting and replacing it.
- Composition from typed arguments only. No free-form body argument on any subcommand.
- Creation and update require user approval. Agents may propose at a gate; no forge
  stage writes a constraint unattended.

- `constraints.md` is created on its first entry. No empty scaffold.

## Soft cap

Twelve. Crossing it prints a note listing the current set; it never refuses. The cap is
not housekeeping — smallness is what keeps test 0 real. A file short enough to re-read
at every session start is one where a rule that stopped being true gets noticed.

## Session-start hook

`hooks/session-start` stops asserting that logged decisions are constraints, and
supplies the constraints themselves.

**Out of scope:** when the hook fires, and whether it should claim a repo uses the forge
flow on directory presence alone. That is issue #49's routing fix, with its own design.
This phase changes only what the hook supplies.

## Citations

~33 in-code citations of the form `(DECISIONS 2026-07-16)` across 14 files, resolving to
~8 distinct decisions. Each is repointed to the most specific durable target:

| what the comment invokes | cite |
|---|---|
| a rule spanning systems | the constraint id |
| a contract for this system | the owning spec |
| historical why | the PR |
| nothing durable | no citation |

Expected outcome: most become spec references. That is correct — it keeps
`constraints.md` small.

## Rationale after the freeze

PR bodies and spec changelogs. The "log the decision" step is **deleted** from
brainstorming and planning; `project-memory`'s DECISIONS section goes with it.

Consequence, accepted: there is no decision log afterward. "Why did we choose X" is a
PR search, not a file read.

## Migration

- Read all 37 entries; surface only what passes all six tests, with rejects grouped by
  the test they failed.
- Author the survivors through the CLI, user-approved in one review pass.
- `git mv docs/forge/DECISIONS.md docs/forge/archive/DECISIONS.md` with a header:
  historical, not authoritative, no new entries.
- `docs/forge/DECISIONS.md` does not exist when the phase completes.

If the surviving set approaches fifteen, the authoring tests are too loose — not the cap
too low.

## Testing

- `update-constraint` changes only named fields; the unnamed ones survive byte-identical.
- `update-constraint` on an unknown id exits non-zero naming the id; the file is untouched.
- A budget overrun on update is rejected and leaves the file unchanged.
- `scope` defaults to `repo` when omitted and is never empty in a rendered record.
- `added` is absent from the schema; a record carrying it fails to parse.
- `source` is required; omitting it exits non-zero.
- Crossing the soft cap prints a note and still writes the record.
- Repo-wide grep: no live reference to `DECISIONS.md` as a write target or read path.
- Full suite green.

## Acceptance

- `docs/forge/DECISIONS.md` absent; `docs/forge/archive/DECISIONS.md` present with header.
- `constraints.md` exists, holds only user-approved records, and passes `fmt --check`.
- No skill, script, hook, or doc instructs anyone to log a decision to a file.
- `hooks/session-start` supplies constraints and makes no claim about decisions.
- Every repointed citation names a constraint id, a spec, or a PR that exists.

## Out of scope

`ROADMAP.md` retirement, issue reconciliation in brainstorming, `audit-issues` and the
issue-taxonomy standard (Phase 4); spec de-dating (Phase 5); the session-start
jurisdiction fix (#49).
