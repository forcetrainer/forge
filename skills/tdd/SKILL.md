---
name: tdd
description: Use when implementing any feature or bugfix, before writing implementation code. Not for trivial mechanical edits — typos, copy changes, config values, style tweaks.
---

# Test-Driven Development (TDD)

**Trigger:** implementing features, bug fixes, refactors, behavior changes. **Don't trigger:** trivial mechanical edits — typos, copy changes, config values, style tweaks.

## The Iron Law

NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST.

Code before test? Delete it. Start over. Don't keep it as "reference." Don't adapt it while writing tests. Don't look at it. Delete means delete. Implement fresh from tests.

## Test Infrastructure Gate

Building test infrastructure is plan-level work, never drive-by.

- Existing test suite → use it. New tests go in the existing harness. Never a parallel test stack.
- Approved plan that includes test setup → build it as planned. New codebases get tests this way.
- Ad-hoc edit, no harness → stop. Ask one question: set up testing for this project, or verify this change manually? Not an unrequested project.

The gate governs where tests run, not whether code gets tested. Wherever a harness exists or a plan sanctions one, the Iron Law applies in full.

## Red-Green-Refactor

**RED.** Write one minimal test showing what should happen. One behavior per test. Clear, behavior-describing name. Shows the intended API. Real code over mocks unless unavoidable.

**Verify RED. Mandatory, never skip.** Run the test. Confirm it fails, not errors, and fails for the expected reason — feature missing, not a typo. Passes immediately? You're testing existing behavior — fix the test. Errors instead of failing? Fix until it fails correctly.

**GREEN.** Write the simplest code that passes the test. No extra features, no unrelated refactors, no improvements beyond the test.

**Verify GREEN. Mandatory.** Run the full suite. Confirm the new test passes, other tests still pass, output is clean. Test still fails? Fix the code, not the test. Other tests now fail? Fix now, before moving on.

**REFACTOR.** Only after green. Remove duplication, improve names, extract helpers. Keep tests green. Don't add behavior.

**Repeat.** Next failing test for the next piece of behavior.

## Bug Fixes

Start with a failing test that reproduces the bug. Follow the same cycle. The test proves the fix and guards against regression. Never fix a bug without a test.

## Verification Checklist

Before marking work complete:
- Every new function/method has a test.
- Watched each test fail before implementing.
- Each test failed for the expected reason.
- Wrote minimal code to pass each test.
- All tests pass, output pristine.
- Tests use real code; mocks only if unavoidable.
- Edge cases and errors covered.

Can't check every box? TDD was skipped. Start over.

## Testing Anti-Patterns

Adding mocks or test utilities? Read @testing-anti-patterns.md first.

## Final Rule

Production code needs a test that exists and failed first. Otherwise it isn't TDD. No exceptions without your human partner's permission.
