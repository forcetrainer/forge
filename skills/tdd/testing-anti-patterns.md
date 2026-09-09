# Testing Anti-Patterns

Load this when reaching for a mock or fixture, testing something that cannot be executed, or asserting on text.

A test that cannot fail for the reason stated is not a test.

## 1. Asserting on what the test itself set up

**Trigger:** You wrote the input, and you're about to assert that the input is what you wrote it to be — checking a fixture back against itself instead of checking what the code under test produced.

**Gate:** If this assertion failed, would that mean the code is broken, or only that the setup step didn't run? If the answer is "only that," the assertion proves nothing.

**Instead:** Assert on something the code under test computed or changed, not on a value you handed it. Trace the assertion back to a line of production code that could have produced a different answer.

## 2. Substituting away the behavior the assertion depends on

**Trigger:** You're replacing a real dependency with a stand-in to keep the test fast or isolated, and the assertion you're about to write depends on something that dependency does.

**Gate:** Does the behavior this assertion checks pass through the part being replaced? If yes, the stand-in removes the only thing that could make the test fail for the right reason.

**Instead:** Replace only the parts the assertion doesn't depend on. Push the substitution boundary further out, toward the slow or external edge, so the path the assertion exercises stays real.

## 3. Doubles shaped by assumption rather than an observed instance

**Trigger:** You're building a stand-in for something you haven't looked at directly, filling in its shape and fields from memory or a guess about what it probably returns.

**Gate:** Have you looked at, or run against, a real instance of the thing you're standing in for, recently enough to trust the shape? If not, the double encodes a belief, not a fact.

**Instead:** Look at the real thing first, or build the double from an actual observed instance. Include what's actually present, not only the fields today's test happens to read.

## 4. Testing text instead of running it

**Trigger:** What you're verifying is prose, configuration, or some other artifact with nothing to execute, and you're reaching to write an assertion that reads like a runtime test anyway.

**Gate:** Is there anything here that runs? If nothing executes, a test that pretends otherwise checks nothing; the honest check is mechanical — text present or absent, structure well-formed, a command's exit status.

**Instead:** Verify the artifact with checks proportional to what it is: search for required text, confirm files exist or don't, run whatever command consumes the artifact and check its result. Don't dress a text check up as a behavior test.

## 5. Asserting on descriptions instead of effects

**Trigger:** Something in the system can actually run, and you're about to assert on a label, comment, or description of what it does rather than triggering it and checking what happened.

**Gate:** Could you run this and observe the outcome instead? When something executable is available, asserting on its description instead is the failure — the opposite case from entry four, where nothing executable exists at all.

**Instead:** Run it. Assert on the state it left behind, the value it returned, or the effect it had, not on text that claims those things happened.
