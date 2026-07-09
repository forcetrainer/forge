# Codex execution (no Workflow tool)

Same plan, same tasks, same tiers — dispatched sequentially instead of pipelined, because Codex CLI has no Workflow tool to spawn/track parallel workers.

**Sequential dispatch only:** one worker at a time. Spawn a worker by naming the tier agent directly (e.g. "Have forge-standard implement task N"). `Depends on` order is enforced serially — never start task N+1 until task N's worker has reported back and review has passed. No pipelining, no worktree isolation.

**Briefs and review packets unchanged:** generate each worker's brief with `scripts/extract-brief.py`; route diffs through `scripts/review-packet.py`. Same mechanics as pipelined execution — only the dispatch loop is different. Both live in the plugin root's `scripts/` directory (see the location note in SKILL.md).

**Orchestrator no-work rule (hard):** during dispatched execution the orchestrator never opens or edits implementation files — dispatch, read the one-paragraph report, run acceptance commands, update the ledger. Catching yourself about to edit a source file means you owed a dispatch instead. A worker that fails the 2-iteration rework cap escalates to the user with the outstanding findings; the orchestrator never absorbs the work inline.

**Dispatch ledger:** plan-file checkboxes double as the worker tracker. On dispatch, annotate the task line with the worker nickname (e.g. `dispatched: forge-standard-2`). On completion, annotate the review outcome. Agent-list rows resolve to plan lines via the tier-prefixed nicknames.

**No lifecycle machinery:** worker accumulation/quota bugs are harness-side (openai/codex #19197, #22779) — sequential dispatch minimizes accumulation by construction; nothing else is built to work around it.

**Review flow, proportional review, deferral rule, final review:** identical to the Execution section — trivial tasks skip subagent review, standard/complex tasks get the combined review, non-spec scope may be deferred with a DEFERRALS.md entry, and a final broad review runs once every task passes. All executed sequentially, same as everything else in this mode.
