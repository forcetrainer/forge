# Retire ROADMAP.md — ARCHIVE

**Historical. Not authoritative. No new entries.**

Superseded by [`docs/forge/specs/project-memory.md`](../../specs/project-memory.md), which describes this
system as it is now. This document is kept for provenance: it records what was
specified at the time, not what is true today.

Phase 4 of the structured-memory program (issue #46). Deletes the last flat memory
file as a live read path. Phase decomposition becomes GitHub issues carrying native
parent/child and blocked-by edges; status becomes issue state plus the human Projects
board. Adds `audit-issues`, deferred here from Phase 2.

## Motivating defect

`ROADMAP.md` carries three facts per phase — identity, order, status — and is the
only one of the three that no other system can check. Status drifts first: a phase
line reads `in-progress` for as long as nobody remembers to edit it, while the work
it names is finished or abandoned. Identity and order duplicate what the issue list
and the board already hold, so the file is a second copy free to drift from both.

## Programs and phases

A **program** is an epic issue. A **phase** is an issue that is a real GitHub
sub-issue of that epic, blocked by its predecessor. Both are ordinary issues: same
six-label taxonomy, same origin rule, no forge-only marker.

Two subcommands on `scripts/forge_memory.py`, both GitHub-only (`GitHubStore`):

```
add-program --name <≤80> --why <≤300> --kind feature|defect|debt|risk
add-phase --epic <n> --seq <int> --of <int> --title <≤80> --why <≤300> --kind <…>
```

- `add-program` creates the epic — its issue title is `--name` verbatim, with no
  rendering — and prints the issue number to stdout, alone on the last line, so a
  caller can pass it straight to `--epic`.
- `add-phase` renders the title as `<program name> <seq>/<of>: <title>`, where the
  program name is read from the epic issue's own title. Nothing parses a sequence
  back out of a title; the rendered prefix is display only and is never an input.
- Edges, in this order, each a `gh api` POST: `issues/<epic>/sub_issues` to attach
  the phase, then `issues/<new>/dependencies/blocked_by` naming the epic's previously
  last sub-issue. A first phase (`--seq 1`) gets no blocked-by edge.
- Ordering is insertion order. `add-phase` reads the epic's sub-issues and requires
  `seq == len(sub_issues) + 1`; any other value exits non-zero naming both numbers.
  Out-of-order filing is a caller error, never reordered silently.
- `--kind` is required with no default. `--of` is rendering only and is not
  validated against sibling titles.
- Origin is `by:human` unconditionally, with no flag. A decomposition exists only
  because a human approved it at a brainstorming gate; the value is structurally
  fixed rather than defaulted, which is why this does not reopen `defer`'s
  required-`--by` rule.

Records go through the existing schema table: two new types, `program` and `phase`,
with per-field character budgets enforced as hard errors like every other record.

### Partial failure

Issue creation and each edge are separate network calls with no transaction. On a
failure after the issue exists, the command exits non-zero, names the call that
failed, and prints the created issue number and which edges did land — never a bare
traceback and never a silent retry. The user completes it by hand or re-runs the
missing edge; no repair subcommand ships.

## Status is not forge's

`planned | in-progress | done` retires with the file. Open/closed is the only status
forge reads or writes; ordering and priority are the Projects board, which forge
never calls (`gh project` stays untouched, per the Phase 1 spec).

Planning stops marking phases `in-progress` at kickoff. At plan completion it closes
the phase issue with a reason.

`resolve-deferral` is renamed `resolve`, same `--ref` / `--reason` flags and same
behavior against both stores. A phase and a deferral are both issues; the
deferral-shaped verb was the only thing suggesting otherwise. No alias is kept, so
every caller moves in the same commit: `hooks/guard-memory-writes` (its deny
message), `skills/project-memory/SKILL.md`, and the tests. The Phase 1 spec
`2026-09-05-project-memory-engine-design.md` is amended in place with a changelog
line; the Phase 1 plan is a frozen record and is left alone.

## Brainstorming reconciliation

Step 1 ("Explore context") reads the open issue list instead of `ROADMAP.md`, plus
the epic when the work belongs to a program.

Step 2 ("Scope check") records a decomposition through `add-program` + `add-phase`
instead of writing roadmap lines.

At close-out the agent presents a three-way reconciliation against open issues and
takes approval on it as a set:

- **Addresses** — issues this spec covers; closed by planning at completion.
- **Obsoletes** — issues the design makes moot; the user closes them, or declines.
- **Discovers** — scope found while designing; filed only if accepted.

The agent proposes; it never closes an issue on its own. A wrongly closed issue is
invisible afterward, so the gate is cheaper than the recovery.

## audit-issues

```
forge_memory.py audit-issues
```

Read-only, manual, never wired into `forge_lint.py` or run start — it makes network
calls, and no run may depend on GitHub being reachable. It never mutates a label.

Walks **every open issue**, paged explicitly rather than relying on `gh`'s default
of 30, and reports per issue:

- kind label count ≠ 1 (`feature` `defect` `debt` `risk`)
- origin label count ≠ 1 (`by:human` `by:agent`)
- any retired label present: `forge:deferral`, `forge:backlog`, `via:reported`,
  `via:implementation`

Output is one line per offending issue — number, title, failed checks. Exit 1 if any
offender, else a single all-clear line and exit 0. Closed issues are out of scope.

It needs no forge marker precisely because it examines everything, including issues
filed by people who have never heard of forge. That is why Phase 2 could drop the
`forge:deferral` read-back check.

## Migration

- `git mv docs/forge/ROADMAP.md docs/forge/archive/ROADMAP.md`, with the header
  `docs/forge/archive/DECISIONS.md` uses: historical, not authoritative, no new
  entries, and a pointer saying phases are issues now. Every line in it is `[done]`
  except Phase 8, which records its own decomposition into Phases 10–12 — the
  archive strands no live work.
- Dogfood: create the structured-memory
  epic with `add-program --name "structured memory"`, attach #43–#47 as its
  sub-issues with the blocked-by chain, and rename them to the new grammar. The five
  issues already exist, so the renames are `gh issue edit` by hand and the edges are
  `gh api` by hand — `add-phase` files new phases and never adopts existing ones.
- Other repos: no tooling. The `project-memory` skill's Legacy section gains one
  sentence — a `ROADMAP.md` in an existing repo is now an unread file; its phases
  belong in issues.

## Documentation and skill changes

- `skills/project-memory/SKILL.md` — the `## ROADMAP.md` section becomes
  `## Programs and phases` (the two subcommands, the title grammar, the edges, the
  status rule); frontmatter `description` and the opening paragraph drop the roadmap.
- `skills/brainstorming/SKILL.md` — steps 1 and 2 as above; close-out reconciliation.
- `skills/planning/SKILL.md` — completion closes the phase issue; no `in-progress`.
- `skills/planning/codex-execution.md` and `scripts/forge-run.py` — the doc-sync
  prompt stops listing ROADMAP status among what it reconciles.
- `README.md` — project memory is `constraints.md` plus GitHub issues; phases are
  issues with native edges; the skills table row drops ROADMAP.

## Testing

- `add-program` prints a parseable issue number as its last stdout line.
- `add-phase` renders `<program> <seq>/<of>: <title>` from the epic's title.
- `add-phase --seq` disagreeing with the epic's sub-issue count exits non-zero naming
  both numbers; no issue is created.
- `add-phase --seq 1` files no blocked-by edge; `--seq 2` blocks on the first phase.
- A budget overrun on either command is rejected before any network call.
- Missing `--kind` exits non-zero; no value is inferred.
- A failed edge call after issue creation exits non-zero and reports the created
  number and the edges that landed.
- `audit-issues` flags a missing kind, two kinds, a missing origin, and each retired
  label; exits 1 with offenders and 0 when clean; mutates nothing.
- `audit-issues` pages past 30 open issues.
- `resolve` behaves as `resolve-deferral` did; `resolve-deferral` is gone.
- The `session-start` tests assert the new sentences on both the current and legacy
  paths, and that no roadmap sentence survives.
- Repo-wide grep: no live reference to `ROADMAP.md` as a read or write path.
- Full suite green.

## Acceptance

- `docs/forge/ROADMAP.md` absent; `docs/forge/archive/ROADMAP.md` present with the
  non-authoritative header.
- The structured-memory epic exists with #43–#47 attached as sub-issues, chained
  blocked-by, and renamed to the title grammar.
- `audit-issues` runs clean against this repo's open issues.
- No skill, script, hook, or doc instructs anyone to read or update a roadmap file.
- `hooks/session-start` makes no roadmap claim on either the current or legacy path.

## Out of scope

Spec de-dating and migration (Phase 5); the session-start jurisdiction fix (#49);
migration tooling for other repos' roadmap files; any `gh project` call; a repair
subcommand for partially filed programs; issue-hierarchy checks in `audit-issues`
(an epic with no children, a phase with no parent). No version bump — the program's
lockstep bump lands with Phase 5.

## Note

Dated per the current convention. Phase 5 de-dates and migrates it with the rest of
`docs/forge/specs/`.
