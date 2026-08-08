#!/usr/bin/env bash
# PreCompact hook. Compaction is the largest memory-loss event in a session:
# the transcript that holds every rejected approach, every reason a thing was
# done this way, and every dead end already paid for gets summarized down,
# and only what reached the disk survives intact.
#
# This hook does not try to rescue that reasoning — a hook cannot read a
# transcript and decide what mattered. It answers one deterministic question
# instead: at the moment of compaction, had anything this session learned
# been written down? Then it records the answer where the next context will
# certainly see it.
#
# Deliberately NOT built on stdout: whether a PreCompact hook's stdout is
# injected into the compaction summary is undocumented, and a hook whose
# entire value rested on an undocumented mechanism would be inert without
# ever saying so — the failure class this plugin exists to refuse. The
# durable side effect is a marker file that SessionStart reads, and
# SessionStart injection is the backbone this plugin already relies on. The
# stdout line below is a bonus if the summarizer sees it and costs nothing
# if it does not.
#
# It never blocks compaction. A hook that can wedge a session by refusing to
# free context is a worse failure than the memory it was protecting.
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
why="$(printf '%s' "$input" | fpl_json_get compact_reason)"
sd="$(fpl_state_dir)"
mkdir -p "$sd" 2>/dev/null || exit 0

FPL_DIFF_BASE="$(fpl_base_ref "$sid")"
export FPL_DIFF_BASE

now_sha="$(fpl_memory_sha)"
start_sha="$(cat "$sd/$sid.snapshot" 2>/dev/null || echo "")"
worked="no"; fpl_code_dirty && worked="yes"
flushed="yes"
[ -n "$start_sha" ] && [ "$now_sha" = "$start_sha" ] && flushed="no"
[ -f "$sd/$sid.nodecision" ] && flushed="declared"

printf '{"when":"%s","reason":"%s","codeChanged":"%s","flushed":"%s"}\n' \
  "$(date -u +%FT%TZ)" "${why:-unknown}" "$worked" "$flushed" \
  >"$sd/$sid.compacted"

state="$(fpl_state_file || true)"
echo "Flux Point: context is being compacted (${why:-unknown})."
echo "- Durable state survives this intact and does not need summarizing: ${state:-the work file}, and .claude/fluxpoint/ (evidence, decisions, lessons, counterexamples, the inbox)."
if [ "$worked" = "yes" ] && [ "$flushed" = "no" ]; then
  echo "- Nothing was written to ${state:-the work file}'s Decisions or Notes this session, and code did change. Any choice made and any approach already ruled out exists only in the transcript being compacted — preserve those verbatim in the summary, because after this they are gone."
fi
exit 0
