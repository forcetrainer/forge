"""Convergence engine: the deterministic pass/rework/halt decision and the
cross-attempt state fold. Pure functions — no codex, no git, no plan loop.

The disposition matrix (Task 2) sets each finding's disposition; this task turns a
classified finding set + the running convergence state into one of pass / rework /
halt, and folds the attempt into the state for the next round (resolved-id set,
carried-fix set, acceptance result)."""
import unittest

from _forge_support import *  # noqa: F401,F403
import forge_common


def _fix(id="f1", carried_from=None):
    """A fix-disposition review finding (in-diff x contract-breaking) — the only
    quadrant the loop reworks."""
    return forge_common.Finding(
        id=id, summary="fix me", file="foo.py", lines="10",
        provenance="in-diff", impact="contract-breaking", contract_ref="AC1",
        carried_from=carried_from, disposition="fix",
    )


def _defer(id="d1"):
    """A defer-disposition finding (improvement) — collected, never reworked."""
    return forge_common.Finding(
        id=id, summary="nit", file="foo.py", lines="10",
        provenance="in-diff", impact="improvement", disposition="defer",
    )


def _halt(id="h1"):
    """A halt-disposition finding (pre-existing x contract-breaking) — a scope
    decision."""
    return forge_common.Finding(
        id=id, summary="scope", file="foo.py", lines="99",
        provenance="pre-existing", impact="contract-breaking", contract_ref="AC2",
        disposition="halt",
    )


def _exec_fail():
    """The implicit execution-failure finding execute_task synthesizes on a worker
    crash/timeout or acceptance failure: disposition fix (cannot converge to pass),
    but no provenance/impact so it never scope-halts, defers, or counts as carried
    — only regression (green->red) and the backstop apply."""
    return forge_common.Finding(
        id="exec-failure", summary="worker timed out", file=None, lines=None,
        provenance=None, impact=None, disposition="fix",
    )


class ConvergenceDecisionTests(unittest.TestCase):
    """convergence_decision: the deterministic precedence over a classified
    finding set, the running state, acceptance, the attempt count, and the
    autofix mode."""

    def test_progress_resolves_one_fix_leaves_another_reworks(self):
        state = forge_run.ConvergenceState()
        both = [_fix("f1"), _fix("f2")]
        self.assertEqual(
            forge_run.convergence_decision(both, state, True, 1, "auto"),
            ("rework", None),
        )
        forge_run.advance_state(state, both, True)
        # f1 resolved this round, f2 remains -> still rework (progress made).
        self.assertEqual(
            forge_run.convergence_decision([_fix("f2")], state, True, 2, "auto"),
            ("rework", None),
        )

    def test_regression_when_resolved_id_reappears(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)   # attempt 1: f1 seen
        forge_run.advance_state(state, [_fix("f2")], True)   # attempt 2: f1 resolved
        self.assertIn("f1", state.resolved_ids)
        # Attempt 3: a fix undid an earlier fix — f1 comes back.
        self.assertEqual(
            forge_run.convergence_decision([_fix("f1")], state, True, 3, "auto"),
            ("halt", "regression"),
        )

    def test_regression_when_acceptance_goes_green_to_red(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)   # prior acceptance green
        # This round acceptance fails (an execution failure) — green -> red.
        self.assertEqual(
            forge_run.convergence_decision([_exec_fail()], state, False, 2, "auto"),
            ("halt", "regression"),
        )

    def test_stuck_when_same_fix_carried_two_attempts(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)   # attempt 1
        # Attempt 2: the identical fix finding is back, nothing resolved.
        self.assertEqual(
            forge_run.convergence_decision([_fix("f1")], state, True, 2, "auto"),
            ("halt", "stuck"),
        )

    def test_stuck_matches_via_carried_from(self):
        # The reviewer re-issues the same issue under a new id, echoing the prior
        # id in carried_from; the runner matches it to the original -> stuck.
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)
        self.assertEqual(
            forge_run.convergence_decision(
                [_fix("f2", carried_from="f1")], state, True, 2, "auto"),
            ("halt", "stuck"),
        )

    def test_clean_no_findings_passes(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([], state, True, 1, "auto"),
            ("pass", None),
        )

    def test_defers_only_passes(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_defer()], state, True, 1, "auto"),
            ("pass", None),
        )

    def test_scope_decision_halt_on_halt_disposition(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_halt()], state, True, 1, "auto"),
            ("halt", "scope-decision"),
        )

    def test_backstop_on_fifth_attempt_with_fix_findings(self):
        state = forge_run.ConvergenceState()
        # Churn four attempts — each resolves the prior and surfaces a new one, so
        # no finding is ever carried (no stuck) — reaching the backstop.
        for i in range(1, 5):
            findings = [_fix("f{}".format(i))]
            self.assertEqual(
                forge_run.convergence_decision(findings, state, True, i, "auto"),
                ("rework", None),
            )
            forge_run.advance_state(state, findings, True)
        self.assertEqual(
            forge_run.convergence_decision([_fix("f5")], state, True, 5, "auto"),
            ("halt", "backstop"),
        )

    def test_backstop_default_is_max_attempts_backstop(self):
        # The backstop defaults to the shared constant, not a hard-coded 5.
        state = forge_run.ConvergenceState()
        for i in range(1, forge_common.MAX_ATTEMPTS_BACKSTOP):
            findings = [_fix("f{}".format(i))]
            forge_run.convergence_decision(findings, state, True, i, "auto")
            forge_run.advance_state(state, findings, True)
        n = forge_common.MAX_ATTEMPTS_BACKSTOP
        action, reason = forge_run.convergence_decision(
            [_fix("f{}".format(n))], state, True, n, "auto")
        self.assertEqual((action, reason), ("halt", "backstop"))

    def test_gate_mode_halts_on_any_finding(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_defer()], state, True, 1, "gate"),
            ("halt", "gate"),
        )

    def test_execution_failure_attempt_one_reworks(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_exec_fail()], state, False, 1, "auto"),
            ("rework", None),
        )

    def test_execution_failure_not_gated_in_gate_mode(self):
        # A worker crash is retried, never gated — the implicit fix-retry finding
        # carries no impact, so the gate short-circuit skips it (pre-Phase-7
        # behavior: gate halts on reviewer findings, not on a transient crash).
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_exec_fail()], state, False, 1, "gate"),
            ("rework", None),
        )

    def test_net_progress_not_required_reworks(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)   # attempt 1
        # f1 resolved, a brand-new f2 surfaces -> rework, not halt.
        self.assertEqual(
            forge_run.convergence_decision([_fix("f2")], state, True, 2, "auto"),
            ("rework", None),
        )

    def test_reviewer_finding_then_execution_failure_no_false_regression(self):
        # attempt 1 reviewer surfaces f1 -> rework; attempt 2 the worker crashes
        # (execution failure) with acceptance still green -> rework; attempt 3 the
        # still-unfixed f1 reappears. The crash yielded no review signal, so the
        # runner must NOT have recorded f1 as resolved — a reappearance here is
        # 'stuck' (carried across two reviewed attempts), never a false
        # 'regression' (nothing regressed; the worker merely timed out once).
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_fix("f1")], state, True, 1, "auto"),
            ("rework", None),
        )
        forge_run.advance_state(state, [_fix("f1")], True)
        self.assertEqual(
            forge_run.convergence_decision([_exec_fail()], state, True, 2, "auto"),
            ("rework", None),
        )
        forge_run.advance_state(state, [_exec_fail()], True)
        self.assertNotIn("f1", state.resolved_ids)  # the crash never resolved f1
        self.assertEqual(
            forge_run.convergence_decision([_fix("f1")], state, True, 3, "auto"),
            ("halt", "stuck"),
        )

    def test_gate_precedes_scope_decision(self):
        # A halt-disposition finding under --gate still reports the gate reason
        # (gate short-circuits before the matrix).
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_halt()], state, True, 1, "gate"),
            ("halt", "gate"),
        )

    # --- approved-finding exemption (Task 3) --------------------------------

    def test_approved_halt_finding_falls_through_to_remaining_rules(self):
        # h1 is approved -> step 2 no longer halts on it; with no fix findings
        # and acceptance green, the decision falls through to pass.
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision(
                [_halt("h1")], state, True, 1, "auto", approved_ids={"h1"}),
            ("pass", None),
        )

    def test_non_approved_halt_finding_still_halts_when_another_is_approved(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision(
                [_halt("h1"), _halt("h2")], state, True, 1, "auto",
                approved_ids={"h1"}),
            ("halt", "scope-decision"),
        )

    def test_approved_finding_still_trips_regression_on_reappearance(self):
        # h1 was approved and its canonical id landed in the runner's resolved
        # set from a prior attempt; it reappears now -> regression still fires,
        # even though it is exempt from the scope-decision rule.
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("h1")], True)
        forge_run.advance_state(state, [_fix("f2")], True)
        self.assertIn("h1", state.resolved_ids)
        self.assertEqual(
            forge_run.convergence_decision(
                [_halt("h1")], state, True, 3, "auto", approved_ids={"h1"}),
            ("halt", "regression"),
        )

    def test_approval_matches_canonical_id_via_carried_from(self):
        # h1 is approved; the reviewer re-issues it under a new id h2 pointing
        # carried_from back at h1 -> still exempt.
        state = forge_run.ConvergenceState()
        reissued = forge_common.Finding(
            id="h2", summary="scope", file="foo.py", lines="99",
            provenance="pre-existing", impact="contract-breaking",
            contract_ref="AC2", carried_from="h1", disposition="halt",
        )
        self.assertEqual(
            forge_run.convergence_decision(
                [reissued], state, True, 1, "auto", approved_ids={"h1"}),
            ("pass", None),
        )

    def test_gate_mode_still_halts_on_approved_finding(self):
        # Gate mode (step 1) precedes the approval exemption (step 2) — an
        # approved finding still halts under --gate.
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision(
                [_halt("h1")], state, True, 1, "gate", approved_ids={"h1"}),
            ("halt", "gate"),
        )

    def test_empty_approved_ids_reproduces_existing_behavior(self):
        state = forge_run.ConvergenceState()
        self.assertEqual(
            forge_run.convergence_decision([_halt("h1")], state, True, 1, "auto"),
            ("halt", "scope-decision"),
        )
        self.assertEqual(
            forge_run.convergence_decision(
                [_halt("h1")], state, True, 1, "auto", approved_ids=frozenset()),
            ("halt", "scope-decision"),
        )


class AdvanceStateTests(unittest.TestCase):
    """advance_state folds one attempt into the convergence state: authoritative
    resolved-id set, carried-fix set, and the acceptance result."""

    def test_records_resolved_carried_and_acceptance(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)
        self.assertEqual(state.carried_ids, {"f1"})
        self.assertEqual(state.resolved_ids, set())
        self.assertTrue(state.prev_acceptance_ok)
        # f1 disappears (resolved), f2 appears; acceptance now red.
        forge_run.advance_state(state, [_fix("f2")], False)
        self.assertIn("f1", state.resolved_ids)
        self.assertEqual(state.carried_ids, {"f2"})
        self.assertFalse(state.prev_acceptance_ok)

    def test_carried_ids_retains_persisting_fix(self):
        # A fix finding present across two reviewed attempts stays in the carried
        # set (membership is what the stuck rule reads — no per-finding count).
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)
        self.assertEqual(state.carried_ids, {"f1"})
        forge_run.advance_state(state, [_fix("f1")], True)
        self.assertEqual(state.carried_ids, {"f1"})
        self.assertEqual(state.resolved_ids, set())

    def test_execution_failure_preserves_carried_and_resolved(self):
        # An execution-failure attempt yields no review signal, so it leaves the
        # resolved-id set and the carried-fix set exactly as the prior reviewed
        # attempt left them (only acceptance is updated) — a crash never resolves
        # or re-carries a reviewer finding, so a later reappearance is stuck, not a
        # false regression.
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_fix("f1")], True)   # f1 outstanding
        self.assertEqual(state.carried_ids, {"f1"})
        forge_run.advance_state(state, [_exec_fail()], True)  # worker crash
        self.assertEqual(state.carried_ids, {"f1"})           # unchanged
        self.assertEqual(state.resolved_ids, set())           # f1 NOT resolved
        self.assertTrue(state.prev_acceptance_ok)

    def test_execution_failure_from_empty_state_stays_empty(self):
        # The implicit execution-failure finding carries no identity — it never
        # seeds the resolved-id set or the carried-fix set.
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_exec_fail()], False)
        self.assertEqual(state.resolved_ids, set())
        self.assertEqual(state.carried_ids, set())

    def test_defers_do_not_enter_carried_or_resolved(self):
        state = forge_run.ConvergenceState()
        forge_run.advance_state(state, [_defer("d1")], True)
        self.assertEqual(state.carried_ids, set())
        self.assertEqual(state.resolved_ids, set())


if __name__ == "__main__":
    unittest.main()
