# Claude Dispatch Parity (Phase 7 canon on Claude) — ARCHIVE (fixture)

**Historical. Not authoritative. No new entries.**

Trimmed fixture derived from
`docs/forge/archive/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md`,
owned by the test suite rather than read live off disk. Kept realistic — the
same headings and content shape as the source — but pared to what
`tests/test_forge_lint.py` actually exercises.

## Scope

- In: extract classification + convergence from `forge-run.py` into shared
  `scripts/forge_dispose.py` (pure functions + a CLI); a sequential
  orchestrator-driven Claude dispatch loop that calls the CLI.
- Out: the terminal doc-sync stage on the Claude path; any parallel
  implementation of substantive tasks on Claude.

## The shared decision helper

Move the pure decision logic out of `forge-run.py` into `forge_dispose.py`:
classification, verdict parse, and convergence functions. Imports
`Finding`/`Verdict` from `forge_common` as a plain module so there is exactly
one `Finding` class identity. `forge-run.py` imports these names from
`forge_dispose` and re-exports them, so existing tests keep working
untouched.

## Claude execution model

The Claude dispatch path becomes an orchestrator-driven sequential loop,
identical in shape to `forge-run.py`. Per task: dispatch implementer, run
acceptance, dispatch reviewer, decide via `forge_dispose`, act on the
decision (rework / halt / pass), committing the task on pass.

## Retirements

`review-packet.py` stays the Codex mechanism only, documented as
Codex-path-only. `SKILL.md`'s dispatch branch is rewritten to the
sequential-loop canon; `README.md` gains a serial-by-design note.
