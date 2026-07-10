#!/usr/bin/env bash
# Stop-hook Definition-of-Done gate. Blocks the stop while the repo harness
# or the hygiene scan is red; yields with a checkpoint notice after
# FPL_MAX_BLOCKS consecutive blocks (three failed paths is a user
# checkpoint, not a retreat). The counter bounds the loop, so no
# stop_hook_active early-exit is needed.
set -u
[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
proj="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | fpl_json_get cwd)}"
cd "${proj:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

sid="$(printf '%s' "$input" | fpl_json_get session_id)"
sid="${sid:-nosession}"
sd="$(fpl_state_dir)"
dirty="$sd/$sid.dirty"
counter="$sd/$sid.blocks"
[ -f "$dirty" ] || exit 0
mkdir -p "$sd"

if [ ! -f scripts/harness.sh ]; then
  rm -f "$dirty"
  fpl_json_obj systemMessage "fluxpoint-loop: code changed this session but scripts/harness.sh is absent, so the DoD gate has nothing to enforce. /fluxpoint-loop:loop-init scaffolds it."
  exit 0
fi

nl=$'\n'
findings=""
hlog="$sd/full.log"
if ! bash scripts/harness.sh --full >"$hlog" 2>&1; then
  findings="harness --full RED (last 40 lines):${nl}$(tail -n 40 "$hlog")${nl}${nl}"
fi
hy="$(fpl_scan_hygiene | head -n 40)"
if [ -n "$hy" ]; then
  findings="${findings}hygiene scan RED, new code carries forbidden markers:${nl}${hy}${nl}"
fi

ts="$(date -u +%FT%TZ)"
if [ -z "$findings" ]; then
  printf 'PASS %s\n' "$ts" >"$sd/last-harness"
  rm -f "$dirty" "$counter"
  exit 0
fi
printf 'FAIL %s\n' "$ts" >"$sd/last-harness"

count="$(cat "$counter" 2>/dev/null || echo 0)"
case "$count" in '' | *[!0-9]*) count=0 ;; esac
max="${FPL_MAX_BLOCKS:-3}"

if [ "$count" -ge "$max" ]; then
  fpl_json_obj systemMessage "fluxpoint-loop DoD gate: still red after $max blocked stops. Checkpoint: the task is NOT done.${nl}${findings}"
  exit 0
fi
printf '%s\n' "$((count + 1))" >"$counter"
fpl_json_obj decision block reason "DoD gate red, attempt $((count + 1))/$max. The harness decides done, not the agent. Fix every item below, re-run scripts/harness.sh --full until it exits 0, then stop.${nl}${findings}"
exit 0
