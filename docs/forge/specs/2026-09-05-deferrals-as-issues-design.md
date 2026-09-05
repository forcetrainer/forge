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

Unchanged during a run. Only close-out changes.

- **Codex** — `forge-run.py` aggregates `defer`-disposition findings into `run.json`
  under `deferrals`, exactly as today. The runner **never files an issue** and never
  writes a durable record. At completion it **stages and emits**: prints each staged
  deferral plus a ready-to-run `forge_memory.py defer` command.
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

## User-initiated deferrals

A deferral the user asks for mid-session ("I want to work on this later") files
**immediately** through `forge_memory.py defer` with `from: user`. No close-out gate —
the gate exists to put judgment in front of machine-generated deferrals, and a user
request already carries it. `follow-up` is whatever the user states, defaulting to
`backlog`.

## Staging and idempotency

- Staged shape in `run.json` is the existing finding dict. No new runner fields.
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
- `GitHubStore.list`'s `errors` out-parameter is replaced by a `(records, errors)`
  return. One method must not switch between raising and collecting based on whether a
  caller passed a mutable list.

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
- Repo-wide grep: no live reference to `docs/forge/DEFERRALS.md` outside
  `docs/forge/archive/` and this spec's changelog.

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
