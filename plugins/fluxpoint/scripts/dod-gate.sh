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
# Everything below judges the tree against where this session started, not
# against HEAD. Committing is not a way to stop being judged.
FPL_DIFF_BASE="$(fpl_base_ref "$sid")"
export FPL_DIFF_BASE
# Two independent signals arm the gate. The PostToolUse marker records edits
# that were later committed (git-clean but real work); fpl_code_dirty catches
# everything the marker cannot see, because that hook only matches
# Write|Edit|MultiEdit — source written through the Bash tool (cat >, sed -i,
# git apply) would otherwise leave the gate disarmed and let the session stop
# with the harness never run.
if [ ! -f "$dirty" ] && ! fpl_code_dirty; then
  exit 0
fi
mkdir -p "$sd"

if [ ! -f scripts/harness.sh ]; then
  rm -f "$dirty"
  fpl_json_obj systemMessage "fluxpoint: code changed this session but scripts/harness.sh is absent, so the DoD gate has nothing to enforce. /fluxpoint:init scaffolds it."
  exit 0
fi

nl=$'\n'
findings=""
state_file="$(fpl_state_file || true)"
hlog="$sd/full.log"
yielded="$sd/$sid.yielded"

# WHICH contract the gate enforces is the repo's call, because "full" is not a
# fixed cost. Measured 2026-08-11: one repo's --full is ~24 minutes against a
# hook ceiling of 600s that cannot be raised (the hook is killed at 600s
# whatever FPL_GATE_TIMEOUT says), so the gate could never finish, never
# disarm, and the only escape was FPL_DISABLE=1 — losing the gate entirely.
# A cheaper contract enforced every stop beats a perfect one enforced never.
# Resolved HERE, above the yield guard, because that guard names it too and
# `set -u` turns a use-before-assign into a crashed gate rather than a warning.
read -r -a harness_args <<<"${FPL_HARNESS_ARGS:---full}"
harness_desc="${harness_args[*]}"

# The work file is excluded throughout: this gate writes its own row into it,
# so counting it would mean every verdict reported a tree one path dirtier
# than the last — the measurement moving because the measuring happened.
work_file="$(fpl_state_file || true)"
gate_status() {
  if [ -n "$work_file" ]; then
    git status --porcelain -- . ":!$work_file" 2>/dev/null
  else
    git status --porcelain 2>/dev/null
  fi
}
# HEAD alone cannot identify what was tested: in loop mode it is the pre-work
# commit for the whole iteration, so a verdict taken before the first edit and
# one taken after the last carry the same sha. Hashing the working tree makes
# the row name the thing the harness actually ran over.
#
# TWO CALLERS WANT THIS AT DIFFERENT INSTANTS, and they are not the same
# question. The yield guard below needs the tree it is ABOUT TO spend the
# harness on; the Evidence proof needs the tree it ACTUALLY measured. Taking
# one hash for both made every green verdict look new — the run itself moves
# the tree — and duplicated its row on every stop.
gate_tree_sha() {
  local d
  if [ -n "$work_file" ]; then
    d="$(git diff HEAD -- . ":!$work_file" 2>/dev/null)"
  else
    d="$(git diff HEAD 2>/dev/null)"
  fi
  printf '%s\n%s' "$(gate_status)" "$d" | "$FPL_PY" -c '
import hashlib, sys
print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:8])' 2>/dev/null || echo unknown
}

# Yielding is a decision about a TREE, and it has to hold until that tree
# changes. Without this the gate stops blocking after $max but keeps RUNNING:
# a harness that cannot finish inside the hook ceiling is re-run in full on
# every subsequent stop, paying the whole wall-clock to reach a conclusion
# already abandoned. Measured 2026-08-11 against a 35-minute suite on a 540s
# ceiling — every turn cost the operator nine minutes and an Esc.
# Any real edit moves the hash, which re-arms the gate, so this cannot decay
# into "yielded once, disabled forever".
guard_sha="$(gate_tree_sha)"
if [ "$guard_sha" != unknown ] && [ "$(cat "$yielded" 2>/dev/null)" = "$guard_sha" ]; then
  fpl_json_obj systemMessage "fluxpoint DoD gate: already yielded on this exact tree (${guard_sha}); not re-running the harness until something changes. Run scripts/harness.sh ${harness_desc} yourself to re-establish a verdict."
  exit 0
fi
# Mark the run as in flight BEFORE starting it. A gate killed mid-run — the
# hook ceiling is 600s, and aiken check plus a few thousand tests can reach
# it — used to leave the previous PASS sitting there to be read as current.
# A crashed verdict must never look like a passed one.
start_ts="$(date -u +%FT%TZ)"
printf 'RUNNING %s\n' "$start_ts" >"$sd/last-harness"
gate_timeout="${FPL_GATE_TIMEOUT:-540}"
if command -v timeout >/dev/null 2>&1; then
  timeout "$gate_timeout" bash scripts/harness.sh "${harness_args[@]}" >"$hlog" 2>&1
  hrc=$?
else
  bash scripts/harness.sh "${harness_args[@]}" >"$hlog" 2>&1
  hrc=$?
fi
if [ "$hrc" -eq 124 ] || [ "$hrc" -eq 137 ]; then
  # Not red and not green: nothing was established either way, and saying so
  # is the whole point.
  printf 'TIMEOUT %s\n' "$(date -u +%FT%TZ)" >"$sd/last-harness"
  findings="harness ${harness_desc} did not finish within ${gate_timeout}s and was killed.${nl}\
This is not a pass and not a failure — nothing was established. Either the${nl}\
suite got slower than the gate allows or something hung. Run it yourself,${nl}\
then split it or raise FPL_GATE_TIMEOUT (the hook ceiling is 600s).${nl}\
Last 40 lines before the kill:${nl}$(tail -n 40 "$hlog")${nl}${nl}"
elif [ "$hrc" -ne 0 ]; then
  findings="harness ${harness_desc} RED (last 40 lines):${nl}$(tail -n 40 "$hlog")${nl}${nl}"
fi
hy="$(fpl_scan_hygiene | head -n 40)"
if [ -n "$hy" ]; then
  findings="${findings}hygiene scan RED, new code carries forbidden markers:${nl}${hy}${nl}"
fi

# Optional, and off unless asked for: green code with nothing written down is
# a session whose reasoning dies at the next compaction. Three file
# comparisons, no model judgment. Off by default because a check that starts
# by blocking stops is a check people disable, taking the rest of the gate
# with it — arm it per repo once the false-positive rate is known.
if [ "${FPL_DISTILL:-0}" = "1" ] && [ -z "$findings" ]; then
  snap="$(cat "$sd/$sid.snapshot" 2>/dev/null || echo "")"
  if [ -n "$snap" ] && [ "$(fpl_memory_sha)" = "$snap" ] \
     && [ ! -f "$sd/$sid.nodecision" ]; then
    findings="${findings}nothing was written down this session.${nl}\
Code changed, the harness is green, and ${state_file:-the work file}'s Decisions${nl}\
and Notes are byte-identical to how this session found them. Whatever was${nl}\
chosen and whatever was ruled out exists only in a transcript that${nl}\
compaction will summarize away.${nl}\
Record the choice:  decision.py --record < decision.json${nl}\
or say there was none: decision.py --none \"<why>\" --session ${sid}${nl}"
  fi
fi

ts="$(date -u +%FT%TZ)"

# The gate is the only writer of Evidence the agent does not author. It runs
# because the runtime invoked it, at the moment done is claimed, and it
# already holds the exit code, the log, and the tree — so it records its own
# verdict rather than asking the agent to describe it afterwards. Rows are
# written only where a stop actually happens: an ordinary blocked stop is not
# an outcome, it is a correction.
fpl_record_evidence() { # outcome, claim, proof
  local ev state
  state="$(fpl_state_file || true)"
  [ -n "$state" ] || return 0
  ev="$(dirname "$0")/evidence.py"
  [ -f "$ev" ] || return 0
  "$FPL_PY" "$ev" --record --source gate --graph "$state" \
    --outcome "$1" --claim "$2" --proof "$3" >/dev/null 2>&1 || true
}

head_sha="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
status_out="$(gate_status)"
dirty_n="$(printf '%s' "$status_out" | grep -c . | tr -d ' ')"
# The tree, taken AFTER the run: this is what the harness actually measured,
# and the Evidence row dedups on it.
tree_sha="$(gate_tree_sha)"
log_sha="$("$FPL_PY" - "$hlog" <<'PY' 2>/dev/null || echo unknown
import hashlib, sys
try:
    print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()[:8])
except Exception:
    print("unknown")
PY
)"
# The tree, not just the commit: in loop mode HEAD is the pre-work commit for
# the whole iteration, so a sha alone cannot tell a verdict about the work
# from a verdict about the tree before it.
proof="harness ${harness_desc} exit ${hrc} @${head_sha} tree:${tree_sha} dirty:${dirty_n} log:${log_sha}"

if [ -z "$findings" ]; then
  printf 'PASS %s\n' "$ts" >"$sd/last-harness"
  rm -f "$dirty" "$counter"
  fpl_record_evidence PASS \
    "Stop-gate DoD: scripts/harness.sh ${harness_desc} green over ${dirty_n} changed path(s), hygiene scan clean" \
    "$proof"
  # This tree passed, so it is what the next verdict should be measured
  # against. Refreshed only on green: a red or timed-out run has not
  # established anything worth inheriting as a starting point.
  fpl_set_base "$sid"
  touched="$(fpl_trust_base_modified | sort -u | tr '\n' ' ')"
  if [ -n "${touched// /}" ]; then
    fpl_json_obj systemMessage "fluxpoint: gate green, but this session changed the files that decide what green means: ${touched}. A verdict is only as trustworthy as the contract that produced it — review those diffs before trusting this pass."
  fi
  exit 0
fi
printf 'FAIL %s\n' "$ts" >"$sd/last-harness"

count="$(cat "$counter" 2>/dev/null || echo 0)"
case "$count" in '' | *[!0-9]*) count=0 ;; esac
max="${FPL_MAX_BLOCKS:-3}"

if [ "$count" -ge "$max" ]; then
  # The gate yields here, so the session stops with work unfinished. That is
  # the one red outcome worth a durable row: an ordinary blocked stop is a
  # correction the agent gets to act on, but a checkpoint is a stop.
  if [ "$hrc" -eq 124 ] || [ "$hrc" -eq 137 ]; then
    fpl_record_evidence TIMEOUT \
      "Stop-gate DoD: harness ${harness_desc} did not finish within ${gate_timeout}s — nothing was established, neither pass nor failure" \
      "$proof"
  else
    fpl_record_evidence FAIL \
      "Stop-gate DoD: still red after ${max} blocked stops; the task is NOT done" \
      "$proof"
  fi
  # Record WHICH tree was abandoned, so the guard at the top can skip a
  # re-run that cannot change this outcome. Not the counter: the counter says
  # how many times we blocked, the tree says what we blocked over.
  printf '%s\n' "$guard_sha" >"$yielded"
  fpl_json_obj systemMessage "fluxpoint DoD gate: still red after $max blocked stops. Checkpoint: the task is NOT done.${nl}${findings}"
  exit 0
fi
printf '%s\n' "$((count + 1))" >"$counter"
fpl_json_obj decision block reason "DoD gate red, attempt $((count + 1))/$max. The harness decides done, not the agent. Fix every item below, re-run scripts/harness.sh ${harness_desc} until it exits 0, then stop.${nl}${findings}"
exit 0
