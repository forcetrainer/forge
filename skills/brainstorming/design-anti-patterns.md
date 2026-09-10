# Design Anti-Patterns

This is the hunting list for a cold reviewer validating a spec against the codebase, and
the single tuning surface for what that review looks for. To change what the reviewer
hunts, edit this file. It is loaded by reference (`@design-anti-patterns.md`), never
inlined — no other file restates a Gate. Entries use the Trigger / Gate / Instead format
of `testing-anti-patterns.md`. An entry enters on **two independent observations** — one
incident does not mint a permanent rule.

## 1. Designing from assumption instead of from the code

**Trigger:** The design depends on how another function, system, or migration behaves,
and you're about to write the design without having looked.

**Gate:** Three questions. Can you state what a depended-on function does, including its
side effects, without guessing? Did you read an instance of the thing you're relying on,
or infer its behavior from how codebases usually work? What does the thing you're
replacing re-derive or recompute on every pass that your design computes once?

**Instead:** Read the dependency before designing against it. Look at an actual instance
rather than inferring one. Name what the replaced path recomputes so the design can
decide, deliberately, whether to keep computing it or compute it once.

This entry is backed by two required verdict fields (`execution` spec: Document review
contract): `dependencies_read` for the first two gates, `replaced_system` for the third.
Deleting this entry does not remove those fields — they stay required and are checked to
agree with what this entry says, independent of whether the entry still exists here.

Evidence: one September 2026 production run produced eleven failures from this pattern —
six from an unread dependency (two of them introduced by fixes for other defects), five
from a replaced system's unwritten guarantee, plus a redo from an inferred-not-checked
migration claim contradicted by four earlier migrations in the same folder. Independently,
`testing-anti-patterns.md` entry 3 (doubles shaped by assumption rather than an observed
instance) named the same shape for test doubles before that run.

## 2. A criterion a conforming-but-wrong implementation would satisfy

**Trigger:** You're about to accept an acceptance criterion or interface description as
adequate.

**Gate:** Can you construct an implementation that satisfies this criterion literally and
is still wrong? A spec can be complete and faithfully implemented and still specify the
wrong thing — this gate checks sufficiency, not completeness.

**Instead:** Tighten the criterion until the wrong implementation you constructed no
longer satisfies it, or state directly what the criterion is missing.
