#!/usr/bin/env bash
# Decisions that outlive the context that made them.
#
# `decision.py` gives loop mode the bus that only graph campaigns had, with
# the DecisionV1 floors enforced rather than described — a decision worth
# surviving context death names the alternatives it beat and the best case
# against each, including against the one that won. The compaction-time
# counterpart lives in precompact-test.sh.
set -uo pipefail

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
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
export CLAUDE_PLUGIN_ROOT="$PLUGIN"
DEC="$PLUGIN/scripts/decision.py"
INJECT="$PLUGIN/scripts/inject-state.sh"
GATE="$PLUGIN/scripts/dod-gate.sh"
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

ok()  { printf 'PASS  %-58s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-58s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

GOOD='{
  "question": "how should the vault unlock window be bounded?",
  "options": [
    {"option": "24h", "argued_by": "ops",
     "strongest_objection": "a day may be too slow for incident response"},
    {"option": "72h", "argued_by": "gov",
     "strongest_objection": "three days widens the window an attacker has"}
  ],
  "chosen": "72h",
  "rationale": "the governance timelock already binds operations to 72h, so a shorter unlock buys nothing and adds a race",
  "overturned_prior": true,
  "frozen_by": "genesis",
  "reversible": false,
  "evidence": ["scripts/harness.sh --full exit 0"]
}'

mkrepo() { # $1 = harness exit
  rm -rf "$R"; mkdir -p "$R/scripts" "$R/src"; cd "$R" || exit 1
  git init -q -b main
  printf '#!/usr/bin/env bash\nexit %s\n' "${1:-0}" >scripts/harness.sh
  chmod +x scripts/harness.sh
  printf 'x = 1\n' >src/app.py
  cat >WORK.md <<'EOF'
# work

STATUS: WIP
MODE: loop

## Decisions

| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |
|---|---|---|---|---|---|

## Evidence

| When (UTC) | Source | Outcome | Claim | Proof |
|---|---|---|---|---|

## Notes for the next iteration
EOF
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
  printf '{"session_id":"s","cwd":"%s"}' "$R" | bash "$INJECT" >/dev/null 2>&1
}

dec() { "$FPL_PY" "$DEC" --root "$R" --graph WORK.md "$@"; }
dec_rows() { grep -cE '^\| 20[0-9-]+ [0-9:]+ \| [a-z]' "$R/WORK.md" 2>/dev/null | head -1; }

# ================= 1. the floors are enforced, not described =============
mkrepo
printf '%s' "$GOOD" | dec --record --id vault-window >/dev/null 2>&1
check "a real decision is recorded" 0 "$?"
grep -q '| vault-window | 72h | YES |' "$R/WORK.md" \
  && ok "the row carries the choice and that it overturned the prior" "recorded" \
  || bad "the row carries the choice and that it overturned the prior" "$(grep '^| 20' "$R/WORK.md" | head -1)"

mkrepo
printf '"we picked 72h"' | dec --record >/dev/null 2>&1
check "a bare sentence is refused" 1 "$?"

# Each mutation is written to a file and fed with --result rather than
# piped: under `pipefail` a pipeline inherits decision.py's exit 1, so
# `... | grep -q ... && ok` reports a refusal as a failure and the test lies
# about the code under test.
mutate() { # $1 = python expression mutating `d`
  printf '%s' "$GOOD" | "$FPL_PY" -c "
import json, sys
d = json.load(sys.stdin)
$1
print(json.dumps(d))" >"$R/mut.json"
}

mkrepo
mutate 'd["options"] = d["options"][:1]'
dec --record --result "$R/mut.json" >/dev/null 2>&1
check "one option is a plan, not a decision" 1 "$?"

mutate 'd["options"][1]["strongest_objection"] = "meh"'
out="$(dec --record --result "$R/mut.json" 2>&1)"
case "$out" in *"was not examined"*)
  ok "an option nobody argued against is refused" "refused" ;;
  *) bad "an option nobody argued against is refused" "${out:0:44}" ;; esac

mutate 'd["rationale"] = "it is better"'
dec --record --result "$R/mut.json" >/dev/null 2>&1
check "a lazy rationale is refused" 1 "$?"

# The subtle one: a well-formed record whose chosen value was never on the
# table. Every floor passes and the decision is still incoherent.
mutate 'd["chosen"] = "48h"'
out="$(dec --record --result "$R/mut.json" 2>&1)"
case "$out" in *"not one of the options"*)
  ok "choosing something never considered is refused" "refused" ;;
  *) bad "choosing something never considered is refused" "${out:0:44}" ;; esac

# ================= 2. silence can be declared, but not faked =============
mkrepo
dec --none "n/a" --session s >/dev/null 2>&1
check "a throwaway --none reason is refused" 2 "$?"
dec --none "this slice was a mechanical rename with no choice to make" --session s >/dev/null 2>&1
check "a real one is recorded" 0 "$?"
[ -f "$R/.claude/fluxpoint/s.nodecision" ] \
  && ok "and silence becomes a statement on disk" "marker written" \
  || bad "and silence becomes a statement on disk" "missing"

# ================= 3. no table, no silent loss ===========================
mkrepo
printf '# nothing here\n' >"$R/BARE.md"
printf '%s' "$GOOD" | dec --record --graph BARE.md >/dev/null 2>&1
check "a file with no Decisions table is reported, not written" 3 "$?"

# ================= 4. the distill check is opt-in ========================
mkrepo 0
printf 'y = 2\n' >>src/app.py
gate() { printf '{"session_id":"s","cwd":"%s"}' "$R" | bash "$GATE" 2>/dev/null; }
out="$(gate)"
case "$out" in *"nothing was written down"*)
  bad "the distill check is off by default" "blocked without being asked" ;;
  *) ok "the distill check is off by default" "silent" ;; esac

mkrepo 0
printf 'y = 2\n' >>src/app.py
out="$(printf '{"session_id":"s","cwd":"%s"}' "$R" | FPL_DISTILL=1 bash "$GATE" 2>/dev/null)"
case "$out" in *"nothing was written down"*)
  ok "armed, it blocks a green stop that recorded nothing" "blocked" ;;
  *) bad "armed, it blocks a green stop that recorded nothing" "allowed" ;; esac

mkrepo 0
printf 'y = 2\n' >>src/app.py
printf '%s' "$GOOD" | dec --record --id vault-window >/dev/null 2>&1
out="$(printf '{"session_id":"s","cwd":"%s"}' "$R" | FPL_DISTILL=1 bash "$GATE" 2>/dev/null)"
case "$out" in *"nothing was written down"*)
  bad "armed, a recorded decision clears it" "still blocked" ;;
  *) ok "armed, a recorded decision clears it" "allowed" ;; esac

mkrepo 0
printf 'y = 2\n' >>src/app.py
dec --none "a mechanical rename with no choice in it" --session s >/dev/null 2>&1
out="$(printf '{"session_id":"s","cwd":"%s"}' "$R" | FPL_DISTILL=1 bash "$GATE" 2>/dev/null)"
case "$out" in *"nothing was written down"*)
  bad "armed, declared silence clears it too" "still blocked" ;;
  *) ok "armed, declared silence clears it too" "allowed" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
