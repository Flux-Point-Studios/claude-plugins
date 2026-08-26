#!/usr/bin/env bash
# Proof-strength ratchet, against fixtures in every supported prover.
#
# The property under test is narrow and load-bearing: a checker exits 0 just
# as happily on an assumed lemma as on a proved one, so the only mechanical
# defence is that the number of assumptions may not rise. These cases prove
# the ratchet catches a rise, permits a fall, and stays out of the way in a
# repo that does no verification at all.
set -uo pipefail

# Interpreter name differs by platform: `python3` on Linux/macOS, `python` on a
# standard Windows install. Resolve by running each candidate, because a
# name on PATH is not evidence of an interpreter.
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
# Force UTF-8 on every embedded interpreter's stdio. Without it Windows writes
# cp1252, so a header like "## Plan --" emitted with an em-dash comes back as
# 0x97 and every consumer that greps for the UTF-8 bytes silently misses it.
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
PG=""$FPL_PY" $PLUGIN/scripts/proof-guard.py --root"
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
  spend(mk_datum(1000), Void, mk_ctx(owner: True, now: 2000))
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
"$FPL_PY" - <<'PY'
import pathlib
p = pathlib.Path("validators/vault.ak")
# Discharged with an actual argument, not a substituted constant — see the
# next case for why that distinction is load-bearing.
p.write_text(p.read_text().replace(
    'todo @"later"', "list.all(tx.outputs, fn(o) { lovelace_of(o.value) >= min_ada })"))
PY
commit proved
out="$($PG "$ROOT/r" --check 2>&1)"; rc=$?
check "falling count passes" 0 "$rc"
case "$out" in *"stronger than the baseline"*) ok "falling count is called out" "reported" ;;
  *) bad "falling count is called out" "$out" ;; esac

# Swapping a `todo` for a bare `True` lowers aiken.todo to zero and looks
# like progress on every line-based count. It is the same hole with the
# label removed, so the structural category has to catch the trade.
mkrepo; write_aiken
printf '\nfn h() -> Bool {\n  todo @"later"\n}\n' >>validators/vault.ak
commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
"$FPL_PY" -c "
import pathlib
p = pathlib.Path('validators/vault.ak')
p.write_text(p.read_text().replace('todo @\"later\"', 'True'))
"
commit laundered
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "todo swapped for True does not read as progress" 1 "$rc"
case "$err" in *aiken.constant_predicate*) ok "the trade is named as a constant predicate" "reported" ;;
  *) bad "the trade is named as a constant predicate" "${err:0:70}" ;; esac

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

# ========= 6. tests that cannot fail: the ratchet's old blind spot ========
# Gutting a test leaves every escape-hatch count unchanged, so this was
# invisible until it was counted structurally. False positives matter more
# than the catch here: one would train people to ignore the ratchet.
mkrepo; write_aiken; commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *"spend_allows_owner"*) bad "a real test body is not vacuous" "flagged" ;;
  *) ok "a real test body is not vacuous" "clean" ;; esac

# Gut the negative test: body becomes a bare True. No hatch is added.
"$FPL_PY" -c "
import pathlib
p = pathlib.Path('validators/vault.ak')
t = p.read_text().replace(
    '  spend(mk_datum(1000), Void, mk_ctx(owner: True, now: 2000))', '  True')
p.write_text(t)
"
commit gutted
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "a gutted test fails the ratchet" 1 "$rc"
case "$err" in *vacuous_test*) ok "gutted test: named as vacuous" "reported" ;;
  *) bad "gutted test: named as vacuous" "${err:0:70}" ;; esac
case "$err" in *"cannot fail"*) ok "gutted test: says why it is a hole" "reported" ;;
  *) bad "gutted test: says why it is a hole" "${err:0:70}" ;; esac
case "$err" in *aiken.todo*) bad "gutted test: no hatch was added" "misattributed" ;;
  *) ok "gutted test: caught with no hatch added" "structural" ;; esac

# ========= 6b. bodies that only look trivial must not be flagged ==========
mkrepo; mkdir -p validators
cat >validators/real.ak <<'REALEOF'
test compares_two_things() {
  balance_after == balance_before + amount
}

test calls_the_validator() {
  spend(mk_datum(1000), Void, mk_ctx())
}

test multiline_and_real() {
  let result = spend(mk_datum(1), Void, mk_ctx())
  result == True
}

test negative_case() fail {
  spend(mk_datum(0), Void, mk_ctx())
}

test trailing_comment_only() {
  spend(mk_datum(2), Void, mk_ctx()) // True
}
REALEOF
commit real
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *vacuous*) bad "no false positives on five real bodies" "flagged one" ;;
  *) ok "no false positives on five real bodies" "clean" ;; esac

# Self-comparison proves nothing even though it reads like an assertion.
printf '\ntest looks_like_an_assertion() {\n  owner == owner\n}\n' >>validators/real.ak
commit selfcmp
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *looks_like_an_assertion*) ok "self-comparison counts as vacuous" "caught" ;;
  *) bad "self-comparison counts as vacuous" "missed" ;; esac

# A parameter list carries its own parentheses — a fuzzer in a property test
# and a tuple in a predicate signature. Matching them with a [^)]* class
# stopped at the wrong paren, so every parameterised property test was
# invisible to this scan: the one place a gutted body is least likely to be
# re-read, and the one an agent asked to "make the suite pass" reaches for.
printf '\ntest property_that_cannot_fail(n: Int via bounded_int(1, 99)) {\n  True\n}\n' \
  >>validators/real.ak
printf '\npub fn tuple_pred(p: (Int, Int)) -> Bool {\n  True\n}\n' >>validators/real.ak
commit params
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *property_that_cannot_fail*)
  ok "a gutted PROPERTY test is caught too" "caught" ;;
  *) bad "a gutted PROPERTY test is caught too" "missed — fuzzer parens hid it" ;; esac
case "$out" in *tuple_pred*)
  ok "a constant predicate with a tuple parameter is caught" "caught" ;;
  *) bad "a constant predicate with a tuple parameter is caught" "missed" ;; esac

# ========= 6c. the conservative boundary, pinned deliberately ==============
# Nested braces must not confuse the brace matcher, and the known misses are
# recorded here rather than assumed covered. Missing a vacuous test is a
# cost we accept; flagging a real one is not, because an ignored ratchet is
# worse than no ratchet.
mkrepo; mkdir -p validators
cat >validators/nested.ak <<'NESTEOF'
test real_when_block() {
  when parse(datum) is {
    Some(d) -> d.owner == expected
    None -> False
  }
}

test deeply_nested_but_real() {
  let outer = when a is { Some(b) -> b > 0; None -> False }
  outer
}

test single_arm_returning_true() {
  when x is {
    _ -> True
  }
}

test truly_empty() {
}
NESTEOF
commit nested
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *real_when_block*) bad "nested braces: real when-block not flagged" "flagged" ;;
  *) ok "nested braces: real when-block not flagged" "clean" ;; esac
case "$out" in *deeply_nested_but_real*) bad "nested braces: deep real body not flagged" "flagged" ;;
  *) ok "nested braces: deep real body not flagged" "clean" ;; esac
case "$out" in *truly_empty*) ok "an empty test body is caught" "caught" ;;
  *) bad "an empty test body is caught" "missed" ;; esac
# KNOWN MISS, pinned so nobody assumes it is covered: a single catch-all arm
# returning True is vacuous, but recognising it needs real expression
# analysis. The proof-auditor covers it; this ratchet does not claim to.
case "$out" in *single_arm_returning_true*)
    bad "known miss is still a miss (boundary moved)" "now flagged — update the docs" ;;
  *) ok "known miss recorded: when-with-one-True-arm" "not claimed" ;; esac

# ========= 6d. conditions discharged by fiat in a helper ==================
# The call site is the disguise: `signed_by(..) && credential_matches(..)`
# reads as a checked conjunction, and the helper is where the obligation
# went. No hatch is added, so this needs the same structural treatment as a
# gutted test.
mkrepo; mkdir -p validators
cat >validators/preds.ak <<'PREDEOF'
pub fn signed_by(tx: Transaction, key: ByteArray) -> Bool {
  list.has(tx.extra_signatories, key)
}

pub fn after_deadline(tx: Transaction, deadline: Int) -> Bool {
  when tx.validity_range.lower_bound.bound_type is {
    Finite(t) -> t >= deadline
    _ -> False
  }
}

fn fee_cap() -> Int {
  2_000_000
}

fn untyped_helper() {
  True
}
PREDEOF
commit preds
out="$($PG "$ROOT/r" --scan 2>&1)"
case "$out" in *constant_predicate*) bad "real predicates are not flagged" "flagged one" ;;
  *) ok "real predicates are not flagged" "clean" ;; esac
case "$out" in *fee_cap*) bad "a non-Bool constant is not a predicate" "flagged" ;;
  *) ok "a non-Bool constant is not a predicate" "clean" ;; esac
# KNOWN MISS, pinned: without a declared `-> Bool` this cannot be told from
# a constructor, and guessing would cost false positives.
case "$out" in *untyped_helper*) bad "known miss moved (untyped helper now flagged)" "update the docs" ;;
  *) ok "known miss recorded: helper with no declared return type" "not claimed" ;; esac

$PG "$ROOT/r" --baseline >/dev/null 2>&1
printf '\npub fn credential_matches(_o: Output, _k: ByteArray) -> Bool {\n  True\n}\n' \
  >>validators/preds.ak
commit fiat
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "a helper that always agrees fails the ratchet" 1 "$rc"
case "$err" in *"decides nothing"*) ok "constant predicate: says why it is a hole" "reported" ;;
  *) bad "constant predicate: says why it is a hole" "${err:0:70}" ;; esac
case "$err" in *aiken.expect*) bad "constant predicate: no hatch was added" "misattributed" ;;
  *) ok "constant predicate: caught with no hatch added" "structural" ;; esac

# A pre-existing constant is absorbed by the baseline: this ratchets the
# diff that introduces one, it does not indict a repo that already had one.
mkrepo; mkdir -p validators
printf 'pub fn flag() -> Bool {\n  True\n}\n' >validators/flag.ak
commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
$PG "$ROOT/r" --check >/dev/null 2>&1
check "a pre-existing constant predicate is not retroactive" 0 "$?"

# ========= 7. untracked and build output are out of scope =================
mkrepo; write_aiken; commit base
$PG "$ROOT/r" --baseline >/dev/null 2>&1
mkdir -p build; printf 'fn x() { todo }\n' >build/generated.ak   # untracked
$PG "$ROOT/r" --check >/dev/null 2>&1
check "untracked build output is ignored" 0 "$?"

# ========= 8. the baseline file is shared; a sibling's section is not arming =
# seam-guard writes a `seams` section into this same file, and init.md tells
# EVERY repo with tests to do so. Reading a file that lacks `counts` as an
# armed all-zero baseline turned the first `--no-verify` in a tracked
# workflow file into a proof-guard RED — in a JS repo with zero proof files.
mkrepo
mkdir -p .github/workflows
printf 'run: git push --no-verify\n' >.github/workflows/x.yml
printf '{"seams": {"first_party": 0, "third_party": 0}}\n' >.fluxpoint-proof-baseline.json
commit base
$PG "$ROOT/r" --check >/dev/null 2>&1
check "a seams-only baseline does not arm the hatch ratchet" 0 "$?"
# ...and in a proof repo the same file still demands a deliberate arming.
mkrepo; write_aiken
printf '{"seams": {"first_party": 0, "third_party": 0}}\n' >.fluxpoint-proof-baseline.json
commit base
err="$($PG "$ROOT/r" --check 2>&1 >/dev/null)"; rc=$?
check "with proof files it is unarmed, not silently green" 0 "$rc"
case "$err" in *"NOT armed"*) ok "and says the ratchet is not armed" "reported" ;;
  *) bad "and says the ratchet is not armed" "${err:0:60}" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
