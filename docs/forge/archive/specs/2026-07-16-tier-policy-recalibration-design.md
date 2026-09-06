# Tier Policy Recalibration — design

Cross-harness change to how forge classifies task difficulty and routes it to a model. Reframes tier assignment from "predict how hard this is" to "sit at the floor; move off it only on named evidence," and lowers per-tier effort defaults to each provider's recommended starting point. Applies to both the Claude dispatch/inline path and the Codex runner.

Owns the tier definitions, classification burden, model/effort routing, and reviewer model for **both** harnesses. Supersedes the "Tier mapping" and reviewer-routing rows in `2026-07-13-codex-exec-runner-design.md` (amend there with a changelog pointer at implementation) and the routing table + review rules in `skills/planning/SKILL.md` / `codex-execution.md`.

## Scope

- In: the three-tier model with **standard as the floor**; a justification-bearing `Tier:` plan field with off-floor enforcement; recalibrated model·effort defaults on both harnesses; reviewer routing collapsed to task-tier + fresh-context; final-review tier tracks the plan's highest task tier.
- Out: **auto-escalation** on demonstrated failure — the mechanism that bumps a halted task's model/effort is Phase 7 (Codex) / Phase 8 (Claude), not this spec. The rework-cap value (currently 2) — revisit in Phase 7. No change to the number of tiers, the agent names, or the dispatch transport.

## Tier model

Three tiers. **Standard is the default** — a task sits there unless evidence moves it, and the burden of proof is on *leaving* standard in either direction.

| Tier | Reached by | Evidence contract |
|---|---|---|
| trivial | *downward* evidence | Purely mechanical — no design content (rename, single config value, one field through one call site). If mechanicalness can't be shown, it is **not** trivial. |
| standard | **default** | No justification. May touch many files and carry a real test path — that is still standard. |
| complex | *upward* evidence | A **named** design decision or cross-cutting invariant that standard demonstrably cannot resolve. |

**Not evidence for complex** (rejected justifications): file count, task category ("code implementation"), "touches core", "feels complex". These name a shape, not a decision.

## Classification contract — the `Tier:` plan field

The existing per-task `Tier:` line becomes justification-bearing off the floor:

```
Tier: standard                          # floor — no justification
Tier: complex — <the named decision>    # e.g. "reconciles two conflicting retry semantics no single call site owns"
Tier: trivial — <mechanical rationale>  # e.g. "single enum value, one call site, no logic"
```

- `standard` takes no justification; any trailing text is ignored.
- `complex` and `trivial` **require** a non-empty justification after `— `.
- The **presence** of a justification is mechanically checkable; its **quality** (a real decision vs. a rejected shape) is not — a script cannot tell "reconciles two retry semantics" from "touches core". Quality is enforced at authoring by the planning-skill self-review, never by the runner.
- The justification persists in the plan and is surfaced per-task in the execution offer's tier breakdown (below).

## Routing — model·effort per tier

First pass runs at each provider's **recommended default**. The stronger settings removed as defaults (xhigh/max on Claude; sol·high/xhigh on Codex) are not deleted — they remain the target a human may bump to when a task **halts after exhausting rework** (existing human-on-halt behavior, unchanged). This spec lowers defaults only; it wires no automatic escalation.

**Claude path** (`skills/planning/SKILL.md` Execution table):

| Tier | Agent | Profile: current → new |
|---|---|---|
| trivial | `forge:forge-light` | haiku *(no effort knob — unchanged)* |
| standard | `forge:forge-standard` | sonnet · high → **sonnet · medium** |
| complex | `forge:forge-deep` | opus · xhigh → **opus · high** |

**Codex path** (`scripts/forge-run.py` `TIER_MAP`):

| Tier | Model | Effort: current → new |
|---|---|---|
| trivial | gpt-5.6-luna | medium → **low** |
| standard | gpt-5.6-terra | high → **medium** |
| complex | gpt-5.6-sol | high → **medium** |

Cross-harness asymmetry (opus·high vs sol·medium) is intentional — each value is its own provider's stated default, not a forced-symmetric number.

## Reviewer model

The reviewer's value is **fresh context** — an independent pass that does not reason over the work already done and rationalize its defects — **not** a stronger model.

- **Reviewer tier = task tier.** Dispatched as a fresh process/subagent at the same tier as the task it reviews. `REVIEW_MAP` is **retired**; reviewer routing reads `TIER_MAP`. (Resolves the `codex-execution.md` hazard: REVIEW_MAP silently going stale against TIER_MAP on a model-churn edit.)
- **No strength escalation.** The "dispatch a second reviewer on `forge:forge-deep` if the first finds issues" pass is removed. A finding → same-tier rework by the implementer → re-review at the same tier with fresh context. Model strength never enters the review loop; the rework cap → halt is the only failure backstop.
- **Final (integration) review** runs at the **plan's highest task tier** with fresh context — an all-standard plan gets a standard-tier final review, not a pinned-ceiling one. Was: pinned opus·xhigh / sol·high.

A reviewer finding an issue is **not** a failure and never triggers escalation — it is the loop working. Only exhausting the rework cap without resolution is failure, and that halts for a human today.

## Enforcement

- **Codex:** `forge-run.py` validates the `Tier:` field on load. A `complex`/`trivial` tier with a **missing** justification is a **loud contract error** (exit 1), consistent with the runner's fail-hard-on-contract stance. The runner checks presence only — justification *quality* is the planner's job (above), not the runner's.
- **Claude:** the planning-skill self-review enforces the justification before the offer. The self-review's tier check flips from symmetric ("would a smaller model handle this / does a trivial task hide a decision") to the **directional burden**: every non-standard tier carries a valid, non-categorical justification, else it is pushed back to standard.
- **Offer disclosure:** the execution offer already discloses resolved routing (`SKILL.md` "disclose the resolved routing"). It additionally surfaces **each non-standard task's justification** in the tier breakdown, so an up-tier is visible and overridable before anything runs.

## Acceptance

- `skills/planning/SKILL.md`: Tier definition (§Tier), the `Tier:` field format, self-review directional burden, Execution routing table, offer disclosure of justifications, reviewer/proportional/final-review rules all reflect this spec.
- `skills/planning/codex-execution.md`: reviewer routing + `REVIEW_MAP` retirement + final-review tier reflect this spec.
- `scripts/forge-run.py`: `TIER_MAP` recalibrated; `REVIEW_MAP` retired (reviewer reads `TIER_MAP`); `Tier:` justification validation with a loud contract error on off-floor tiers lacking valid evidence; final review dispatched at the plan's max task tier.
- `2026-07-13-codex-exec-runner-design.md`: Tier mapping + reviewer-routing rows amended with a changelog pointer to this spec.
- Existing forge-run tests updated for the new `TIER_MAP` values, `REVIEW_MAP` removal, off-floor justification validation (present / missing / rejected-evidence), and max-tier final review.
