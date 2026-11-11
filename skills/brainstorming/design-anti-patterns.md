# Design Anti-Patterns

The conditions a spec must meet before spec review, and the hunting list for the cold
reviewer validating it against the codebase. The author is held to this file and the
reviewer loads it; it is the single tuning surface for what either looks for. It is
loaded by reference (`@design-anti-patterns.md`), never inlined — no other file
restates a Gate.

The six conditions are fixed by the `pipeline` spec (Spec authoring). Each states what
must hold and what shows that it holds; work on a condition stops when that showing
exists. A failure is filed under the finding `kind` its condition maps to:

| Condition | Finding `kind` |
|---|---|
| Grounded | `groundedness` |
| Consistent | `contradiction` |
| Testable, Decided, Bounded, Complete | `sufficiency` |

Entries use the Trigger / Gate / Instead format of `testing-anti-patterns.md` and sit
under the condition they serve. An entry enters on **two independent observations** —
one incident does not mint a permanent rule. The bar applies to entries, not to the six
conditions.

## Grounded

**Holds when:** every claim the design makes about existing code is true of that code,
and every piece of existing code the design relies on is named.

**Shows:** a dependency record entry per piece of existing code — symbol, file, what it
does including side effects — each confirmed by reading the code, not by the record's
own citation.

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
contract): `dependencies_read` for the dependency gates, `replaced_system` for the
replaced-system gate. Deleting this entry does not remove those fields — they stay
required and are checked to agree with what this entry says, independent of whether the
entry still exists here.

Evidence: one September 2026 production run produced eleven failures from this pattern —
six from an unread dependency (two of them introduced by fixes for other defects), five
from a replaced system's unwritten guarantee, plus a redo from an inferred-not-checked
migration claim contradicted by four earlier migrations in the same folder. Independently,
the 2026-11 spec reviews of one repository found about four findings in ten were facts
about existing code the author had not read — an observation from different work and a
different author than the September run.

## Consistent

**Holds when:** no two statements in the spec, or between the spec and the specs it
cites, can both be true.

**Shows:** the whole document read against itself, including sections an amendment did
not touch. A `contradiction` finding's evidence quotes the two statements that cannot
both hold.

## Testable

**Holds when:** each requirement can be checked by an observation that fails when the
requirement is not met.

**Shows:** for each criterion, the wrong implementation that would pass it was looked
for, and either none exists or the criterion was tightened.

## 2. A criterion a conforming-but-wrong implementation would satisfy

**Trigger:** You're about to accept an acceptance criterion or interface description as
adequate.

**Gate:** Can you construct an implementation that satisfies this criterion literally and
is still wrong? A spec can be complete and faithfully implemented and still specify the
wrong thing — this gate checks sufficiency, not completeness.

**Instead:** Tighten the criterion until the wrong implementation you constructed no
longer satisfies it, or state directly what the criterion is missing.

## Decided

**Holds when:** every choice an implementer would otherwise make for the user is made in
the spec, or is stated as `<behaviour>: not constrained`.

**Shows:** no point where two reasonable implementations differ in a way the user would
care about and the spec is silent.

## Bounded

**Holds when:** the spec says what is out of scope, and nothing in it reaches past the
system it names.

**Shows:** each requirement traces to the stated purpose; what the design forecloses is
written down.

## Complete

**Holds when:** the requirements are enough for the stated purpose. This is the one
condition that rests on judgment — where the author judges a requirement incomplete, it
asks the user.

**Shows:** a walk from the purpose through each behaviour it needs finds each one stated
or deliberately left out.

## Reviewer bounds

- The reviewer checks each dependency record entry against the code and never accepts a
  claim because a citation is present.
- A record entry the code contradicts, and a dependency the design relies on that the
  record omits, are `groundedness` findings.
- A finding is a failed condition, never a preferred design.
- A `sufficiency` finding carries a demonstration: the concrete case that shows the
  condition failed.
- An unmentioned edge with no such case is not a finding.
- A behaviour the spec states as not constrained is closed.
