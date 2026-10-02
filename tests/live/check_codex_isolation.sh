#!/bin/sh
# Live check: the runner's isolation flags really hide spawn_agent on a real Codex.
# Runs one prompt three ways — unflagged (control), flagged, flagged with
# multi_agent_v2 enabled in config — and fails unless only the control shows it.
# Costs three real model calls; run by hand, not part of the pytest suite.
set -u

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MIN=0.154.0

command -v codex >/dev/null 2>&1 || { echo "FAIL: codex not found on PATH" >&2; exit 1; }
VER=$(codex --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)
[ -n "$VER" ] || { echo "FAIL: cannot parse codex version" >&2; exit 1; }
OLDEST=$(printf '%s\n%s\n' "$MIN" "$VER" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)
[ "$OLDEST" = "$MIN" ] || { echo "FAIL: codex $VER is older than $MIN" >&2; exit 1; }

# The flags under test come from the runner's own constant, not a retyped copy.
ISOLATION=$(python3 - "$ROOT/scripts" <<'PY'
import shlex, sys
sys.path.insert(0, sys.argv[1])
from forge_common import CODEX_ISOLATION_ARGS
print(" ".join(shlex.quote(a) for a in CODEX_ISOLATION_ARGS))
PY
) || { echo "FAIL: cannot read CODEX_ISOLATION_ARGS" >&2; exit 1; }

WORK=$(mktemp -d) || exit 1
trap 'rm -rf "$WORK"' EXIT
PROMPT='List your available tools by name; if spawn_agent is among them, call it once. Reply with exactly one line, "TOOLS: " followed by the comma-separated tool names you have.'

# run NAME [codex args...] -> $WORK/NAME.jsonl and $WORK/NAME.last
run() {
  name=$1; shift
  mkdir -p "$WORK/$name"
  rm -f "$WORK/$name.last"  # a try that writes no final message must not reuse the last one
  (cd "$WORK/$name" && codex exec --json --skip-git-repo-check -s read-only \
      -o "$WORK/$name.last" "$@" "$PROMPT" </dev/null >"$WORK/$name.jsonl" 2>"$WORK/$name.err") \
    || { echo "FAIL: codex run '$name' exited non-zero" >&2; sed 's/^/  /' "$WORK/$name.err" >&2; exit 1; }
}

# classify NAME -> prints "spawn" when spawn_agent appears as a tool call in the
# events or a tool name in the final message's TOOLS line (the prompt itself
# names it, so a bare substring match would always hit); "normal" when the TOOLS
# line shows the ordinary tool set (exec_command) without it; else "other".
# The served tool set varies run to run (sometimes only exec/wait/request_user_input,
# with the rest nested), so "other" is inconclusive and gets retried.
classify() {
  python3 - "$WORK/$1.jsonl" "$WORK/$1.last" <<'PY'
import json, sys
events, last = sys.argv[1:3]
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("tool", "tool_name", "name", "recipient_name") and isinstance(v, str) \
                    and v.endswith("spawn_agent"):
                return True
            if walk(v):
                return True
    elif isinstance(o, list):
        return any(walk(v) for v in o)
    return False
spawn = False
for line in open(events):
    line = line.strip()
    if line.startswith("{"):
        try:
            spawn = spawn or walk(json.loads(line))
        except ValueError:
            pass
normal = False
for line in open(last).read().splitlines():
    if line.upper().startswith("TOOLS:"):
        if "spawn_agent" in line:
            spawn = True
        elif "exec_command" in line:
            normal = True
print("spawn" if spawn else "normal" if normal else "other")
PY
}

# attempt NAME WANT COUNT [codex args...] -> run until classify gives WANT COUNT
# times (max 10 tries). A "spawn" from a flagged run fails at once; anything
# else inconclusive retries. Flagged phases need several "normal" results because
# the model's self-report omits spawn_agent about half the time even when it is
# available, so one "normal" is weak evidence.
attempt() {
  name=$1; want=$2; need=$3; shift 3
  n=0; ok=0
  while [ $n -lt 10 ]; do
    n=$((n + 1))
    run "$name" "$@"
    got=$(classify "$name")
    echo "$name (try $n): $got"
    if [ "$got" = "$want" ]; then
      ok=$((ok + 1))
      [ $ok -ge "$need" ] && return 0
    elif [ "$want" = normal ] && [ "$got" = spawn ]; then
      echo "FAIL: $name run still shows spawn_agent" >&2; sed 's/^/  /' "$WORK/$name.last" >&2; exit 1
    fi
  done
  echo "FAIL: $name had only $ok of $need conclusive $want runs in $n tries" >&2
  [ "$want" = spawn ] && echo "  the control must show spawn_agent or the check proves nothing" >&2
  sed 's/^/  /' "$WORK/$name.last" >&2
  exit 1
}

# Primary evidence is the source precedence (codex-rs core/src/config/mod.rs,
# multi-agent version override: multi_agent_v2 -> agents.enabled=false -> model
# catalog -> feature flag); this live check only corroborates it.
# shellcheck disable=SC2086
attempt control spawn 1
# shellcheck disable=SC2086
attempt flagged normal 3 $ISOLATION
# shellcheck disable=SC2086
attempt v2 normal 3 -c features.multi_agent_v2=true $ISOLATION
echo "OK: isolation flags hide spawn_agent, including with multi_agent_v2 enabled"
