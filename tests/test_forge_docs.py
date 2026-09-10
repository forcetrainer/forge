"""Mechanical checks on skills/brainstorming/design-anti-patterns.md.

This is a prose artifact — nothing in it executes. Per `pipeline` spec's
"prose artifacts take mechanical acceptance" rule, the checks here are text
presence, structure, and counts, not invented behavioral tests. They must not
embed a Gate line verbatim: doing so would violate the constraint under test
(no file outside the document restates a Gate).
"""
import pathlib
import re
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "skills" / "brainstorming" / "design-anti-patterns.md"


def _text():
    return DOC_PATH.read_text()


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
