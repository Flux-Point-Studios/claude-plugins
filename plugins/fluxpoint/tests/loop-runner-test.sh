#!/usr/bin/env bash
# The outer runner's two silent failures.
#
# Both are failures of a check that reports success rather than of a check
# that breaks, which is why neither showed up in a run log.
#
#   co-change   pair-guard diffs against HEAD by default. The runner commits
#               the iteration before grading it, so `git diff HEAD` is empty
#               and every declared pair reports "no changes to compare". A
#               pair with no `parity` command is then enforced by nothing at
#               all inside the loop — and co-change-only is the normal
#               starting state, since only the consuming repo can write a
#               parity vector.
#
#   notify      `find ... | head -1` with no `|| true` dies under
#               `set -euo pipefail` on a machine with no ~/.claude/plugins,
#               on the very line that exists to file the budget-exhausted
#               notice. It exits 1, which is what the intended path would
#               have exited too, so nothing looks wrong.
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
GUARD="$PLUGIN/scripts/pair-guard.py"
ROOT="$(mktemp -d)"
pass=0; fail=0
trap 'cd /; rm -rf "$ROOT"' EXIT

ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

# A repo with one co-change-only pair: two files that must move together and
# no parity command to fall back on.
newrepo() {
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r"; cd "$ROOT/r" || exit 1
  git init -q -b main
  printf 'on-chain\n'  >predicate.txt
  printf 'off-chain\n' >builder.txt
  "$FPL_PY" - <<'PY' >.fluxpoint-pairs.json
import json
print(json.dumps([{
    "id": "predicate-builder",
    "source": ["predicate.txt"],
    "mirror": ["builder.txt"],
    "why": "the builder constructs what the predicate accepts",
}], indent=2))
PY
  git add -A
  git -c user.email=t@t -c user.name=t commit -qm base
}

# --- 1. the co-change tier, before and after the runner's commit ---------

newrepo
printf 'on-chain tightened\n' >predicate.txt      # one side only
"$FPL_PY" "$GUARD" --check >/dev/null 2>&1
check "a one-sided change is caught in the working tree" 1 $?

git add -A
git -c user.email=t@t -c user.name=t commit -qm "loop: iteration 1"
"$FPL_PY" "$GUARD" --check >/dev/null 2>&1
check "and is INVISIBLE once the runner commits it" 0 $?

base="$(git rev-parse HEAD~1)"
"$FPL_PY" "$GUARD" --check --against "$base" >/dev/null 2>&1
check "unless graded against where the iteration started" 1 $?

# --- 2. the runner passes that base, and the harness honors it -----------

grep -q 'iter_base="\$(git rev-parse HEAD 2>/dev/null || true)"' \
  "$PLUGIN/templates/loop.sh"
check "loop.sh captures the pre-iteration base" 0 $?

grep -q 'FPL_PAIR_AGAINST="\${FPL_PAIR_AGAINST:-\$iter_base}"' \
  "$PLUGIN/templates/loop.sh"
check "and passes it to the harness it grades with" 0 $?

grep -q 'pair_base="\${FPL_PAIR_AGAINST:-\${FPL_DIFF_BASE:-}}"' \
  "$PLUGIN/templates/harness.sh"
check "harness.sh falls back to the Stop gate's session base" 0 $?

# The Stop gate exports FPL_DIFF_BASE precisely so nothing is judged against
# HEAD; pair-guard was the one check that did not read it.
grep -q 'export FPL_DIFF_BASE' "$PLUGIN/scripts/dod-gate.sh"
check "and that base is the one the Stop gate already exports" 0 $?

# --- 3. the notification survives a machine with no plugins directory ----

tail_of_loop() {  # everything from the budget-exhausted notice down
  sed -n '/iteration budget exhausted/,$p' "$PLUGIN/templates/loop.sh"
}

cat >"$ROOT/notify.sh" <<EOF
set -euo pipefail
FPL_PY=$(command -v "$FPL_PY")
HOME="$ROOT/emptyhome"
$(tail_of_loop | sed 's/^exit 1$/echo REACHED-THE-END/')
EOF
mkdir -p "$ROOT/emptyhome"
out="$(bash "$ROOT/notify.sh" 2>&1)"
case "$out" in
  *REACHED-THE-END*) ok "the notify block survives an absent plugins dir" "reached" ;;
  *) bad "the notify block survives an absent plugins dir" "died early: ${out:0:40}" ;;
esac

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
