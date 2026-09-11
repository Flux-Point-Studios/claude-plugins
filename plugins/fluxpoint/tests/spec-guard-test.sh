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

# ================= 5. languages it does not parse say so ==================
mkrepo; write_aiken
printf 'postulate foo : Set\n' >src/thm.agda
commit; sg --baseline >/dev/null
out="$(sg --check)"
case "$out" in *"NOT covered"*Agda*) ok "a tracked Agda file is named as uncovered" "named" ;;
  *) bad "a tracked Agda file is named as uncovered" "${out:0:50}" ;; esac
check "and does not fail the gate on its own" 0 "$(rc_of --check)"

# ================= 6. Lean, Coq, Isabelle, TLA+ and Kani are covered =====
# Each language gets the same four probes the Aiken and Dafny sections use:
# the statement is found, a weakened statement is CHANGED, a deleted one is
# REMOVED, and a comment or reformat stays green. The fixtures carry a
# commented-out declaration so a scanner reading comments would over-count.
write_lean() {
  mkdir -p src; cat >src/Foo.lean <<'EOF'
/-- The doc comment mentions theorem inside_doc : False, which is prose. -/
theorem add_zero' (n : Nat) : n + 0 = n := by
  simp
-- theorem commented_out : False := sorry
@[simp] lemma two_mul' (n : Nat) : 2 * n = n + n :=
  by omega
theorem cases_ex : ∀ n : Nat, n = n
  | 0 => rfl
  | n + 1 => rfl
def notAnObligation : Nat := 3
EOF
}
write_coq() {
  mkdir -p src; cat >src/Bar.v <<'EOF'
(* Lemma inside_comment : False. *)
Theorem plus_O_n : forall n : nat, 0 + n = n.
Proof. intros n. reflexivity. Qed.
Lemma with_dot_in_name : Nat.add 1 1 = 2.
Proof. reflexivity. Qed.
Definition not_one := 1.
EOF
}
write_isabelle() {
  mkdir -p src; cat >src/Baz.thy <<'EOF'
theory Baz imports Main begin
lemma add_comm': "a + b = (b::nat) + a" by simp
theorem long_one [simp]:
  assumes "x > (0::nat)"
  shows "x + 1 > 1"
  using assms by simp
(* lemma commented: "False" *)
definition foo :: nat where "foo = 1"
lemma in_cart: ‹foo = 1› unfolding foo_def by simp
end
EOF
}
write_tla() {
  mkdir -p specs; cat >specs/Spec.tla <<'EOF'
---- MODULE Spec ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Inv ==
  /\ x >= 0
  /\ x < 100
(* THEOREM Commented == FALSE *)
THEOREM Safety == Spec => []Inv
PROOF OMITTED
Spec == Init /\ [][Next]_x
====
EOF
  cat >specs/Spec.cfg <<'EOF'
SPECIFICATION Spec
INVARIANT Inv
\* PROPERTY Nope
EOF
  printf '[metadata]\nname = x\n' >setup.cfg
}
write_kani() {
  mkdir -p src; cat >src/lib.rs <<'EOF'
// #[kani::proof] fn commented() {}
#[cfg(kani)]
mod verification {
    #[kani::proof]
    #[kani::unwind(3)]
    fn check_add() {
        let a: u8 = kani::any();
        assert!(a.wrapping_add(0) == a);
    }
}
#[kani::requires(x > 0)]
#[kani::ensures(|r| *r > x)]
pub fn bump(x: u32) -> u32 { x + 1 }
fn plain() {}
EOF
}
poly_state() { mkrepo; write_lean; write_coq; write_isabelle; write_tla; write_kani; commit; }

poly_state
out="$(sg --scan)"
for want in "lean:src/Foo.lean:add_zero'" "lean:src/Foo.lean:cases_ex" \
            "coq:src/Bar.v:plus_O_n" "coq:src/Bar.v:with_dot_in_name" \
            "isabelle:src/Baz.thy:long_one" "isabelle:src/Baz.thy:in_cart" \
            "tla:specs/Spec.tla:Safety" "tla:specs/Spec.cfg:Inv" \
            "kani:src/lib.rs:check_add" "kani:src/lib.rs:bump"; do
  case "$out" in *"$want"*) ok "scan finds $want" "found" ;; *) bad "scan finds $want" "missing" ;; esac
done
for absent in commented_out inside_comment "inside_doc" "Commented" "commented()" \
              "Nope" "notAnObligation" "not_one" "plain" "setup.cfg"; do
  case "$out" in *"$absent"*) bad "and does not read $absent" "counted" ;;
    *) ok "and does not read $absent" "ignored" ;; esac
done
case "$out" in *"INVARIANT Inv == /\\ x >= 0 /\\ x < 100"*)
  ok "a TLC invariant is bound to the operator it checks" "bound" ;;
  *) bad "a TLC invariant is bound to the operator it checks" "unbound" ;; esac
case "$out" in *"NOT COVERED"*) bad "nothing is reported uncovered" "reported" ;;
  *) ok "nothing is reported uncovered" "clean" ;; esac
sg --baseline >/dev/null
check "armed over five languages: green" 0 "$(rc_of --check)"

# A Dafny declaration carrying attributes between the keyword and the name
# (`lemma {:axiom} Helper`, `method {:verify false} Skipped`) is its own
# obligation; a scanner that missed the attributes folded their clauses into
# the previous method, which a real file showed.
mkrepo; mkdir -p src; cat >src/vault.dfy <<'EOF'
module Vault {
  method Withdraw(bal: int, amt: int) returns (out: int)
    requires amt > 0
    ensures out == bal - amt
    ensures out >= 0
  {
    out := bal - amt;
  }

  lemma {:axiom} Helper(x: int)
    ensures x * x >= 0

  method {:verify false} Skipped(n: int) returns (r: int)
    ensures r > n
  {
    r := n;
  }

  method UsesAssume(n: int) returns (r: int)
    ensures r > 0
  {
    assume n > 0;
    r := n;
  }
}
EOF
commit
out="$(sg --scan)"
case "$out" in *"dafny:src/vault.dfy:Helper"*"dafny:src/vault.dfy:Skipped"*"dafny:src/vault.dfy:UsesAssume"*)
  ok "Dafny declarations carrying attributes are their own obligations" "found" ;;
  *) bad "Dafny declarations carrying attributes are their own obligations" "${out:0:60}" ;; esac
case "$out" in *"Withdraw"*"x * x"*) bad "and their clauses do not fold into the previous method" "folded" ;;
  *) ok "and their clauses do not fold into the previous method" "separate" ;; esac

weaken() {  # name, file, sed-expr, expected-word
  poly_state; sg --baseline >/dev/null
  sed -i "$3" "$2"
  check "$1 is red" 1 "$(rc_of --check)"
  case "$(sg --check 2>&1)" in *"$4"*) ok "  and reported as $4" "named" ;;
    *) bad "  and reported as $4" "not named" ;; esac
}
weaken "Lean: a weakened theorem" src/Foo.lean 's/n + 0 = n/n + 0 = n + 0/' CHANGED
weaken "Lean: a deleted theorem" src/Foo.lean '/two_mul/,/omega/d' REMOVED
weaken "Coq: a weakened lemma" src/Bar.v 's/0 + n = n\./0 + n = 0 + n./' CHANGED
weaken "Coq: a deleted theorem" src/Bar.v '/plus_O_n/,/Qed/d' REMOVED
weaken "Isabelle: a weakened lemma" src/Baz.thy 's/shows "x + 1 > 1"/shows "x + 1 > 0"/' CHANGED
weaken "Isabelle: a deleted lemma" src/Baz.thy '/in_cart/d' REMOVED
weaken "TLA+: a weakened invariant body" specs/Spec.tla 's/x < 100/x < 1000/' CHANGED
weaken "TLA+: an INVARIANT dropped from the .cfg" specs/Spec.cfg '/INVARIANT/d' REMOVED
weaken "TLA+: a weakened THEOREM" specs/Spec.tla 's/Spec => \[\]Inv/Spec => Inv/' CHANGED
weaken "Kani: a loosened unwind bound" src/lib.rs 's/unwind(3)/unwind(1)/' CHANGED
weaken "Kani: a dropped contract" src/lib.rs '/kani::ensures/d' CHANGED
weaken "Kani: a deleted harness" src/lib.rs '/#\[kani::proof\]/,/^    }/d' REMOVED

poly_state; sg --baseline >/dev/null
sed -i 's/^theorem add_zero. (n : Nat) : n + 0 = n := by/theorem add_zero'"'"'  (n : Nat)  :  n + 0 = n  := by/' src/Foo.lean
sed -i 's/^-- theorem commented_out.*/-- a different comment/' src/Foo.lean
sed -i 's/(\* Lemma inside_comment : False. \*)/(* nothing *)/' src/Bar.v
sed -i 's/(\* THEOREM Commented == FALSE \*)/(* still a comment *)/' specs/Spec.tla
sed -i 's|^// #\[kani::proof\] fn commented() {}|// another comment|' src/lib.rs
check "reformatting and comment edits stay green across languages" 0 "$(rc_of --check)"
printf '\ntheorem extra (n : Nat) : n = n := rfl\n' >>src/Foo.lean
check "adding a Lean theorem is allowed" 0 "$(rc_of --check)"

# ================= 7. a DoD line that cites an obligation is a claim ======
base_state
cat >WORK.md <<'EOF'
# work

## Definition of Done
- [x] withdraw never overdraws — proof: dafny:src/vault.dfy:Withdraw
- [ ] settle on preview — proof: tx hash
- [ ] later — proof: dafny:src/vault.dfy:NotYetWritten

## Decisions
EOF
check "a checked box citing a live obligation is green" 0 "$(rc_of --check)"
check "an unchecked box citing a missing one is not a claim" 0 "$(rc_of --check)"
sed -i 's/- \[ \] later/- [x] later/' WORK.md
check "checking a box whose obligation does not exist is red" 1 "$(rc_of --check)"
case "$(sg --check 2>&1)" in *NotYetWritten*"DoD CLAIM"*) ok "and names the missing id" "named" ;;
  *) bad "and names the missing id" "silent" ;; esac
sed -i 's/- \[x\] later/- [ ] later/' WORK.md
sed -i 's/  ensures out >= 0\n//' src/vault.dfy
"$FPL_PY" - <<'PY'
p = "src/vault.dfy"
open(p, "w").write(open(p).read().replace("  ensures out >= 0\n", ""))
PY
check "a checked box over a weakened obligation is red (CHANGED)" 1 "$(rc_of --check)"

# ================= 8. the attack taxonomy: unspecified is red =============
TAX="$PLUGIN/templates/attack-taxonomy.json"
n_classes="$("$FPL_PY" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["classes"]))' "$TAX")"
check "the shipped taxonomy names eight classes" 8 "$n_classes"

base_state; cp "$TAX" .fluxpoint-attacks.json
check "an Aiken repo with the manifest and no attack tests is red" 1 "$(rc_of --check)"
out="$(sg --check 2>&1)"
case "$out" in *"UNSPECIFIED"*attack_double_satisfaction*"One output cannot pay"*)
  ok "each class is named with the property to state" "named" ;;
  *) bad "each class is named with the property to state" "${out:0:60}" ;; esac
n_red="$(printf '%s' "$out" | grep -c 'attack:attack_')"
check "every class is its own finding" 8 "$n_red"
case "$(sg --scan)" in *"attack class attack_datum_hijack: UNSPECIFIED"*)
  ok "--scan reports the taxonomy state" "reported" ;;
  *) bad "--scan reports the taxonomy state" "silent" ;; esac

write_attacks() {  # every class as a property test over a generator
  for cls in $("$FPL_PY" -c 'import json,sys; print(" ".join(c["id"] for c in json.load(open(sys.argv[1]))["classes"]))' "$TAX"); do
    printf '\ntest %s(n: Int via bounded_int(1, 99)) {\n  !spend(mk_datum(n), Void, mk_ctx(attack: True))\n}\n' "$cls" >>validators/vault.ak
  done
}
write_attacks
check "with a property test per class it is green" 0 "$(rc_of --check)"
sg --baseline >/dev/null
sed -i 's/test attack_foreign_utxo(n: Int via bounded_int(1, 99))/test attack_foreign_utxo()/' validators/vault.ak
case "$(sg --check 2>&1)" in *"attack_foreign_utxo is a unit test"*) ok "a unit test under a class name is noted" "noted" ;;
  *) bad "a unit test under a class name is noted" "silent" ;; esac

base_state; cp "$TAX" .fluxpoint-attacks.json; write_attacks
sed -i '/test attack_unbounded_validity/,/^}/d' validators/vault.ak
"$FPL_PY" - <<'PY'
import json
p = ".fluxpoint-attacks.json"; d = json.load(open(p))
d["waived"] = {"attack_unbounded_validity": "n/a"}
json.dump(d, open(p, "w"), indent=2)
PY
check "a waiver without a real reason is red" 1 "$(rc_of --check)"
case "$(sg --check 2>&1)" in *"WAIVED WITHOUT A REASON"*) ok "and says so" "said" ;; *) bad "and says so" "silent" ;; esac
"$FPL_PY" - <<'PY'
import json
p = ".fluxpoint-attacks.json"; d = json.load(open(p))
d["waived"] = {"attack_unbounded_validity": "this validator has no time-dependent logic at all; the range is never read"}
json.dump(d, open(p, "w"), indent=2)
PY
check "a waiver with a reason is green" 0 "$(rc_of --check)"
case "$(sg --check)" in *"attack_unbounded_validity waived"*) ok "and the waiver is echoed" "echoed" ;;
  *) bad "and the waiver is echoed" "silent" ;; esac

printf '{not json' >.fluxpoint-attacks.json
check "a malformed manifest is red, never a pass" 1 "$(rc_of --check)"
mkrepo; printf 'print(1)\n' >app.py; cp "$TAX" .fluxpoint-attacks.json; commit
check "the manifest in a repo with no Aiken is green with a note" 0 "$(rc_of --check)"
case "$(sg --check)" in *"gates nothing"*) ok "  and the note says it gates nothing" "said" ;;
  *) bad "  and the note says it gates nothing" "silent" ;; esac

# ================= 9. --axioms: the prover's own assumption audit =========
# The provers are not installed where this suite runs, so each toolchain is
# a shim on PATH that prints the listing its real counterpart printed for the
# same probe — captured 2026-09-11 from Lean 4.15.0 (`lake env lean` on a
# `#print axioms` file), Coq 8.18.0 (`coqc` on a `Print Assumptions` file,
# module path from _CoqProject) and Dafny 4.9.1 (`dafny audit
# --report-format txt`), and pinned against the same three parsers end to
# end on those installs.
mkshims() {
  mkdir -p "$ROOT/bin"
  cat >"$ROOT/bin/lake" <<'EOF'
#!/usr/bin/env bash
# lake env lean <probe>
[ "$1" = env ] && [ "$2" = lean ] || { echo "shim: unexpected $*" >&2; exit 2; }
name="$(sed -n 's/^#print axioms \(.*\)$/\1/p' "$3")"
if [ -n "${SHIM_LEAN_GARBAGE:-}" ]; then echo "error: unknown package 'Foo'"; exit 1; fi
if [ -z "${SHIM_LEAN_AXIOMS:-}" ]; then echo "'$name' does not depend on any axioms"
else echo "'$name' depends on axioms: [${SHIM_LEAN_AXIOMS}]"; fi
EOF
  cat >"$ROOT/bin/coqc" <<'EOF'
#!/usr/bin/env bash
for last; do :; done
name="$(sed -n 's/^Print Assumptions \(.*\)\.$/\1/p' "$last")"
[ -n "$name" ] || { echo "shim: no Print Assumptions in $last" >&2; exit 2; }
if [ -z "${SHIM_COQ_AXIOMS:-}" ]; then echo "Closed under the global context"
else printf 'Axioms:\n'; for a in $SHIM_COQ_AXIOMS; do printf '%s : forall P : Prop, P\n' "$a"; done; fi
EOF
  cat >"$ROOT/bin/dafny" <<'EOF'
#!/usr/bin/env bash
# dafny audit --report-format txt <files>, as Dafny 4.9.1 prints it: the
# ordinary warnings first, one `file(l,c):Name: message` row per finding,
# then the completion lines. 4.9.1 rejects `text` for the format.
[ "$1" = audit ] || { echo "shim: unexpected $*" >&2; exit 2; }
if [ "$2" != --report-format ] || [ "$3" != txt ]; then
  printf "Argument '%s' not recognized. Must be one of:\n\t'html'\n\t'txt'\n" "$3"; exit 1
fi
printf 'src/vault.dfy(22,4): Warning: assume statement has no {:axiom} annotation\n'
printf '%b' "${SHIM_DAFNY_AUDIT:-}"
printf 'Dafny auditor completed with %s findings\n\nDafny program verifier did not attempt verification\n' \
  "$(printf '%b' "${SHIM_DAFNY_AUDIT:-}" | grep -c .)"
EOF
  chmod +x "$ROOT/bin/"*
}
ax_state() {
  poly_state
  cat >src/Bar.v <<'EOF'
Theorem plus_O_n : forall n : nat, 0 + n = n.
Proof. intros n. reflexivity. Qed.
EOF
  printf -- '-R src Vault\n' >_CoqProject
  write_dafny; commit; sg --baseline >/dev/null
}
mkshims
export SHIM_LEAN_AXIOMS="propext, Classical.choice"
export SHIM_COQ_AXIOMS="classic"
export SHIM_DAFNY_AUDIT='src/vault.dfy(10,17):Helper: Declaration has explicit `{:axiom}` attribute. Possible mitigation: Provide a proof or test.\n'
HEADS="--headline lean:src/Foo.lean:add_zero' --headline coq:src/Bar.v:plus_O_n --headline dafny:*"
with_shims() { PATH="$ROOT/bin:$PATH" "$FPL_PY" "$SG" --root "$ROOT/r" "$@"; }
rc_shims() { with_shims "$@" >/dev/null 2>&1; echo $?; }
# A PATH with git and the interpreter and nothing else, so the "toolchain
# absent" case holds on a machine where a real prover happens to be installed.
mkdir -p "$ROOT/nobin"
for _t in git "$FPL_PY" sh bash env sed grep; do
  _p="$(command -v "$_t")"; [ -n "$_p" ] && ln -sf "$_p" "$ROOT/nobin/$(basename "$_t")"
done
without_tools() { PATH="$ROOT/nobin" "$FPL_PY" "$SG" --root "$ROOT/r" "$@"; }
rc_none() { without_tools "$@" >/dev/null 2>&1; echo $?; }

ax_state
check "a headline that is not an obligation is refused" 1 "$(rc_shims --baseline --axioms --headline lean:src/Foo.lean:nope)"
check "recording the assumption sets" 0 "$(rc_shims --baseline --axioms $HEADS)"
rec="$("$FPL_PY" -c 'import json; d=json.load(open(".fluxpoint-proof-baseline.json")); print(",".join(d["axioms"]["recorded"]["lean:src/Foo.lean:add_zero'"'"'"]))')"
check "  Lean's listing is recorded verbatim" "Classical.choice,propext" "$rec"
rec="$("$FPL_PY" -c 'import json; d=json.load(open(".fluxpoint-proof-baseline.json")); print(len(d["axioms"]["recorded"]["dafny:*"]))')"
check "  dafny audit finding rows are recorded, warnings are not" 1 "$rec"
check "same assumptions: green" 0 "$(rc_shims --check)"
check "a new axiom under a headline is red" 1 "$(SHIM_LEAN_AXIOMS='propext, Classical.choice, sorryAx' rc_shims --check)"
case "$(SHIM_LEAN_AXIOMS='propext, Classical.choice, sorryAx' with_shims --check 2>&1)" in
  *"NEW AXIOM"*sorryAx*) ok "  and names it" "named" ;; *) bad "  and names it" "silent" ;; esac
check "fewer axioms is green" 0 "$(SHIM_LEAN_AXIOMS=propext rc_shims --check)"
check "a new dafny audit row is red" 1 "$(SHIM_DAFNY_AUDIT='src/vault.dfy(10,17):Helper: Declaration has explicit `{:axiom}` attribute. Possible mitigation: Provide a proof or test.\nsrc/vault.dfy(13,25):Skipped: Declaration has `{:verify false}` attribute. Possible mitigation: Remove `{:verify false}` attribute and prove if possible.\n' rc_shims --check)"
check "the same row at another line is not new" 0 "$(SHIM_DAFNY_AUDIT='src/vault.dfy(40,17):Helper: Declaration has explicit `{:axiom}` attribute. Possible mitigation: Provide a proof or test.\n' rc_shims --check)"
check "a new Coq assumption is red" 1 "$(SHIM_COQ_AXIOMS='classic functional_extensionality' rc_shims --check)"
check "--check --axioms runs only the audit" 0 "$(rc_shims --check --axioms)"
sed -i 's/x + 1 > 1/x + 1 > 0/' src/Baz.thy
check "  (a weakened statement elsewhere does not enter it)" 0 "$(rc_shims --check --axioms)"
check "  while the full --check still sees it" 1 "$(rc_shims --check)"
ax_state; with_shims --baseline --axioms $HEADS >/dev/null
sg --baseline >/dev/null
has_ax="$("$FPL_PY" -c 'import json; print("yes" if "axioms" in json.load(open(".fluxpoint-proof-baseline.json")) else "no")')"
check "re-recording statements preserves the axiom section" yes "$has_ax"
check "without the toolchains --check is green and says NOT RUN" 0 "$(rc_none --check)"
case "$(without_tools --check)" in *"AXIOM AUDIT NOT RUN"*"lake is not on PATH"*) ok "  naming the missing tool" "named" ;;
  *) bad "  naming the missing tool" "silent" ;; esac
check "a listing that cannot be read is red, never clean" 1 "$(SHIM_LEAN_GARBAGE=1 rc_shims --check)"
case "$(SHIM_LEAN_GARBAGE=1 with_shims --check 2>&1)" in *"AXIOM AUDIT UNREADABLE"*) ok "  and says UNREADABLE" "said" ;;
  *) bad "  and says UNREADABLE" "silent" ;; esac
rm _CoqProject
case "$(with_shims --check 2>&1)" in *"NOT RUN"*"_CoqProject"*) ok "Coq without _CoqProject is NOT RUN, naming why" "named" ;;
  *) bad "Coq without _CoqProject is NOT RUN, naming why" "silent" ;; esac
case "$(with_shims --scan --axioms 2>&1)" in *"add_zero'"*"Classical.choice"*) ok "--scan --axioms prints each headline's set" "printed" ;;
  *) bad "--scan --axioms prints each headline's set" "silent" ;; esac
unset SHIM_LEAN_AXIOMS SHIM_COQ_AXIOMS SHIM_DAFNY_AUDIT

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
