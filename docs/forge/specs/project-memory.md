---
system: project-memory
supersedes:
  - archive/specs/2026-09-05-project-memory-engine-design.md
  - archive/specs/2026-09-05-deferrals-as-issues-design.md
  - archive/specs/2026-09-05-constraints-design.md
  - archive/specs/2026-09-05-retire-roadmap-design.md
---

# Project memory

What the project remembers across sessions: one CLI-managed file
(`docs/forge/constraints.md`) holding the rules that must not be broken, and GitHub
issues holding deferred work and the program/phase decomposition. `scripts/forge_memory.py`
is the only author of both; `scripts/forge_memory_store.py` holds the two backends.

There is no decision log, no roadmap file, and no deferrals file. Rationale — "we chose
X over Y because Z" — lives in PR bodies and in spec changelogs.

## Records

Field lists and per-field budgets live in a schema table **in code**
(`forge_memory.SCHEMA`), versioned with forge. Not configurable: a repo that can widen a
budget can disable the mechanism.

Budgets are per-field **character counts**, enforced as errors. Structural validation
alone is insufficient — the drift these budgets exist to stop happens in word count
*inside* a structurally valid field, where a 400-word `why` still parses. Budget overrun
is an error, never a truncation and never a warning.

Every record is its type and its fields. There is no store-assigned metadata on a
`Record` — no `ref`, no id from the backend — so two records with equal fields are equal,
by construction rather than by an equality exclusion.

Every field renders as a single line, so a newline or other control character inside a
value is a validate-time defect in the same pass and the same class as a budget overrun:
it would produce text `render` writes and `parse` cannot read back, bricking the file for
every later operation while layer 1 denies the direct edit needed to repair it.

### constraint

Always file-backed at `docs/forge/constraints.md`, ignoring store config entirely. Read
at session start; must be local and free.

| field | budget / form | required |
|---|---|---|
| `id` | ≤40 chars, kebab-case, unique in file | yes |
| `rule` | ≤200 chars, one sentence, imperative | yes |
| `scope` | ≤80 chars, path glob or subsystem | yes; CLI defaults to `repo` |
| `because` | ≤300 chars — what breaks if ignored | yes |
| `source` | ≤120 chars — issue, PR, spec path, or archive entry | yes, no default |

- Slug ids, not sequential. Sequential ids plus removal semantics reissue a retired
  number to an unrelated rule, invalidating citations already in git history. Slug reuse
  after retirement is permitted: a re-added slug denotes the same concept.
- Retirement **removes** the record. No tombstones, no status field, no `added` date. The
  removal commit is the history.
- `because` is not decoration: an agent that does not understand a rule routes around it
  while technically complying.
- `source` is read-time material — a constraint authored in one context and applied in
  another may need its originating code, issue, or spec to be understood. An archive
  entry (`docs/forge/archive/DECISIONS.md <date>`) is legal for a rule that predates the
  constraints file; it is historical provenance, **not** a live authority.
- `constraints.md` is created on its first entry. No empty scaffold.

### deferral

A GitHub issue by default. File backend (`docs/forge/deferrals.md`) only by explicit
opt-in.

| field | budget / form | required |
|---|---|---|
| `title` | ≤80 chars, imperative → issue title | yes |
| `why` | ≤300 chars | yes |
| `from` | plan path plus the stage that produced it, or `user` | yes; see below for how the CLI supplies it |

`from` reads `<plan path>, Task N` for a per-task finding and `<plan path>, final review`
for one the plan-level final review raised — named rather than collapsed to a bare plan
path, and never given an invented task number. `user` is reserved for a deferral the user
asked for directly, which is why the CLI never falls back to it blindly: an explicit
`--from` wins; failing that, a `--run` filing *derives* provenance from the run
(`forge_status.deferral_provenance` — the plan plus the entry's stage, or the run.json
path itself when the run records no plan, which is still real provenance and still not a
claim that a human initiated it); only a filing with neither reaches the `user` default, which is what
keeps that default meaningful rather than a placeholder.

There is no disposition or `follow-up` field. A deferral **is** an open issue nobody is
working on: `backlog` only restates that, `drop` means don't file it, and
`revisit-when:<condition>` is a comment on the issue. Removing the field also removes the
closed-set constraint that forced an unbounded condition into a bounded label.

### program and phase

A **program** is an epic issue; a **phase** is a real GitHub sub-issue of that epic,
blocked by its predecessor. Both are GitHub-only — a program or phase *is* an issue with
native edges, so there is no file-store equivalent to opt into, and neither type has a
local render/parse path.

| type | fields |
|---|---|
| `program` | `name` (≤80), `why` (≤300) |
| `phase` | `title` (≤80), `why` (≤300) |

They go through the same schema table as every other record, so the same per-field
budgets are enforced as hard errors. The first field listed for a type is its
**identifying** field, which is what becomes the issue title — never the literal key
`title`, since a program has no such key.

## CLI

`scripts/forge_memory.py`. Pure functions plus a thin CLI, one implementation for both
harnesses — the `forge_lint.py` / `forge_dispose.py` pattern.

```
forge_memory.py add-constraint    --id <slug> --rule <text> --because <text>
                                  [--scope <glob>] --source <ref>
forge_memory.py update-constraint --id <slug> [--rule <text>] [--because <text>]
                                  [--scope <glob>] [--source <ref>]
forge_memory.py retire-constraint --id <slug>
forge_memory.py list-constraints  [--scope <glob>] [--json]

forge_memory.py defer --title <text> --why <text> --by human|agent [--from <ref>]
                      [--run <run.json> --finding-id <id> [--occurrence N]]
forge_memory.py resolve --ref <issue-number|slug> --reason <text>

forge_memory.py add-program --name <text> --why <text> --kind feature|defect|debt|risk
forge_memory.py add-phase --epic <n> --seq <int> --of <int> --title <text>
                          --why <text> --kind feature|defect|debt|risk

forge_memory.py audit-issues
forge_memory.py fmt (--check | --write) [PATH ...]
forge_memory.py install-guards [--pre-commit] [--ci]
```

**Composition contract:** the CLI builds every record from typed arguments. It accepts no
free-form body on any subcommand and never reads existing entries to derive format.
`--help` is the template; no prose template exists anywhere to drift from. This is the
primary anti-drift mechanism — validation is the backstop, not the mechanism.

- `update-constraint` changes only the named fields and validates the whole record
  afterward. `id` is the key and is never itself settable: a renamed constraint is a
  retire plus an add, which is honest, since every citation pointing at the old id
  breaks. Editing in place is the normal operation — the file holds only what is
  currently true, so rewording a rule is sharpening it, not replacing it.
- Creation and update of a constraint require user approval. Agents may propose at an
  approval gate; no forge stage writes a constraint unattended. This is an operator rule
  that nothing enforces: the deny hook blocks a direct file edit and points at the CLI,
  which is itself ungated.
- `defer`'s `--by` is required and never defaulted — the engine cannot infer who noticed
  something, and a guess would put a wrong origin label on a real issue. It never applies
  a kind label and has no flag for one.
- `--run`/`--finding-id` record the filed issue number back into a run's `run.json`;
  `--occurrence N` disambiguates when two staged deferrals share a finding id.
- `--kind` on `add-program`/`add-phase` is required with no default. Origin for both is
  `by:human` unconditionally, with no flag: a decomposition exists only because a human
  approved it at a brainstorming gate. The value is structurally fixed rather than
  defaulted, which is why it does not reopen `defer`'s required-`--by` rule.
- `install-guards` installs the layer-2 pre-commit hook and the layer-3 CI workflow (from
  `templates/forge-memory-check.yml`). Neither runs by default and no other forge stage
  invokes it; installation is always explicit.

There is no `list-deferrals`. Reading filed deferrals back is the GitHub issue list's
job — the CLI files and closes, it never lists issues back.

## Stores

One interface, two implementations:

```
create(record, by=None) -> ref
retire(ref, reason=None) -> None   # constraints pass no reason; removal is the record
```

`by` is the record's **origin** (`"human"` or `"agent"`), not a schema field: on GitHub it
becomes the issue's `by:` label, and a backend with no label facility drops it rather
than inventing a field to mirror one.

- **GitHubStore** — `gh issue create/close`, plus `gh api` for sub-issue and blocked-by
  edges and `open_issues` for `audit-issues`. It has **no record read path**: no `scan`,
  no `list`, and nothing that parses a filed issue body back into a `Record`.
  `open_issues` is not that — it reads issue *metadata* (number, title, labels) for the
  audit and never reconstructs a record. Issue bodies are produced by the *same* renderer
  as the file backend.
- **FileStore** — canonical markdown, machine-written only. Identical schema, validator,
  budgets, and rendering: a second drawer, not a degraded path. `scan(type, **filters)`
  returns `(records, errors)`, collecting every unparsable record with the line it failed
  at; `list` raises on the first error `scan` reports. `list` keeps raising because
  `create` depends on it when checking id uniqueness — collecting instead would let a
  duplicate id through against a partially-parsed file.

**Validation happens at write time**, before the network: `GitHubStore.create` validates
the record and checks the origin and kind labels *before* `_gh_ready`, so a malformed or
over-budget record never reaches GitHub. That is the half that keeps forge honest about
its own output. Read-back validation of already-filed issue bodies does not exist and is
not wanted: it would duplicate the GitHub UI, and the threat it would guard (a human
editing a body) does not propagate, because the next record is composed from CLI
arguments rather than read from the last one.

**Selection** (`select_store`): constraints are always `FileStore`; programs and phases
are always `GitHubStore`; deferrals read `docs/forge/config.json` — absent ⇒ GitHub.

```json
{ "deferrals": { "store": "file" } }
```

is the only opt-out, and the file exists only when opted out. No other keys are read.
Budgets, field lists, schema, and rendering are never configurable. An unknown store
value or malformed JSON is a loud `ConfigError` naming the file and the legal values.
Missing `gh`, unauthenticated, or Issues disabled is a loud `StoreUnavailable` naming the
fix (`gh auth login`, install `gh`, enable Issues) — never a silent slide to the file
store. `select_store` only *constructs* a store, so it never calls `gh`.

`managed_paths(repo_root)` is the single definition of which local files are
machine-managed: `constraints.md` when it exists, plus `deferrals.md` only when config
selects the file store. Both `fmt` and `forge_lint.check_memory_files` call it rather
than keeping a second copy.

## Rendering

One renderer; the parser is its exact inverse. Round-trip must be idempotent:
`render(parse(render(r))) == render(r)`.

`fmt --write` parses a file into records and re-renders it — `gofmt` for project memory.
Consequences, accepted deliberately:

- A hand edit that parses is normalized back to canonical form; structural drift has no
  stable state to accumulate in.
- A hand edit that does not parse, or overruns a budget, fails loud naming the line.
- Human prose formatting in `constraints.md` is destroyed on the next `fmt --write`.
  Constraints are terse, budgeted, and few; this is the intent.

`--check` reports **every** defect in one pass, not the first.

## Enforcement — four layers

| layer | mechanism | covers | gap |
|---|---|---|---|
| 0 | CLI composition (above) | removes the motive to read-then-imitate | none |
| 1 | `PreToolUse` hook denies `Edit\|Write\|MultiEdit` on managed paths; the deny reason names the CLI command | Claude Code path, immediate feedback | Bash writes; Codex |
| 2 | `fmt --check` in `forge_lint.py` at run start; `pre-commit` hook installed **on request** | harness-agnostic — inspects the artifact, not the actor | `--no-verify` |
| 3 | CI workflow running `fmt --check`; template scaffolded **on request** | unbypassable merge gate | — |

Layer 2 is the guarantee; layer 1 is fast feedback. The hook (`hooks/guard-memory-writes`)
reuses `session-start`'s signal-directory walk, runs that walk before reading stdin, and
is inert outside forge repos. It compares by filesystem identity rather than string
equality, so a differently-cased spelling of a managed path on a case-folding filesystem
is still denied. It emits nothing and exits 0 on any internal failure: a hook that blocked
on its own failure would break every edit in the repo, and layer 2 still catches what this
layer misses. forge never writes CI into a downstream repo unprompted.

## What a constraint is

A rule that, if broken, produces a **defect**. Not a preference, not a record of a choice.
`constraints.md` is a snapshot of what is true **now**, never a log.

A decision — "we chose X over Y because Z" — is not a constraint.

### Authoring tests

A candidate must pass all six. Test 0 governs.

0. **It is true right now.** If it stops being true it is deleted, not annotated. A stale
   constraint is worse than a missing one: an agent obeys it into a conflict.
1. **It is a rule, not a choice.** Phrasable as an imperative. "We chose X over Y" is
   rationale — PR body.
2. **Violating it produces a defect, not a difference.** If breaking it yields something
   merely other, it is taste.
3. **Nothing already enforces it.** If a test, linter, or type enforces the rule, the code
   is the constraint; a prose copy is a second source that drifts.
4. **It is non-obvious.** It captures what a competent agent would otherwise get wrong.
5. **Scope is honest.** A rule binding only one harness says so.

### Soft cap

Twelve. Crossing it prints a note listing the current set; it never refuses. The cap is
not housekeeping — smallness is what keeps test 0 real. A file short enough to re-read at
every session start is one where a rule that stopped being true gets noticed. If the set
approaches fifteen, the authoring tests are too loose, not the cap too low.

### Session-start hook

`hooks/session-start` reads `constraints.md` and supplies its rules as session context, so
no skill needs to read the file to surface it. It parses with the engine's own
SCHEMA-driven `parse`, never a regex over rendered prose. It emits `id`, `rule` (with
`scope` shown only when it is not `repo`) and `because` — `because` is what stops an agent
that does not understand a rule from routing around it, and `id` is what lets a
`(constraint: <id>)` citation in code resolve back. `source` is deliberately omitted: a
follow-it-when-you-need-it pointer is not worth carrying every session. Any failure —
missing file, unparseable, `forge_memory` not importable — degrades to the flow context
alone; this hook never fails a session over a malformed `constraints.md`.

When the hook fires, and whether it should claim a repo uses the forge flow on directory
presence alone, is not this system's: that is issue #49's routing fix.

### Citations

A citation in code names the most specific durable target:

| what the comment invokes | cite |
|---|---|
| a rule spanning systems | the constraint id |
| a contract for this system | the owning spec |
| historical why | the PR |
| nothing durable | no citation |

Most land on a spec reference. That is correct — it keeps `constraints.md` small.

### No decision log

Rationale lives in PR bodies and spec changelogs. There is no "log the decision" step in
brainstorming or planning. Consequence, accepted: "why did we choose X" is a PR search,
not a file read.

## Deferrals

**Agency rule:** agents defer **non-spec scope only** — nice-to-haves, refactors, edge
polish they judge out of scope. Anything the spec requires is never silently deferred; it
surfaces at the review gate.

### Flow

- **Codex** — `forge-run.py` aggregates `defer`-disposition findings into `run.json`
  under `deferrals`. The runner **never files an issue** and never writes a durable
  record. At completion it **stages and emits**: it prints each staged deferral plus a
  `forge_memory.py defer` command template with every flag filled in except
  `--title`/`--why`, which are visible placeholders for the reviewer to author. The
  template carries `--from` (plan path, plus `, Task N` when the staged entry records
  one) and `--occurrence N` when two staged deferrals share a finding id. Both the
  template and `defer`'s own `--run` derivation call the same
  `forge_status.deferral_provenance`, so a filled-in `--from` and an omitted one produce
  the identical value — and neither can produce `user`, the marker reserved for a
  deferral that skipped this gate.
- **Claude** — the orchestrator holds `defer` findings in context (dispatch and inline
  alike), presents them at close-out, and files accepted ones by invoking
  `forge_memory.py defer`. It never writes an issue body or a file directly.
- **The runner stages a task number** with each defer-disposition finding. `from` is a
  plan path *plus* a task number, and nothing downstream can recover which task produced a
  finding, so the runner must record it at staging time.
- **The runner reads prior deferrals back on resume**, the way `_read_seeded_findings`
  does for seeded findings. Otherwise a resumed run's terminal write would replace
  `deferrals` with only the current invocation's entries, erasing earlier staged deferrals
  and their recorded `issue` numbers.
- **The reviewer verdict contract carries no `title` field.** A reviewer `summary`
  (100–200 chars) cannot become an ≤80-char `title` without truncation, and truncation is
  a budget error; authorship therefore happens at the close-out gate, where judgment is
  present.

### Close-out review gate

Runs after the final review passes and the full suite is green, before the
branch-disposition question.

- Every staged deferral is presented with proposed `title` (≤80), `why` (≤300) and `from`.
- The user may accept, edit, or drop each. Only accepted deferrals are filed, with
  `--by agent` — the runner stages what an agent found.
- Deferrals are surfaced for review because they can affect what comes next; the gate is a
  decision point, not a notification.
- An autonomous Codex run ends with deferrals **staged, not filed**. Accepted
  consequence: an unreviewed auto-deferral must not become a permanent issue.
- A **halted** run may file. Its collected deferrals are real findings and the user
  reviews them at the gate either way; discarding them because the run stopped early
  would lose work. `is_terminal` answers "is anything still writing this file", which is
  the only question filing safety depends on — not whether the run finished clean.
- Filed deferrals' issue numbers are listed in the end-of-plan summary.

### User-initiated deferrals

A deferral the user asks for mid-session ("I want to work on this later") files
**immediately** through `forge_memory.py defer` with `--by human` and `from: user`. No
close-out gate — the gate exists to put judgment in front of machine-generated deferrals,
and a user request already carries it.

### Labels

Six labels, every one meaningful to a human. The engine applies one of them; the rest are
human judgment.

- **Kind** — exactly one, required: `feature` | `defect` | `debt` | `risk`. Mutually
  exclusive and exhaustive by design; the forcing function is the value. Set by a human in
  the GitHub UI. `defer` never applies one — whether something is a defect or debt is a
  call the engine cannot make, and a guess reads as a judgment somebody made.
  `add-program`/`add-phase` do take `--kind`, because a human is at the keyboard filing it.
- **Origin** — exactly one, required: `by:human` | `by:agent`. Who noticed it. The label
  is created in the repo on first use if missing.
- **Which phase is NOT a label.** The record's `from:` field already names the plan and
  the task, which is finer than a phase label and cannot drift from it.

Three cases, all expressible:

| case | kind | origin | `from:` |
|---|---|---|---|
| human files an issue | one of four | `by:human` | `user` |
| agent finds it mid-phase | one of four | `by:agent` | plan path, Task N |
| human spots it mid-phase | one of four | `by:human` | plan path, Task N |

No forge-only marker label exists. `forge:deferral`, `forge:backlog`, `via:reported` and
`via:implementation` are retired, and `audit-issues` flags any of them still present.

### Staging and idempotency

- Staged shape in `run.json` is the existing finding dict plus the stage that produced it:
  `task_number: N` for a per-task finding, `stage: "final-review"` for one the plan-level
  final review raised.
- A resumed run re-reports findings from the task it re-runs. An entry is skipped only
  when the PRIOR invocation already staged one with the same `(task_number, stage, id)` —
  task 1's `F1` and task 2's `F1` are different deferrals and both are kept. Within one
  invocation staging is lossless: every `defer` finding a verdict raised is staged, none
  collapsed.
- A reviewer verdict naming two findings with one id is malformed and is rejected (retry
  once, then contract error), like a duplicate coverage id. Ids are unique within a
  verdict, never namespaced across a run.
- Staged deferrals persist across a resume. A deferral, once staged, is never lost and
  never re-emitted as unfiled once it carries an `issue`.
- `--occurrence` selects among entries sharing a finding id by ordinal position. That key
  is only sound because the list persists across invocations — it depends on the resume
  rule above, not merely on the runner writing the list once. An ambiguous id without it
  is refused, never guessed.
- On filing, the issue number is recorded back into `run.json` under the deferral's
  `issue` key. Close-out re-run skips any staged deferral carrying an `issue`, so filing
  is idempotent across a resumed or repeated close-out.

### gh unavailable at filing

- Loud message naming the fix (`gh auth login`, enable Issues, install `gh`).
- Staged deferrals stay in `run.json`; nothing is lost.
- The exact `forge_memory.py defer` commands are printed for later filing.
- The run is **not** marked failed — every task and the final review already passed, and
  filing is a close-out step, not a build step.
- No fallback to the file store. The no-silent-degradation rule holds here too.

## Programs and phases

Filed only when brainstorming decomposes work into multiple sub-projects. Both are
ordinary issues: same six-label taxonomy, same origin rule, no forge-only marker.

- `add-program` creates the epic — its issue title is `--name` verbatim, with no
  rendering — and prints the issue number to stdout, alone on the last line, so a caller
  can pass it straight to `--epic`.
- `add-phase` renders the title as `<program name> <seq>/<of>: <title>`, where the program
  name is read from the epic issue's own title. Nothing parses a sequence back out of a
  title; the rendered prefix is display only and is never an input.
- Edges, in this order, each a `gh api` POST: `issues/<epic>/sub_issues` to attach the
  phase, then `issues/<new>/dependencies/blocked_by` naming the epic's previously last
  sub-issue. A first phase (`--seq 1`) gets no blocked-by edge.
- Ordering is insertion order. `add-phase` reads the epic's sub-issues and requires
  `seq == len(sub_issues) + 1`; any other value exits non-zero naming both numbers.
  Out-of-order filing is a caller error, never reordered silently.
- `--of` is rendering only and is not validated against sibling titles.
- `add-phase` files new phases and never adopts issues that already exist.
- A budget overrun is rejected **before any network call**: `add-phase` checks the raw
  `--title` against the schema's 80-char budget before it reads the epic, since a title
  that already fails can never be salvaged by rendering.

### Partial failure

Issue creation and each edge are separate network calls with no transaction. On a failure
after the issue exists, the command exits non-zero, names the call that failed, and prints
the created issue number and which edges did land — never a bare traceback and never a
silent retry. The user completes it by hand or re-runs the missing edge; there is no
repair subcommand.

### Status is not forge's

Open/closed is the only status forge reads or writes. There is no
`planned | in-progress | done`. Ordering and priority live on the human's GitHub Projects
board, which forge never calls — `gh project` is never invoked.

Planning does not mark a phase `in-progress` at kickoff. At plan completion it closes the
phase issue with a reason.

### Brainstorming reconciliation

Step 1 ("Explore context") reads the open issue list, plus the epic when the work belongs
to a program.

Step 2 ("Scope check") records a decomposition through `add-program` + `add-phase`.

At close-out the agent presents a three-way reconciliation against open issues and takes
approval on it as a set:

- **Addresses** — issues this spec covers; closed by planning at completion.
- **Obsoletes** — issues the design makes moot; the user closes them, or declines.
- **Discovers** — scope found while designing; filed only if accepted.

The agent proposes; it never closes an issue on its own. A wrongly closed issue is
invisible afterward, so the gate is cheaper than the recovery.

## audit-issues

```
forge_memory.py audit-issues
```

Read-only, manual, never wired into `forge_lint.py` or run start — it makes network calls,
and no run may depend on GitHub being reachable. It never mutates a label.

Walks **every open issue** — passing an explicit high `--limit` rather than relying on
`gh`'s default of 30 to have listed everything it must see — and reports per issue:

- kind label count ≠ 1 (`feature` `defect` `debt` `risk`)
- origin label count ≠ 1 (`by:human` `by:agent`)
- any retired label present: `forge:deferral`, `forge:backlog`, `via:reported`,
  `via:implementation`

Output is one line per offending issue — number, title, failed checks. Exit 1 if any
offender, else a single all-clear line and exit 0. Closed issues are out of scope, and so
are hierarchy checks (an epic with no children, a phase with no parent).

It needs no forge marker precisely because it examines everything, including issues filed
by people who have never heard of forge.

## Legacy repos

`docs/forge/DECISIONS.md`, `DEFERRALS.md` and `ROADMAP.md` are frozen under
`docs/forge/archive/`: historical, not authoritative, no new entries. Nothing reads them
as a live path; a retirement notice may name them.

In someone else's repo there is no migration tooling. A `ROADMAP.md`, `DECISIONS.md` or
`DEFERRALS.md` found in an existing repo is simply an unread file — its phases belong in
issues, its rules in `constraints.md` if they still bind. A repo with `docs/superpowers/`
and no `docs/forge/` gets a one-time `git mv` offer rather than two read paths forever.

## Changelog

2026-09-05: consolidated from four dated specs — project-memory-engine, deferrals-as-issues, constraints, retire-roadmap (#47)
2026-09-05: dropped every source's "Testing", "Acceptance", "Out of scope", "Migration" and "Documentation and skill changes" section — one-time phase gates and migration worklists, all satisfied and expired; the standing rules a few of them carried are stated in place (the no-`gh project` rule under Status is not forge's, the no-repair-subcommand rule under Partial failure, the no-hierarchy-checks rule under audit-issues, the no-migration-tooling rule under Legacy repos, the agency rule under Deferrals, and issue #49's session-start jurisdiction question under Session-start hook) (#47)
2026-09-05: dropped the engine spec's "Program end state" and the three "Motivating defect"/"Motivating evidence" sections that exist — the engine spec's (`DECISIONS.md`'s prose-expanded `Why`), the deferrals spec's (`DEFERRALS.md`'s 25 over-budget entries) and retire-roadmap's (`ROADMAP.md`'s status drift) — a forecast of this document plus the arrival narratives of three now-archived files; the constraints spec has no such section, and its equivalent argument survives under What a constraint is. The standing rule those narratives argued for, that budgets are per-field character counts enforced as errors because structural validation alone cannot see drift inside a valid field, is kept under Records (#47)
2026-09-05: dropped the dated-filename remark each source's "Note" carried — each said its own filename followed the then-current convention and would be de-dated by a later migration; performed by this merge. The deferrals spec's "Note" additionally held seven amendment entries, whose content is kept: uniform `scan`/`list` (superseded — see the next entry), halted runs may file (Close-out review gate), the emitted `defer` command being a fill-in template carrying `--from`/`--occurrence` (Flow), the runner staging a task number and persisting deferrals across a resume (Flow), `Record.ref` removed (Records), the six-label rework retiring `forge:deferral`, the follow-up field, read-back validation and `list-deferrals` (Labels, deferral record, CLI), and the sweep of that spec's own prose against the label amendment (absorbed — no `follow-up` or `--local-only` claim survives here) (#47)
2026-09-05: dropped the engine spec's own "Changelog" as a section — its three amendments are folded into the body they amended (`install-guards` in the CLI surface, the constraint-record rework — `added` removed, `source` required, `update-constraint` added — in Records, `resolve-deferral` renamed `resolve`); this document's changelog starts here, and the superseded originals keep theirs (#47)
2026-09-05: dropped the deferrals spec's "Carried fixes" claim that `scan`/`list` become uniform across both stores — superseded within the same phase by the label amendment, which removed `GitHubStore`'s record read path entirely; `scan`/`list` are `FileStore`'s alone, and `Store` declares only `create`/`retire`. The section's other two claims survive: `Record` carries no `ref` (Records) and `FileStore.list` keeps raising, because `create` depends on it for id uniqueness (Stores) (#47)
2026-09-05: dropped the constraints spec's enumerated citation-migration worklist (~33 citations across 14 files) and its 37-entry read-through — one-time migrations; the standing target-selection table is kept under Citations (#47)
2026-09-05: verified the CLI surface against `forge_memory.py` rather than the sources: `resolve-deferral` is `resolve`, `add-program`/`add-phase`/`audit-issues` exist, `list-deferrals` and the `follow-up` field do not, `add-constraint` takes `--source` rather than `--issue`/`--spec`, and `defer` takes `--by` plus the `--run`/`--finding-id`/`--occurrence` write-back trio (#47)
