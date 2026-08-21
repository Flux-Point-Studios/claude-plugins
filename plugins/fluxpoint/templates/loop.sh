#!/usr/bin/env bash
# Outer Ralph loop: a fresh Claude context per iteration; all state lives in
# WORK.md and git. Promotion is decided by the harness, never by the agent.
# Unattended runs belong in a sandboxed container with allow-listed egress
# and zero reachable key material; only there is
#   PERMISSION_ARGS="--dangerously-skip-permissions"
# acceptable. Halt any time with: touch .claude/fluxpoint/STOP
set -euo pipefail

# Interpreter name differs by platform: `python3` on Linux/macOS, `python` on a
# standard Windows install. Resolve by running each candidate, because a
# name on PATH is not evidence of an interpreter.
if [ -z "${FPL_PY:-}" ]; then
  # Probe each candidate by RUNNING it, rather than asking whether the name
  # exists. Windows ships a `python3` App Execution Alias that is on PATH by
  # default on a machine with no python3 at all: it satisfies `command -v`,
  # then prints "Python was not found" and exits 49 for every argument.
  for _fpl_cand in python3 python; do
    if command -v "$_fpl_cand" >/dev/null 2>&1 &&
       "$_fpl_cand" -c "import sys" >/dev/null 2>&1; then
      FPL_PY="$_fpl_cand"
      break
    fi
  done
  unset _fpl_cand
  if [ -z "${FPL_PY:-}" ]; then
    echo "fluxpoint: no working python interpreter on PATH" >&2
    exit 127
  fi
fi
# Force UTF-8 on every embedded interpreter's stdio. Without it Windows writes
# cp1252, so a header like "## Plan --" emitted with an em-dash comes back as
# 0x97 and every consumer that greps for the UTF-8 bytes silently misses it.
export PYTHONIOENCODING=utf-8

MAX_ITER="${MAX_ITER:-25}"
MAX_TURNS="${MAX_TURNS:-40}"
PERMISSION_ARGS="${PERMISSION_ARGS:---permission-mode acceptEdits}"

sd=".claude/fluxpoint"
mkdir -p "$sd/logs"
rm -f "$sd/STOP"

for ((i = 1; i <= MAX_ITER; i++)); do
  if [ -f "$sd/STOP" ]; then
    echo "loop: STOP file present, halting after $((i - 1)) iteration(s)"
    exit 0
  fi
  echo "loop: iteration $i/$MAX_ITER"
  # Where this iteration started. The commit below empties `git diff HEAD`,
  # so without a base the co-change tier judges an empty diff and every
  # declared pair reports "no changes to compare" — silently, and for the
  # one class of pair that has no parity command to fall back on.
  # `|| true` because an unborn HEAD must not kill the runner.
  iter_base="$(git rev-parse HEAD 2>/dev/null || true)"
  # shellcheck disable=SC2086
  claude -p "$(cat WORK_PROMPT.md)" $PERMISSION_ARGS --max-turns "$MAX_TURNS" \
    --output-format stream-json --verbose \
    >"$sd/logs/iter-$i.jsonl" 2>&1 ||
    echo "loop: claude exited non-zero on iteration $i (see logs)" >&2
  git add -A
  git commit -q -m "loop: iteration $i" || true
  if FPL_PAIR_AGAINST="${FPL_PAIR_AGAINST:-$iter_base}" bash scripts/harness.sh --full \
     && grep -q '^STATUS: DONE' WORK.md; then
    echo "loop: harness green and STATUS DONE after iteration $i"
    exit 0
  fi
done
echo "loop: iteration budget exhausted, harness red or STATUS still ACTIVE. Checkpoint." >&2
# An unattended run that gives up silently is a run nobody learns about
# until they wonder why nothing shipped.
# `|| true` is load-bearing, and templates/harness.sh documents the same
# trap: find exits 1 when ~/.claude/plugins is absent, pipefail carries
# that through the pipe, and set -e then kills the runner ON THIS LINE —
# before the notification these three lines exist to send, and with the
# same exit 1 the intended path would have produced, so nothing looks
# wrong. The deterministic trigger is a machine with no plugins dir:
# exactly the sandboxed container this file's own header recommends.
ib="$({ find "$HOME/.claude/plugins" -type f -name inbox.py 2>/dev/null || true; } | head -1)"
[ -n "${FPL_PLUGIN_ROOT:-}" ] && [ -f "$FPL_PLUGIN_ROOT/scripts/inbox.py" ] \
  && ib="$FPL_PLUGIN_ROOT/scripts/inbox.py"
[ -n "$ib" ] && "$FPL_PY" "$ib" --add --kind budget-exhausted \
  --detail "outer loop spent its iteration budget with the harness red or STATUS not DONE" >/dev/null 2>&1
exit 1
