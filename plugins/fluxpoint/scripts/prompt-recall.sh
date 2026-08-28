#!/usr/bin/env bash
# UserPromptSubmit hook: per-prompt memory recall, DARK BY DEFAULT.
#
# Ships gated behind FPL_MEM_PROMPT=1 because the strongest external result
# on agent memory (SWE-ContextBench) says wrongly retrieved memories cost
# more than none: autonomous retrieval of the wrong summaries scored BELOW
# the no-memory baseline. Until an artifact-grounded eval shows lift in this
# repo's own campaigns, injecting on every prompt is a bet, and bets ship
# opt-in. The gate is the first check so the off state costs one comparison.
#
# When armed: recall.py --for-prompt reads the hook's JSON from stdin,
# ranks offline against the existing index (never rebuilding, never
# touching the network), applies a precision floor — two genuinely
# independent retrieval legs, or a lexical match on at least two
# informative query tokens; the graph leg never corroborates, because its
# seeds come from the lexical top ranks — and prints at most 3 items in
# 1200 bytes. Stdout on exit 0 becomes context; exit is 0 unconditionally.
set -u
[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
[ "${FPL_MEM_PROMPT:-0}" = "1" ] || exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
fpl_cd_project "$input" || exit 0
printf '%s' "$input" | "$FPL_PY" "$here/recall.py" --for-prompt 2>/dev/null || true
exit 0
