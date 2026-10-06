# Scoped Finding Identity Implementation Plan

> **For agentic workers:** Execute task-by-task following the Execution section
> of the planning skill, with strict TDD per task. Checkboxes track progress.

**Goal:** Every finding carries a runner-stamped review-scoped identity, and every run-wide set — approvals, seeds, unverified entries, halt-record findings — is keyed by it, so approving one task's `f1` never exempts another review's `f1` (#128).
**Architecture:** `classify_findings` stamps `Finding.identity` as `<scope>:<canonical id>` from a required `scope` argument and the convergence functions compare identities instead of recomputing `carried_from or id`; the runner supplies `t<N>` or `final` at its two call sites and persists the identity on every finding dict it writes, while `--resolve`, `--status` and the `forge_dispose` CLI's `--approved` read and print identities. No wire field is removed; `identity` is added.
**Tech stack:** Python 3 standard library, unittest.
**Global Constraints:**
- Scripts use the Python 3 standard library only.
- No finding identity is ever derived from a bare `id` outside `finding_identity`.
**Spec files:**
- docs/forge/specs/execution.md
- docs/forge/specs/codex-runner.md
- docs/forge/specs/project-memory.md

### Task 1: Identity in the decision helper
- [x] Done

**Files:**
- Modify: `scripts/forge_common.py` (`Finding.identity` field; `finding_to_dict` emits it)
- Modify: `scripts/forge_dispose.py` (`finding_identity`; `classify_findings` takes `scope` and stamps identity; `validate_finding_ids` rejects `:` in a reviewer id; `_canon` removed and its callers read `identity`; `ConvergenceState.from_dict` and `to_dict` round-trip identities; CLI gains required `--scope`; `--approved` help names identities)
- Test: `tests/test_forge_classify.py`
- Test: `tests/test_forge_convergence.py`
- Test: `tests/test_forge_dispose.py`

**Spec:** [execution] Reviewer verdict contract, [execution] The shared decision helper, [execution] Rework loop and convergence, [execution] The `convergence: "resolved"` label is honored

**Interface:**
- `forge_common.Finding.identity: str | None = None` — set by the runner, never by the reviewer; serialized by `finding_to_dict` under the key `identity`.
- `forge_dispose.SCOPE_PATTERN` — the regular expression a scope must match: `t<digits>` or `final`.
- `forge_dispose.finding_identity(finding, scope) -> str` — `carried_from` verbatim when it begins with `t<digits>:` or `final:`, else `scope + ":" + (carried_from or id)`; raises `ValueError` naming the scope when `scope` does not match `SCOPE_PATTERN`.
- `forge_dispose.classify_findings(verdict, diff_text, scope, run_diff=None, carried_ids=None)` — `scope` is positional and required; every returned finding has `identity` set; the `carried_ids` resolved-label check compares identities.
- `forge_dispose.validate_finding_ids(verdict)` — additionally reports a reviewer `id` containing `:` as a defect naming the id.
- `forge_dispose.convergence_decision(...)` and `forge_dispose.advance_state(...)` — unchanged signatures; `approved_ids`, `state.resolved_ids` and `state.carried_ids` hold identities and every comparison reads `finding.identity`; a finding whose `identity` is `None` raises `ValueError` naming the finding id.
- `forge_dispose.execution_failure_finding(detail)` — its synthetic finding carries `identity` `"exec-failure"`.
- CLI: `--scope <t<N>|final>` required; omitted or malformed is an argparse usage error naming the flag; `--approved` values are identities; every finding in `decision.json` carries `identity`.

**Tests:**
- finding_identity prefixes a bare id with the scope
- finding_identity prefixes a bare carried_from with the scope
- finding_identity keeps a carried_from that already carries a task or final scope
- finding_identity raises naming a malformed scope
- classify_findings stamps every finding's identity under the given scope
- validate_finding_ids reports a reviewer id containing a colon
- the resolved label is honored only when the identity is in carried_ids
- approving t2:f1 does not exempt a scope-decision finding f1 raised under scope t5
- approving t2:f1 exempts a final-review finding whose carried_from is t2:f1
- a finding with identity None raises in convergence_decision naming the id
- advance_state records identities in resolved_ids and carried_ids
- the CLI rejects a missing --scope with a usage error naming it
- the CLI rejects a scope that is neither t<N> nor final
- decision.json findings and state carry identities

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_classify.py` passes
- `python3 -m unittest discover -s tests -p test_forge_convergence.py` passes
- `python3 -m unittest discover -s tests -p test_forge_dispose.py` passes
- `python3 scripts/forge_dispose.py --help` prints `--scope`

**Tier:** standard

**Depends on:** nothing.

### Task 2: Identity through the runner, resume and status
- [x] Done

**Files:**
- Modify: `scripts/forge-run.py` (`execute_task` passes scope `t<N>`, `run_final_review_loop` passes `final`; seeded findings persisted with `id` replaced by identity and `carried_from` null; `_collect_unverified` keys finding entries by identity; `--resolve` validation and the reconciliation brief match identities with bare-id disambiguation; halt-record findings lacking `identity` raise naming run.json)
- Modify: `scripts/forge_status.py` (outstanding findings and the `resume with:` line print identities, read from the `identity` field)
- Test: `tests/test_forge_resume.py`
- Test: `tests/test_forge_final_review.py`
- Test: `tests/test_forge_seed.py`
- Test: `tests/test_forge_status.py`

**Spec:** [execution] Halt resolution, [execution] Final review, [execution] Receipts and run state, [codex-runner] Resume, [project-memory] Staging and idempotency

**Interface:**
- `forge-run.py` `_resolve_identity(name, outstanding) -> str` — `name` returned when it is an outstanding identity; a bare name matching exactly one outstanding identity's local part returns that identity; zero matches raises `RuntimeError` listing the outstanding identities; more than one raises `RuntimeError` naming each candidate.
- `forge-run.py` `_seed_record(finding) -> dict` — `finding_to_dict` with `id` set to the identity and `carried_from` `None`, the shape replayed into the final discovery packet.
- Halt record `findings` entries and every receipt finding carry `identity`; `run.json` `approved` keys and `seeded_findings[].id` are identities; `unverified` finding entries' `id` is the identity.
- `forge_status.render_halt(halt)` reads each finding's `identity` and raises `ValueError` naming the missing field when a halt-record finding lacks it.

**Tests:**
- a scope-decision finding f1 approved in task 2 still halts task 5 on its own f1
- an approved task finding re-raised by the final reviewer under carried_from t2:f1 passes
- a seeded finding is replayed into the final packet with its identity as id and no carried_from
- unverified finding entries are keyed by identity so two tasks' f1 seeds both survive the merge
- --resolve accepts a full identity on a scope-decision halt
- --resolve accepts a bare id that matches exactly one outstanding identity
- --resolve raises listing candidates when a bare id matches two outstanding identities
- --resolve raises naming an identity no halt record carries
- the reconciliation brief names resolved findings by identity
- a halt record whose findings lack identity raises on resume naming run.json
- status prints outstanding identities and a resume command naming them
- the status resume command round-trips into --resolve without error
- a task receipt's findings each carry their runner-stamped identity
- a final-review receipt's findings each carry their runner-stamped identity
- --resolve t2:f1=defer stages exactly task 2's f1 deferral with its task_number, not task 5's f1, and records t2:f1 in run.json approved
- --resolve on an unverified stage halt records the call on the entry matching a full identity
- --resolve on an unverified stage halt resolves a bare id to the one open entry carrying it and raises listing candidates when two do

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_resume.py` passes
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p test_forge_seed.py` passes
- `python3 -m unittest discover -s tests -p test_forge_status.py` passes
- `python3 -m unittest discover -s tests -p 'test_*.py'` passes

**Tier:** standard

**Depends on:** Task 1.

### Task 3: Skill text names the identity
- [ ] Done

**Files:**
- Modify: `skills/planning/codex-execution.md` (`--resolve` paragraphs name the identity form and the bare-id rule)
- Modify: `skills/planning/SKILL.md` (the Claude-path `forge_dispose.py` invocation lists `--scope` and `--prior-identities`; the approved-finding exemption names identities)

**Spec:** [codex-runner] Resume, [execution] The shared decision helper

**Tests:** none — prose artifact; acceptance is the mechanical text check below.

**Acceptance:**
- `grep -q 'final:<id>' skills/planning/codex-execution.md` passes
- `grep -q -- '--scope' skills/planning/SKILL.md` passes
- `grep -q -- '--prior-identities' skills/planning/SKILL.md` passes
- `grep -c 'never namespaced' skills/planning/SKILL.md skills/planning/codex-execution.md` exits 1

**Tier:** trivial — text edits naming three flags and one id form, no logic.

**Depends on:** Task 4.

### Task 4: A scoped carried_from must name a supplied prior finding
- [ ] Done

**Files:**
- Modify: `scripts/forge_dispose.py` (`classify_findings` takes `prior_identities`; `validate_carried_from` reports a scoped `carried_from` outside that set; `finding_identity` takes the set and raises on an unknown scoped value; CLI gains `--prior-identities`)
- Modify: `scripts/forge-run.py` (both call sites pass the identities of the prior findings placed in the packet: the seeds on the final discovery lap, the outstanding findings on a verification lap, nothing on a task discovery lap)
- Test: `tests/test_forge_classify.py`
- Test: `tests/test_forge_dispose.py`
- Test: `tests/test_forge_final_review.py`
- Test: `tests/test_forge_convergence.py` (the Task 1 and Task 2 cases that keep a scoped carried_from now supply it as a prior identity)
- Test: `tests/test_forge_resume.py` (same)

**Spec:** [execution] Reviewer verdict contract, [execution] The shared decision helper

**Interface:**
- `forge_dispose.finding_identity(finding, scope, prior_identities=frozenset()) -> str` — a scoped `carried_from` is returned verbatim only when it is in `prior_identities`; otherwise raises `ValueError` naming the value and the finding id; a bare `carried_from` and a bare `id` behave as before.
- `forge_dispose.validate_carried_from(verdict, prior_identities) -> list[str]` — one defect line per finding whose scoped `carried_from` is not in `prior_identities`, naming the finding id and the value; empty list when none; run with the other verdict validations so the defect is retried once then a contract error.
- `forge_dispose.classify_findings(verdict, diff_text, scope, run_diff=None, carried_ids=None, prior_identities=frozenset())`.
- CLI: `--prior-identities <path>` — a JSON array of identity strings; omitted means the empty set, so every scoped `carried_from` is a defect on a review given no prior findings.
- `forge-run.py` `_prior_identities(prior_findings) -> frozenset[str]` — the `identity` of every prior finding dict placed in the packet, raising `ValueError` naming the entry when one lacks it; seeds (Task 2) and a verification lap's outstanding findings (`finding_to_dict`, Task 1) both carry it.

**Tests:**
- a scoped carried_from naming a supplied prior identity is kept verbatim
- a scoped carried_from naming no supplied prior identity is a validation defect naming the finding and the value
- a scoped carried_from on a review given no prior findings is a validation defect
- a bare carried_from is still prefixed with the scope
- the CLI reads --prior-identities and accepts a scoped carried_from in that set
- the CLI without --prior-identities rejects a scoped carried_from
- the final discovery lap passes the seeds' identities so a final finding carried from a seed classifies
- a task discovery lap passes no prior identities so a borrowed t2:f1 on a task 5 finding is a defect, not an exemption
- a verification lap passes the outstanding findings' identities so a re-review carried_from naming one of them verbatim is kept
- _prior_identities raises naming a prior finding entry that lacks identity
- the existing scoped-carried_from cases in the convergence, final-review and resume suites supply the prior identity set and still pass

**Acceptance:**
- `python3 -m unittest discover -s tests -p test_forge_classify.py` passes
- `python3 -m unittest discover -s tests -p test_forge_dispose.py` passes
- `python3 -m unittest discover -s tests -p test_forge_final_review.py` passes
- `python3 -m unittest discover -s tests -p 'test_*.py'` passes

**Tier:** standard

**Depends on:** Task 2.
