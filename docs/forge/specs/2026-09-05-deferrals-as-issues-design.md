# Deferrals as GitHub Issues — design

Phase 2 of the structured-memory program (issue #44). Retires `docs/forge/DEFERRALS.md`;
deferrals become GitHub issues via the Phase 1 record engine.

Depends on Phase 1 (`docs/forge/specs/2026-09-05-project-memory-engine-design.md`) —
schema, budgets, `GitHubStore`, and the `defer` CLI already exist and are unchanged here.

## Motivating evidence

`DEFERRALS.md`, 25 entries. The five oldest `**Why:**` fields are 72–186 chars. Every
entry after them exceeds the 300-char budget, rising to 1571. Each entry taught the next
to be longer. 20 of 25 cannot be migrated mechanically — migration is authorship.

## Flow

Close-out changes; the runner changes in two narrow ways, both required by contracts
below that it alone can satisfy.

- **The runner stages a task number** with each defer-disposition finding. `from` is
  specified as plan path **plus task number**; nothing downstream can recover which
  task produced a finding, so the runner must record it at staging time.
- **The runner reads prior deferrals back on resume**, the way `_read_seeded_findings`
  already does for seeded findings. Otherwise a resumed run's terminal write replaces
  `deferrals` with only the current invocation's entries, erasing earlier staged
  deferrals and their recorded `issue` numbers.

- **Codex** — `forge-run.py` aggregates `defer`-disposition findings into `run.json`
  under `deferrals`, exactly as today. The runner **never files an issue** and never
  writes a durable record. At completion it **stages and emits**: prints each staged
  deferral plus a `forge_memory.py defer` command template — every flag filled
  in except `--title`/`--why`, which are visible placeholders for the reviewer
  to author (see the 2026-09-05 note below).
- **Claude** — the orchestrator holds `defer` findings in context as today (dispatch and
  inline alike), presents them at close-out, and files accepted ones by invoking
  `forge_memory.py defer`. It never writes an issue body or a file directly.
- **Reviewer verdict contract is unchanged.** No `title`/`follow-up` field is added.
  A reviewer `summary` (100–200 chars) cannot become an ≤80-char `title` without
  truncation, and truncation is a budget error; authorship therefore happens at the
  close-out gate, where judgment is present.

## Close-out review gate

Runs after the final review passes and the full suite is green, before the
branch-disposition question.

- Every staged deferral is presented with proposed `title` (≤80), `why` (≤300),
  `from` (plan path + task number, or `user`), `follow-up` (`backlog`).
- User may accept, edit, or drop each.
- Only accepted deferrals are filed.
- `follow-up` defaults to `backlog` for every runner-generated deferral. No stage
  guesses `drop` or `revisit-when:<condition>`.
- Deferrals are surfaced for review because they can affect the next phase — the gate
  is a decision point, not a notification.
- An autonomous Codex run ends with deferrals **staged, not filed**. Accepted
  consequence: an unreviewed auto-deferral must not become a permanent issue.
- A **halted** run may file. Its collected deferrals are real findings and the user
  reviews them at the gate either way; discarding them because the run stopped early
  would lose work. `is_terminal` answers "is anything still writing this file", which
  is the only question filing safety depends on — not whether the run finished clean.

## User-initiated deferrals

A deferral the user asks for mid-session ("I want to work on this later") files
**immediately** through `forge_memory.py defer` with `from: user`. No close-out gate —
the gate exists to put judgment in front of machine-generated deferrals, and a user
request already carries it. `follow-up` is whatever the user states, defaulting to
`backlog`.

## Staging and idempotency

- Staged shape in `run.json` is the existing finding dict plus the stage that
  produced it: `task_number: N` for a per-task finding, `stage: "final-review"`
  for one the plan-level final review raised. A final-review finding belongs to
  no single task, so its `from` reads `<plan>, final review` — named rather than
  collapsed to a bare plan path, and never given an invented task number.
- A resumed run re-reports findings from the task it re-runs. An entry is
  skipped only when the PRIOR invocation already staged one with the same
  `(task_number, stage, id)` — task 1's `F1` and task 2's `F1` are different
  deferrals and both are kept. Within one invocation staging is lossless:
  every `defer` finding a verdict raised is staged, none collapsed.
- A reviewer verdict naming two findings with one id is malformed and is
  rejected (retry once, then contract error), like a duplicate coverage id.
  Ids are unique within a verdict, never namespaced across a run.
- Staged deferrals persist across a resume. A deferral, once staged, is never lost and
  never re-emitted as unfiled once it carries an `issue`.
- `--occurrence` selects among entries sharing a finding id by ordinal position. That
  key is only sound because the list persists across invocations — it depends on the
  resume rule above, not merely on the runner writing the list once.
- On filing, the issue number is recorded back into `run.json` under the deferral's
  `issue` key.
- Close-out re-run skips any staged deferral carrying an `issue`. Filing is idempotent
  across a resumed or repeated close-out.

## gh unavailable at filing

- Loud message naming the fix (`gh auth login`, enable Issues, install `gh`).
- Staged deferrals stay in `run.json`; nothing is lost.
- The exact `forge_memory.py defer` commands are printed for later filing.
- The run is **not** marked failed — every task and the final review already passed,
  and filing is a close-out step, not a build step.
- No fallback to the file store. Phase 1's no-silent-degradation rule holds.

## Carried fixes

Phase 2 is the second consumer of both; settled here rather than deferred again.

- `Record.ref` becomes `field(compare=False, repr=False)`. A ref is storage metadata;
  excluding it restores the equality and repr semantics `Record` had before Phase 1
  Task 3, with no loss of data.
- `GitHubStore.list`'s `errors` out-parameter is removed. One method must not switch
  between raising and collecting based on whether a caller passed a mutable list.
- Both behaviors become **uniform across both stores**, so no call site branches on the
  concrete store class:
  - `scan(type, **filters) -> (records, errors)` — collects every unparsable record,
    errors carrying the record's ref (issue number, or file line).
  - `list(type, **filters) -> [Record]` — raises when `scan` reports any error.
  A divergent return shape between `FileStore` and `GitHubStore` would break the
  substitutability the `Store` interface exists for, and contradict Phase 1's
  "a second drawer, not a degraded path".
- `FileStore.list` keeps raising. `FileStore.create` depends on it when checking id
  uniqueness — collecting instead would let a duplicate id through against a
  partially-parsed file.

## Documentation and skill changes

`DEFERRALS.md` must survive nowhere as a live target.

- `skills/planning/SKILL.md` — the deferral rule: issues, not a file; close-out review
  gate; end-of-plan summary lists filed issue numbers.
- `skills/planning/codex-execution.md` — the DEFERRALS write-back section becomes the
  stage-and-emit contract.
- `skills/project-memory/SKILL.md` — the DEFERRALS section. ROADMAP and DECISIONS
  sections are Phases 3–4 and stay.
- `README.md`, `CONTRIBUTING.md` — project memory is no longer three files.
- The agency rule is unchanged: agents defer **non-spec scope only**; spec'd
  requirements surface at the review gate and are never deferred.

## Migration

A guided task, not a script.

- Walk all 25 entries with the user; each is live or not.
- Live entries are re-authored as budget-conforming issues through the CLI.
- `git mv docs/forge/DEFERRALS.md docs/forge/archive/DEFERRALS.md`, with a header:
  historical, not authoritative, no new entries.
- `docs/forge/DEFERRALS.md` does not exist when the phase completes.

## Testing

- Runner still aggregates to `run.json` and files nothing; assert no `gh` invocation
  from `forge-run.py` on a clean run (process-level, PATH-stubbed).
- Staged deferral emission includes a runnable `defer` command per entry.
- Filing records the issue number into `run.json`; a second close-out files nothing.
- gh-unavailable at filing: loud, staged, commands printed, run not failed.
- `Record` equality ignores `ref`; two records differing only by `ref` compare equal.
- `GitHubStore.list` returns `(records, errors)`; no out-parameter remains.
- Repo-wide grep: no reference to `docs/forge/DEFERRALS.md` as a write target or a
  live read path. A notice stating that it is retired may name it.

## Acceptance

- `docs/forge/DEFERRALS.md` absent; `docs/forge/archive/DEFERRALS.md` present.
- No skill, script, hook, or doc references `DEFERRALS.md` as a write target.
- A `defer`-disposition finding reaches a GitHub issue only through the close-out gate.
- `forge-run.py` makes no `gh` call.
- Full suite green.

## Out of scope

`constraints.md` adoption and the `DECISIONS.md` freeze (Phase 3); `ROADMAP.md`
retirement, brainstorming's issue reconciliation, and the full `project-memory` skill
rewrite (Phase 4); spec de-dating (Phase 5). The `--local-only` filename-inference
fragility and `select_store` constructing an unqueried `GitHubStore` remain deferred —
neither is touched by this phase.

## Note

Dated per the current convention. Phase 5 de-dates and migrates it.

2026-09-05: D1's fix made uniform across both stores (scan/list) — a GitHubStore-only tuple return forced isinstance branching at call sites and contradicted the Phase 1 Stores contract (Task 1 review, issue #44).
2026-09-05: halted runs may file — is_terminal gates on write safety, not clean completion; acceptance grep permits a retirement notice naming the retired path (Task 4 review, issue #44).
2026-09-05: the emitted `defer` command is a fill-in template, not a runnable command — `--title`/`--why` are placeholders. It carries `--from` (plan path, plus `, Task N` when the staged entry records one), since an omitted `--from` files as `from: user`, the marker reserved for deferrals that skip this gate; and `--occurrence N` when two staged deferrals share a finding id, so both can be filed and neither is attributed to the other's finding — an ambiguous id without it is refused, never guessed (Phase 2 final review, issue #44).
2026-09-05: the runner is NOT unchanged — it must stage a task number (nothing downstream can recover it) and persist deferrals across a resume (or staged entries and their issue numbers are erased). Both surfaced by the final review; the original claim that forge-run.py needed no change was wrong (issue #44).
