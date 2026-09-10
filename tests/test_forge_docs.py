"""Mechanical checks on skills/brainstorming/design-anti-patterns.md.

This is a prose artifact — nothing in it executes. Per `pipeline` spec's
"prose artifacts take mechanical acceptance" rule, the checks here are text
presence, structure, and counts, not invented behavioral tests. They must not
embed a Gate line verbatim: doing so would violate the constraint under test
(no file outside the document restates a Gate).
"""
import ast
import inspect
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "skills" / "brainstorming" / "design-anti-patterns.md"
SKILL_PATH = REPO_ROOT / "skills" / "brainstorming" / "SKILL.md"
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
import forge_docreview  # noqa: E402
import forge_lint  # noqa: E402


def _text():
    return DOC_PATH.read_text()


def _skill_text():
    return SKILL_PATH.read_text()


def test_names_itself_the_tuning_surface():
    text = _text()
    assert "single tuning surface" in text


def test_states_two_observation_bar():
    text = _text()
    assert "two independent observations" in text


def test_every_entry_has_trigger_gate_instead():
    text = _text()
    triggers = re.findall(r"^\*\*Trigger:\*\*", text, flags=re.MULTILINE)
    gates = re.findall(r"^\*\*Gate:\*\*", text, flags=re.MULTILINE)
    insteads = re.findall(r"^\*\*Instead:\*\*", text, flags=re.MULTILINE)
    assert len(triggers) == 2
    assert len(gates) == 2
    assert len(insteads) == 2


def test_schema_backed_entries_name_their_fields():
    text = _text()
    assert "dependencies_read" in text
    assert "replaced_system" in text
    # The marking must say deletion doesn't remove the field.
    assert "does not remove" in text


FIELD_MARKER_RE = re.compile(r"^\*\*(Trigger|Gate|Instead):\*\*")
HEADING_RE = re.compile(r"^#{1,6}\s")
ENTRY_HEADING_RE = re.compile(r"^##\s+\d+\.", flags=re.MULTILINE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")
MIN_NEEDLE_LEN = 30


def _gate_blocks():
    """Derive each Gate's full block from the document itself at runtime,
    rather than hardcoding a copy in this test file. A literal copy here
    would put Gate text into a tracked file the instant this file is
    committed, tripping the very check it exists to run (issue #98's
    shape) — and it would go stale against the document instead of
    tracking edits to it.

    This repo wraps markdown at ~88 columns, so a Gate's prose is usually
    several physical lines. A Gate's block runs from the ``**Gate:**``
    marker through the following lines until a blank line, the next
    ``**Field:**`` marker, or a heading — the same block-bounding shape
    `scripts/extract-brief.py` uses for a plan's field clauses
    (`is_wrapped_continuation`, `parse_field_clauses`). That script's
    functions are wired to plan-specific vocabulary (``**Goal:**``,
    ``**Spec:**``, fence-masking for code blocks a plan may contain) and to
    task-block scoping, so importing them directly doesn't fit; this reuses
    the block-bounding rule, not the code, for this document's own
    Trigger/Gate/Instead markers — a document declared to have no code
    blocks, so no fence mask is needed here.
    """
    text = _text()
    lines = text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("**Gate:**"):
            block = [line[len("**Gate:**"):].strip()]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "" or FIELD_MARKER_RE.match(nxt) or HEADING_RE.match(nxt):
                    break
                block.append(nxt.strip())
                j += 1
            block_text = _normalize_ws(" ".join(block))
            if len(block_text) < 40:
                raise AssertionError(
                    f"extracted Gate block is suspiciously short ({len(block_text)} "
                    f"chars): {block_text!r} — extraction likely broke"
                )
            blocks.append(block_text)
            i = j
        else:
            i += 1

    if not blocks:
        raise AssertionError(
            "no Gate blocks extracted from the document — the restatement "
            "check would silently guard nothing"
        )

    entry_count = len(ENTRY_HEADING_RE.findall(text))
    if entry_count != len(blocks):
        raise AssertionError(
            f"document declares {entry_count} entries but {len(blocks)} Gate "
            "blocks were extracted — extraction disagrees with document structure"
        )

    return blocks


def _gate_needles():
    """Sentence-level needles derived from each Gate block.

    Matching on the whole block only catches a full, unbroken copy of a
    Gate. A restatement that keeps most of a Gate's substance but drops or
    reorders a clause — e.g. everything past a Gate's first sentence,
    pasted on its own — still restates it and must still be caught, so each
    qualifying sentence inside a block is its own needle. A whole-block
    copy trivially contains every one of its sentences, so this subsumes
    exact-copy detection rather than replacing it. Sentences too short to
    be a meaningful needle on their own (below MIN_NEEDLE_LEN) are dropped;
    a block that has nothing left after dropping those fails loudly rather
    than silently guarding nothing.
    """
    needles = []
    for block in _gate_blocks():
        sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(block) if s.strip()]
        qualifying = [s for s in sentences if len(s) >= MIN_NEEDLE_LEN]
        if not qualifying:
            raise AssertionError(
                f"Gate block has no sentence long enough to serve as a needle "
                f"(min {MIN_NEEDLE_LEN} chars): {block!r}"
            )
        needles.extend(qualifying)
    return needles


def _normalize_ws(s):
    # Collapse any run of whitespace (including a line-wrap newline) to a
    # single space, so a needle that spans a wrapped line still matches.
    # Case is left alone: a restatement is a copy-paste, and copy-pasted
    # text keeps its case; normalizing case would let unrelated prose that
    # happens to share words in different case count as a "hit" (the same
    # false-positive risk that made the first version of this test flag the
    # plan's own paraphrase).
    return re.sub(r"\s+", " ", s).strip()


def _restatement_hits(needle, files):
    """files: iterable of (label, contents). Returns labels containing
    needle once both needle and contents are whitespace-normalized."""
    target = _normalize_ws(needle)
    hits = []
    for label, contents in files:
        if target in _normalize_ws(contents):
            hits.append(label)
    return hits


def _tracked_files(exclude=None):
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel_path in tracked:
        path = REPO_ROOT / rel_path
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        if not path.is_file():
            continue
        try:
            contents = path.read_text(errors="ignore")
        except (UnicodeDecodeError, OSError):
            continue
        yield rel_path, contents


def test_no_gate_text_restated_outside_the_document():
    # Grep the repo (tracked files only) for each Gate's full text, derived
    # from the document rather than copied into this file, excluding the
    # document itself, and assert no other tracked file matches.
    files = list(_tracked_files(exclude=DOC_PATH))
    for needle in _gate_needles():
        hits = _restatement_hits(needle, files)
        assert hits == [], f"Gate text {needle!r} restated outside the document: {hits}"


def test_restatement_check_catches_a_line_wrapped_copy():
    # Proves the detector in the test above actually detects, rather than
    # merely finding nothing today. A naive `needle in contents` check is
    # blind to this: this repo's markdown convention wraps at ~88 columns,
    # so a pasted Gate rewrapped by an editor is the most likely real-world
    # restatement, and it must still be caught.
    needle = _gate_needles()[0]
    words = needle.split(" ")
    midpoint = len(words) // 2
    wrapped = " ".join(words[:midpoint]) + "\n" + " ".join(words[midpoint:])
    assert needle not in wrapped  # sanity: the naive check would miss this

    planted = [("some/other/file.md", f"prefix text\n{wrapped}\nsuffix text")]
    hits = _restatement_hits(needle, planted)
    assert hits == ["some/other/file.md"]


# --- Task 5: wiring the gate into the brainstorming flow -------------------


def test_spec_review_step_between_self_review_and_close_out():
    text = _skill_text()
    self_review_pos = text.index("Self-review the spec")
    spec_review_pos = text.index("**Spec review**")
    close_out_pos = text.index("**Close out**")
    assert self_review_pos < spec_review_pos < close_out_pos


def test_planning_does_not_begin_until_spec_review_passes():
    text = _skill_text()
    assert "Planning does not begin until spec review passes" in text


def test_skill_carries_the_at_reference_form():
    text = _skill_text()
    assert "@design-anti-patterns.md" in text


def test_skill_names_the_docreview_module():
    text = _skill_text()
    assert "scripts/forge_docreview.py" in text


def test_skill_states_the_invalid_verdict_retry_rule():
    text = _skill_text()
    assert "one retry naming the specific defect" in text
    assert "contract error" in text


# --- agreement: doc's schema-backed markings vs the module's required fields

BACKED_FIELDS_RE = re.compile(r"required verdict fields?(.*?)Deleting", re.DOTALL)
FIELD_TOKEN_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")


def _doc_marked_fields(text=None):
    """The verdict field names `design-anti-patterns.md` marks as
    schema-backed for entry 1, derived from its own "backed by ... required
    verdict fields" sentence rather than hardcoded here."""
    text = _text() if text is None else text
    m = BACKED_FIELDS_RE.search(text)
    if not m:
        raise AssertionError(
            "could not locate the 'backed by ... required verdict fields' "
            "sentence in design-anti-patterns.md — extraction broke"
        )
    span = m.group(1)
    fields = {name for name in FIELD_TOKEN_RE.findall(span) if "_" in name}
    if not fields:
        raise AssertionError(
            "no underscored field names found in the marked-fields span"
        )
    return fields


def _module_required_top_level_fields(text=None):
    """The top-level verdict fields `forge_docreview.py` actually enforces
    as required, derived from `_required_verdict_fields_text()`'s rendered
    output — the same text the module renders for its own CLI help —
    rather than a hardcoded copy of the two names."""
    text = forge_docreview._required_verdict_fields_text() if text is None else text
    fields = set()
    for line in text.splitlines():
        m = re.match(r"^- `([a-zA-Z_][a-zA-Z0-9_.]*)`.*(?:may be empty only when|is required)", line)
        if m:
            fields.add(m.group(1).split(".")[0])
    if not fields:
        raise AssertionError(
            "no required top-level fields parsed out of "
            "_required_verdict_fields_text() — extraction broke"
        )
    return fields


def test_marked_fields_agree_with_module_required_fields():
    doc_fields = _doc_marked_fields()
    module_fields = _module_required_top_level_fields()
    assert doc_fields == module_fields


def test_agreement_check_catches_a_field_removed_from_the_doc():
    # Proves the test above can actually go red, rather than merely passing
    # today. Mutate the document's own text (never the file on disk) to
    # drop one marked field, re-run the real extraction (`_doc_marked_fields`)
    # on the mutated text, and confirm the comparison against the module
    # side goes red. Popping an element from an already-extracted set would
    # prove nothing about the extraction itself — this re-derives the doc
    # side from text the way the real test does.
    original = _text()
    mutated_text = original.replace(
        "`dependencies_read` for the first two gates, `replaced_system` for the third.",
        "`dependencies_read` for the first two gates.",
    )
    assert mutated_text != original  # sanity: the mutation actually landed
    mutated_doc_fields = _doc_marked_fields(mutated_text)
    module_fields = _module_required_top_level_fields()
    assert mutated_doc_fields != module_fields


def test_agreement_check_catches_a_field_removed_from_the_module():
    # Same proof, mutating the module side instead: drop one required field
    # from the rendered required-field text and confirm the comparison
    # against the doc's marked fields goes red.
    doc_fields = _doc_marked_fields()
    rendered = forge_docreview._required_verdict_fields_text()
    one_marked = next(iter(doc_fields))
    mutated_lines = [
        line for line in rendered.splitlines()
        if not line.startswith("- `{}".format(one_marked))
    ]
    mutated_module_fields = _module_required_top_level_fields("\n".join(mutated_lines))
    assert doc_fields != mutated_module_fields


# --- lint_living_spec still exposes exactly five rules ----------------------
#
# Counted structurally from the function's own source, not from a fixture: a
# fixture only proves "the rules I thought to violate fired" and stays green
# if a sixth rule is added that the fixture happens not to trip (verified
# live against a tab-character rule added to lint_living_spec — a
# violate-all-known-rules fixture didn't move). Each of the function's
# top-level statements whose subtree mutates the local `defects` list —
# `.append(...)`, `.extend(...)`, `defects += ...`, or a self-referential
# rebind (`defects = defects + [...]`) — is one rule. `scripts/forge_lint.py`
# is read-only for this task, so the count comes from `inspect.getsource` +
# `ast`, never from re-implementing the rules.
#
# Residual limit, stated rather than implied away: this is a syntactic
# property of `lint_living_spec`'s own top-level statements, not a semantic
# one. A rule implemented entirely inside a helper function invoked from an
# existing statement, or two rules sharing one top-level statement, mutates
# `defects` zero or one times at *this* level regardless — no AST walk
# confined to this function's body can see a mutation that isn't lexically
# present in it, so both cases are out of reach and this check cannot
# detect them. It catches every rule shaped like the five it counts today;
# it does not catch a rule hidden inside one of them.


def _mutates_defects(stmt):
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "defects"
        ):
            return True
        if isinstance(node, ast.AugAssign) and (
            isinstance(node.target, ast.Name) and node.target.id == "defects"
        ):
            return True
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "defects" for t in node.targets
        ):
            # A rebind counts only when self-referential (`defects =
            # defects + [...]`) — the function's own `defects = []`
            # initializer is also an Assign to the name `defects` but does
            # not reference `defects` on its right-hand side, so it must
            # not be counted as a rule.
            rhs_names = {
                n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)
            }
            if "defects" in rhs_names:
                return True
    return False


def _count_rule_statements(source):
    tree = ast.parse(source)
    funcs = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "lint_living_spec"
    ]
    if len(funcs) != 1:
        raise AssertionError(
            "expected exactly one top-level 'lint_living_spec' function "
            "definition in the extracted source — found {}".format(len(funcs))
        )
    func = funcs[0]
    return sum(1 for stmt in func.body if _mutates_defects(stmt))


def test_lint_living_spec_exposes_exactly_five_rules():
    source = inspect.getsource(forge_lint.lint_living_spec)
    assert _count_rule_statements(source) == 5


def _spliced(source, sixth_rule_body):
    marker = "    return defects\n"
    assert source.endswith(marker)  # sanity: splice point still exists
    mutated = source[: -len(marker)] + sixth_rule_body + marker
    assert mutated != source
    return mutated


def test_five_rules_check_catches_a_sixth_rule_via_append():
    # Proves the check above can actually go red, in each of the three
    # mutation forms it claims to recognise. Never touches
    # scripts/forge_lint.py: each works on an in-memory copy of the
    # function's source text with one more defect-mutating top-level
    # statement spliced in, and confirms the count moves to six.
    source = inspect.getsource(forge_lint.lint_living_spec)
    mutated = _spliced(
        source,
        "    if False:\n"
        "        defects.append('sixth rule fired')\n",
    )
    assert _count_rule_statements(mutated) == 6


def test_five_rules_check_catches_a_sixth_rule_via_extend():
    source = inspect.getsource(forge_lint.lint_living_spec)
    mutated = _spliced(
        source,
        "    if False:\n"
        "        defects.extend(['sixth rule fired'])\n",
    )
    assert _count_rule_statements(mutated) == 6


def test_five_rules_check_catches_a_sixth_rule_via_aug_assign():
    source = inspect.getsource(forge_lint.lint_living_spec)
    mutated = _spliced(
        source,
        "    if False:\n"
        "        defects += ['sixth rule fired']\n",
    )
    assert _count_rule_statements(mutated) == 6


def test_five_rules_check_does_not_see_a_rule_hidden_in_a_helper():
    # Documents the counter's stated limit rather than leaving it implied:
    # a rule implemented inside a helper invoked from an existing statement
    # mutates `defects` zero times at lint_living_spec's own top level, so
    # the count stays at five — this is the case the comment above says is
    # out of reach, proven rather than merely asserted.
    source = inspect.getsource(forge_lint.lint_living_spec)
    hidden_helper = (
        "def _sixth_rule_hidden_helper(defects):\n"
        "    defects.append('sixth rule fired from inside a helper')\n"
        "\n"
    )
    mutated_source = source.replace(
        "    return defects\n",
        "    _sixth_rule_hidden_helper(defects)\n"
        "    return defects\n",
    )
    assert mutated_source != source
    full_module = hidden_helper + mutated_source
    assert _count_rule_statements(full_module) == 5
