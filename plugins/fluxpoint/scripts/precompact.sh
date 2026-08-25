#!/usr/bin/env bash
# PreCompact hook. Compaction is the largest memory-loss event in a session:
# the transcript that holds every rejected approach, every reason a thing was
# done this way, and every dead end already paid for gets summarized down,
# and only what reached the disk survives intact.
#
# A hook cannot read a transcript and decide what mattered — but it can
# refuse to let the transcript be discarded while nothing durable changed.
# The gate asks one deterministic question: since this compaction window
# began, did any durable surface change — the work file's Decisions/Notes,
# the project's memory store, or the session's task board? If none did and
# the session did work, compaction is refused ONCE (exit 2), with the reason
# on stderr, so the flush happens before the reasoning is gone.
#
# The refusal is bounded per window, which is what makes blocking safe: the
# block leaves a marker, the next attempt always proceeds and clears it, and
# each allowed compaction re-seeds the window snapshot. Two consecutive
# attempts can never both block, so the gate cannot wedge a session — the
# worst case is one retry — while windows later in a marathon session are
# still protected. FPL_COMPACT_BLOCK=0 demotes it to warn-only.
#
# Non-git roots are in scope: without git there is no "did code change"
# signal, so an unflushed window there blocks on the flush evidence alone.
# The old git-guard exit made the hook inert in exactly the long non-repo
# sessions where compaction hurts most.
#
# Deliberately NOT built on stdout: whether a PreCompact hook's stdout is
# injected into the compaction summary is undocumented, and a hook whose
# entire value rested on an undocumented mechanism would be inert without
# ever saying so — the failure class this plugin exists to refuse. The
# durable side effects are the marker files SessionStart reads; the stdout
# lines on the allowed pass are a bonus if the summarizer sees them.
set -u
[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
proj="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | fpl_json_get cwd)}"
cd "${proj:-.}" 2>/dev/null || exit 0

sid="$(printf '%s' "$input" | fpl_json_get session_id)"
sid="${sid:-nosession}"
why="$(printf '%s' "$input" | fpl_json_get compact_reason)"
sd="$(fpl_state_dir)"
mkdir -p "$sd" 2>/dev/null || exit 0

worked="unknown"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  FPL_DIFF_BASE="$(fpl_base_ref "$sid")"
  export FPL_DIFF_BASE
  worked="no"; fpl_code_dirty && worked="yes"
fi

now_mem="$(fpl_memory_sha)"
now_aux="$(fpl_aux_sha "$sid")"
cwin="$sd/$sid.cwin"

# "Flushed" is judged against the window snapshot — seeded at SessionStart
# and re-seeded by each allowed compaction — so every compaction window
# carries its own flush evidence. A session-start snapshot alone would let
# hour-one notes excuse an hour-six compaction. No baseline means no verdict:
# the gate allows and seeds rather than guessing.
flushed="unknown"
if [ -f "$cwin" ]; then
  flushed="yes"
  [ "$now_mem" = "$(sed -n 1p "$cwin" 2>/dev/null)" ] \
    && [ "$now_aux" = "$(sed -n 2p "$cwin" 2>/dev/null)" ] && flushed="no"
fi
[ -f "$sd/$sid.nodecision" ] && flushed="declared"

state="$(fpl_state_file || true)"
blockmark="$sd/$sid.cblock"
if [ "$flushed" = "no" ] && [ "$worked" != "no" ] \
   && [ ! -f "$blockmark" ] && [ "${FPL_COMPACT_BLOCK:-1}" != "0" ]; then
  date -u +%FT%TZ >"$blockmark"
  {
    echo "COMPACTION BLOCKED — once. This gate never blocks twice in a row: the next attempt proceeds no matter what."
    echo "Since this compaction window began, no durable surface changed: not ${state:-the work file}'s Decisions or Notes, not the memory store, not the task board. Whatever this window learned — decisions made, approaches ruled out, findings — exists only in the transcript about to be summarized."
    echo "Before retrying: write down what should survive (a decision row or note in ${state:-the work file}, a memory file, a task update). If genuinely nothing is worth keeping, retry as-is and it will proceed."
  } >&2
  exit 2
fi

blocked="no"; [ -f "$blockmark" ] && blocked="yes"
printf '{"when":"%s","reason":"%s","codeChanged":"%s","flushed":"%s","blocked":"%s"}\n' \
  "$(date -u +%FT%TZ)" "${why:-unknown}" "$worked" "$flushed" "$blocked" \
  >"$sd/$sid.compacted"
printf '%s\n%s\n' "$now_mem" "$now_aux" >"$cwin"
rm -f "$blockmark"

echo "Flux Point: context is being compacted (${why:-unknown})."
echo "- Durable state survives this intact and does not need summarizing: ${state:-the work file}, and .claude/fluxpoint/ (evidence, decisions, lessons, counterexamples, the inbox)."
if [ "$flushed" = "no" ] && [ "$worked" != "no" ]; then
  echo "- Nothing durable was written in this compaction window. Any choice made and any approach already ruled out exists only in the transcript being compacted — preserve those verbatim in the summary, because after this they are gone."
fi
exit 0
