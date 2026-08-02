#!/usr/bin/env bash
# Proof-strength ratchet, against fixtures in every supported prover.
#
# The property under test is narrow and load-bearing: a checker exits 0 just
# as happily on an assumed lemma as on a proved one, so the only mechanical
# defence is that the number of assumptions may not rise. These cases prove
# the ratchet catches a rise, permits a fall, and stays out of the way in a
# repo that does no verification at all.
set -uo pipefail
PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
PG="python3 $PLUGIN/scripts/proof-guard.py --root"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

mkrepo() {
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/validators"; cd "$ROOT/r" || exit 1
  git init -q -b main
}
commit() { git add -A; git -c user.email=t@t -c user.name=t commit -qm "$1"; }

# A validator with one legitimate `expect` and no holes.
write_aiken() {
  cat >validators/vault.ak <<'EOF'
use aiken/transaction.{ScriptContext}

validator {
  fn spend(datum: Data, redeemer: Data, ctx: ScriptContext) -> Bool {
    expect Some(owner) = datum_owner(datum)
    // we assume nothing here — this is prose, not an assumption
    owner == signer(ctx)
  }
}

test spend_allows_owner() {
  True
}
EOF
}

# ================= 1. dormant in a repo with no proof files ================
mkrepo; printf 'print("hi")\n' >app.py; commit base
$PG "$ROOT/r" --check >/dev/null 2>&1
check "no proof files: check exits 0 (dormant)" 0 "$?"
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *dormant*) ok "no proof files: scan says dormant" "dormant" ;;
  *) bad "no proof files: scan says dormant" "$out" ;; esac

# ================= 2. Aiken: baseline, then a new hole ====================
mkrepo; write_aiken; commit base
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *aiken.expect*) ok "aiken: counts expect" "counted" ;;
  *) bad "aiken: counts expect" "$out" ;; esac
case "$out" in *"we assume nothing"*) bad "aiken: prose in comments ignored" "counted a comment" ;;
  *) ok "aiken: prose in comments ignored" "ignored" ;; esac

$PG "$ROOT/r" --baseline >/dev/null 2>&1
check "aiken: baseline written" 0 "$?"
[ -f .fluxpoint-proof-baseline.json ] && ok "aiken: baseline is a committed artifact" "present" \
  || bad "aiken: baseline is a committed artifact" "missing"
$PG "$ROOT/r" --check >/dev/null 2>&1
check "aiken: unchanged repo passes" 0 "$?"

# The failure this exists to catch: an agent makes a proof obligation
# disappear with `todo` instead of discharging it.
printf '\nfn hard_invariant() -> Bool {\n  todo @"prove later"\n}\n' >>validators/vault.ak
commit hole
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "aiken: a new todo fails the ratchet" 1 "$rc"
case "$err" in *"aiken.todo: 0 -> 1"*) ok "aiken: names the category and the rise" "reported" ;;
  *) bad "aiken: names the category and the rise" "$err" ;; esac
case "$err" in *"vault.ak"*) ok "aiken: names the file" "reported" ;;
  *) bad "aiken: names the file" "no file" ;; esac

# ================= 3. proving what was assumed is always allowed ==========
mkrepo; write_aiken
printf '\nfn h() -> Bool {\n  todo @"later"\n}\n' >>validators/vault.ak
commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("validators/vault.ak")
p.write_text(p.read_text().replace('todo @"later"', "True"))
PY
commit proved
out="$($PG "$ROOT/r" --check 2>&1)"; rc=$?
check "falling count passes" 0 "$rc"
case "$out" in *"stronger than the baseline"*) ok "falling count is called out" "reported" ;;
  *) bad "falling count is called out" "$out" ;; esac

# ================= 4. every other prover's escape hatch ===================
probe() { # suffix, content, expected category
  mkrepo
  mkdir -p proofs
  printf '%b' "$2" >"proofs/p$1"
  commit base
  $PG "$ROOT/r" --baseline >/dev/null 2>&1
  # Re-write with one extra hatch appended.
  printf '%b' "$2$2" >"proofs/p$1"
  commit more
  err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -q "$3"; then
    ok "$3 ratchets" "caught"
  else
    bad "$3 ratchets" "rc=$rc ${err:0:60}"
  fi
}
probe .dfy  'method m() { assume x > 0; }\n'            dafny.assume
probe .dfy  'lemma {:axiom} L()\n'                      dafny.axiom
probe .lean 'theorem t : True := by sorry\n'            lean.sorry
probe .v    'Lemma l : True.\nAdmitted.\n'              coq.admitted
probe .rs   '#[verifier::external_body]\nfn f() {}\n'   rust.external_body
probe .thy  'lemma l: "True" sorry\n'                   isabelle.sorry
probe .tla  'ASSUME NoBadThing\n'                       tla.assume

# ================= 5. verification switched off from a script =============
mkrepo; write_aiken
mkdir -p ci; printf '#!/bin/sh\naiken check\n' >ci/run.sh
commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
printf '#!/bin/sh\naiken check --skip-tests\n' >ci/run.sh
commit weakened
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "a --skip-tests flag fails the ratchet" 1 "$rc"
case "$err" in *flags.verification_off*) ok "the flag is named as an escape hatch" "reported" ;;
  *) bad "the flag is named as an escape hatch" "${err:0:60}" ;; esac

# ================= 6. untracked and build output are out of scope =========
mkrepo; write_aiken; commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
mkdir -p build; printf 'fn x() { todo }\n' >build/generated.ak   # untracked
$PG "$ROOT/r" --check >/dev/null 2>&1
check "untracked build output is ignored" 0 "$?"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
