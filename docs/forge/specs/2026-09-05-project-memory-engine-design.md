# Project Memory Engine — design

Phase 1 of the structured-memory program. Replaces the flat-file project memory
(`DECISIONS.md`, `DEFERRALS.md`, `ROADMAP.md`) with a schema-driven record engine.
Later phases consume this engine; nothing else in the program can land first.

## Program end state (context for the contracts below)

- `DEFERRALS.md` → GitHub issues, label `forge:deferral`.
- `ROADMAP.md` → deleted. All tracked work is a GitHub issue. Prioritization is a
  human-run Projects board; forge never calls `gh project`.
- `DECISIONS.md` → frozen to `docs/forge/archive/DECISIONS.md` (historical, not
  authoritative, no new entries). Replaced by `docs/forge/constraints.md` — binding,
  currently-true rules only. Decision *history* lives in PR bodies and spec changelogs.
- Specs become per-system living documents, de-dated, amended in place.

Phases 2–5 each get their own spec. Program phases are tracked as GitHub issues.

## Motivating defect

Entries in `DECISIONS.md` are structurally valid (4–5 lines) but prose-expanded: the
newest `**Why:**` is one ~400-word paragraph. Agents author by reading prior entries
for format cues, so each drifted entry raises the next one's baseline. Drift is in
*word count inside a valid field*, not in structure — so structural validation alone
is insufficient. Budgets must be per-field character counts, enforced as errors.

## Records

Two types. Field lists and budgets live in a schema table **in code**, versioned with
forge. Not configurable — a repo that can widen a budget can disable the mechanism.

### constraint

Always file-backed at `docs/forge/constraints.md`. Read at session start; must be
local and free.

| field | budget / form | source |
|---|---|---|
| `id` | ≤40 chars, kebab-case, unique in file | user |
| `rule` | ≤200 chars, one sentence, imperative | user |
| `scope` | ≤80 chars, path glob or subsystem; CLI defaults to `repo` | user |
| `because` | ≤300 chars | user |
| `source` | ≤120 chars — issue, PR, spec path, or archive entry | user, `--source` |

- Slug ids, not sequential. Sequential ids + removal semantics reissue a retired
  number to an unrelated rule, invalidating citations in git history.
- Slug reuse after retirement is permitted: a re-added slug denotes the same concept.
- Retirement **removes** the record. No tombstones, no status field. The removal
  commit is the history.
- Creation requires user approval. Agents may propose at an approval gate; no forge
  stage writes a constraint unattended.

### deferral

GitHub issue by default (`forge:deferral` + follow-up label). File backend only by
explicit opt-in.

| field | budget / form | source |
|---|---|---|
| `title` | ≤80 chars, imperative → issue title | user/agent |
| `why` | ≤300 chars | user/agent |
| `from` | plan+task ref, or `user` | machine where derivable |
| `follow-up` | `backlog` \| `drop` \| `revisit-when:<condition>` | user/agent |

- `roadmap` as a follow-up value is retired with `ROADMAP.md`. `backlog` = the issue
  stays open.
- Agency rule unchanged: agents defer **non-spec scope only**; spec'd requirements
  are surfaced at the review gate, never deferred.

## CLI

`scripts/forge_memory.py`. Pure functions + thin CLI, one implementation for both
harnesses — the `forge_lint.py` / `forge_dispose.py` pattern.

```
forge_memory.py add-constraint --id <slug> --rule <text> --because <text>
                               [--scope <glob>] [--issue N | --spec PATH]
forge_memory.py retire-constraint --id <slug>
forge_memory.py list-constraints [--scope <glob>] [--json]

forge_memory.py defer --title <text> --why <text> --follow-up <val> [--from <ref>]
forge_memory.py list-deferrals [--json]
forge_memory.py resolve-deferral --ref <issue-number|slug> --reason <text>

forge_memory.py fmt [--check | --write] [PATH ...]

forge_memory.py install-guards [--pre-commit] [--ci]
```

`install-guards` installs the layer-2 pre-commit hook and the layer-3 CI workflow.
Neither runs by default; no other forge stage invokes it. Installation is always
explicit, per "on request" in the enforcement table.

**Composition contract:** the CLI builds every record from typed arguments. It accepts
no free-form body and never reads existing entries to derive format. `--help` is the
template; no prose template exists anywhere to drift from. This is the primary
anti-drift mechanism — validation is the backstop, not the mechanism.

Budget overrun is an error, never a truncation and never a warning.

## Stores

One interface, two implementations:

```
create(record) -> ref
list(type, filters) -> [record]
retire(ref, reason=None) -> None   # constraints pass no reason; removal is the record
```

- **GitHubStore** — `gh issue create/list/close --json`. Issue bodies are produced by
  the *same* renderer as the file backend, so `fmt --check` validates open
  `forge:deferral` issues as well as files.
- **FileStore** — canonical markdown at `docs/forge/deferrals.md`, machine-written
  only. Identical schema,
  validator, budgets, and rendering. A second drawer, not a degraded path.

**Selection:** absent config ⇒ `github`. Missing `gh`, unauthenticated, or Issues
disabled ⇒ loud failure naming the fix (`gh auth login`, enable Issues). Never a
silent slide to the file store. File store is reachable only via committed config:

```json
{ "deferrals": { "store": "file" } }
```

at `docs/forge/config.json`, which exists only when opted out. No other keys. Budgets,
field lists, schema, and rendering are never configurable.

Constraints are unaffected by store selection — always FileStore.

## Rendering

One renderer; the parser is its exact inverse. Round-trip must be idempotent:
`render(parse(render(r))) == render(r)`.

`fmt --write` parses a file into records and re-renders it — `gofmt` for project
memory. Consequences, accepted deliberately:

- A hand edit that parses is normalized back to canonical form; structural drift has
  no stable state to accumulate in.
- A hand edit that does not parse, or overruns a budget, fails loud naming the line.
- Human prose formatting in `constraints.md` is destroyed on the next `fmt --write`.
  Constraints are terse, budgeted, and few; this is the intent.

`--check` reports **every** defect in one pass, not the first (the `forge_lint.py` rule).

## Enforcement — four layers

| layer | mechanism | covers | gap |
|---|---|---|---|
| 0 | CLI composition (above) | removes the motive to read-then-imitate | none |
| 1 | `PreToolUse` hook denies `Edit\|Write\|MultiEdit` on managed paths; deny reason names the CLI command | Claude Code path, immediate feedback | Bash writes; Codex |
| 2 | `fmt --check` in `forge_lint.py` at run start; `pre-commit` hook installed **on request** | harness-agnostic — inspects the artifact, not the actor | `--no-verify` |
| 3 | CI workflow running `fmt --check`; template scaffolded **on request**, enabled on `forcetrainer/forge` | unbypassable merge gate | — |

Layer 2 is the guarantee; layer 1 is fast feedback. The hook reuses `session-start`'s
signal-directory walk and is inert outside forge repos. forge never writes CI into a
downstream repo unprompted.

Managed paths: `docs/forge/constraints.md`, and the deferral file when file-backed.

## Testing

`tests/test_forge_memory.py`, per-module convention. `gh` stubbed throughout; no network.

- Each field at budget, one char over, and empty.
- Slug uniqueness; kebab-case rejection; retirement removes cleanly; slug reusable after.
- Round-trip idempotence.
- `fmt --check` detects hand-drift and reports all defects in one pass.
- Store selection: absent config, explicit `file`, missing `gh`, unauthenticated,
  Issues disabled — each failure loud and named.
- GitHub issue body round-trips through the shared renderer.
- `forge_lint.py` fails the run on a drifted constraints file.

## Acceptance

- `forge_memory.py` exists with the CLI surface above; every subcommand covered.
- Budget overrun exits non-zero with the field and limit named.
- `fmt --check` passes on a CLI-authored file, fails on a hand-drifted one.
- `forge_lint.py` run start fails on a drifted `constraints.md`.
- `PreToolUse` hook denies a direct `Write` to `constraints.md` in a forge repo and is
  silent elsewhere.
- Full suite green.

## Out of scope (later phases)

Runner deferral write-back and the `defer` disposition path (Phase 2); `constraints.md`
adoption, `DECISIONS.md` freeze, citation repointing, `session-start` rewrite (Phase 3);
`ROADMAP.md` deletion, issue reconciliation in brainstorming, `project-memory` skill
rewrite (Phase 4); spec de-dating and migration (Phase 5).

## Note

This spec is dated per the current convention. Phase 5 de-dates and migrates it with
the rest of `docs/forge/specs/`.

## Changelog

2026-09-05: added `install-guards` to the CLI surface — layers 2 and 3 required an
explicit installation path that the original CLI list omitted (planning, issue #43).
2026-09-05: constraint record amended by Phase 3 — `added` removed (it supported retirement deliberation, and a constraint that stops being true is deleted rather than annotated, so there is none); `source` is user-supplied and required via `--source`, replacing the machine-set `--issue`/`--spec`, which could not express a PR and let a required field be satisfied by the placeholder `user`; `scope` and `source` gained budgets; `update-constraint` added (issue #45).
