# Living Specs — ARCHIVE

**Historical. Not authoritative. No new entries.**

Superseded by [`docs/forge/specs/pipeline.md`](../../specs/pipeline.md), which describes this
system as it is now. This document is kept for provenance: it records what was
specified at the time, not what is true today.

Phase 5 of the structured-memory program (issue #47), and its last. Specs stop being
dated per-change snapshots and become per-system documents amended in place. The
sixteen dated specs merge into four; the originals are archived, not deleted.

This spec is itself dated, and is migrated by its own migration — see Self-migration.

## Motivating defect

`specs-amend-in-place` has been a constraint since Phase 1, but the filename
convention contradicts it: a document named for the date it was written invites a
second document the next time, and sixteen of them accumulated. Nothing says which
of the seven specs touching the execution loop is current, so a reader either reads
all seven in date order and reconstructs the state themselves, or reads one and gets
a superseded answer. The date in the name is the defect.

## Systems

Four, each one document, named `docs/forge/specs/<system>.md`. The `-design` suffix
is dropped — redundant inside `specs/`.

| system | absorbs |
|---|---|
| `execution` | phase7 scope-autonomy, phase10 codex-inline, phase11 inline-finding-process, phase12b claude-dispatch-parity, tier-policy-recalibration, halt-precision, review-continuity |
| `project-memory` | project-memory-engine, deferrals-as-issues, constraints, retire-roadmap |
| `codex-runner` | phase3 codex-dual-harness, codex-exec-runner, forge-run-monitor |
| `pipeline` | phase1 pipeline-skill-edits, phase2 execution-efficiency, this spec (see Self-migration) |

Four is the whole set. A fifth document requires a genuinely new system, not a new
change to an existing one.

## Frontmatter

YAML, at the top of every living spec, exactly two keys:

```yaml
---
system: execution
supersedes:
  - archive/specs/2026-07-16-phase7-scope-autonomy-design.md
  - archive/specs/2026-08-21-halt-precision-design.md
---
```

- `system` — kebab id, equal to the filename stem. The equality is what makes the id
  unforgeable: there is no second place to declare identity, so it cannot drift.
- `supersedes` — paths of the dated specs this document answers for, relative to
  `docs/forge/`.
  Provenance for the merge, and the list a reader audits it against. Absent (not an
  empty list) on a spec that supersedes nothing.

No date field. A document that records when it was last touched grows a second
history competing with git and the changelog.

## Merge method — anchor and fold

Per system: take the newest source spec as the **anchor** — each later phase was
written as a delta against its predecessors, so the newest already describes current
state — then fold in from the older sources only the claims still true. A claim
contradicted by a later spec is dropped; a claim a later spec restates is kept once,
in the anchor's words.

The merged document describes **what is true now**. It carries no phase numbers, no
"Phase 7 changed X to Y", no narrative of how the system arrived. That history is in
the archive, the changelog, and git.

### Section accounting

The merge is lossy and nothing mechanical catches loss, so accounting is manual and
required. For each system, `grep '^## ' <source specs>` is the inventory. Every
section in it is either:

- **present** — its content lives in the merged document, under any heading, or
- **dropped** — named in the merged document's changelog with the reason it is no
  longer true.

A section that is neither is a defect. This is the acceptance criterion for every
migration task; it is a list a reviewer walks, not a judgment call.

## Changelog

Every living spec ends with `## Changelog`. One line per amendment:

```
2026-09-05: <what changed> (<issue, PR, or commit>)
```

Cross-spec amendment — a change to system A that alters what B asserts:

```
2026-09-05: amended by [execution] — coverage is discovery-only (#46)
```

The bracketed id names a system that exists. This is the notation that makes a
cross-system change visible from the amended document, rather than only from the
changing one.

## Lint

Five rules, in `scripts/forge_lint.py`, applied to a living spec. No sixth.

1. The filename carries no `YYYY-MM-DD` prefix.
2. Frontmatter parses, and `system` equals the filename stem.
3. Every `supersedes` path resolves to a file that exists.
4. `## Changelog` is present, and every entry matches `YYYY-MM-DD: <text>`.
5. Every `amended by [<id>]` names a system that exists.

Applied to the spec named by a plan at run start, as today, plus a corpus mode —
`forge_lint.py --specs` — that lints every file in `docs/forge/specs/`. Frontmatter is parsed without a YAML library — the two keys
are a fixed grammar, and `stdlib-only` binds.

A dated spec under `archive/` is never linted: the archive is frozen and its
documents are not living specs.

## Citations

Live references to dated spec paths are repointed to the living document. `plans/`
and `archive/` are **not** repointed — both are frozen records, and rewriting a
record to match a later reorganization is the drift this program exists to remove.

The live set:

- `docs/forge/constraints.md` — two `Source:` fields, moved through
  `forge_memory.py update-constraint`; the file is hook-protected and a direct edit
  is denied, which is the mechanism working, not an obstacle.
- `docs/forge/execution-loop.md`, `docs/forge/running-on-codex.md`
- `scripts/forge_common.py` (3), `scripts/forge-run.py` (1)
- `tests/test_forge_lint.py` (2)
- the cross-spec reference in the deferrals-as-issues spec, which the merge dissolves

## Skill and convention changes

`skills/brainstorming/SKILL.md` currently instructs every forge user to write
`docs/forge/specs/YYYY-MM-DD-<topic>-design.md`. It becomes: amend the owning
system's spec in place; create `docs/forge/specs/<system>.md` only for a genuinely
new system. This changes the convention for every repo running forge, not only this
one — the widest-reach edit in the phase.

`skills/planning/SKILL.md` and `skills/project-memory/SKILL.md` follow where they
name the dated form.

## Self-migration

This spec is written under the outgoing convention and is migrated by the work it
specifies: at completion it is folded into `pipeline.md` (the spec-and-plan document
contracts are pipeline's system) and archived with the rest. A living spec named
`living-specs.md` would be a document about document conventions, which is
`pipeline`'s subject, not a fifth system.

## Testing

- Frontmatter with a `system` that disagrees with the filename stem fails lint.
- A `supersedes` path that does not resolve fails lint.
- A missing `## Changelog`, and a malformed entry, each fail lint.
- `amended by [nosuchsystem]` fails lint; a real id passes.
- A dated filename fails lint; the same content renamed passes.
- A spec under `archive/` is not linted, even with a dated name and no frontmatter.
- Corpus mode reports every offending spec in one run, not the first only.
- Frontmatter parsing uses no third-party import.
- Full suite green.

## Acceptance

- `docs/forge/specs/` contains exactly four files: `execution.md`,
  `project-memory.md`, `codex-runner.md`, `pipeline.md`.
- Each carries frontmatter whose `system` matches its stem and whose `supersedes`
  paths all resolve.
- Every `##` section of every archived source is present in its merged document or
  named in that document's changelog as dropped, with a reason.
- The sixteen dated specs are under `docs/forge/archive/specs/` with the
  non-authoritative header.
- No live file outside `plans/` and `archive/` cites a dated spec path.
- `skills/brainstorming/SKILL.md` instructs amendment in place and the
  `<system>.md` form.
- Both plugin manifests read `0.12.0`.

## Out of scope

Rewriting `plans/` or the archive to the new convention; any migration tooling for
other repos' dated specs; a fifth system; splitting review out of `execution`
(considered and declined — the review contract is part of the loop it gates).
