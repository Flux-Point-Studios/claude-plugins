#!/usr/bin/env bash
# SessionStart hook. Stdout is injected as context Claude can read; content
# is phrased as factual statements because imperative "system command"
# phrasing can trip prompt-injection defenses and surface the text to the
# user instead. Runs on startup, resume, clear, and post-compaction.
set -u
[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
proj="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | fpl_json_get cwd)}"
cd "${proj:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

sd="$(fpl_state_dir)"
find "$sd" -maxdepth 1 \( -name '*.dirty' -o -name '*.blocks' \) -mtime +3 -delete 2>/dev/null

branch="$(git branch --show-current 2>/dev/null)"
dirtyn="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

echo "Flux Point work context, generated $(date -u +%FT%TZ):"
echo "- Branch: ${branch:-detached}; uncommitted changes: ${dirtyn} path(s)."

verdict=""
for d in "$sd" "$(fpl_legacy_state_dir)"; do
  [ -f "$d/last-harness" ] && { verdict="$(cat "$d/last-harness")"; break; }
done
if [ -n "$verdict" ]; then
  echo "- Last recorded harness verdict: ${verdict}."
else
  echo "- No harness verdict has been recorded in this repo yet."
fi

if [ -f scripts/harness.sh ]; then
  echo "- Harness: scripts/harness.sh is present. A Stop-hook DoD gate runs '--full' plus a hygiene scan whenever code changed this session; red results block the stop, up to ${FPL_MAX_BLOCKS:-3} consecutive times, after which the gate yields with a checkpoint notice. Green is the only clean exit."
else
  echo "- Harness: scripts/harness.sh is absent, so the DoD gate is dormant in this repo. The /fluxpoint:init command scaffolds it."
fi

# Latest graph run, if this repo runs campaigns.
runs="$sd/runs"
if [ -d "$runs" ]; then
  last="$(ls -1t "$runs"/*.json 2>/dev/null | head -1)"
  if [ -n "$last" ]; then
    echo "- Last graph run: $(fpl_run_summary "$last")."
  fi
fi

state="$(fpl_state_file || true)"
if [ -n "$state" ]; then
  echo "- ${state} (first 80 lines):"
  sed -n '1,80p' "$state"
  if [ "$state" = "LOOP.md" ]; then
    echo "- Note: LOOP.md is the pre-1.0 state file and is still honored. /fluxpoint:migrate folds it into WORK.md, which carries one Definition of Done and one Evidence table for both loop slices and graph campaigns."
  fi
fi
exit 0
