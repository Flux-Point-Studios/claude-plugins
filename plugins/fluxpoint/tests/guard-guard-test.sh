#!/usr/bin/env bash
# The ratchet for guards that stop money, tested against the shape that
# defeated it: a proof that cannot run at all.
#
# --verify disables a guard and requires its proof to fail. Read alone, that
# rule cannot tell a guard that bites from a proof that never ran: a broken
# import, a collection error, a missing interpreter and a suite red for a
# fortnight all exit non-zero, and all used to report "the guard is real".
# The third case below is this tool's own motivating story passing itself,
# which is why the intact run is the property under test rather than a
# refinement of it.
#
# Executed, not inspected: every case builds a real repo, runs the real
# script, and reads its exit status.
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
GG="$PLUGIN/scripts/guard-guard.py"
pass=0; fail=0
ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A repo whose guard really is held down by its proof. `run` is a bare
# interpreter call rather than pytest, so the suite does not depend on a
# test runner being installed.
mkrepo() {
  r="$WORK/$1"; rm -rf "$r"; mkdir -p "$r"
  printf 'def fee_ok(amount):\n    if amount > 100:\n        return False\n    return True\n' >"$r/server.py"
  printf 'import server\n\n\ndef test_fee():\n    assert server.fee_ok(50)\n    assert not server.fee_ok(500), "guard gone"\n\n\ntest_fee()\n' >"$r/test_fee.py"
  cat >"$r/.fluxpoint-guards.json" <<JSON
{"guards": [{
  "id": "fee-bound",
  "protects": "a fee above the cap must never be accepted",
  "guard": {"file": "server.py", "contains": "if amount > 100"},
  "proof": {"file": "test_fee.py", "test": "test_fee"},
  "mutation": {"find": "if amount > 100", "replace": "if False"},
  "run": "$FPL_PY test_fee.py"
}]}
JSON
}

# ================= 1. the healthy case still verifies =====================
mkrepo good
"$FPL_PY" "$GG" --root "$WORK/good" --verify >/dev/null 2>&1
check "a guard held down by its proof verifies" 0 $?

# ================= 2. a decorative proof is caught ========================
# The proof passes even with the guard disabled: it asserts nothing about it.
mkrepo decorative
printf 'import server\n\n\ndef test_fee():\n    assert server.fee_ok(50)\n\n\ntest_fee()\n' >"$WORK/decorative/test_fee.py"
out="$("$FPL_PY" "$GG" --root "$WORK/decorative" --verify 2>&1)"; rc=$?
check "a proof that passes without its guard is caught" 1 "$rc"
case "$out" in *"PASSED with the guard disabled"*) ok "and is named as proving nothing" "reported" ;;
  *) bad "and is named as proving nothing" "${out:0:60}" ;; esac

# ================= 3. THE REGRESSION: a proof that cannot run =============
# Every one of these exits non-zero with the guard disabled, which is exactly
# what a bitten guard looks like. Only the intact run tells them apart.
mkrepo broken
printf 'import no_such_module_at_all\n\n\ndef test_fee():\n    pass\n\n\ntest_fee()\n' >"$WORK/broken/test_fee.py"
out="$("$FPL_PY" "$GG" --root "$WORK/broken" --verify 2>&1)"; rc=$?
check "a proof that cannot even import is NOT 'the guard is real'" 1 "$rc"
case "$out" in *"does not pass with the guard INTACT"*) ok "and says the proof is the problem" "reported" ;;
  *) bad "and says the proof is the problem" "${out:0:60}" ;; esac

mkrepo redsuite
printf 'import server\n\n\ndef test_fee():\n    assert False, "red for a fortnight"\n\n\ntest_fee()\n' >"$WORK/redsuite/test_fee.py"
"$FPL_PY" "$GG" --root "$WORK/redsuite" --verify >/dev/null 2>&1
check "a permanently red proof is NOT 'the guard is real'" 1 $?

mkrepo nocmd
sed -i.bak 's#"run": .*#"run": "definitely-not-a-real-binary-xyz test_fee.py"#' "$WORK/nocmd/.fluxpoint-guards.json"
"$FPL_PY" "$GG" --root "$WORK/nocmd" --verify >/dev/null 2>&1
check "a command-not-found proof is NOT 'the guard is real'" 1 $?

# ================= 4. the tree is always restored =========================
mkrepo restore
"$FPL_PY" "$GG" --root "$WORK/restore" --verify >/dev/null 2>&1
grep -q 'if amount > 100' "$WORK/restore/server.py" \
  && ok "the guard is restored after a verify" "intact" \
  || bad "the guard is restored after a verify" "LEFT DISABLED"
[ ! -e "$WORK/restore/.fluxpoint-guards-restoring" ] \
  && ok "and the in-flight sentinel is cleared" "clean" \
  || bad "and the in-flight sentinel is cleared" "SENTINEL LEFT"

# A killed run is the case `finally` does not cover. SIGTERM mid-proof must
# still put the guard back, because a repo left with `if False:` in place of
# a fee bound is the silent-guard-death this tool exists to prevent.
mkrepo killed
printf 'import time\nimport server\n\n\ndef test_fee():\n    time.sleep(5)\n\n\ntest_fee()\n' >"$WORK/killed/test_fee.py"
"$FPL_PY" "$GG" --root "$WORK/killed" --verify >/dev/null 2>&1 &
gg_pid=$!
# The sentinel only appears once the INTACT run has finished and the mutated
# window is open, so the wait has to outlast a full proof run, not half of one.
waited=0
while [ "$waited" -lt 120 ]; do
  [ -e "$WORK/killed/.fluxpoint-guards-restoring" ] && break
  sleep 0.25
  waited=$((waited+1))
done
if [ -e "$WORK/killed/.fluxpoint-guards-restoring" ]; then
  kill -TERM "$gg_pid" 2>/dev/null
  wait "$gg_pid" 2>/dev/null
  grep -q 'if amount > 100' "$WORK/killed/server.py" \
    && ok "SIGTERM mid-proof still restores the guard" "intact" \
    || bad "SIGTERM mid-proof still restores the guard" "LEFT DISABLED"
else
  kill -TERM "$gg_pid" 2>/dev/null; wait "$gg_pid" 2>/dev/null
  bad "SIGTERM mid-proof still restores the guard" "window never opened"
fi

# ================= 5. --check and the removal floor =======================
mkrepo floor
"$FPL_PY" "$GG" --root "$WORK/floor" --check >/dev/null 2>&1
check "a registered guard passes --check" 0 $?
sed -i.bak 's/if amount > 100/if True/' "$WORK/floor/server.py"
"$FPL_PY" "$GG" --root "$WORK/floor" --check >/dev/null 2>&1
check "a deleted guard fails --check" 1 $?

mkrepo removed
"$FPL_PY" "$GG" --root "$WORK/removed" --baseline >/dev/null 2>&1
printf '{"guards": []}\n' >"$WORK/removed/.fluxpoint-guards.json"
"$FPL_PY" "$GG" --root "$WORK/removed" --check >/dev/null 2>&1
check "dropping a guard from the manifest fails the floor" 1 $?

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
