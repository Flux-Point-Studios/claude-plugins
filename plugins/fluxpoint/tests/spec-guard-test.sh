#!/usr/bin/env bash
# Statement ratchet: what is being proved may not quietly get weaker.
#
# proof-guard covers the proof body; every case here is a weakening it
# cannot see — a conjunct dropped from an `ensures`, a property test
# deleted, a `fail` test flipped to a normal one, a generator narrowed.
# Each keeps the hatch counts flat and every checker exiting 0, which is
# exactly why this ratchet exists.
#
# The other half of the property matters as much: the cases that must NOT
# be red. Reformatting, reordering clauses, moving a file, adding an
# obligation, and a change justified by a Decisions row all stay green — a
# ratchet that cries wolf gets re-baselined blind, which is worse than not
# having one.
set -uo pipefail

if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
SG="$PLUGIN/scripts/spec-guard.py"
PG="$PLUGIN/scripts/proof-guard.py"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()  { printf 'PASS  %-56s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-56s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

sg() { "$FPL_PY" "$SG" --root "$ROOT/r" "$@"; }
rc_of() { sg "$@" >/dev/null 2>&1; echo $?; }

mkrepo() {
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/validators" "$ROOT/r/src"
  cd "$ROOT/r" || exit 1
  git init -q -b main
}
commit() { git add -A; git -c user.email=t@t -c user.name=t commit -qm "${1:-x}"; }

write_aiken() {
  cat >validators/vault.ak <<'EOF'
validator {
  fn spend(datum: Data, redeemer: Data, ctx: ScriptContext) -> Bool {
    owner_signed(datum, ctx) && no_double_satisfaction(ctx)
  }
}

test spend_allows_owner() {
  spend(mk_datum(1000), Void, mk_ctx(owner: True))
}

test double_satisfaction_is_rejected(n: Int via bounded_int(1, 99)) {
  !spend(mk_datum(n), Void, mk_ctx(two_scripts: True))
}

test spend_rejects_stranger() fail {
  spend(mk_datum(1000), Void, mk_ctx(owner: False))
}
EOF
}

write_dafny() {
  cat >src/vault.dfy <<'EOF'
method Withdraw(bal: int, amt: int) returns (out: int)
  requires amt > 0
  requires amt <= bal
  ensures out == bal - amt
  ensures out >= 0
{
  out := bal - amt;
}
EOF
}

# ================= 1. dormant, then armed =================================
mkrepo; printf 'print("hi")\n' >app.py; commit
check "no proof files: --check is silent and green" 0 "$(rc_of --check)"

mkrepo; write_aiken; write_dafny; commit
out="$(sg --check)"
case "$out" in *"NOT armed"*) ok "obligations present but unarmed says so" "said" ;;
  *) bad "obligations present but unarmed says so" "silent: ${out:0:40}" ;; esac
check "and does not block bootstrapping" 0 "$(rc_of --check)"

out="$(sg --scan)"
n="$(printf '%s' "$out" | grep -c '^  [ad]')"
check "scan finds every Aiken test and the Dafny method" 4 "$n"
case "$out" in *"double_satisfaction_is_rejected"*)
  ok "the property test is an obligation" "found" ;;
  *) bad "the property test is an obligation" "missing" ;; esac

sg --baseline >/dev/null
check "armed: a fresh baseline is green" 0 "$(rc_of --check)"

# ================= 2. the weakenings a hatch count cannot see =============
base_state() { mkrepo; write_aiken; write_dafny; commit; sg --baseline >/dev/null; }

# A conjunct dropped from a Dafny postcondition.
base_state
"$FPL_PY" - <<'PY'
import re
p = "src/vault.dfy"
s = open(p).read().replace("  ensures out >= 0\n", "")
open(p, "w").write(s)
PY
check "a dropped ensures conjunct is red" 1 "$(rc_of --check)"
case "$(sg --check 2>&1)" in *CHANGED*) ok "and is reported as CHANGED" "named" ;;
  *) bad "and is reported as CHANGED" "not named" ;; esac

# A widened precondition — the hard case is now out of scope.
base_state
sed -i 's/requires amt <= bal/requires amt <= bal + 1/' src/vault.dfy
check "a widened requires is red" 1 "$(rc_of --check)"

# A deleted property test. Nothing else in the system sees this.
base_state
"$FPL_PY" - <<'PY'
p = "validators/vault.ak"
s = open(p).read()
i = s.index("test double_satisfaction_is_rejected")
j = s.index("test spend_rejects_stranger")
open(p, "w").write(s[:i] + s[j:])
PY
check "a deleted property test is red" 1 "$(rc_of --check)"
case "$(sg --check 2>&1)" in *REMOVED*) ok "and is reported as REMOVED" "named" ;;
  *) bad "and is reported as REMOVED" "not named" ;; esac

# A renamed test: the obligation nothing points at any more.
base_state
sed -i 's/test double_satisfaction_is_rejected/test ds_check/' validators/vault.ak
check "a renamed obligation is red" 1 "$(rc_of --check)"

# A negative test flipped positive asserts the opposite of what it did.
base_state
sed -i 's/test spend_rejects_stranger() fail {/test spend_rejects_stranger() {/' \
  validators/vault.ak
check "flipping a fail test is red" 1 "$(rc_of --check)"

# A narrowed generator checks less ground under the same name.
base_state
sed -i 's/bounded_int(1, 99)/bounded_int(1, 2)/' validators/vault.ak
check "a narrowed fuzzer is red" 1 "$(rc_of --check)"

# The whole specification stripped off a method.
base_state
"$FPL_PY" - <<'PY'
p = "src/vault.dfy"
s = "\n".join(l for l in open(p).read().splitlines()
              if not l.strip().startswith(("requires", "ensures")))
open(p, "w").write(s + "\n")
PY
check "a method that lost its entire spec is red" 1 "$(rc_of --check)"

# ================= 3. what must stay green ================================
base_state
sed -i 's/  ensures out == bal - amt/  ensures  out  ==  bal - amt/' src/vault.dfy
check "reformatting is not weakening" 0 "$(rc_of --check)"

base_state
"$FPL_PY" - <<'PY'
p = "src/vault.dfy"
s = open(p).read()
s = s.replace("  requires amt > 0\n  requires amt <= bal\n",
              "  requires amt <= bal\n  requires amt > 0\n")
open(p, "w").write(s)
PY
check "reordering clauses is not weakening" 0 "$(rc_of --check)"

base_state
printf '\ntest another_property(n: Int via bounded_int(1, 9)) {\n  True == True\n}\n' \
  >>validators/vault.ak
check "adding an obligation is always allowed" 0 "$(rc_of --check)"
case "$(sg --check)" in *"1 obligation(s) added"*) ok "and the addition is reported" "said" ;;
  *) bad "and the addition is reported" "silent" ;; esac

# Moving a file relocates every obligation in it without changing any.
base_state
mkdir -p validators/nested && git mv validators/vault.ak validators/nested/vault.ak
check "moving a file is not weakening" 0 "$(rc_of --check)"
case "$(sg --check)" in *moved*) ok "the move is reported as a move" "said" ;;
  *) bad "the move is reported as a move" "silent" ;; esac

# The in-band justification: a Decisions row naming the obligation id.
base_state
sed -i 's/requires amt <= bal/requires amt <= bal + 1/' src/vault.dfy
cat >WORK.md <<'EOF'
# work

## Decisions

| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |
|---|---|---|---|---|---|
| 2026-08-08 01:00 | widen-withdraw-precondition | allow the boundary case | YES | none | dafny:src/vault.dfy:Withdraw is intentionally widened; the caller now enforces the bound and the old clause double-counted it |
EOF
check "a weakening a Decisions row names is allowed" 0 "$(rc_of --check)"
case "$(sg --check)" in *"a Decisions row names it"*) ok "and the justification is echoed" "echoed" ;;
  *) bad "and the justification is echoed" "silent" ;; esac

# A Decisions row about something else must not launder it.
sed -i 's/dafny:src\/vault.dfy:Withdraw/some other unrelated decision/' WORK.md
check "an unrelated Decisions row does not launder it" 1 "$(rc_of --check)"

# ================= 4. the two ratchets share one file =====================
base_state
"$FPL_PY" "$PG" --root "$ROOT/r" --baseline >/dev/null
has_spec() { "$FPL_PY" - "$ROOT/r/.fluxpoint-proof-baseline.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("yes" if d.get("spec", {}).get("obligations") else "no")
PY
}
check "re-arming proof-guard preserves the spec section" "yes" "$(has_spec)"
"$FPL_PY" "$SG" --root "$ROOT/r" --baseline >/dev/null
has_counts() { "$FPL_PY" - "$ROOT/r/.fluxpoint-proof-baseline.json" <<'PY'
import json, sys
print("yes" if "counts" in json.load(open(sys.argv[1])) else "no")
PY
}
check "and re-arming spec-guard preserves the counts" "yes" "$(has_counts)"
check "both ratchets still green afterwards" 0 "$(rc_of --check)"

# ================= 5. uncovered languages say so ==========================
mkrepo; write_aiken
printf 'theorem foo : 1 = 1 := rfl\n' >src/thm.lean
printf 'ASSUME NoCrash == TRUE\n' >src/spec.tla
commit; sg --baseline >/dev/null
out="$(sg --check)"
case "$out" in *"NOT covered"*Lean*) ok "a tracked Lean file is named as uncovered" "named" ;;
  *) bad "a tracked Lean file is named as uncovered" "${out:0:50}" ;; esac
case "$out" in *"TLA+"*) ok "so is TLA+" "named" ;;
  *) bad "so is TLA+" "not named" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
