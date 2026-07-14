# Decisions

## 2026-07-13 — Codex execution moves to a deterministic `codex exec` runner; machinery now justified; ultra effort prohibited
**Why:** Supersedes the execution half of the 2026-07-03 dual-harness decision. In-session dispatch testing surfaced the sharp edges directly: explicit-spawn friction, fragile model/effort routing, and completed child threads consuming quota — and the prose rework cap proved unenforceable (reviewers failed out, the process never stopped). The earlier "discipline over machinery" rationale rested on quota bugs being harness-side and unavoidable; `codex exec`'s process boundary eliminates them by construction, so the machinery objection no longer holds. `scripts/forge-run.py` (stdlib, sequential, whole-plan scope) owns dispatch, review, the rework cap as a loop counter, receipts, and ledger annotations; the conversational orchestrator only invokes, relays escalations, and holds gates — it cannot absorb work inline because it is not in the loop. Tier mapping updated to GPT-5.6 (trivial → luna/medium, standard → terra/high, complex → sol/high — Sol at high supersedes 5.5/xhigh on generation uplift); `ultra` effort prohibited at every tier because it spawns subagents inside the worker, breaking brief isolation; `max` is a human-only escalation, never a default. `codex/agents/*.toml` retired — `codex exec` takes model/effort as flags, and the worker contract text is sourced from `agents/*.md`, removing a manually-synced copy. Receipts stay ephemeral (`.forge/runs/`, gitignored) with plan checkboxes as the durable record, consistent with the 2026-07-02 ephemeral-artifacts rule. Rejected: per-task launcher and hybrid runner/conversation review splits — both re-expose the orchestrator to the drift the runner exists to remove; keeping in-session dispatch as a documented fallback — two paths to keep in sync, no environment that needs the second.
**Where:** docs/forge/specs/2026-07-13-codex-exec-runner-design.md, scripts/, skills/planning/codex-execution.md, codex/agents/ (deleted)

## 2026-07-11 — Plan-parsing scripts fail loud at brief generation; strict, single-form syntax over tolerant parsing
**Why:** Strengthens the 2026-07-02 "fail loudly over thin briefs" rule after two issues (#8, #9) showed the guarantee held only for *missing* input, not *malformed* input — the scripts silently absorbed structural drift, so a bad plan surfaced only when someone later noticed a review packet failed or a brief looked thin, the most expensive place to catch it. Standing constraint: `extract-brief.py`/`review-packet.py` accept exactly one documented form per field and raise at generation on any deviation, naming the real cause — never degrade, never guess intent. Concretely: task headings are exactly `### Task N:` (three #, colon), and `**Goal:**`/`**Spec:**` are each a single line — `**Spec:**` bare comma-separated heading names only (no parentheticals, no `;`, one `--spec` file). Rejected tolerant parsing (strip parentheticals, join wrapped lines) — it entrenches ambiguous syntax and re-introduces silent guesswork; the module's job is to eliminate model reading, so its failures must be legible to the author, not smoothed over. Fixes carry an authoring-side belt (the planning skill states each contract) plus the script-side enforcement — the script is the guarantee, the prose is prevention. Any future plan-parsing script inherits this: one form, loud failure, cause named.
**Where:** scripts/extract-brief.py, scripts/review-packet.py, skills/planning/SKILL.md, tests/test_extract_brief.py

## 2026-07-03 — Renamed theforge → forge; legacy signal accepted forever; migration is a hook nudge, not a script
**Why:** "theforge" was daily typing friction in every namespace (`theforge:planning`, update commands, `docs/theforge/`), and the agents were already `forge-*` — the "the" was a collision-avoidance artifact, not a choice. "forge" is generic in software (SourceForge, Laravel/Atlassian/Electron Forge) but genericness costs nothing at personal scope; alternatives (anvil, kiln, crucible) traded the intended meaning — the place where things get built — for distinctiveness nobody needs. The SessionStart hook accepts `docs/theforge/`/`.theforge/` indefinitely and appends a one-line `git mv docs/theforge docs/forge` nudge in legacy repos — rejected a migration script because the migration is one command (relative links inside the tree survive a parent rename). Historical plans/ideation docs keep the old name; the Phase 3 spec was amended (living-spec rule) since its manifest-name assertions became false.
**Where:** hooks/session-start, .claude-plugin/, .codex-plugin/, .agents/, skills/, agents/, codex/agents/, README.md

## 2026-07-03 — Codex dual-harness: same repo with dual manifests; divergence isolated to a reference file; discipline over machinery for orchestrator leaks and worker tracking
**Why:** Skills, scripts, and the session-start script are harness-portable verbatim, so a split repo or build step is machinery without benefit (superpowers v6 precedent). The Codex execution procedure lives in `codex-execution.md` loaded only when the Workflow tool is absent — the earlier "no reference files" rejection applies to rules that always bind; this one binds on one harness only. Codex lacks structural orchestrator/worker separation, so work-leak prevention is an explicit hard rule (orchestrator never edits implementation files; 2-iteration rework cap escalates to the user, never absorbs inline). Worker tracking: tier-prefixed `nickname_candidates` pools replace Codex's random names, and plan checkboxes double as the dispatch ledger — rejected any cleanup/lifecycle machinery because accumulation and quota bugs are harness-side (openai/codex #19197, #22779). Tier mapping mirrors intent, not price: gpt-5.4-mini/low, gpt-5.4/high, gpt-5.5/xhigh; agents install by documented copy because Codex plugins cannot bundle agents.
**Where:** docs/forge/specs/2026-07-03-phase3-codex-dual-harness-design.md, codex/agents/, skills/planning/codex-execution.md

## 2026-07-02 — Execution efficiency rules live in the planning skill; reviewer rules live in agent files, split by actor
**Why:** Each rule loads only where it binds: reviewer-facing integrity rules (read-only, "can't verify from diff" is a valid verdict, implementer rationales never suppress findings) go in forge-standard/forge-deep review paragraphs — relaying them through orchestrator prompts is where rules get dropped; the orchestrator-facing rule (never pre-rate severity) stays in the planning Execution section. Rejected: a separate execution reference file (planning skill loads whole at execution anyway — indirection without savings). Context-lifetime rule replaces the ≤3-task inline threshold; forge-deep gains the final-integration-review role for multi-task plans.
**Where:** docs/forge/specs/2026-07-02-phase2-execution-efficiency-design.md, skills/planning/SKILL.md, agents/

## 2026-07-02 — Two stdlib Python scripts with ephemeral output; spec sections declared on the task at plan time
**Why:** Scripts must eliminate model reading, not typing: extract-brief.py assembles worker briefs (plan header + task block + declared spec sections), review-packet.py bundles task metadata + git diff for reviewers — plan, spec, and diff content never transit the orchestrator. Spec sections are declared per-task via a `**Spec:**` heading-list line written when the planner has full context, so extraction is fully mechanical (rejected: CLI-arg section refs at dispatch — requires the orchestrator to read the spec, the exact reading being eliminated). Python stdlib over bash/awk for parsing robustness; `--out` defaults ephemeral, nothing committed (rejected: committed briefs dir — per-dispatch artifacts polluting target-repo history). Both scripts fail loudly over emitting thin briefs.
**Where:** docs/forge/specs/2026-07-02-phase2-execution-efficiency-design.md, scripts/

## 2026-07-02 — Three-gear pipeline; gear 2 lives inside the brainstorming skill
**Why:** Proportionality at pipeline level: gear 1 = trigger floors (unchanged), gear 2 = delta to an already-spec'd system (conversational design, one gate, straight to tdd — no spec/plan files), gear 3 = full flow for new architecture. Routing test is architectural (creates vs. operates within), not size. Rejected: a separate gear-2 skill (fourth trigger surface that must not mis-fire against brainstorming's) and putting the gear-2 procedure in planning (the gate is design dialogue, which brainstorming owns). Tripwires: no nameable owning spec → gear 3; design outgrows a paragraph → escalate, never stretch the conversational gate.
**Where:** docs/forge/specs/2026-07-02-phase1-pipeline-skill-edits-design.md, skills/brainstorming/SKILL.md

## 2026-07-02 — Living specs: amend in place, dated changelog inside the spec, no fourth memory file
**Why:** Specs are per-system documents, not frozen snapshots — a gear-2 change that alters what a spec asserts amends that spec, with a one-line dated changelog entry at the spec's end. Keeps the memory surface at specs/plans/DECISIONS/DEFERRALS/ROADMAP; the self-anchoring rule ("which spec owns this?") doubles as the gear-2/3 tripwire.
**Where:** docs/forge/specs/2026-07-02-phase1-pipeline-skill-edits-design.md

## 2026-07-02 — Specs/plans are agent-consumed: telegraphic style contract; spec re-review demoted
**Why:** Every sentence must carry a requirement, contract, or decision — cut narration, never information (edge cases, interfaces, acceptance criteria stay). Clarifying questions batch 2–3 per turn (single only on design forks); check-ins are decision digests; the sectioned walkthrough IS the approval gate, so the mandatory end-of-brainstorm file re-review is demoted to a close-out notification. Guard: trim toward decision-relevant, not short — gates that rubber-stamp are dead controls.
**Where:** docs/forge/specs/2026-07-02-phase1-pipeline-skill-edits-design.md, skills/brainstorming/SKILL.md, skills/planning/SKILL.md

## 2026-07-02 — TDD skill cut to operational core (~600 words); anti-rationalization armor dropped
**Why:** The rationalization tables, sunk-cost lectures, and good/bad code examples were written to police weaker models, and the text loads into every implementing worker on every task. Iron Law, infrastructure gate, verify-red/verify-green, and the final checklist carry the entire discipline; testing-anti-patterns.md stays as on-demand reference.
**Where:** docs/forge/specs/2026-07-02-phase1-pipeline-skill-edits-design.md, skills/tdd/SKILL.md

## 2026-06-10 — TDD infrastructure gate: harness creation is plan-level work, never drive-by
**Why:** Third superpowers sink (after embedded code and 3-agent review): the near-verbatim tdd skill had no trigger floor, so a trivial edit to an untested artifact ran the full Iron Law — observed in Codex as 1.5 hours bootstrapping a test stack for a single 67kb HTML file. The gate keys on sanctioned scope, not harness existence: existing suite → use it, never a parallel stack; approved plan including test setup → build it (new codebases get tests this way, through the plan gate); ad-hoc edit with no harness → ask one question (set up testing vs. verify manually). Trivial mechanical edits don't trigger the skill at all, and the don't-trigger exclusions now live in the frontmatter descriptions — the always-loaded trigger surface — not just the skill bodies.
**Where:** skills/tdd/SKILL.md, skills/brainstorming/SKILL.md

## 2026-06-10 — Session-independent model/effort routing via per-task tiers and three depth-profile agents
**Why:** Subagents inherit the session's model *and* effort by default, so an accidental `/model` switch (or a deliberately cheap session) silently degrades plan execution. The planner tags each task trivial/standard/complex by characteristics — not category — at plan time (re-checked in self-review), and tags route absolutely: forge-light (haiku), forge-standard (sonnet/high), forge-deep (opus/xhigh). Agent frontmatter is the only mechanism that can pin effort, which is why the plugin ships agent definitions despite the lean ethos. Routing is disclosed in the execution offer; the user overrides at the gate. A dedicated router agent was considered and rejected — it has less context than the planner and adds a hop.
**Where:** skills/planning/SKILL.md, agents/

## 2026-06-10 — Plans specify what/where, never implementation code
**Why:** Embedded code was superpowers' single biggest token sink — written without compiler/test feedback, usually wrong by execution time, written twice. The contract rule governs exceptions: signatures, schemas, wire formats, and requirement-algorithms are decisions and belong; bodies and test code are solutions and don't.
**Where:** skills/planning/SKILL.md, docs/notes/superpowers-assessment.md

## 2026-06-10 — Conditional session hook, ~60 words, opt-in by signal
**Why:** Discovery doesn't need a hook (frontmatter descriptions are always loaded); the hook is for continuity only. Signal = `docs/forge/` or `.forge/` exists; self-bootstrapping because the first spec creates the signal. Scratch sessions pay zero.
**Where:** hooks/session-start

## 2026-06-10 — Visual companion is display-only with a refinement checkpoint
**Why:** Click-to-select was never used — choices happen in the CLI anyway (~360 lines removed). After the user picks a direction, ask "refine further, or good enough?" — the question hands the user the brake; never build straight from a selection.
**Where:** skills/brainstorming/visual-companion.md

## 2026-06-10 — Proportional review replaces 3-agents-per-task
**Why:** No proportionality was superpowers' second-biggest sink. Trivial tasks: acceptance commands only. Substantive: one combined spec+quality review, second reviewer only on real findings.
**Where:** skills/planning/SKILL.md (Execution)

## 2026-06-10 — Two memory files + roadmap on decomposition; fourth skill owns formats
**Why:** Decisions (read before builds) and deferrals (read when revisiting scope) serve different reads — separate files keep DECISIONS high-signal. Roadmap only when brainstorming decomposes into phases. project-memory skill triggers on ad-hoc "log this decision."
**Where:** skills/project-memory/SKILL.md

## 2026-06-10 — Agents may defer non-spec scope only
**Why:** Gives implementers agency over nice-to-haves/refactors/polish (logged with reasons), while spec'd requirements can only be flagged at the review gate, never silently skipped.
**Where:** skills/planning/SKILL.md, skills/project-memory/SKILL.md

## 2026-06-10 — Dropped: using-superpowers, SDD, executing-plans, worktrees, code-review skills, verification, writing-skills, systematic-debugging
**Why:** Redundant with the current harness (native Workflow tool, worktree isolation, /code-review, evidence-before-claims in system prompts) or marginal on current models.
**Where:** docs/notes/superpowers-assessment.md (full skill-by-skill verdict)
