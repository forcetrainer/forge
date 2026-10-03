#!/bin/sh
# Live check: a read-only `codex exec` reviewer opens a spec file named only by path.
# The prompt gives the file's path and nothing of its text; the marker line exists only
# in that file, so quoting it back proves the reviewer read it. Costs one real model
# call; run by hand, not part of the pytest suite.
# Exit 0: the last message quotes the marker. 1: it does not. 2: codex is not installed.
set -u

command -v codex >/dev/null 2>&1 || { echo "codex is not on PATH; cannot run the live check" >&2; exit 2; }

WORK=$(mktemp -d) || exit 1
trap 'rm -rf "$WORK"' EXIT

MARKER="SPEC-BY-PATH-MARKER-$(date +%s)-$$"
cat > "$WORK/spec.md" <<SPEC
---
system: live-check
---
# Live check spec

## Marker

$MARKER
SPEC

PROMPT="Open the file $WORK/spec.md and quote, verbatim, the single line under its '## Marker' heading. Reply with that line only."

(cd "$WORK" && codex exec --skip-git-repo-check -s read-only \
    -o "$WORK/last.txt" "$PROMPT" </dev/null >"$WORK/out.log" 2>"$WORK/err.log") \
  || { echo "FAIL: codex exec exited non-zero" >&2; sed 's/^/  /' "$WORK/err.log" >&2; exit 1; }

if [ -f "$WORK/last.txt" ] && grep -qF "$MARKER" "$WORK/last.txt"; then
  echo "PASS: the reviewer quoted the marker from the spec it was given only by path"
  exit 0
fi
echo "FAIL: the last message does not contain $MARKER" >&2
[ -f "$WORK/last.txt" ] && sed 's/^/  /' "$WORK/last.txt" >&2
exit 1
