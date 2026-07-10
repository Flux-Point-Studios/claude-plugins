#!/usr/bin/env bash
# PostToolUse hook on Write|Edit|MultiEdit. Marks the session dirty for the
# Stop gate and runs the repo's fast scoped checks; exit 2 feeds stderr back
# to Claude so failures get corrected at the edit, not at the gate.
set -u
[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
proj="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | fpl_json_get cwd)}"
cd "${proj:-.}" 2>/dev/null || exit 0

file="$(printf '%s' "$input" | fpl_json_get tool_input.file_path)"
[ -n "$file" ] || exit 0
case "$file" in
  *.md | */.claude/* | .claude/*) exit 0 ;;
esac

sid="$(printf '%s' "$input" | fpl_json_get session_id)"
sid="${sid:-nosession}"
sd="$(fpl_state_dir)"
mkdir -p "$sd"
: >"$sd/$sid.dirty"

[ -x scripts/harness.sh ] || exit 0
log="$sd/changed.log"
if ! scripts/harness.sh --changed "$file" >"$log" 2>&1; then
  {
    printf 'harness --changed RED for %s (last 30 lines):\n' "$file"
    tail -n 30 "$log"
  } >&2
  exit 2
fi
exit 0
