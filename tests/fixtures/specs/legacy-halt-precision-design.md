# Halt Precision — ARCHIVE (fixture)

**Historical. Not authoritative. No new entries.**

Trimmed fixture derived from `docs/forge/archive/specs/2026-08-21-halt-precision-design.md`,
owned by the test suite rather than read live off disk, so a plan-grammar
change elsewhere never breaks lint tests for reasons unrelated to lint
itself. Kept realistic — the same headings and content shape as the source —
but pared to what `tests/test_forge_lint.py` actually exercises.

## Scope

- In: plan/spec lint at run start (`scripts/forge_lint.py`, new, shared);
  honoring the `convergence: "resolved"` label.
- Out: convergence rules, the backstop, `--autofix` semantics.

## Plan lint

New shared module `scripts/forge_lint.py` — pure functions + CLI. Runs at
**run start**: after the clean-tree precondition, before any dispatch.

Checks, all against **documented grammar only** — never taste, never style:

| check | failure |
|---|---|
| every `### Task N:` heading at level 3, numbers unique | names the offending heading and line |
| `**Tier:**` present, valid after normalization, justification present for complex/trivial | names the task and value |
| `**Spec:**` single line, no parenthetical or `;`, every name resolving uniquely in the spec | names the unresolvable or ambiguous heading |

**Reports every defect in one run**, never the first only. Exit 1 with the
full list on any error; warnings do not fail.

## Convergence label honored

A finding carrying `convergence: "resolved"` is dropped before disposition,
making "listed as resolved" behave identically to "omitted". The label is
honored only when the finding's canonical id is in the prior attempt's
carried-fix set; otherwise it is ignored and the finding is dispositioned
normally.

## Touch points

Changelog pointers on the amended specs, the roadmap status line, and a
lockstep version bump on both plugin manifests.
