## parsers-fail-loud
**Rule:** Parsers raise on malformed input, naming the cause and the line; never guess intent, never fall back to a default.
**Scope:** repo
**Because:** A tolerant parser turns a syntax error into a wrong answer downstream, where it costs a debugging session instead of a two-second error. Guessing intent is how one malformed tier line made eleven plans unparseable.
**Source:** docs/forge/archive/DECISIONS.md 2026-07-11
## plans-carry-contracts
**Rule:** Plans specify what and where — files, interfaces, test cases, acceptance — never implementation code.
**Scope:** repo
**Because:** Code written in a plan is written twice and wrong the first time, with no compiler or test to correct it. The plan holds the decisions; the code is their consequence.
**Source:** docs/forge/archive/DECISIONS.md 2026-06-10
## defer-non-spec-only
**Rule:** Agents may defer non-spec scope only; anything the spec requires surfaces at the review gate and is never silently deferred.
**Scope:** repo
**Because:** Silent deferral of a spec'd requirement ships an incomplete feature that looks complete. Non-spec polish is the only thing an agent may judge out of scope on its own.
**Source:** docs/forge/archive/DECISIONS.md 2026-06-10
## discovery-review-is-cold
**Rule:** A task's discovery review runs on a fresh agent; only verification laps may resume it.
**Scope:** repo
**Because:** Independence is the entire justification for a separate reviewer — a resumed discovery review inherits the worker's framing and stops being a second opinion. Verification re-checks named findings, so continuity costs nothing there.
**Source:** docs/forge/specs/execution.md
## inline-never-for-speed
**Rule:** Never choose inline execution for wall-clock. It is a task-shape decision, and inline gives up the independent review gate.
**Scope:** repo
**Because:** Dispatch is often slower, and that was never why it exists. Choosing inline for speed silently drops the bias check that inline's safety case depends on being confined to small, low-stakes work.
**Source:** docs/forge/archive/DECISIONS.md 2026-07-18
## test-harness-is-plan-work
**Rule:** Creating or changing a test harness is plan-level work, never drive-by inside another task.
**Scope:** repo
**Because:** Harness work inside a task balloons its scope invisibly and leaves the review base meaning something other than that task's own change.
**Source:** docs/forge/archive/DECISIONS.md 2026-06-10
## specs-amend-in-place
**Rule:** A change that alters what a spec asserts amends that spec; never add a superseding one.
**Scope:** docs/forge/specs/
**Because:** Two specs describing the same system disagree the moment one changes, and nothing says which is current. Amending in place is also what forces you to read what you are contradicting.
**Source:** docs/forge/specs/pipeline.md
## stdlib-only
**Rule:** Scripts use the Python 3 standard library only; no third-party dependency enters the plugin.
**Scope:** scripts/
**Because:** forge installs into other people's repos and runs on whatever Python is there. A dependency makes the plugin's behavior contingent on an environment we do not control.
**Source:** docs/forge/archive/DECISIONS.md 2026-07-02
## hooks-inert-without-signal
**Rule:** A hook performs its signal-directory check before any other work and exits silently when there is none.
**Scope:** hooks/
**Because:** Plugin hooks fire in every repo the user has forge installed in. Work done before the signal check is paid by people not using forge — one hook cost about 35ms on every edit that way.
**Source:** docs/forge/archive/DECISIONS.md 2026-06-10
