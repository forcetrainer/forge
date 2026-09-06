# Living Specs Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Merge sixteen dated specs into four per-system living documents, archive the originals, and add the frontmatter, changelog and lint rules that keep them living.
**Architecture:** Task 1 ships the grammar and its lint rules first, so every later migration is checked by the tooling as it lands. Tasks 2-5 migrate one system each, smallest first, by anchor-and-fold with mandatory section accounting. Task 6 archives the originals, repoints live citations, and changes the authoring convention in the skills.
**Tech stack:** Python 3 stdlib, unittest; markdown documents.
**Global Constraints:** stdlib only — frontmatter is parsed by hand, no YAML library. Parsers fail loud: name the file and the rule, never guess. `plans/` and `archive/` are frozen records and are never repointed. Every acceptance command runs the FULL suite (`python3 -m pytest -q`), never a single module.

### Task 1: Living-spec grammar and lint rules
- [ ] Done

**Files:**
- Modify: `scripts/forge_lint.py` (frontmatter parsing, the five rules, `--specs` corpus mode)
- Test: `tests/test_forge_lint.py`

**Spec:** Frontmatter, Changelog, Lint

**Interface:**
```python
def parse_frontmatter(lines): ...   # -> (dict, body_start_index); raises on malformed
def lint_living_spec(path, *, repo_root): ...   # -> list[str] defects, one per rule broken
def lint_spec_corpus(repo_root): ...            # -> list[str]; every offending spec, not the first
```
CLI: `forge_lint.py --specs` lints every file in `docs/forge/specs/` and exits non-zero on any defect.

The five rules, and nothing beyond them: no `YYYY-MM-DD` filename prefix; frontmatter parses and `system` equals the filename stem; every `supersedes` path resolves relative to `docs/forge/`; `## Changelog` present with every entry matching `YYYY-MM-DD: <text>`; every `amended by [<id>]` names a system that exists. Files under `archive/` are never linted.

**Tests:**
- `system` disagreeing with the filename stem is a defect naming both.
- A `supersedes` path that does not resolve is a defect naming the path.
- A missing `## Changelog` is a defect; a malformed entry is a defect naming the line.
- `amended by [nosuchsystem]` is a defect; a real system id is not.
- A dated filename is a defect; the same content renamed is clean.
- A dated, frontmatter-less spec under `archive/` produces no defect.
- Corpus mode reports every offending spec in one run, not the first only.
- Malformed frontmatter (unterminated block, non-`key: value` line) raises naming the file and line.
- No third-party import is added.

**Acceptance:** `python3 -m pytest -q` shows no regression against the 837-passed baseline; `python3 scripts/forge_lint.py --specs` runs and reports the pre-migration corpus as non-compliant.

**Tier:** standard

**Depends on:** nothing.

### Task 2: pipeline.md
- [ ] Done

**Files:**
- Create: `docs/forge/specs/pipeline.md`
- Test: none — document migration; verification is the acceptance accounting below.

**Spec:** Systems, Merge method, Frontmatter, Section accounting

**Interface:** anchor is `2026-07-02-phase2-execution-efficiency-design.md`; folds `2026-07-02-phase1-pipeline-skill-edits-design.md`, plus this phase's own `2026-09-05-living-specs-design.md` (Self-migration). Frontmatter `system: pipeline`, `supersedes` listing all three sources at their CURRENT paths (`specs/<dated-name>`), so rule 3 resolves the moment the file is written. Task 6 rewrites them to `archive/specs/...` as part of the move — lint stays green at every task boundary rather than being knowingly red for four tasks.

Deliberate exception to the newest-is-anchor rule: `living-specs-design` is the newest source but covers only the spec-document convention, so it contributes a section rather than the document's spine. State the exception in the changelog rather than leaving a reader to infer it.

**Tests:** none — prose.

**Acceptance:** `python3 scripts/forge_lint.py --specs` reports no defect for `pipeline.md`; every `##` section of the three sources is present in `pipeline.md` or named in its changelog as dropped with a reason — verified by walking `grep '^## '` over the sources; `python3 -m pytest -q` shows no regression.

**Tier:** complex — reconciling superseded claims across specs written months apart, where a later document silently retired an earlier one's rule and only prose says so.

**Depends on:** Task 1.

### Task 3: codex-runner.md
- [ ] Done

**Files:**
- Create: `docs/forge/specs/codex-runner.md`
- Test: none — document migration.

**Spec:** Systems, Merge method, Frontmatter, Section accounting

**Interface:** anchor is `2026-07-15-forge-run-monitor-design.md`; folds `2026-07-13-codex-exec-runner-design.md` and `2026-07-03-phase3-codex-dual-harness-design.md`. Frontmatter `system: codex-runner`, `supersedes` at the sources' CURRENT `specs/<dated-name>` paths (Task 6 rewrites them on the move).

Known trap: the runner spec's §Session awareness describes `--notify` and the `UserPromptSubmit` hook, both of which were later REMOVED (see the archived decision log, 2026-07-14 entries). Do not carry a removed feature into a living document as though it ships.

**Tests:** none — prose.

**Acceptance:** `python3 scripts/forge_lint.py --specs` reports no defect for `codex-runner.md`; section accounting complete over the three sources; `grep -n "notify" docs/forge/specs/codex-runner.md` shows no claim that `--notify` exists; `python3 -m pytest -q` shows no regression.

**Tier:** complex — the same reconciliation, plus a removed subsystem still documented as present in its own source spec.

**Depends on:** Task 1.

### Task 4: project-memory.md
- [ ] Done

**Files:**
- Create: `docs/forge/specs/project-memory.md`
- Test: none — document migration.

**Spec:** Systems, Merge method, Frontmatter, Section accounting

**Interface:** anchor is `2026-09-05-retire-roadmap-design.md`; folds `2026-09-05-project-memory-engine-design.md`, `2026-09-05-deferrals-as-issues-design.md`, `2026-09-05-constraints-design.md`. Frontmatter `system: project-memory`, `supersedes` at the sources' CURRENT `specs/<dated-name>` paths (Task 6 rewrites them on the move).

Known trap: each of the four names the others' work in its "Out of scope (later phases)" section. Those sections describe a sequencing that no longer exists — all four phases shipped — and must not survive as if the work were still pending.

**Tests:** none — prose.

**Acceptance:** `python3 scripts/forge_lint.py --specs` reports no defect for `project-memory.md`; section accounting complete over the four sources; `grep -n "later phase\|Phase [1-5]" docs/forge/specs/project-memory.md` shows no pending-work claim; `python3 -m pytest -q` shows no regression.

**Tier:** complex — four specs written as a sequence must read as one system with no sequence left in it.

**Depends on:** Task 1.

### Task 5: execution.md
- [ ] Done

**Files:**
- Create: `docs/forge/specs/execution.md`
- Test: none — document migration.

**Spec:** Systems, Merge method, Frontmatter, Section accounting

**Interface:** anchor is `2026-08-21-halt-precision-design.md`; folds `2026-08-21-review-continuity-design.md`, `2026-07-17-phase12b-claude-dispatch-parity-design.md`, `2026-07-17-phase11-inline-finding-process-design.md`, `2026-07-17-phase10-codex-inline-design.md`, `2026-07-16-phase7-scope-autonomy-design.md`, `2026-07-16-tier-policy-recalibration-design.md`. Frontmatter `system: execution`, `supersedes` at the sources' CURRENT `specs/<dated-name>` paths (Task 6 rewrites them on the move).

Known traps: the disposition matrix is stated in three sources at different widths — the three-way-provenance version in halt-precision is current. The terminal doc-sync stage exists on Codex only and is open work on Claude (issue #52); it must not read as shipped on both. Phase 10 documents inline finding-handling that Phase 11 replaced.

**Tests:** none — prose.

**Acceptance:** `python3 scripts/forge_lint.py --specs` reports no defect for `execution.md`; section accounting complete over the seven sources; the merged document states the disposition matrix exactly once, with three provenance values; `python3 -m pytest -q` shows no regression.

**Tier:** complex — seven specs, three of which restate the same matrix at different widths, and one shipped feature that exists on one harness only.

**Depends on:** Task 1.

### Task 6: archive, repoint, and change the authoring convention
- [ ] Done

**Files:**
- Modify: the seventeen dated specs (sixteen pre-existing, plus this phase's own) → `docs/forge/archive/specs/` via `git mv`, with the non-authoritative header
- Modify: the four living specs' `supersedes` lists — `specs/<dated-name>` → `archive/specs/<dated-name>`, in the same commit as the move, so rule 3 never resolves against a path that stopped existing
- Modify: `docs/forge/constraints.md` — two `Source:` fields, **only** through `python3 scripts/forge_memory.py update-constraint` (direct edits are hook-denied)
- Modify: `docs/forge/execution-loop.md`, `docs/forge/running-on-codex.md` (spec links)
- Modify: `scripts/forge_common.py` (3 references), `scripts/forge-run.py` (1)
- Modify: `tests/test_forge_lint.py` (2 fixture paths)
- Modify: `skills/brainstorming/SKILL.md` (the spec-path convention), `skills/planning/SKILL.md`, `skills/project-memory/SKILL.md` where each names the dated form
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (lockstep `0.12.0`)

**Spec:** Citations, Skill and convention changes, Acceptance

**Interface:** none. Content contracts:
- The brainstorming skill instructs: amend the owning system's spec in place; create `docs/forge/specs/<system>.md` only for a genuinely new system. It no longer names a dated filename anywhere.
- The archive header matches `docs/forge/archive/DECISIONS.md`'s form.
- `plans/` and `archive/` citations are left untouched, deliberately.

**Tests:** none — prose and moves; verification is the acceptance commands.

**Acceptance:** `ls docs/forge/specs/` lists exactly `codex-runner.md execution.md pipeline.md project-memory.md`; `ls docs/forge/archive/specs/ | wc -l` is 17 dated specs plus the archive header file, whose form (one directory-level header, or a banner per file) is the implementer's choice; no TRACKED file outside `docs/forge/plans/` and `docs/forge/archive/` cites a dated spec path (an untracked, gitignored local settings file is out of scope, and the four living specs' own `supersedes` lists naming `archive/specs/2026-...` are the citation, not a stale one); `grep -rn "YYYY-MM-DD" skills/brainstorming/SKILL.md` returns nothing; `grep -h '"version"' .claude-plugin/plugin.json .codex-plugin/plugin.json` shows `0.12.0` twice; `python3 scripts/forge_lint.py --specs` exits 0; `python3 -m pytest -q` shows no regression.

**Tier:** standard

**Depends on:** Task 2, Task 3, Task 4, Task 5.
