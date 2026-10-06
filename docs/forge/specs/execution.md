---
system: execution
supersedes:
  - archive/specs/2026-07-16-phase7-scope-autonomy-design.md
  - archive/specs/2026-07-16-tier-policy-recalibration-design.md
  - archive/specs/2026-07-17-phase10-codex-inline-design.md
  - archive/specs/2026-07-17-phase11-inline-finding-process-design.md
  - archive/specs/2026-07-17-phase12b-claude-dispatch-parity-design.md
  - archive/specs/2026-08-21-halt-precision-design.md
  - archive/specs/2026-08-21-review-continuity-design.md
---

# Execution

How a plan is executed: how a task is tiered and routed, whether it runs inline or
dispatched, what a reviewer is held to, and what the loop does with every finding —
fix, defer, seed, or halt. The model is one model on both harnesses; only the
substrate enforcing it differs. `scripts/forge_dispose.py` is the one implementation
of the decision, called in-process by the Codex runner and via its CLI by the Claude
orchestrator, so the same verdict and the same inputs produce the same decision regardless of
who acts on it (the Claude CLI's one missing input is named under The shared decision
helper). `skills/planning/SKILL.md` is the orchestrator-facing statement of this
contract; the Codex-mechanical half — process dispatch, packets, receipts, live logs
— belongs to the `codex-runner` spec.

## Tier policy

Three tiers, and **standard is the floor**. A task sits at standard unless evidence
moves it, and the burden of proof is on *leaving* standard in either direction.

| Tier | Reached by | Evidence contract |
|---|---|---|
| trivial | *downward* evidence | Purely mechanical — no design content (a rename, a single config value, one field through one call site). If mechanicalness cannot be shown, it is not trivial. |
| standard | **default** | No justification. May touch many files and carry a real test path — that is still standard. |
| complex | *upward* evidence | A **named** design decision or cross-cutting invariant that standard demonstrably cannot resolve. |

Not evidence for complex: file count, task category ("code implementation"), "touches
core", "feels complex". These name a shape, not a decision.

### The `Tier:` plan field

```
Tier: standard                          # floor — no justification
Tier: complex — <the named decision>    # e.g. "reconciles two conflicting retry semantics no single call site owns"
Tier: trivial — <mechanical rationale>  # e.g. "single enum value, one call site, no logic"
```

`standard` takes no justification and any trailing text is ignored; `complex` and
`trivial` require a non-empty justification after `— `. The **presence** of a
justification is mechanically checkable; its **quality** — a real decision versus a
rejected shape — is not, and is enforced at authoring by the planning skill's
self-review, never by a runner script. The justification persists in the plan and is
surfaced per task in the execution offer's tier breakdown, so an up-tier is visible
and overridable before anything runs.

### Routing — model and effort per tier

The first pass runs at each provider's **recommended default**, with one deliberate
exception, Codex complex (below). The stronger settings
are not deleted; they are what a human may bump a single task to at the escalation
gate after rework is exhausted. Nothing escalates model or effort automatically.

| Tier | Claude agent · profile | Codex model · effort |
|---|---|---|
| trivial | `forge:forge-light` · haiku | gpt-6-luna · low |
| standard | `forge:forge-standard` · sonnet · medium | gpt-6.1-sol · medium |
| complex | `forge:forge-deep` · opus · high | gpt-6.1-sol · high |

Codex routes three tiers onto two models. GPT-6 has no mid-tier model, and Sol is
priced where the old mid-tier was, so standard and complex share Sol and differ by
effort alone. Complex at sol·high departs from the provider's default of medium on
purpose: with the model shared, effort is the only thing that gives a complex task
more than a standard one. The tiers themselves stay three on both harnesses — the
`Tier:` field, its evidence contract and the Claude routing are unchanged. Routing is
absolute: the session's own model and effort settings never apply to a dispatched
worker. The Codex half of the table lives in `forge_common.TIER_MAP`, the single
update point on model churn (`codex-runner` spec).

### Enforcement

- **Codex:** `forge-run.py` validates the `Tier:` field on load. An off-floor tier
  with a missing justification is a loud contract error (exit 1), consistent with the
  runner's fail-hard-on-contract stance. The runner checks presence only.
- **Claude:** the planning skill's self-review enforces the justification before the
  offer. Its tier check is **directional**, not symmetric: every non-standard tier
  carries a valid, non-categorical justification, else it is pushed back to standard.

## Reviewer model

The reviewer's value is **fresh context** — an independent pass that does not reason
over the work already done and rationalize its defects — not a stronger model.

- **Reviewer tier = task tier.** The reviewer is a fresh process or subagent at the
  same tier as the task it reviews. Reviewer routing reads the same tier table as
  worker routing; a separate reviewer table is retired, because two tier tables
  silently diverge on a model-churn edit.
- **No strength escalation.** There is no second, stronger reviewer dispatched when
  the first finds issues. A finding drives same-tier rework by the implementer and a
  re-review at the same tier. Model strength never enters the review loop.
- **Final (integration) review** runs at the **plan's highest task tier** with fresh
  context — an all-standard plan gets a standard-tier final review, not a pinned
  ceiling.

A reviewer finding an issue is not a failure and never triggers escalation; it is the
loop working. Only a halt is a failure, and a halt is a human's call.

### Reviewer write discipline

A reviewer verifies by executing, not only by reading — and execution needs writes
(test harnesses create temp files and sockets; breaking a behavior means editing it).
The guarantee forge keeps is **the tree the orchestrator commits is the tree the worker
left**, enforced by the orchestrator, not by a sandbox.

- **No read-only sandbox.** Reviewer dispatches carry no sandbox override on either
  harness. Codex reviewers run under the `codex exec` default (workspace-write, network
  off); Claude reviewers run as any agent does.
- **Mutate only in a scratch copy.** Every mutation a reviewer makes for verification
  happens in a copy of the tree it makes itself in a temporary directory — never in the
  repository. `git stash` in any form is forbidden. The reviewer chooses the copy
  mechanism; it knows what its harness needs.
- **Break the code.** For each behavior the task's tests claim to cover, the reviewer
  mutates that behavior in the scratch copy and confirms a named test fails. Evidence
  rules: the scratch baseline passes before any mutant; one behavior-specific mutant at
  a time, baseline restored between mutants; the failure must be attributable to the
  assertion that checks the behavior — a collection error, an import failure or an
  unrelated failure is inconclusive, not a kill. A test that stays green on a broken
  behavior is a finding; the mutation is its evidence. This is a standing review step
  in the shared agent contracts, not a per-prompt addition.
- **Never re-run acceptance against the worker's tree.** The orchestrator has already
  run every acceptance **command clause** and hands the results to the reviewer
  (Reviewer input, below); repeating them on the unchanged tree is waste. Running a
  test command in the scratch copy — baseline or mutant — is verification, not a
  re-run, even when that command also appears in Acceptance. Prose acceptance clauses
  have no execution and remain the reviewer's checklist obligation.
- **Unchanged-repository check.** The orchestrator fingerprints the repository before
  each reviewer dispatch (cold or resumed, task or final) and again on every exit —
  verdict returned, crash or timeout alike. The fingerprint covers the working tree
  (tracked and untracked non-ignored content, captured via a temporary index), the
  real index, HEAD and the current branch ref, so a reviewer that commits, stages,
  checks out or moves a ref is caught as surely as one that edits a file. The
  fingerprint covers exactly what a commit would record, no more: a change a commit
  cannot record — bytes a clean filter normalizes away, a dirty working tree inside a
  submodule — is outside it by design, because the guarantee is about the tree the
  orchestrator commits. A mismatch is
  a halt of class **`reviewer-wrote`** — a task escalation on a task review, a stage
  escalation on the final review — naming the changed state; the verdict is discarded
  whatever it said. Like every halt class it **freezes** — but what it parks under the task's or
  stage's ref is the **pre-review capture**: the tree as it stood when the review began,
  which is exactly the worker's work and nothing of the reviewer's. The fingerprint
  already holds that tree, so the freeze is a commit of it; the reviewer's changes are
  recorded in the halt record as the fingerprint diff and then discarded from the
  working tree, which returns to the checkpoint. Reconcile therefore restores the
  worker's attempt alone, never re-injecting a reviewer write as task work, and the
  class-aware brief names the reviewer write as the cause of the halt. Because the
  fingerprint also records HEAD and the branch, the halt path restores them too — the
  branch ref is pointed back at the recorded HEAD sha and HEAD re-attached to that
  branch — the one place the orchestrator ever moves a ref, so a reviewer-made commit
  or branch switch cannot survive into the resumed run; such a commit's sha is named in
  the halt record and otherwise left unreachable, never deleted. It is not a contract error (which writes no receipt and
  freezes nothing). The freeze, the ref restore and the tree's return to the checkpoint
  *are* the halt path; what the halt forbids is continuing: no retry, no resume within
  the same invocation, and it propagates through the resume wrappers without a cold
  fallback or coverage retry — a reviewer that wrote to the repository broke its
  contract, and silently carrying on would hide that. Reconcile happens only on a later
  invocation, as for every halt. A fingerprint that cannot be taken (git failed) is the
  packet-generation-class contract error it always was. One
  helper, in `forge_git`, shared by both harnesses (the Claude loop calls its CLI, as it
  calls `forge_dispose`).

## Execution mode — inline or dispatch

The **mode** is chosen first and is harness-independent; the harness only determines
*how the dispatch branch runs*. **Dispatch is the default, and the burden of proof is
on choosing inline.** Wall-clock is never a reason either way: dispatch's per-task
fresh-context spawn is often slower than inline, an accepted tradeoff, since speed was
never why dispatch exists (constraint: `inline-never-for-speed`).

Dispatch stands on two independent reasons, either sufficient alone:

1. **Context economy** — worker context is born, used, and discarded; inline context
   compounds forever across a session.
2. **Error correction against the orchestrator's own drift** — a long-running
   orchestrator accumulates a narrative of its own prior conclusions, and writing a
   brief from that narrative instead of the source document silently propagates a
   paraphrased decision. A fresh worker pointed at the spec, plan and constraints is
   structurally forced to ground itself in the authoritative text.

**Inline, only when both hold:** a later task genuinely benefits from having seen an
earlier task's in-context output, **and** the change is genuinely simple and small.
"Dispatch machinery feels like overkill" is not a standalone reason — the second
condition is the same gate inline's no-independent-reviewer safety case rests on, so
loosening it undercuts that decision rather than just this one. Inline work runs on
the session model; the offer says so. **Inline is the same act on both harnesses.**

## Plan lint

`scripts/forge_lint.py` validates plan and spec documents against **documented
grammar only** — never taste, never style — before any dispatch: after the clean-tree
precondition, before the first task. On Codex `forge-run.py` calls it in-process; on
Claude the orchestrator invokes the CLI. The same CLI, same rules, also runs at plan
authoring as part of Plan review (`pipeline` spec: Plan review); the run-start
invocation stays, since a plan can be edited between review and dispatch.

| check | failure |
|---|---|
| every `### Task N:` heading at level 3, numbers unique | names the offending heading and line |
| `**Tier:**` present, valid after normalization, justification present for complex/trivial | names the task and value |
| `**Goal:**` present, a single non-empty line | names the defect |
| `**Spec:**` single line, no parenthetical or `;`, every entry resolving in its declared spec by the `pipeline` spec's resolution order (Plan documents) | names the unresolvable or ambiguous heading |
| `**Depends on:**` references existing task numbers, no cycles | names the missing task or the cycle |
| `**Acceptance:**` present per task | names the task |
| every `**Acceptance:**` clause beginning with inline code parses as a command clause (`pipeline` spec: Acceptance clause grammar) | names the task and line, quotes the clause, lists the legal outcomes |
| `**Tests:**` parses — bulleted form, or `none — <reason>` | names the task and quotes the offending line |
| checklist generates for every task and for `--final` | names the task; an empty checklist is a **warning**, not an error |
| every **changed** spec section, in every spec the plan declares, is named by some task's `**Spec:**` line | names the spec and the unclaimed section |
| `**Spec files:**` parses; every path is a living spec; no two share a spec id | names the path and the defect |
| `**Spec files:**` and `--spec` are not both given | names both |
| every `**Spec:**` entry carries a declared `[<spec id>]` when the plan declares more than one spec file, and none carries an undeclared one | names the task and entry, lists the declared ids |
| a spec changed since the merge base that the plan does not declare | a **warning**, not an error, naming the undeclared spec file |

It **reports every defect in one run**, never the first only — the same
anti-one-per-lap principle the reviewer's coverage requirement installs. Any error is
a contract error: exit 1 with the full list, no run dir, nothing dispatched. Warnings
do not fail.

Lint exists because a defect in a plan or spec can never be classified `in-diff` — a
task's diff contains code, not the document specifying it — so every such defect would
otherwise land `pre-existing × contract-breaking` and halt for a human round-trip on
what is really a syntax error.

**Spec coverage is that same argument applied one level up.** The flow amends a spec
before the plan is written **and commits it**, so the baseline is the branch's **merge
base with the default branch**, never `HEAD`: against `HEAD` the amendment is already
committed by the time lint runs, nothing reads as changed, and the rule is inert in the
exact flow it exists for. Every section whose content changed since the merge base must
be named by at least one task's `**Spec:**` line. When no merge base resolves — the
default branch itself, an unborn or detached HEAD — the baseline falls back to `HEAD`
and the rule degrades to inert rather than failing loudly, since a plan is not wrong
merely because lint cannot establish what the branch changed. A spec with no committed
version — a genuinely new system — treats every section as changed.

**The rule runs once per declared spec** (`pipeline` spec: Plan documents,
`**Spec files:**`), so a plan amending three specs is checked against all three. A
spec's claimed sections come only from `**Spec:**` entries that resolve in that spec: an
entry carrying its id, or a bare entry in a plan declaring one spec. An entry that does
not resolve claims nothing here and is reported once, by the entry row.

**An undeclared changed spec is a warning.** A plan amending two specs and declaring
one is checked against one and silent on the other — the gap `--spec` had, moved from
the caller to the author. Lint therefore warns, naming the file, for each `.md` file
that sits directly in a directory holding a declared spec, is not itself declared, and
was added or modified between the merge base and the working tree in some section other
than the two exempt ones. It is a warning, not an error, because the baseline is the
whole branch: on a branch carrying several plans, a spec an earlier plan built is
changed and rightly undeclared by a later one. The warning's owner is the plan's author
(`pipeline` spec: Plan review). Deleted files do not warn. Like the changed-section
rule, it is inert when no merge base resolves, and a legacy plan is exempt.

**`## Changelog` and `## Risks / constraints` are exempt.** Both are structurally
unbuildable: every other section states a requirement a task can deliver, while these
two record history and judgment. No task is ever assigned to add a changelog line or a
risk entry, so demanding they be claimed would train the reader to ignore the rule. The
exemption is that principle, not a list to extend — a section is exempt because nothing
in it can be built, never because claiming it is inconvenient. An unclaimed
changed section means the plan cannot deliver what the spec now asserts, and that gap
is not discoverable from any single task's diff: it surfaces mid-run as a reviewer
finding against code that is doing exactly what its task asked. Catching it before the
first line is written costs seconds; catching it at task 5 costs a halt and a
re-plan.

The rule is deliberately scoped to *changed* sections. Most sections of a living spec
describe behavior that already shipped, and requiring a plan to claim all of them would
warn on a dozen every run until the warning was ignored. A plan that genuinely should
not build a changed section still has an escape that is on the record rather than
silent: name it on a task's `**Spec:**` line and let the reviewer mark it `n/a` with a
reason.

**Lint must never reject a legal plan.** `**Spec:**`, `**Global Constraints:**` and
prose acceptance are all optional per the planning skill; absence is never an error.
And lint never edits: a plan or spec is the contract, and a worker rewriting its own
contract is self-dealing, so a document defect is always reported for a human, never
auto-corrected mid-run.

`--specs` runs the living-spec corpus checks instead of a plan; those rules belong to
the `pipeline` spec.

## Contract checklist

Nothing else defines *done* for a review, so a review returns when it has found
*something* and findings arrive one per lap. `scripts/forge_checklist.py` derives, from
existing plan and spec grammar, the checklist a reviewer's `coverage` array must
satisfy — no new authoring burden, no new plan fields.

| id form | source |
|---|---|
| `spec:<heading>` | **final review only:** each spec section named on any task's `**Spec:**` line, union across all tasks, resolved via `extract-brief.py`'s `find_spec_sections`; `<heading>` is the section's heading text, whitespace-collapsed — not a slug. When the plan declares more than one spec file the id is `spec:[<spec id>] <heading>` — `<spec id>` the frontmatter `system` value, one space after the bracket — so two specs' same-named sections never collide. The id is built from the **resolved** spec and heading, never from how the entry was written, so a bare entry and a bracketed one naming the same section yield one id |
| `g<N>` | each clause of the plan header's `**Global Constraints:**` |
| `t<N>.t<M>` | each test case listed on task N's `**Tests:**` line — a **coverage** item on that task's review only, but **citable** at the final review too, so a seeded finding can still name the case it was raised against |
| `t<N>.a<M>` | each **prose clause** of task N's `**Acceptance:**` field (`pipeline` spec: Acceptance clause grammar) — command clauses are checked deterministically by the acceptance runner and would be dead checklist weight |
| `t<N>` | final review only: task N's title, as an integration item |
| `t<N>.c<M>` | **plan review only:** each **command clause** of task N's `**Acceptance:**` field, numbered among command clauses in field order, emitted only by `forge_checklist.py`'s plan-promise builder — never a coverage item or citable ref in a task or final review, where the acceptance runner checks it; `build_task_checklist`, `build_final_checklist` and `citable_refs` never return one |

**A task's checklist is what that task promised, not what the spec asserts.** Spec
sections are a **final-review** source only. A task is allocated a slice of a spec
section by the plan, and nothing in the packet says which slice — so asking a per-task
reviewer to render `coverage` on the whole section poses a question whose honest answer
is always "not yet," and whose only truthful status (`violated`) obliges a backing
finding. That manufactured finding cites the section, satisfies the named-evidence
rule, and lands `in-diff × contract-breaking` → `fix`: rework generated by the
checklist, not by a defect. The whole-spec question is real and is asked once, by
`build_final_checklist`, at the altitude where `run_base` *is* the diff base and the
answer can be true. This is the `seed` rationale applied to coverage: an integration
obligation is judged at integration.

The task's `**Spec:**` line is unchanged and still pulls its sections into the worker
brief and the review packet — as **context** the reviewer reads, never as an item it
must return a verdict on.

**Covering and citing are different acts, and only covering was ever the problem.**
Two sets exist per review:

| set | what it is | what it governs |
|---|---|---|
| **coverage items** | the task's own promises (`t<N>.t<M>`, `t<N>.a<M>`, `g<N>`) | every id the reviewer must return a `coverage` verdict on |
| **citable refs** | the coverage items **plus** the `spec:<heading>` id of every section the task's `**Spec:**` line names | every id a finding's `contract_ref` may cite |

The coverage set is what a reviewer is *obliged to answer for*, and posing a whole spec
section there is what manufactured findings. The citable set is what a finding may
*point at* as the obligation it violates, and narrowing that was a mistake: a reviewer
that finds real code contradicting the spec must be able to say which section, or the
named-evidence rule downgrades it to an improvement and a genuine defect is deferred
instead of halting. Observed 2026-09-06 — a reviewer correctly identified a
`pre-existing` contract-breaking defect, had no citable id for it, emitted
`contract_ref: null`, and the finding dispositioned to `defer` rather than reaching the
human gate it was written for. In the final review, citable refs are every final coverage item plus every
task's test-case id, so a seeded finding keeps the citation it was written with.

**Citable ids are printed, never derived.** Every reviewer input — the Codex review
packet and the Claude reviewer prompt alike; task and final, discovery and verification
— carries a `## Citable refs` section listing that review's
citable set, one id per line, and the verdict instruction tells the reviewer to copy a
`contract_ref` verbatim from it. The section opens with one line stating its role:
these are ids a finding's `contract_ref` may cite, not coverage items — `coverage`
answers the `## Contract checklist` only. Unlabeled, the list read as more items to
cover: observed 2026-10-03, both reviewers shown it covered its `spec:` ids and failed
validation on unknown checklist ids. Validation stays exact membership: a near-miss id is a
defect, never normalized to the id it resembles (`parsers-fail-loud`). Observed
2026-10-02: 6 of 16 validation retries across one repo's runs cited invented slugs
(`spec:verification`, `spec:python-inspector-contract`) — the instruction said
`spec:<slug>` and no packet printed the real id.

CLI: `forge_checklist.py <plan.md> [--spec <spec.md>] [--task N | --final] [--out
<path>]` (`--spec` for a legacy plan only; otherwise the specs come from the plan's
`**Spec files:**`) → JSON `[{"id", "source", "text"}]` plus a rendered `## Contract checklist`
markdown section. Codex's `review-packet.py` imports the module; the Claude
orchestrator invokes the CLI. An unresolvable `**Spec:**` name raises, reusing
`find_spec_sections`' existing raise — never a silently thin checklist.

An empty checklist is a **library-level** contract error: invoked directly, CLI or
import, `forge_checklist.py` raises naming the absent source, because an author who
explicitly asks for a checklist and gets nothing has a defect to see. At the
**runner/orchestrator** layer it is a **skip**, not an error: a task with no
`**Tests:**`, no `**Global Constraints:**` and an `**Acceptance:**` of nothing but
command clauses is a legal plan with no contract material to cover, and forcing an
error there would make legal plans unexecutable. Dropping spec sections as a task
source makes this case materially more reachable than before, which is what the plan
lint warning on an empty checklist is for. The review dispatches with no
checklist section, no `coverage` validation and no retry, and the receipt records
`coverage_skipped`, surfaced in the end-of-plan summary. Skipped visibly, never
silently: a plan that earns no coverage enforcement says so on its receipts.

Verification laps carry the **reduced** checklist — only the items named by an
outstanding finding's `contract_ref` — not the full one. Trivial-tier tasks generate no
checklist: they skip reviewer dispatch entirely.

## Reviewer verdict contract

The reviewer emits exactly one JSON object as its final message; the last parseable
object is authoritative. `forge_common.REVIEW_VERDICT_INSTRUCTION` holds the
instruction text and is shared **verbatim** by the Codex runner's reviewer prompt and
the Claude reviewer subagent's prompt, so both harnesses' reviewers are held to an
identical contract.

```json
{
  "verdict": "pass" | "findings",
  "coverage": [
    {"id": "t3.a1", "status": "satisfied" | "violated" | "n/a" | "unverifiable", "evidence": "file:line, hunk, or why n/a / why unverifiable", "finding": "f1"}
  ],
  "findings": [
    {
      "id": "f1",
      "summary": "one line",
      "location": {"file": "path", "lines": "12-20"},
      "provenance": "in-diff" | "pre-existing",
      "impact": "contract-breaking" | "improvement" | "unverifiable",
      "contract_ref": "an id copied verbatim from the packet's ## Citable refs" | null,
      "convergence": "resolved" | "carried" | "new" | null,
      "carried_from": "f1" | null,
      "repair_task": {"title": "…", "files": ["…"], "spec": "…", "tests": ["…"], "acceptance": ["…"], "tier": "standard"} | null
    }
  ]
}
```

- `provenance` as emitted is two-valued. The runner recomputes it and may resolve it to
  `in-run`, a value the reviewer never emits.
- **Reporting is unconditional on provenance.** Every finding the reviewer sees is
  emitted; the runner derives the disposition. A reviewer must never withhold a finding
  because the code predates this diff — `pre-existing × contract-breaking` is a real cell
  whose whole purpose is to halt for a human, and it can only fire on a finding that was
  reported. Withholding also loses the finding outright rather than downgrading it: the
  runner parses the verdict JSON and discards surrounding prose, so a defect mentioned
  only in commentary reaches no receipt, no deferral, and no gate. Observed 2026-09-06 —
  a reviewer correctly identified an unchecked-enum defect, judged it out of scope
  itself, and omitted it from the verdict; it survived only because a human read the
  chat transcript (#63).
- `impact` is `contract-breaking` only when `contract_ref` names **a citable ref for
  this review** — a coverage item, or a `spec:<heading>` section the task declares, as
  listed in the packet's `## Citable refs` (Contract checklist, above). A null `contract_ref`, or one naming anything outside that set,
  downgrades the finding to `improvement` regardless of the reviewer's label — the
  named-evidence rule, mirroring the tier-policy floor. Membership, not non-nullness, is
  the test: a bare presence check costs the reviewer one arbitrary string, which makes
  the strongest disposition in the matrix the cheapest claim to assert. The citable set
  is deliberately wider than the coverage set, so a finding against code that
  contradicts the spec can still name what it breaks without the reviewer being asked
  to certify the whole section.

  **Two mechanisms enforce this, and they are not the same.** Where a citable set is
  supplied, a non-member ref is a *validation defect* — one retry naming it, then a
  contract error — so the reviewer is asked to correct the citation rather than having
  its finding silently reclassified. Where none is supplied (`validate_contract_refs`
  skips a falsy set, and `derive_disposition` tests only non-nullness), the
  named-evidence rule degrades to the presence check and the downgrade to `improvement`
  is what remains. Membership is therefore enforced at the callers that supply the set,
  not by the disposition matrix itself; a caller that omits it gets the weaker
  guarantee, silently.
- `impact: "unverifiable"` is the honest verdict for a finding the reviewer cannot
  settle from the diff in front of it — the agent contract has always called that a
  valid answer, and until now the schema had nowhere to put it. It requires a reason in
  the finding's `summary`, carries no `repair_task`, and dispositions to `seed`
  (disposition matrix, below): logged, never reworked in-loop, carried into the final
  review where the whole-plan diff makes it answerable. Cannot-verify is not
  wrong-and-unfixed; for an obligation whose proof is the whole chain, a per-task
  reviewer structurally cannot answer, and ordering a repair on that basis is how a
  speculation becomes a code change and then a green→red regression halt.
- `convergence` and `carried_from` are set only on a re-review, labeling each current
  finding against the prior attempt's findings supplied in the packet. `resolved`
  findings may be listed or omitted; both behave identically.
- `repair_task` is required on a finding in the scope-decision cell (pre-existing ×
  contract-breaking) — the payload of that human gate, never auto-applied — optional on
  any other verifiable finding, and `null` on an `unverifiable` one. Which findings
  halt is the autonomy mode's business, not the reviewer's: in `gate` mode every
  finding halts, and the requirement does not widen with it.
- A finding id names exactly one finding within a verdict. Duplicate ids inside one
  verdict are a validation defect: the id is the runner's only handle on a finding, and
  the carried/resolved sets, the honored `resolved` label and staged-deferral selection
  all treat it as unique. Ids are reviewer-authored per review; a reviewer-authored
  `id` containing `:` is a validation defect, because the runner reserves that
  character for the identity below.
- **Finding identity is runner-owned and scoped to the review that raised it.** The
  runner stamps every classified finding with an `identity` of the form
  `<scope>:<canonical id>` — scope `t<N>` for task N's review and `final` for the
  final review, canonical id `carried_from` else `id`. A `carried_from` that already
  carries a scope prefix (`t<N>:` or `final:`) is taken verbatim **only when it names a
  prior finding this review was given** — a seed replayed into the final discovery
  packet, or an outstanding finding on a verification lap; that is how a seeded
  per-task finding keeps the identity it was raised under. A scoped `carried_from`
  naming anything else is a validation defect (one retry naming it, then a contract
  error), never prefixed and never kept: otherwise a reviewer could borrow an approved
  identity on a finding it invented and dodge the scope-decision halt, the same
  escape hatch the honored-`resolved` guard closes. One identity serves every comparison — the carried/resolved sets within a
  review, the honored `resolved` label, and every run-wide set (`approved`,
  `seeded_findings`, `unverified`, a halt record's outstanding findings). Reviewers
  may reuse `f1` in every review; the runner never confuses task 2's `f1` with task
  5's, and an approval given to `t2:f1` exempts exactly that finding (#128).
- An unparseable verdict is a loud failure naming the cause — a contract error (exit 1),
  distinct from a task halt (exit 2). Never guessed at, never silently retried.

The contract is shared, so a Codex-only capability that would fork it is out: verdict
shape is never forced by `--output-schema` on one harness.

### Coverage validation, and coverage is discovery-only

`coverage` is **required on discovery verdicts**, `pass` included, and **omitted on
verification verdicts**; a verification verdict that carries it anyway is accepted, not
penalized. Every packet marks which kind of review it is (`## Review kind`) so the
reviewer knows which contract applies. Verification's scope is the prior findings plus
the repair delta — the discovery pass's coverage is what carries the exhaustiveness
guarantee, and a redundant full sweep on verification is what killed reviewers
mid-verdict on the largest reviews.

`forge_dispose.py` validates the array alongside the verdict parse — the reviewer
proposes, the runner decides:

- every checklist id appears exactly once; a missing, unknown or duplicated id is a
  defect;
- every `violated` entry carries `finding`, naming a finding in the same verdict that is
  **effectively** contract-breaking — `impact: "contract-breaking"` **and** a non-null
  `contract_ref` that is a citable ref for this review, i.e. a finding the named-evidence
  rule does not downgrade. A missing `finding`, one naming no finding in the verdict, one
  naming a finding of any other impact, or one naming a finding whose `contract_ref` is
  null or not citable is a defect; otherwise a violated item could be backed by a
  finding that dispositions to `defer` and is never fixed or halted. Several entries may
  name the same finding — one defect commonly breaks several items, and the backing
  link runs from the coverage entry to the finding, so the finding's single
  `contract_ref` must be citable but never has to equal each violated id. That equality was the previous
  rule and is **replaced, not kept as an alternative**: it forced a reviewer to split
  one defect into a finding per violated item or under-report what it breaks. Observed
  2026-10-02: 12 of 16 validation retries across one repo's runs were unbacked
  `violated` ids, one of them naming six items at once;
- `finding` on an entry whose status is not `violated` is a defect;
- every finding's non-null `contract_ref` is a citable ref for this review — an id
  listed in the packet's `## Citable refs`; one naming anything else is a defect.
  Together with the `violated` rule above this closes the loop in both directions — a
  violated item must have a finding, and a finding must cite a real item;
- `evidence` is non-empty on every entry. `n/a` requires a reason in `evidence` — it is
  the honest escape for a checklist item the diff cannot touch, and it is what keeps the
  requirement from degrading into rubber-stamping.
- `unverifiable` requires a reason in `evidence` and, unlike `violated`, obliges **no**
  backing finding. That asymmetry is the point: `violated` demanding a finding is
  correct for a known break and coercive for an unsettled one, and a reviewer with no
  truthful status left reaches for the one that manufactures work.

On invalid: **one retry**, which **resumes the reviewer that emitted the invalid
verdict** — on discovery too, since by then that reviewer's session exists. The prompt
is the defect list and the instruction to resubmit the full verdict, changing only what
the defects require; no packet, since the resumed reviewer holds it. A failed resume
falls back to a cold spawn with the full packet plus the defect list, recorded as
`resume_fallback` (Session continuity). The retry corrects one verdict; it is not a
second review. A cold retry was: it re-read the packet blind, its findings replaced the
original's, and both differed — observed 2026-10-02, a final review's 1 finding became
6 and a task review's 3 became 2. That retry never advances the attempt counter or the
convergence state — it is not a rework lap. A second invalid verdict is a contract
error, consistent with the unparseable-verdict behavior. Location validation (below)
runs on both review kinds and feeds the same retry mechanism.

## Document review contract

The verdict a spec reviewer emits (`pipeline` spec: Spec review). **Separate from the
reviewer verdict contract above, deliberately:** a document review has no diff, so
`location.lines` and `provenance` carry no meaning. Bending the diff-shaped schema to fit
is how a malformed claim passes validation while reporting success (#63).

One JSON object:

```
{"verdict": "pass" | "findings",
 "references": [{"ref", "disposition": "intended-new"|"wrong"|"unverifiable", "evidence"}],
 "dependencies_read": [{"symbol", "file", "behavior"}],
 "dependencies_waiver": "<reason>" | null,
 "replaced_system": {"applies": true|false, "guarantees": [...]},
 "findings": [{"id", "summary", "kind": "groundedness"|"sufficiency"|"contradiction",
               "section", "evidence", "citation": "<file>:<line>"|null,
               "proposed_amendment"}]}
```

- `references` — one entry per **unresolved** reference in the packet's table. A missing
  entry, or one naming a ref not in the table, invalidates the verdict.
- `dependencies_read` — the functions the design depends on and what they actually do.
  Empty is legal only with a non-null `dependencies_waiver`.
- `replaced_system` — `applies: false` requires no `guarantees`; `applies: true` requires
  a non-empty list. This is the field behind "what does the old path re-derive that the
  new one computes once".
- `dependencies_read` and `replaced_system` are **required**. They are the mechanical
  enforcement of two hunting-list gates: guidance alone is ignorable, a required field is
  not. Each is marked in `design-anti-patterns.md`, and the two are checked to agree.

**`kind` drives disposition, and `groundedness` is guarded.** A `groundedness` finding
**requires** a `citation`, and the citation is validated — the file exists and the line is
within it. Absent or unresolvable, the finding **downgrades to `sufficiency`** and
surfaces rather than auto-applying. A reviewer cannot route a design opinion into the
auto-amend path by labelling it a fact.

**Disposition:**

- `groundedness` → the author amends the spec; the review re-runs over the **whole
  document**, never a slice of it (`pipeline` spec: Spec review — scoping was specified
  and dropped, since "the changed sections" never fixed a baseline). Converges on the
  rework loop's rules, same backstop.
- `sufficiency`, `contradiction` → **surfaced to the user** with `proposed_amendment`.
  Nothing in these kinds is auto-applied: a spec defect is often a decision, not a repair.

**Invalid verdict:** one retry naming the specific defect, then a contract error —
identical to the reviewer verdict contract's behavior, and for the same reason. The
retry resumes the reviewer that emitted the invalid verdict (on Claude, `SendMessage`
to it), with the defect list as its prompt; a failed resume falls back to a fresh
reviewer given the packet plus the defect list.

### Plan review verdict

The verdict a plan reviewer emits (`pipeline` spec: Plan review). Its own schema, for
the reason the spec verdict has one: a plan review has no diff and no codebase
references, so neither existing schema fits.

One JSON object:

```
{"verdict": "pass" | "findings",
 "coverage": [{"section", "requirements": [{"requirement", "covered_by": ["<id>", ...],
                                             "na": "<reason>" | null}]}],
 "findings": [{"id", "summary", "kind": "uncovered"|"contradiction"|"spec-defect",
               "section", "task": <N> | null, "evidence", "proposed_amendment"}]}
```

- `coverage` — exactly one entry per section in the packet's section table, `section`
  copied verbatim (the resolved heading text, prefixed `[<spec id>] ` when the plan
  declares more than one spec file). A missing section, a duplicate, or a
  section not in the table invalidates the verdict. `requirements` is non-empty. When a
  section and its subsection are both in the table, the subsection's requirements are
  listed under the subsection only.
- `covered_by` — promise ids copied verbatim from the packet's promise table: `g<N>`,
  `t<N>.t<M>`, `t<N>.a<M>`, `t<N>.c<M>`. An id not in the table invalidates the verdict;
  a near-miss is a defect, never normalized (`parsers-fail-loud`).
- `na` — non-null marks a requirement the plan deliberately does not build. It requires
  a non-empty reason and an empty `covered_by`, and takes no finding.
- A requirement with an empty `covered_by` and a null `na` is **uncovered**: it requires
  an `uncovered` finding naming its section, and an `uncovered` finding requires such a
  requirement in its section. Either without the other invalidates the verdict.
- `verdict: "pass"` with any finding, and `verdict: "findings"` with none, are both
  invalid.
- `kind` outside the three values invalidates the verdict. It is never downgraded or
  defaulted (#63).
- Finding `id`s are unique within a verdict.
- Finding `section` — for `uncovered`, a section in the section table; for
  `contradiction` and `spec-defect`, any heading in a declared spec, by its exact text,
  with the same `[<spec id>] ` prefix when the plan declares more than one. A value
  naming no such heading invalidates the verdict.
- `task` — the task number a finding is raised against; `null` means the plan header.
  A number naming no task invalidates the verdict. On an `uncovered` finding, `task` is
  `null` or a task whose `**Spec:**` line names that section.

**Disposition:**

- `uncovered`, `contradiction` → the plan's author amends the plan; the review re-runs
  over the whole plan, resuming the reviewer — a failed resume falls back to a fresh
  reviewer given the full packet — and its verdict carries the full
  `coverage` array — the discovery-only `coverage` rule of the reviewer verdict contract
  does not apply here. Converges on the rework loop's rules, same backstop. The spec is the approved document and the plan is derived from it, so a plan
  defect is a repair, not a decision.
- `spec-defect` — the plan is faithful and the spec is wrong or silent → **surfaced to
  the user** with `proposed_amendment`, never auto-applied. A spec amended in response
  re-enters Spec review; plan lint re-runs against the amended spec, and plan review
  then restarts cold.

**Invalid verdict:** as above — one retry naming the specific defect, resuming the
reviewer that emitted it, then a contract error.

**Known limit:** the requirements a reviewer lists inside a section are its own
reading; validation enforces that each section is answered and each answer is
well-formed, not that the list is complete. Whether a cited promise actually covers its
requirement, and whether an `na` reason is sound, are likewise the reviewer's judgment
and are not validated.

## The disposition matrix

Every finding, per-task and final, is classified on two independent axes and dispatched
by cell. **Provenance** is computed by the runner against real diffs and never trusted
from the reviewer — a finding outside the diff is not `in-diff` no matter how it was
labeled:

| value | test |
|---|---|
| `in-diff` | intersects this review's diff (per task: review base = the prior commit) |
| `in-run` | intersects `git diff <run_base>` but not this task's own diff |
| `pre-existing` | neither |

Crossed with contract impact:

| | contract-breaking | improvement | unverifiable |
|---|---|---|---|
| `in-diff` | **fix** | defer | **seed** |
| `in-run` | **seed** | defer | **seed** |
| `pre-existing` | **halt** | defer | **seed** |

`unverifiable` collapses the provenance axis deliberately: what the runner cannot
settle is *where the answer lives*, not where the code sits, and every cell routes to
the final review for the same reason.

- **fix** — reworked in-loop. The only auto-fix cell. An autonomous fixer's failure mode
  is over-fixing, and over-fixing is diff over-scoping wearing a new hat, so the rule is
  fix-what-you-broke-against-the-contract and nothing else.
- **defer** — logged, never fixed. The whole right column, our own new code included: no
  gold-plating what this plan just wrote.
- **seed** — logged, the run **continues**, and the finding is carried into the final
  review's **discovery packet** as a pre-seeded prior finding. Two routes reach it, for
  one reason. A cross-task defect (`in-run × contract-breaking`) is an integration
  defect, and integration review is where it can actually be judged; a task's rework
  loop editing another task's committed work would break the linear vertical-slice
  history the per-task review base depends on. An `unverifiable` finding, at any
  provenance, is the same shape of problem seen from the other side — the evidence that
  would settle it is not in this review's diff. Both say the answer lives at
  integration, so both go there rather than driving a repair on a question nobody has
  answered yet.
- **halt** — a scope decision: pre-existing code the plan never claimed. It must never
  be silently fixed *or* silently deferred, and never fixed autonomously. The halt
  surfaces the drafted `repair_task` for the human and freezes the paused task, so the
  run is resumable once they resolve it (Halt resolution).

In the final review, `run_base` **is** the diff base, so `in-run` and `in-diff` coincide
and the cross-task route to `seed` is unreachable. The `unverifiable` route is not:
the final reviewer can report a finding its diff cannot settle, and there is no later
review to carry it to. A `seed` the final review produces is **terminal**: it is
recorded on the final receipt as `unverified`, the review still converges on the
convergence rule (seed findings never block a pass), and every `unverified` finding is
presented at the **close-out gate** beside the staged deferrals, with the reviewer's
reason — the human's call on each entry is one of `accept` (leave it, recording the human's
own evidence — required on a coverage entry, which has no finding behind it), `defer`
(stage it as a deferral) or `repair` (the human fixed it by hand). **How the gate is
reached differs by harness**, because a Codex runner cannot hold a conversation: on
Codex a final-review pass with any entry lacking a call is a **stage halt** of class
`unverified` — frozen under the stage rule like any stage halt (there is nothing to
freeze after a pass, and the helper runs all the same), doc-sync not run, the halt
payload listing every open entry with its reason, which *is* the presentation; the
human answers on re-invocation with `--resolve <id>=accept:<evidence>|defer|repair`
(`codex-runner` spec: Resume), and the resumed run re-runs the final review as every
stage re-run does, halting again only on an entry still without a call. On Claude the
orchestrator asks in conversation and records the same calls. No disposition is
promoted and nothing is re-classified: `unverifiable` stays `seed`, and the human's
call is the terminal act. The
`unverified` set holds two kinds of entry: a `seed`-disposition finding, and a final
coverage entry whose status is `unverifiable` — that status needs no backing finding
(Coverage validation), so without this the obligation would vanish on a `pass` verdict
with no findings; the entry keeps its checklist id and reason. The set **accumulates**
across final-review attempts and across invocations —
it lives in `run.json` beside `seeded_findings`, read back on resume, and a
verification lap that omits a finding never clears it; only the human's recorded call
does. A run never completes with an `unverified` finding nobody looked at.

### Location parsing

`location.lines` accepts a single line (`"12"`), a single range (`"12-20"`), or a
comma-separated list of either (`"12-20,45,60-62"`). A finding is `in-diff` — or
`in-run` — when **any** one range intersects. A reviewer naming five call sites is not
punished for precision.

An absent or unparseable location on a finding claiming `impact: "contract-breaking"` is
a **verdict validation defect**: re-dispatched once naming the defect, then a contract
error, through the same retry mechanism coverage validation uses. It never degrades
silently into `pre-existing`. An improvement finding may still carry no location; it
defers regardless of provenance.

### The `convergence: "resolved"` label is honored

A finding carrying `convergence: "resolved"` is dropped before disposition, so "listed
as resolved" behaves identically to "omitted". Otherwise a reviewer following the
contract literally causes the loop to re-dispatch a repair for something already
repaired, indefinitely, until the backstop.

**Guard:** the label is honored only when the finding's identity (Reviewer verdict
contract) is in the prior attempt's carried-fix set. A `resolved` label on an identity the
runner never tracked as outstanding is meaningless and is ignored — the finding is
dispositioned normally — otherwise a reviewer could dismiss any finding it invented by
self-labeling it resolved. A false claim is still caught: the identity reappearing later
trips the regression rule against the runner's authoritative resolved-id set. The convergence
decision itself is not modified; a dropped finding never reaches it.

## Rework loop and convergence

Per task — standard and complex; trivial runs acceptance only, with no reviewer. Each
attempt: worker → acceptance → reviewer → classify. Acceptance is green when every
command clause meets its stated outcome; prose clauses are the reviewer's. An
**execution failure** (worker crash, worker timeout, a command clause not meeting
its stated outcome) preempts the reviewer and is treated as an
implicit `fix`-retry finding with no provenance and no impact: it never defers, never
scope-halts, and never counts as a carried finding, but it is subject to the regression,
acceptance-stuck and backstop rules. Then the decision is taken deterministically, in this precedence:

1. **Gate mode** and any reviewer finding → **halt** (`gate`). A transient execution
   failure is exempt: it carries no impact.
2. Any **halt-disposition** finding → **halt** (`scope-decision`). A finding whose
   identity is carried in as human-approved (Halt resolution) is exempt from this step
   for the rest of the run; the regression rule (3) still applies to it.
3. **Regression** → **halt**: a finding the runner previously recorded resolved
   reappears, or acceptance went green→red since the prior attempt. This is the
   "shuffling one bad state into another" case — a fix undid an earlier fix, or broke
   the build. A newly-surfaced finding never seen before is *not* a regression;
   incremental reviewer discovery is allowed.
4. **Stuck** → **halt**: a fix finding is carried from the prior attempt with nothing
   resolved this round — the worker cannot crack it. Or **acceptance-stuck**: this
   attempt's execution failure is acceptance, and its **first failing command clause**
   (plan order) is the same clause — identical command text — that was the prior
   attempt's first failing one. Any other prior attempt (passed acceptance, worker crash
   or timeout, a different first failing clause) does not count; output and exit code
   are not compared, since timings in a tail make a repeated failure never compare
   equal. Same halt reason, `stuck`; the halt surfaces the clause, its stated outcome
   and the last output tail.
5. No fix findings remain and acceptance is green → **pass**.
6. The attempt count reaches `MAX_ATTEMPTS_BACKSTOP` (**5**) → **halt** (`backstop`);
   otherwise → **rework**: re-dispatch the worker with the outstanding fix findings, and
   carry the finding set plus the runner's resolved-id set into the next re-review.

Net progress each round is **not** required — a round may resolve one finding and
surface another — and converging work runs to completion. The backstop is a seatbelt
against slow oscillation, not the primary stop. The runner owns the authoritative
resolved-id set across attempts, so a reviewer mislabeling a reappearance as `new` is
still caught. Convergence's halt reasons are exactly `scope-decision`, `regression`, `stuck`,
`backstop`, `gate` — `forge_common.HALT_REASONS`, what `convergence_decision` can
return. Two further halt **classes** are raised outside it and never appear in
`decision.json`: `reviewer-wrote` (Reviewer write discipline) and `unverified` (The
disposition matrix). The receipt and halt-record `halt_reason` field accepts all
seven.

## Halt resolution — freeze, resolve, reconcile

A task halt stops the run for a human decision, but leaves it **resumable**: the paused
work is frozen, the human resolves what stopped it, and the paused task resumes against
the fixed tree rather than restarting from scratch.

**Every halt class freezes**, not only `scope-decision`. Each of the resolutions a halt
invites — amend the brief, re-tier, bump to `max`, defer, fix the named code — is
followed by a re-invocation, so `regression`, `stuck`, `backstop` and `gate` need the
frozen tree for exactly the reason `scope-decision` does. Freezing only one class leaves
the others dirty-and-unrecorded, and a resumed run that then halts on a different class
strands its restored work in a ref nothing points at — worse than the dirty tree this
section exists to remove. What *is* specific to `scope-decision` — and to the
final review's `unverified` stage halt (The disposition matrix) — is the resolution
mechanism below: `--resolve` and the approved-finding exemption answer a question, which
the other classes do not pose.

**Freeze.** The in-progress attempt is captured as a commit — **untracked files
included**, since a task built from new files is otherwise captured as empty — retained
under a forge-owned ref so it is never garbage-collected, and recorded in run state. The
working tree then returns to the last committed checkpoint.

The freeze is **parked off the mainline, never stacked on it**. Were it committed onto
the branch, the human's fix would land on top of it and the resumed task's review base
would already contain the task's own partial work — reviewing as an empty diff. Parking
keeps the checkpoint as the base for both the fix and the resumed task, which is what
makes `git diff <prior commit>` still mean "exactly this task's work" (Commit
discipline).

**The two terminal stages freeze too.** A final-review halt that follows an applied fix
dispatch, and a doc-sync halt that had already edited, both leave edits in the tree that
nothing commits — the `fix: final-review` commit lands only on a pass, and a doc-sync
contradiction returns before its commit. Each is frozen under its own stage-keyed ref and
the tree returns to the checkpoint, for exactly the reason a task halt is. The stage
record is stage-keyed, not task-keyed, so no task is ever resumed from it, and a stage
freeze is **recoverable, not replayed**: the stage re-runs from scratch on the committed
diff, which is the state the human's resolution edits against.

The clean-tree precondition is **unchanged**: freezing is what keeps every invocation
boundary clean, so no run ever accepts a dirty tree, and no snapshot ref or recorded
dirty-path set is needed.

**Resolve.** On a `scope-decision` halt the drafted `repair_task` — `{title, files, spec,
tests, acceptance, tier}`, required on exactly that cell (Reviewer verdict contract) — is
surfaced to the human with the halt. Other halt classes carry no drafted repair; their
resolution is the human action the halt reason names. **The runner never dispatches a repair on its own.** Editing pre-existing code
the plan never claimed is the highest-risk write available, and both the decision to make
it and the making of it stay with the human. The resolution is carried back into the
resumed run (Approved findings, below); the fix itself is an ordinary commit like any
other human edit, which the runner neither authors nor requires.

**Reconcile.** On resume, the frozen work is replayed onto the current HEAD:

- **Applies cleanly** → restored to the working tree. The worker resumes holding the
  **resolution delta** — everything that changed while the task was paused, `git diff`
  from the checkpoint the freeze was taken against to HEAD — and the resolved finding,
  and decides: proceed unchanged, adjust, or discard and rebuild.
- **Conflicts** → not restored. The task restarts from the clean checkpoint with the
  frozen diff supplied as **reference text** alongside the resolution delta. A conflict
  is the signal that the fix invalidated the work, not an error, and the runner never
  resolves one.

The worker's judgment is a proposal, not a verdict: the task's own review still runs, and
its reviewer is cold on discovery (`discovery-review-is-cold`) and is never told the work
was frozen — the structural guard against a worker's sunk-cost bias toward keeping what
it already built. That is a requirement on the **packet**, not only on the agent: the
`escalated: …` ledger annotation is stripped out of the reviewed task block, and off the
plan file once the freeze is replayed, so it reaches the reviewer through neither the
task block nor the review diff. The reconciliation outcome is recorded on the receipt.

The reconciliation brief itself is **class-aware**. Only `scope-decision` poses a
question, and only its brief names the consequence of resuming unanswered and forbids the
pre-existing edit that would launder the human's decision into an auto-repair. The other
classes are told truthfully why the run stopped and that the human has acted; telling
them clearing the halt "is not your job" would forbid the work the resume exists to let
them do.

**Approved findings.** A human resolution — `repair` (I fixed it) or `defer` (file it
for later) — exempts that finding's **identity** (Reviewer verdict contract) from the
`scope-decision` halt for the
remainder of the run — every later task review **and the final review**, whose
convergence takes the same approved set — so a resumed run does not stop again on a
question already answered, and never exempts a different review's finding that happens
to share the reviewer's local id. On Codex it arrives as `--resolve` on the resumed invocation (`codex-runner`
spec), naming the identity as `--status` prints it; a bare local id is accepted only when
exactly one outstanding identity carries it, otherwise the runner raises listing the
candidates. On Claude the human states it in the conversation and the orchestrator passes
the identity to the decision helper. **Regression is narrower than the
exemption:** rule 3 catches a finding already recorded as resolved; an approved
finding is exempted, not recorded as resolved, so approval alone does not verify that
the human's repair took. The approver owns that verification.

**The human is the bound.** Because no repair is dispatched autonomously, the
fix-resume-find-another loop cannot run away on its own: every round trip costs a human
decision, and that is the rate limiter. Any future autonomy here must supply its own
bound first — a divert is not a rework lap, so the attempt backstop never advances and
would not catch such a loop.

**Both harnesses.** Freeze, human resolution, and worker-judged reconciliation are
harness-neutral and bind the Claude orchestrator as well as the runner. Only the durable
half differs: the runner must write the halt record because its process exits, whereas an
in-session Claude halt is a conversational pause that discards nothing. A Claude halt
that *does* cross a session boundary has no run state to recover from; that gap is the
Claude path's missing run-state store generally, not this section's to close.

## Autonomy flag

`--autofix auto|gate`, chosen by a human at the execution offer — the same offer that
discloses tier routing — default `auto`, and passed to `forge_dispose` on every call.

- `auto` — run the matrix: fix the in-diff × contract-breaking cell, defer the right
  column, seed cross-task defects, halt only genuine scope decisions.
- `gate` — the conservative escape hatch: **any** finding halts, with a receipt
  carrying the findings and their evidence, and nothing is auto-fixed. A drafted
  `repair_task` rides along only when the finding sits in the scope-decision cell
  (pre-existing × contract-breaking) — the one cell whose halt carries one in `auto`
  too; an `unverifiable` finding's `repair_task` is `null` here as everywhere (Reviewer
  verdict contract).

The loop never auto-fixes without a mode a human chose at the offer. Both harnesses take
the same flag with the same semantics, because it is the same code deciding.

## The shared decision helper

`scripts/forge_dispose.py` holds the pure decision logic: verdict parsing
(`parse_verdict`), classification (`diff_line_ranges`, `_parse_lines`,
`verify_provenance`, `derive_disposition`, `classify_findings`), validation
(`validate_locations`, `validate_finding_ids`, `validate_coverage`) and convergence
(`ConvergenceState`, `convergence_decision`, `advance_state`). It contains no dispatch,
no fixing, no committing and no writes to project docs — reviewer output and diff text
in, a decision out. `forge_common` supplies `Finding`, `Verdict`, `HALT_REASONS`,
`AUTOFIX_MODES` and `MAX_ATTEMPTS_BACKSTOP`, imported as a plain module so there is
exactly one `Finding` class identity and no duplicate-dataclass `__eq__` hazard.

The Codex runner imports it; the Claude orchestrator drives the identical logic through
its CLI. One input is missing on that path today: the CLI takes no cumulative run diff,
so `in-run` provenance cannot be computed and a cross-task defect classifies as
`pre-existing` — a `halt` where the runner would `seed` (#89). Parity holds for every
other cell.

```
python3 forge_dispose.py \
  --verdict <verdict.json> \      # the reviewer's proposed verdict
  --base <sha> \                  # diff base: prior commit (per task) | run-start HEAD (final)
  --state <state.json> \          # prior ConvergenceState (empty/absent on attempt 1)
  --attempt <N> \
  --acceptance-ok <true|false> \
  --failed-acceptance <command> \ # first failing command clause's command; only with --execution-failure for an acceptance failure
  --autofix <auto|gate> \
  --scope <t<N>|final> \          # the review's identity scope (Reviewer verdict contract); required
  [--approved <identity> ...] \   # human-resolved finding identities (Halt resolution)
  [--prior-identities <json>]     # identities of the prior findings this review's packet carried; omitted = none, so any scoped carried_from is a defect
```

The CLI computes the authoritative diff itself and writes `decision.json` to stdout:

```json
{
  "action": "pass" | "rework" | "halt",
  "halt_reason": "scope-decision" | "regression" | "stuck" | "backstop" | "gate" | null,
                 // convergence's five; `reviewer-wrote` and `unverified` are halt classes
                 // raised outside `convergence_decision` and appear only on receipts and
                 // the halt record, never in decision.json
  "findings": {
    "fix":    [ {"id": "…", "summary": "…", "file": "…", "lines": "…"} ],
    "defer":  [ {"…": "…", "why_harmless": "reviewer improvement rationale"} ],
    "halt":   [ {"…": "…", "repair_task": {"…": "…"}} ],
    "seeded": [ {"…": "…"} ]
  },
  "state": {"resolved_ids": ["…"], "carried_ids": ["…"], "prev_acceptance_ok": true, "prev_failed_acceptance": "…" | null}
}
```

`fix`, `defer` and `halt` are always present; `seeded` appears only when a seed finding
exists. Every finding in `decision.json` carries its `identity`, and `state`'s id lists
hold identities. `ConvergenceState` round-trips through `--state` as sorted lists. A
missing `--scope` is a usage error, never defaulted.
`prev_failed_acceptance` is the attempt's first failing command clause's command when
acceptance was its execution failure, else `null`; every attempt overwrites it. A state
file without the key reads as `null`. `--failed-acceptance` without
`--execution-failure`, or with `--acceptance-ok true`, is a usage error (exit non-zero,
naming the conflict), never ignored.

**Authority.** `forge_dispose` computes the diff it verifies against. The reviewer's own
diff view is advisory and its provenance claims are overridden regardless, so a reviewer
that ran a slightly different `git diff` cannot corrupt the decision. The pure functions
stay diff-text-in and unit-testable; only the CLI wrapper shells to git. The
orchestrator acts purely off `decision.json` and never re-reads the diff, which is what
keeps it thin.

## The dispatch loop

Both harnesses run the same sequential, orchestrator-driven loop; the Codex runner plays
the orchestrator's role there. Per task, in order:

1. **Precondition** (run start): a clean working tree, else halt. Record `base_commit` =
   run-start HEAD. Then plan lint.
2. **Dispatch the implementer** at the task's tier agent. The prompt carries the
   brief-file path from `scripts/extract-brief.py`, the relevant constraints from
   `docs/forge/constraints.md`, the deferral rule and TDD discipline — never pasted plan
   or spec content. All trivial-tier tasks batch into a single `forge-light` dispatch,
   serial within, respecting `Depends on`, and skip steps 4–6.
3. **Acceptance** — run each command clause and check its stated outcome (`pipeline`
   spec: Acceptance clause grammar); capture pass or fail and, on fail, the first
   failing clause's command for `--failed-acceptance`.
4. **Dispatch the reviewer** at the task's own tier with the review base (the prior
   commit), covering spec compliance and code quality together. On a rework re-review
   the prior attempt's findings — ids and summaries, which are small — ride in the
   prompt so the reviewer can label `resolved`/`carried`/`new`. Fingerprint the repository
   before the dispatch and after it returns; a mismatch halts as a `reviewer-wrote`
   escalation — the attempt frozen, the verdict discarded (Reviewer write discipline).
5. **Decide** — `forge_dispose` over the verdict, base, state, attempt, acceptance result
   and autofix mode. Persist the returned state for the next attempt.
6. **Act** on the decision: `rework` re-dispatches the implementer with the fix findings
   and loops to 3; `halt` stops, surfacing the halt findings and drafted repair task
   plus any outstanding fix findings, and does not start the next task; `pass` collects
   the defer findings and commits the task.

State, the reviewer verdict and `decision.json` live in a scratch directory, never in
orchestrator context. The diff never enters orchestrator context either — the reviewer
holds it in its own, `forge_dispose` holds it in its process.

**Reviewer input.** On **Codex** `scripts/review-packet.py` pre-assembles the reviewer's
input, so the diff is computed once, by the runner, against the right base; the packet
is Codex-path-only machinery. A per-task packet pastes the spec sections the task
names, as context; the final-review packet lists spec paths instead, and the reviewer
reads those files itself (`codex-runner` spec: Runner). A per-task **discovery** packet
also carries an `## Acceptance results` section: one row per acceptance **command**
clause — command, stated outcome, exit code, passed, output tail — opened by the
assertion that these clauses have already been run and are not to be re-run on this
tree, and that prose clauses remain the reviewer's to check. The runner passes its
in-memory results. Verification and final-review packets carry no acceptance section. On **Claude** the reviewer is an agent that self-serves its own
`git diff <prior commit>` **plus every untracked file** (`git ls-files --others
--exclude-standard`, each rendered via `git diff --no-index /dev/null <path>` — plain
`git diff` never sees a new file, and a task's new files are only staged by the commit
*after* review, so a task built entirely from new files would otherwise review as an
empty diff) and reads the spec itself. Thin-orchestrator is preserved on Claude by
subagent self-service plus `forge_dispose` self-computing the diff, not by a packet.
Acceptance results reach a Claude reviewer the same way everything else does: the
orchestrator writes them as JSON — the same records — to the scratch directory and the
reviewer prompt names that path with the do-not-re-run assertion; no packet, no pasted
output.

### Serial by design

Substantive implementation runs **serially, on purpose** — not because parallelism is
unavailable, but because it is the wrong tool for mutating code.

- Parallelism buys **only wall-clock** — no correctness, quality or logic gain.
- The discipline has a **serial spine**: the per-task review base is
  `git diff <prior commit>`, meaningful only on a **linear** history of clean vertical
  slices. Parallel writers break that and force worktree isolation plus ordered
  merge-back, whose conflicts reintroduce exactly the integration mess vertical slices
  exist to prevent.
- Fan-out stays safe for **read-only** work — research, review lenses, no shared mutable
  state. Coding writes, and racing writes is the sketchy part. The genuinely-independent
  coding case, a large mechanical migration, tiers **trivial**, which skips review and
  was never in the loop.

## Session continuity

Every lap otherwise rebuilds context from zero: a fresh worker patches code it did not
write, and a fresh reviewer re-reads the full packet.

| dispatch | continuity |
|---|---|
| task worker, lap 1 | cold |
| task worker, rework lap | **resume** — prompt is the findings alone |
| task reviewer, discovery | **cold — deliberately** |
| task reviewer, verification | **resume** — prompt is the repair delta plus outstanding findings |
| final reviewer, discovery | **cold — deliberately** |
| final reviewer, verification | **resume** — repair delta only |
| final-review fixer | **cold once, then resume**; the brief carries findings and affected paths only |
| any reviewer, verdict-validation retry | **resume** the reviewer that emitted the invalid verdict — prompt is the defect list alone |

**Discovery review stays cold.** An independent first read is the entire justification
for a separate reviewer, and resuming it for discovery would hand the review to an agent
that already holds the worker's reasoning (constraint: `discovery-review-is-cold`).
Verification is a narrower ask — "are f1–f4 resolved, did the repair break what it
touched" — where the residual bias risk is under-flagging *new* issues, which is
precisely what the coverage requirement on the discovery pass guards. This is a
deliberate qualification of the fresh-context rule, not an exception to it: **fresh for
discovery, resumed for verification.** The verdict-validation retry resumes a discovery
reviewer too, and costs it no independence: the session it resumes is the reviewer's
own, holding its own first read and nothing of the worker's.

**Scope and failure.** Resume is scoped to **one invocation**. After a halt a human may
hand-edit code, so a persisted session's context is stale and misleading and
re-invocation always spawns cold. This governs **session handles only**. Run *state* —
the halt record, convergence state, the frozen commit — does survive a halt by design
(Receipts and run state): a stale handle costs a cold spawn, whereas discarded state
costs the whole task and re-asks the human a question already answered. A failed resume — session missing, context overflow, a
non-zero exit before any event — **falls back to a cold spawn with the full packet** and
records the fallback on the receipt. Degraded, never fatal: continuity is an
optimization, and the loop's correctness must not depend on it.

**Handles.** On Codex the handle is a `thread_id` captured per role from the dispatch
event stream and persisted in run state (`codex-runner` spec). On Claude the handle is
the agent's name: `Agent` for a cold spawn, `SendMessage` to the named agent for a
resume, and a failed `SendMessage` falls back to a fresh `Agent` with full context. Same
contract, different mechanism; no thread-id plumbing on the Claude path.

### Delta-scoped verification packets

- A verification packet is the outstanding findings, the **repair delta**, the reduced
  checklist and the `## Citable refs` section. Every prior finding placed in any packet —
  a seed on the final discovery lap, an outstanding finding on a verification lap — is
  presented under its **identity** as its `id`, so the echo the packet asks for
  (`carried_from` = the prior id) is the identity verbatim and a finding keeps its
  identity, its approval exemption and its carried/resolved tracking across laps; a
  bare id there would re-scope a carried finding under the current review on the next
  lap. Not the whole-plan diff, not the full
  spec — the resumed reviewer already holds both in session.
- The repair delta is `git diff <pre-repair tree>`, where the pre-repair tree is
  captured before the repair dispatch by the same temporary-index capture the
  repository fingerprint uses (Reviewer write discipline): tracked **and** untracked
  non-ignored content, seeded from HEAD, the real index and working tree untouched, so
  the single `fix: final-review` commit discipline is undisturbed. `git stash create`
  is not sufficient — it never sees an untracked file, and a task's new files stay
  untracked until the post-review commit, so a repair that edited or deleted one showed
  as a whole-file addition or as nothing (DEFERRALS 2026-09-02, now closed by this
  rule). The delta is a tree-against-tree diff: the post-repair content captured the same
  way, compared to the snapshot — no separate untracked-file append pass, so an
  unchanged formerly-untracked file produces no hunk, an edited one only its edit, and
  an addition or deletion appears exactly once.
- The final-review fixer's brief is findings, affected paths and referenced spec
  sections. The whole-plan diff is never pasted; the fixer reads the repo.

## Final review

Once every task passes, one review runs over the **whole-plan diff**, with fresh
context, at the plan's highest task tier. It is not a single-shot gate: it runs the
**same** disposition matrix, the same convergence rule, the same backstop and the same
halt payload as a task review. Its diff base is `base_commit`, the run-start HEAD. Its
discovery packet carries every seed-disposition finding accumulated across the run as
pre-seeded prior findings, each presented under its identity (`t<N>:<id>`) as its `id`,
so a cross-task defect that never halted a task is judged
here and a finding the final reviewer carries from a seed keeps the identity — and any
approval — it was raised under. The "worker" on rework is a fix dispatch scoped to the outstanding fix findings
against the whole-plan diff, landing as a single `fix: final-review` commit on pass.

Its job is the integration defects a per-task review cannot see — an interface mismatch
between tasks, a contract that does not hold end to end. It runs alongside the full-suite
close-out, not instead of it.

## Deferral handling

Defer-disposition findings — the whole right column — are **collected, not fixed and not
halted on**. Each carries its summary, location, provenance and why-harmless rationale.
They are recorded on the task or final receipt and aggregated into `run.json` under
`deferrals`.

The loop never writes curated project docs mid-run. Deferrals are **staged** as the run
proceeds and filed only after the user reviews them at the close-out gate, once the final
review has passed and the suite is green; accepted ones are filed as issues through
`scripts/forge_memory.py defer` (the `project-memory` spec owns the record schema, the
gate and the labels). Staged deferrals persist across a resume rather than being replaced
by the current invocation's entries, so an earlier stage's entries are never erased. The
end-of-plan summary lists them. The same gate presents the final review's `unverified`
findings (The disposition matrix), each with its reviewer's reason, for the same
three-way call — accept, defer, repair.

Implementers may defer **non-spec scope only** — nice-to-haves, refactors, edge polish.
Anything the spec requires surfaces at the review gate and is never silently deferred
(constraint: `defer-non-spec-only`).

## Inline execution

Inline is the orchestrating session executing the plan task-by-task itself, keeping
accumulated context:

- **TDD per task** — test first, then implementation, per the tdd skill.
- **Self-review before each commit**, disposing of its findings by the canon below. Inline
  does **not** dispatch a separate fresh-context reviewer: fresh-context review is a
  dispatch-only concern, its bias-removal value scales with design content, and TDD plus
  acceptance commands are the objective, unbiased check — the same trust trivial tier
  already places in acceptance alone.
- **Commit per task** on a clean tree, so the working tree is clean between tasks.

**Finding-handling is the same canon.** The self-review classifies each finding on the
same axes and acts by the same matrix. Provenance is judged against the actual diff line
ranges, not assumed; contract-breaking requires a named acceptance criterion, absent which
the finding downgrades to a deferral, keeping "I'd have done it differently" out of both
the fix and the halt. The halt cell — a pre-existing, contract-breaking finding — is the
one that must never be silently fixed *or* silently deferred: the orchestrator drafts a
disposition (a repair-task sketch, or a fix/defer rationale) and **surfaces it to the
user** for the call. Inline runs in-session, so the human gate is the conversation itself;
no notify machinery.

Fix-cell rework **converges** rather than counting. The session holds the prior attempt in
context, so the labels come for free and no re-review packet is needed: the self-review
labels its remaining fix findings resolved, carried or new against the prior attempt, and
halts on regression or stuck, passes when no fix findings remain, and keeps the backstop
of 5. Any halt — regression, stuck, backstop, or a halt-cell finding — surfaces to the
user with the outstanding findings and the drafted disposition.

The **final review** of a multi-task inline plan is the orchestrator's own broad
self-review under the same gate: halt-cell findings surface, fix-cell findings converge.

Inline takes the **coverage half** of the reviewer verdict contract and not the continuity
half. An orchestrator self-review is equally capable of surfacing one finding at a time,
so it generates the checklist through `forge_checklist.py` and states coverage by the same
discipline as a dispatch reviewer; resume is moot, because inline never discarded its
context.

## Terminal doc-sync stage

**Codex only.** After the final review **passes** — never before, so a code defect can
never be masked as doc drift — one dispatch reconciles **existing** documentation against
the shipped whole-plan diff: stale references, changed signatures or behavior, spec
changelog entries the diff made inaccurate. It is bounded to reconciliation: it never
authors new documentation, never touches code, and never reconciles issue status, because
authoring new docs would be exactly the gold-plating the matrix forbids.

Its verdict is one JSON object: `{"doc_sync": "reconciled"}` when edits landed,
`{"doc_sync": "clean"}` when nothing needed changing, or `{"doc_sync": "contradiction",
"contradiction": "<what conflicts>"}` when it finds a doc/contract contradiction it cannot
mechanically reconcile — a doc asserts something the shipped code now contradicts and
choosing the correct side is a human decision. A contradiction makes no edit and halts
with the conflict named. Otherwise the edits land as one `docs: sync` commit, separate so
a bad sync is trivially revertible, and the outcome is recorded in `run.json` and reported
in the completion summary.

The Claude path **does not run this stage** — it stops at the deferral gate and reconciles
by hand. This is the one known *stage* divergence — a terminal reconciliation stage, not
finding-handling logic; the matrix and convergence core are one implementation, with
the single input gap named under The shared decision helper (#89). Porting the stage
is open work (issue #52).

## Commit discipline

- **Clean tree at run start**, on both harnesses and on every invocation including a
  resume. A dirty tree halts before dispatch; a user's uncommitted work is never reset or
  stashed.
- **Commit per passed task**, before the next dispatches, so HEAD advances to a clean
  checkpoint after every task. Rework laps ride inside that task's commit. On Claude the
  orchestrator commits; on Codex the runner does.
- That per-task commit is what makes the **review base the prior commit** — `git diff
  <prior commit>` is exactly this task's work — and keeps the whole-plan final-review diff
  from sweeping in unrelated pre-existing changes.
- **Final-review fixes** land as a single `fix: final-review` commit once the final review
  passes; **doc-sync** as a `docs: sync` commit after it, on Codex.
- **Freeze commits** hold a halted task's in-progress attempt (Halt resolution). Not a
  vertical slice and never a review base: written at the halt, parked off the mainline,
  unwound at reconcile, and the reason the clean-tree precondition above needs no
  exception for a resumed halt.

## Receipts and run state

- `run.json` carries `autofix_mode`, the aggregated `deferrals`, the aggregated
  `seeded_findings`, the final review's accumulated `unverified` entries with each
  human call once made, the run-level `approved` finding ids (every human resolution
  so far — a field of its own, never inside `halt`, because `halt` is cleared when the
  reconciled task passes and an approval must outlive that), the terminal `doc_sync`
  record, and the `halt` record when a run
  stopped on one — freeze commit, task and attempt, serialized convergence state,
  outstanding findings with the drafted `repair_task`, and any
  human-approved finding ids. `deferrals`, `seeded_findings`, `unverified`, `approved` and `halt`
  are **read back on resume** — deliberately unlike the per-role session-handle map, which
  is cleared each invocation. A session handle goes stale the moment a human hand-edits
  code; a seeded finding is a finding about code, and dropping it on resume would silently
  ship the defect it names.
- Every persisted finding — on a receipt, in `seeded_findings`, in a halt record's
  `findings` — carries its runner-stamped `identity`, and `approved` and `unverified`
  are keyed by it. A halt record whose findings lack `identity` is from before this
  rule and a resume against it raises naming the file rather than guessing a scope.
- Task receipts carry each finding's `identity`, `provenance`, `impact`, `contract_ref`, `convergence`
  and derived `disposition`, the verdict's `coverage` array, and on halt the drafted
  `repair_task`. Receipt status is `passed` | `rework` | `escalated`, with escalated
  receipts naming the halt reason.
- Receipts also carry `coverage_skipped` when the checklist was empty, `coverage_retry`
  when the validation retry fired, and `resume_fallback` when continuity degraded to a
  cold spawn. The end-of-plan summary reports per-task lap counts, staged deferrals,
  seeded findings whether or not the final review confirmed them, and the halt reason
  class.
- A lint failure is a contract error before any receipt exists, consistent with a
  malformed plan.

## Claims discipline

Continuity claims fewer laps, better repair continuity, less repeated discovery and tool
work, and *potential* cached-input savings. It does **not** claim lower total input tokens
or lower cost. Resumed transcripts remain model input and are re-billed each turn; prompt
caching applies to eligible exact prefixes on a best-effort basis and is not guaranteed.
Any cost claim requires measurement against a comparable run.

## Risks / constraints

- **Coverage theatre** — a reviewer marks every item `satisfied` with thin evidence.
  Bounded by requiring non-empty evidence and by cross-checking `violated` against
  findings; not fully eliminable.
- **Checklist quality is plan quality** — vague `**Acceptance:**` prose or a thin
  `**Spec:**` list yields a weak checklist. This makes an existing weakness visible rather
  than introducing one, and `coverage_skipped` on the receipt is that visibility.
- **Resumed-transcript growth** — a five-lap resumed reviewer holds the whole-plan diff
  plus every verification round. The overflow path is covered by the cold-spawn fallback;
  if it becomes common, revisit scoping the discovery packet itself.
- **Verification bias** — a resumed reviewer confirming its own findings were addressed.
  Bounded by keeping discovery cold; accepted deliberately.
- **Seeded findings arriving in bulk** at the final review, making its discovery pass
  heavy. Bounded: they arrive pre-classified with contract refs, as prior findings rather
  than raw diff.
- **Lint rejecting a legal plan** — mitigated by checking only documented grammar and by
  warning rather than failing on an empty checklist; the legal-minimal-plan case is the
  guard. The changed-section mapping rule extends what *legal* means rather than
  bending this: a plan silently omitting what its own spec amendment now asserts is a
  defect, and the `n/a` escape keeps a deliberate omission expressible.
- **Reviewer misclassification of contract impact** → over- or under-fixing. Provenance is
  runner-verified rather than trusted, and contract-breaking requires a `contract_ref`
  naming a checklist id supplied in the packet — validated, not merely present — or is
  downgraded. The residual risk is a reviewer citing a *real* checklist id spuriously,
  bounded to the in-diff surface, so the blast radius is the plan's own code.
- **A thinner per-task checklist reviews less.** Dropping spec sections as a task source
  is deliberate, and it does mean a task whose plan under-specified its obligations gets
  a shallower review, with the gap surfacing at final review — later, and possibly
  entangled across tasks. The compensating gates are plan lint's changed-section
  mapping rule and the final review's unchanged whole-spec checklist; if the mapping
  rule proves weak in practice, this trades early false positives for late true
  positives, which is a worse bargain than the one it replaced.
- **Honoring `resolved` increases trust in the reviewer.** Bounded by the carried-set
  guard and by the regression rule, which remains authoritative.
- **Orchestrator drift on the Claude path** — the loop is prose the session follows. The
  decision itself is a tested CLI, so the drift surface is only the glue.
- **Wall-clock on large all-independent plans** — the accepted cost of serial execution.
  Hybrid parallelism is a future optimization, not a defect.
- **Backstop of 5** is a starting value; tune it on the halt-mix the receipts produce.

## Changelog

2026-10-05: every prior finding placed in a packet is presented under its identity as `id`, not only seeds — the final review found a seed-carried finding re-scoped as `final:<id>` on its next lap (#128)
2026-10-05: a scoped `carried_from` is kept only when it names a prior finding supplied to the review; any other is a validation defect — closes the identity-borrowing hole a security review of Task 1 found (#128)
2026-10-05: Finding identity is runner-owned and review-scoped — `<scope>:<canonical id>`, `t<N>` or `final`; a scoped `carried_from` is kept verbatim; `approved`, `seeded_findings`, `unverified` and halt-record findings are keyed by identity; `--resolve` and the decision helper's `--approved` take identities and the helper requires `--scope`; a reviewer id containing `:` is a validation defect. Replaces the "never namespaced across a run" rule, under which approving `f1` in one task exempted an unrelated `f1` later (#128)
2026-10-05: Reviewer write discipline — reviewers lose the read-only sandbox on both harnesses and mutate only in a self-made scratch copy; break-the-code is a standing review step with evidence rules; the orchestrator fingerprints working tree, index, HEAD and branch around every reviewer dispatch and halts non-recoverably as `reviewer-wrote` (a freezing escalation, not a contract error) on a change; discovery packets carry command-clause acceptance results with a do-not-re-run assertion, Claude reviewers get the same records by path (#127). Contract checklist: final citable refs are coverage items plus task test-case ids, not an equal set. Disposition matrix: a final-review `seed` is terminal — recorded `unverified` and presented at the close-out gate, never silently passed. Autonomy flag: a gate halt drafts a `repair_task` only in the scope-decision cell, and the verdict contract's `repair_task` rule names that cell rather than "will halt". Halt resolution: approved ids reach final-review convergence too. Shared decision helper: the Claude CLI's missing run diff is named as the one parity gap (#89), and the opening and doc-sync parity claims are qualified to match. Delta-scoped verification packets: the pre-repair snapshot uses the fingerprint's temporary-index capture, so untracked files are in the repair delta. Receipts: `unverified` (seed findings and unverifiable final coverage entries) and run-level `approved` ids live in `run.json` and are read back on resume. On Codex the close-out gate for unverified entries is the `unverified` stage halt, answered by `--resolve <id>=accept:<evidence>|defer|repair`
2026-10-03: amended by [pipeline] — a plan declares its spec files and names sections by spec id: Plan lint runs its changed-section rule once per declared spec and gains four rows (the header parses, `--spec` not given alongside it, `[<spec id>]` entries resolve, and a warning for a changed spec left undeclared); `spec:` ids and plan-review `section` values carry `[<spec id>]` when a plan declares more than one spec (#62)
2026-10-03: amended by [pipeline] — Plan review: the Document review contract gains a plan review verdict (`coverage` per named spec section, findings of kind `uncovered` | `contradiction` | `spec-defect`); the Contract checklist gains the plan-review-only `t<N>.c<M>` id for acceptance command clauses; Plan lint also runs at plan authoring (#97)

2026-10-02: acceptance is green when every command clause meets its stated outcome, not when every inline-code span exits 0; the checklist takes prose clauses, lint rejects an unparseable command clause; a repeated first-failing acceptance clause on consecutive attempts halts `stuck` (state `prev_failed_acceptance`, CLI `--failed-acceptance`) instead of looping to the backstop — 7 tasks looped on failed acceptance, 4 to the backstop, across 14 audited runs (#112)
2026-10-03: the `## Citable refs` section opens with a line saying its ids are citable, not coverage items — both reviewers shown the unlabeled list covered its `spec:` ids and needed a validation retry
2026-10-02: the verdict-validation retry resumes the reviewer that emitted the invalid verdict (cold only as a failed-resume fallback); every review packet prints its citable ids, and `spec:` ids are the heading text, not a slug; a `violated` coverage entry names its backing finding via `finding`, replacing the contract_ref-equals-id rule — together the cause of 16 of 16 observed retries and of retries replacing findings (1→6, 3→2)
2026-10-02: Codex standard and complex tiers move to gpt-6.1-sol (medium, high); trivial stays gpt-6-luna·low. Standard and complex still share one model and differ by effort. Verified with `codex exec -m` on codex-cli 0.154.0
2026-09-23: Codex routing moves to GPT-6 — trivial gpt-6-luna·low, standard gpt-6-sol·medium, complex gpt-6-sol·high. GPT-6 shipped no mid-tier model, so standard and complex share Sol and complex takes high effort, the one deliberate departure from provider defaults. Supersedes the opus·high-against-sol·medium asymmetry note. Model ids verified with `codex exec` on codex-cli 0.154.0

2026-09-09: a groundedness re-review runs over the whole document, matching pipeline's amended Spec review rule — the two specs contradicted each other for one commit, which plan lint structurally cannot catch since --spec takes a single file (#62, #96)

2026-09-09: document review contract — a spec review emits its own verdict schema rather than the diff-shaped one; `dependencies_read` and `replaced_system` are required fields backing two hunting-list gates; `groundedness` findings need a validated code citation or downgrade to `sufficiency`; facts auto-amend, design surfaces (#96)

2026-09-07: the final review and terminal doc-sync stages freeze their uncommitted edits when they halt — both left the tree dirty, so the very next invocation was refused by the clean-tree precondition the freeze design exists to satisfy. The record is stage-keyed and the stage re-runs from scratch rather than replaying it (#59)
2026-09-07: the `escalated: …` ledger annotation is kept out of a resumed task's review packet — it rode the freeze into the review diff and the extracted task block, telling the cold discovery reviewer the work had been frozen and what the last reviewer said (`discovery-review-is-cold`) (#59)
2026-09-07: the reconciliation brief is class-aware — the scope-decision prose ("the review's scope findings still stand … clearing it is not your job") was being handed to every resumed halt class, where it is false and forbids the work the resume exists to enable (#59)
2026-09-07: every halt class freezes, not only `scope-decision` — the other classes are equally followed by a human fix and a re-invocation, and freezing one class alone let a resumed run strand its restored work in an unreferenced ref. `--resolve` and the approved-finding exemption stay scope-decision-specific (#59)
2026-09-07: autonomous repair dispatch and its divert budget are dropped before implementation — a halt freezes and resumes, but the human makes and applies the fix. Autonomy is deferred to observed halt behavior rather than assumed; any future version must supply its own bound, since the attempt backstop never advances on a divert and would not catch such a loop (#59)
2026-09-06: a `scope-decision` halt freezes the paused task, dispatches the drafted `repair_task` as a fully reviewed task, and reconciles — bounded by a 2-divert budget, no nesting, and approved-finding exemptions that regression still polices; run state survives a halt, session handles still do not (#59)
2026-09-07: `t<N>.t<M>` is coverage-per-task but citable at the final review; membership is enforced by the callers that supply a citable set, not by the matrix (#60)
2026-09-06: Plan lint's check table gains the `**Tests:**` grammar row (#60)
2026-09-06: spec-coverage lint reads the merge base, not HEAD; Risks / constraints joins Changelog as exempt (#60)
2026-09-06: coverage items and citable refs are separate sets — spec sections stay citable, only coverage narrowed (#60)
2026-09-06: reviewers report every finding regardless of provenance; the runner derives disposition (#63)
2026-09-06: a task's checklist is its own promises (Tests + Acceptance + globals); spec sections are final-review-only and packet context per task; `contract_ref` must name a checklist id; `unverifiable` added to coverage status and finding impact, seeding to final review; plan lint requires every changed spec section to be claimed by a task (#60)

2026-09-05: consolidated from seven dated specs — phase7 scope-autonomy, tier-policy-recalibration, phase10 codex-inline, phase11 inline-finding-process, phase12b claude-dispatch-parity, halt-precision, review-continuity (#47)
2026-09-05: dropped the "Testing" section of the four sources carrying one (halt-precision, review-continuity, phase12b, phase7); every source's "Acceptance"/"Acceptance criteria" section, all seven; halt-precision's and review-continuity's "Touch points"; phase7's and phase12b's "Retirements / doc changes"; phase12b's "Runner refactor"; and phase10's and phase11's "Changes" sections with their numbered subsections — one-time phase gates and implementation worklists, all satisfied and expired. Their standing rules are stated in place: `review-packet.py` is Codex-path-only (The dispatch loop), reviewer routing reads the single tier table (Reviewer model), and the shared-module import discipline that keeps one `Finding` class identity (The shared decision helper) (#47)
2026-09-05: dropped the "Scope" section of the five sources carrying one (halt-precision, review-continuity, phase12b, phase7, tier-policy-recalibration; phase10 and phase11 have none) — phase-scoped in/out delta statements. Three standing exclusions they carried are kept: lint never auto-edits a plan or spec, because a worker rewriting its own contract is self-dealing (Plan lint); a Codex-only `--output-schema` verdict forcing is out because it would fork the shared verdict contract (Reviewer verdict contract); and the Claude path's missing doc-sync stage (Terminal doc-sync stage). Model/effort escalation stays a human on-halt action (Tier policy) (#47)
2026-09-05: dropped halt-precision's "Mechanisms being corrected", review-continuity's "Mechanism being corrected", and phase10's and phase11's "Problem" sections — arrival narrative. The arguments they carried that are still load-bearing are kept as rationale in place: a document defect can never be `in-diff` (Plan lint), a review with no definition of done returns one finding per lap (Contract checklist), every lap rebuilding context from zero (Session continuity), and inline's self-review needing a disposition gate because inline has no independent reviewer (Inline execution). Phase10's and phase11's "Decision" sections are not dropped — their rules are stated under Execution mode (mode is chosen before harness; inline is the same act on both) and Inline execution (the self-review disposes by the shared canon and stays self-reviewed) (#47)
2026-09-05: dropped phase10's and phase11's "Non-goals" — phase-boundary statements about work later phases owned and have since shipped (the Phase 7 canon on the Claude dispatch path, commit-per-task and the clean-tree precondition on Claude, the tier-recalibration effort drift). The one that is still a standing rule, that inline never gains a fresh-context reviewer, is stated under Inline execution (#47)
2026-09-05: dropped phase10's inline finding-handling — an over-resolving self-review that silently resolved decision-grade findings, carried deliberately to build a symmetric pair. It was replaced by the disposition canon and the halt-to-user gate, which is what this document states; nothing of the retired model survives (#47)
2026-09-05: dropped review-continuity's "Codex mechanics" and "Live-log rendering" subsections — `codex exec` dispatch argv, `--json`, the never-`--ephemeral` rule, `thread.started` capture and event-to-live-log rendering are all stated in the `codex-runner` spec, which owns the Codex substrate; what is kept here is the harness-independent continuity contract and the fact that the handle is a thread id there and an agent name on Claude (#47)
2026-09-05: dropped the "Changelog" section of the five sources carrying one — halt-precision and tier-policy-recalibration have none. Phase7's, phase12b's and review-continuity's entries are pointers to the later phases whose content this document already states as current (the extraction into `forge_dispose.py`, the `coverage` array, the third provenance value and the `seed` cell, coverage narrowed to discovery). Phase10's and phase11's are the authoring record of those two specs — an "initial spec" entry each, plus phase11's correction of its own Claude-dispatch scope language from a claimed boundary to an unlogged gap in the phase decomposition; the gap is long since closed and the decomposition is not something this document describes. Review-continuity's substantive amendment is kept in the body: an empty checklist is a library-level error but a runner-level skip recording `coverage_skipped`, because the original rule made legal plans unexecutable (Contract checklist). This document's changelog starts here; the superseded originals keep theirs (#47)
2026-09-05: superseded the two-provenance disposition matrix stated in phase7, phase11 and the execution-loop companion — provenance is three-way (`in-diff`, `in-run`, `pre-existing`) and the matrix has five outcomes including `seed`, stated exactly once under The disposition matrix (#47)
2026-09-05: superseded phase7's precedence, which put the `gate` short-circuit before the halt cell as a parenthetical and described stuck as "carried across two consecutive attempts" — the shipped `convergence_decision` checks gate first as step 1, and its stuck rule additionally requires that nothing was resolved this round. Verified against `scripts/forge_dispose.py`; the code wins (#47)
2026-09-05: superseded phase12b's and phase7's deferral write-back — neither the runner nor the orchestrator appends to a `DEFERRALS.md` file. Deferrals are staged in `run.json`, presented at the close-out review gate, and filed as issues via `scripts/forge_memory.py defer`; the file is retired and the gate belongs to the `project-memory` spec (#47)
2026-09-05: added the duplicate-finding-id validation rule, which no source states — `forge_dispose.validate_finding_ids` rejects a verdict naming one id twice, because the id is the runner's only handle on a finding and the carried/resolved sets, the honored `resolved` label and staged-deferral selection all assume it is unique. Verified against `scripts/forge_dispose.py` (#47)
2026-09-05: added that a coverage or location validation retry never advances the attempt counter or the convergence state — stated in `skills/planning/SKILL.md`, the live orchestrator canon, and absent from halt-precision and review-continuity, which specify the retry without saying what it does to the loop (#47)
2026-09-05: added that a verification verdict carrying `coverage` anyway is accepted rather than rejected — `forge_common.REVIEW_VERDICT_INSTRUCTION` says so explicitly, where halt-precision's "omitted on verification verdicts" reads as a prohibition (#47)
2026-09-05: recorded that the terminal doc-sync stage runs on Codex only. Phase7 specifies it without a harness qualifier because it was a Codex-path spec; phase12b left it out of the Claude path deliberately. Porting it is open work, issue #52 (#47)
2026-09-05: dropped review-continuity's "Inline path" framing that inline gets "the coverage half only" of that phase's two halves — the halves are a phase decomposition. The rule survives as stated: inline generates the checklist and states coverage; resume is moot because inline never discarded its context (#47)
