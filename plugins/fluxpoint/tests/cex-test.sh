#!/usr/bin/env bash
# Counterexample ledger: a prover's failing input outlives the run.
#
# Fixtures are shaped exactly like real `aiken check` JSON (v1.1.23): no
# `kind` discriminator — the published --show-json-schema advertises one and
# the binary never writes it — and the per-module array is `tests`, not the
# schema's `test`. A parser written from that schema breaks on day one, so
# these fixtures pin the real shape.
#
# The suite is weighted toward the two ways this feature fails badly: a pin
# that is accepted while proving nothing (gaming), and a red that is not a
# weakening (a guard people learn to ignore is worse than no guard).
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
CEX="$PLUGIN/scripts/cex.py"
INBOX="$PLUGIN/scripts/inbox.py"
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

ok()  { printf 'PASS  %-58s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-58s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

cex() { "$FPL_PY" "$CEX" --root "$R" "$@"; }
rc_of() { cex "$@" >/dev/null 2>&1; echo $?; }
rows() { [ -f "$R/.fluxpoint-cex.jsonl" ] && grep -c . "$R/.fluxpoint-cex.jsonl" || echo 0; }
cexid() { # first cexId in the store
  "$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY'
import json, sys
print(json.loads(open(sys.argv[1]).readline())["cexId"])
PY
}

mkrepo() {
  rm -rf "$R"; mkdir -p "$R/lib" "$R/validators"; cd "$R" || exit 1
  git init -q -b main
  printf 'name = "x/y"\n' >aiken.toml
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
}
commit() { git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm "${1:-x}"; }

# Real v1.1.23 output shape. `prop_shape` fails with a record counterexample,
# `add_one_3` is a failing unit test, `expected_to_fail` is a PASS that
# carries a counterexample because it is `fail`-annotated.
fixture() {
  cat <<'EOF'
{
  "seed": 42,
  "summary": { "total": 4, "passed": 2, "failed": 2, "kind": { "unit": 2, "property": 2 } },
  "modules": [
    { "name": "x/y/vault",
      "summary": { "total": 4, "passed": 2, "failed": 2, "kind": { "unit": 2, "property": 2 } },
      "tests": [
        { "title": "prop_shape", "status": "fail", "on_failure": "fail_immediately",
          "iterations": 3, "counterexample": "Circle { radius: 200 }" },
        { "title": "add_one_3", "status": "fail", "on_failure": "fail_immediately",
          "execution_units": { "mem": 200, "cpu": 16100 },
          "assertion": "× expected\n│ 2\n× to equal\n│ 1" },
        { "title": "prop_ok", "status": "pass", "on_failure": "fail_immediately",
          "iterations": 100, "counterexample": null },
        { "title": "expected_to_fail", "status": "pass", "on_failure": "succeed_eventually",
          "iterations": 1, "counterexample": "Square { side: 9 }" }
      ] }
  ]
}
EOF
}

# ================= 1. dormant, and the store is a TRACKED artifact ========
mkrepo
check "nothing recorded: --check is silent and green" 0 "$(rc_of --check)"

fixture | cex --ingest --exit 1 >/dev/null 2>&1
check "a failing run records its counterexamples" 2 "$(rows)"
[ -f "$R/.fluxpoint-cex.jsonl" ] && ok "the store is at the repo root, not under .claude" "root" \
  || bad "the store is at the repo root, not under .claude" "missing"
# The whole ratchet is worthless if the plugin's own installer gitignores it.
printf '.claude/fluxpoint/\n' >"$R/.gitignore"; commit ignore
git -C "$R" check-ignore -q .fluxpoint-cex.jsonl \
  && bad "the store is committable, not gitignored" "IGNORED — check would be per-machine" \
  || ok "the store is committable, not gitignored" "tracked"
[ -f "$R/.fluxpoint-cex/$(cexid).json" ] && ok "frozen evidence rides alongside it" "written" \
  || bad "frozen evidence rides alongside it" "missing"

# ================= 2. what must NOT be recorded ===========================
out="$(cex --list)"
case "$out" in *expected_to_fail*)
  bad "a fail-annotated PASS is not a counterexample" "recorded a green test" ;;
  *) ok "a fail-annotated PASS is not a counterexample" "skipped" ;; esac
case "$out" in *prop_ok*) bad "a passing property is not recorded" "recorded" ;;
  *) ok "a passing property is not recorded" "skipped" ;; esac

# Re-ingesting the same run is a hit, not a second row.
fixture | cex --ingest --exit 1 >/dev/null 2>&1
check "re-finding the same counterexample appends nothing" 2 "$(rows)"

# ================= 3. the loud path, which is never red ===================
mkrepo
printf '    | FAIL [after 1 test] prop_shape\n' | cex --ingest --exit 1 >/dev/null 2>&1
check "terminal-box output (pre-1.1.6) is not silently dropped" 0 "$?"
check "and it files an inbox item" 1 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
check "but it never turns the build red" 0 \
  "$(printf 'boxes\n' | cex --ingest --exit 1 >/dev/null 2>&1; echo $?)"

mkrepo
check "an empty stdout with exit 0 is dormant" 0 \
  "$(printf '' | cex --ingest --exit 0 >/dev/null 2>&1; echo $?)"
check "and files nothing" 0 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
# The ill-formed-fuzzer panic writes nothing to stdout and must not read as
# "no failures".
printf '' | cex --ingest --exit 101 >/dev/null 2>&1
check "an exit-101 panic with no stdout is reported" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"

# Format drift that keeps modules[].tests[] but changes what is inside it
# would otherwise stop ingestion forever while reporting a clean run.
mkrepo
"$FPL_PY" - <<'PY' | cex --ingest --exit 1 >/dev/null 2>&1
import json
print(json.dumps({"seed": 1, "summary": {"failed": 3},
                  "modules": [{"name": "m", "tests": [
                      {"title": "t", "status": "failed"}]}]}))
PY
check "summary.failed > 0 with nothing extracted is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"

# ================= 4. pinning: the anti-prose check =======================
setup_pinnable() {
  mkrepo
  fixture | cex --ingest --exit 1 >/dev/null 2>&1
  ID="$(cexid)"
}
write_test() { # $1 = path, $2 = body text
  printf 'test %s() {\n  %s\n}\n' "$ID" "$2" >"$R/$1"; commit t >/dev/null
}

setup_pinnable
cex --pin "$ID" >/dev/null 2>&1
check "step 1 leaves the row OPEN" 0 "$(rc_of --check)"
[ -f "$R/.claude/fluxpoint/cex/$ID.ak.draft" ] \
  && ok "step 1 writes a draft into scratch, not into source" "scratch" \
  || bad "step 1 writes a draft into scratch, not into source" "missing"

# The tool must not be able to satisfy its own containment check.
cp "$R/.claude/fluxpoint/cex/$ID.ak.draft" "$R/.claude/fluxpoint/cex/$ID.ak"
check "the tool's own draft is not a legal pin target" 1 \
  "$(rc_of --pin "$ID" --file ".claude/fluxpoint/cex/$ID.ak" --test-name "$ID")"

write_test lib/reg.ak 'pred(Circle { radius: 200 })'
check "a real test containing the value is accepted" 0 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"
check "and --check is green" 0 "$(rc_of --check)"

# Every way of appearing to pin without pinning.
setup_pinnable
write_test lib/reg.ak '// Circle { radius: 200 }
  pred(other)'
check "the value in a COMMENT is refused" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

setup_pinnable
write_test lib/reg.ak 'let _n = @"Circle { radius: 200 }"
  helper() == helper()'
check "the value in a STRING literal is refused" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

setup_pinnable
write_test lib/reg.ak 'let v = Circle { radius: 200 }
  True'
check "a body that binds the value then ignores it is refused" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

setup_pinnable
printf 'test %s() fail {\n  pred(Circle { radius: 200 })\n}\n' "$ID" >"$R/lib/reg.ak"
commit f >/dev/null
check "a fail-annotated regression is refused (it inverts the oracle)" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 199 })'
check "a value that was retyped rather than pasted is refused" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

# Containment is scoped to the named test's body: a scalar sitting anywhere
# else in the file must not satisfy it.
setup_pinnable
printf 'const limit = 200\n\ntest %s() {\n  True == True\n}\n' "$ID" >"$R/lib/reg.ak"
commit s >/dev/null
check "the value elsewhere in the FILE does not count" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

setup_pinnable
write_test validators/reg.ak 'pred(Circle { radius: 200 })'
git -C "$R" rm -q --cached validators/reg.ak
check "an untracked pin target is refused" 1 \
  "$(rc_of --pin "$ID" --file validators/reg.ak --test-name "$ID")"

setup_pinnable
mkdir -p "$R/scratchpad"
printf 'test %s() {\n  pred(Circle { radius: 200 })\n}\n' "$ID" >"$R/scratchpad/reg.ak"
commit o >/dev/null
check "a pin outside a compile root is refused" 1 \
  "$(rc_of --pin "$ID" --file scratchpad/reg.ak --test-name "$ID")"

setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 200 })'
check "a test name not naming the cexId is refused" 1 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "some_other_test")"

# An opaque type renders as something that is not writable source; requiring
# the constructor name would make a legitimate pin impossible.
mkrepo
"$FPL_PY" - <<'PY' | cex --ingest --exit 1 >/dev/null 2>&1
import json
print(json.dumps({"seed": 1, "summary": {"failed": 1}, "modules": [
    {"name": "x/y/v", "tests": [
        {"title": "prop_value", "status": "fail", "iterations": 2,
         "counterexample": 'Dict([(#"2cd15ed0", True)])'}]}]}))
PY
ID="$(cexid)"
printf 'test %s() {\n  pred(dict.from_list([Pair(#"2cd15ed0", True)]))\n}\n' "$ID" \
  >"$R/lib/reg.ak"; commit d >/dev/null
check "an opaque value pinned via its real constructor is accepted" 0 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"

# A unit test has no generated input, so its assertion is the payload — and
# pinning it to the very test that is failing must not be free.
mkrepo
fixture | cex --ingest --exit 1 >/dev/null 2>&1
UID_="$("$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY'
import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r["kind"] == "unit": print(r["cexId"]); break
PY
)"
printf 'test %s() {\n  True == True\n}\n' "$UID_" >"$R/lib/u.ak"; commit u >/dev/null
check "a unit pin with none of the assertion values is refused" 1 \
  "$(rc_of --pin "$UID_" --file lib/u.ak --test-name "$UID_")"
printf 'test %s() {\n  add_one(2) == 1\n}\n' "$UID_" >"$R/lib/u.ak"; commit u2 >/dev/null
check "and accepted when it carries them" 0 \
  "$(rc_of --pin "$UID_" --file lib/u.ak --test-name "$UID_")"

# ================= 5. --check: red only on real weakening =================
setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 200 })'
cex --pin "$ID" --file lib/reg.ak --test-name "$ID" >/dev/null 2>&1
rm "$R/lib/reg.ak"; commit rm >/dev/null
check "a deleted regression file is red" 1 "$(rc_of --check)"

setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 200 })'
cex --pin "$ID" --file lib/reg.ak --test-name "$ID" >/dev/null 2>&1
printf 'test %s() {\n  True\n}\n' "$ID" >"$R/lib/reg.ak"; commit h >/dev/null
check "a regression hollowed out in place is red" 1 "$(rc_of --check)"

# Reformatting is not weakening: aiken fmt wraps and adds a trailing comma.
mkrepo
"$FPL_PY" - <<'PY' | cex --ingest --exit 1 >/dev/null 2>&1
import json
print(json.dumps({"seed": 1, "summary": {"failed": 1}, "modules": [
    {"name": "x/y/v", "tests": [
        {"title": "prop_cfg", "status": "fail", "iterations": 1,
         "counterexample": "Config { alpha: 12, beta: True }"}]}]}))
PY
ID="$(cexid)"
printf 'test %s() {\n  pred(\n    Config {\n      alpha: 12,\n      beta: True,\n    },\n  )\n}\n' "$ID" \
  >"$R/lib/reg.ak"; commit w >/dev/null
check "a reformatted multi-line value still pins" 0 \
  "$(rc_of --pin "$ID" --file lib/reg.ak --test-name "$ID")"
check "and stays green" 0 "$(rc_of --check)"

# A file move is not a weakening; re-pinning is the way out and needs no
# retirement.
git -C "$R" mv lib/reg.ak validators/reg.ak; commit mv >/dev/null
check "a moved regression is red until re-pinned" 1 "$(rc_of --check)"
check "re-pinning an already-pinned row is allowed" 0 \
  "$(rc_of --pin "$ID" --file validators/reg.ak --test-name "$ID")"
check "and it is green again" 0 "$(rc_of --check)"

# An open row never fails the build: finding a bug must not punish the finder.
mkrepo
fixture | cex --ingest --exit 1 >/dev/null 2>&1
check "unpinned counterexamples never fail --check" 0 "$(rc_of --check)"

# ================= 6. the release valve costs a reviewed diff =============
setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 200 })'
cex --pin "$ID" --file lib/reg.ak --test-name "$ID" >/dev/null 2>&1
rm "$R/lib/reg.ak"; commit rm2 >/dev/null
check "retiring with a short reason is refused" 1 \
  "$(rc_of --retire "$ID" --reason "fixed")"
check "retiring with no Decisions row is refused" 1 \
  "$(rc_of --retire "$ID" --reason "the radius field is now a bounded newtype")"
cat >"$R/WORK.md" <<EOF
# w

## Decisions

| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |
|---|---|---|---|---|---|
| 2026-08-08 01:00 | retire-cex | drop the pin | no | none | $ID no longer constructible: radius is a bounded newtype |
EOF
commit dec >/dev/null
check "retiring named in Decisions is allowed" 0 \
  "$(rc_of --retire "$ID" --reason "the radius field is now a bounded newtype")"
check "and --check goes green" 0 "$(rc_of --check)"

# ================= 7. a re-found pinned cex is an inert pin ===============
setup_pinnable
write_test lib/reg.ak 'pred(Circle { radius: 200 })'
cex --pin "$ID" --file lib/reg.ak --test-name "$ID" >/dev/null 2>&1
before="$("$FPL_PY" "$INBOX" --root "$R" --count)"
fixture | cex --ingest --exit 1 >/dev/null 2>&1
after="$("$FPL_PY" "$INBOX" --root "$R" --count)"
[ "$after" -gt "$before" ] \
  && ok "re-finding a PINNED counterexample raises an inert-pin item" "raised" \
  || bad "re-finding a PINNED counterexample raises an inert-pin item" "swallowed"

# ================= 8. the store fails hard, never quietly =================
printf 'not json\n' >>"$R/.fluxpoint-cex.jsonl"
err="$(cex --check 2>&1 >/dev/null)"
case "$err" in *"is not valid JSON"*) ok "a corrupted store fails hard on read" "hard error" ;;
  *) bad "a corrupted store fails hard on read" "${err:0:44}" ;; esac

# ================= 9. a second prover: dafny =============================
# The ledger, the containment rule, the refusal to mint from a green run and
# the INGEST-FAILED row are shared; only the parser is per prover. Fixtures
# are the literal shape `dafny verify --extract-counterexample` prints
# (Dafny 3: `name : type = value` state blocks; Dafny 4: the same model as
# `assume` statements), captured from a real run, never from documentation.
mkdafny() {
  mkrepo
  mkdir -p "$R/src" "$R/test"
  cat >"$R/src/example.dfy" <<'EOF'
module M {
  method Fill(a: array?<int>, n: int, j: int, k: int)
    requires a != null && a.Length == n
    modifies a
  {
    var i := 0;
    while i < n
      invariant 0 <= i <= n
    {
      a[i] := i;
      i := i + 1;
    }
  }
}
EOF
  git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm dafny
}
dafny3() {
  cat <<'EOF'
src/example.dfy(8,16): Error: This loop invariant might not be maintained by the loop.
src/example.dfy(8,16): Related message: loop invariant violation

Dafny program verifier finished with 1 verified, 1 error
Counterexample for first failing assertion: 
src/example.dfy(2,2): initial state:
        a : _System.array?<int> = ()
        n : int = 1237
        j : int = 196
        k : int = 1236
src/example.dfy(6,14):
        a : _System.array?<int> = ()
        n : int = 1237
        i : int = 0
        j : int = 196
        k : int = 1236
EOF
}
dafny4() {
  cat <<'EOF'
src/example.dfy(8,16): Error: this loop invariant could not be proved to be maintained by the loop

Dafny program verifier finished with 1 verified, 1 error
Counterexample for first failing assertion:
src/example.dfy(2,2): initial state:
    assume n == 1237 && j == 196 && k == 1236;
EOF
}
dcex() { cex --ingest --tool dafny "$@"; }
dcexid() { "$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2'
import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r["tool"] == "dafny": print(r["cexId"]); break
PY2
}

mkdafny
dafny3 | dcex --exit 4 >/dev/null 2>&1
check "a dafny 3 model records one counterexample" 1 "$(rows)"
out="$(cex --list)"
case "$out" in *"[dafny] src/example.dfy:Fill"*) ok "the row names the tool and the enclosing method" "selector" ;;
  *) bad "the row names the tool and the enclosing method" "${out:0:80}" ;; esac
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "the initial state is the recorded input, in order" "n, j, k" \
  || bad "the initial state is the recorded input, in order" "wrong payload"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
# Every assignment of the initial state, in the order the prover printed it.
# The opaque array renders as `()` and yields no literal, so it never becomes
# something a pin must contain; it stays in the record because it is evidence.
assert r["input"] == "a == (), n == 1237, j == 196, k == 1236", r["input"]
assert r["signature"] == "counterexample" and r["inputForm"] == "dafny-model"
assert r["containment"] == r["input"]
PY2
dafny3 | dcex --exit 4 >/dev/null 2>&1
check "re-ingesting the same model appends nothing" 1 "$(rows)"

mkdafny
dafny4 | dcex --exit 4 >/dev/null 2>&1
check "the dafny 4 assume spelling records the same literals" 1 "$(rows)"
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "and reads the conjuncts as assignments" "assume" \
  || bad "and reads the conjuncts as assignments" "wrong payload"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
assert r["input"] == "n == 1237, j == 196, k == 1236", r["input"]
PY2

# ----- what a dafny pin has to survive
dpin() { # $1 = body
  printf 'include "../src/example.dfy"\n\nmethod {:test} %s() {\n  %s\n}\n' "$DID" "$1" >"$R/test/reg.dfy"
  commit p >/dev/null
}
mkdafny; dafny3 | dcex --exit 4 >/dev/null 2>&1; DID="$(dcexid)"
cex --pin "$DID" >/dev/null 2>&1
[ -f "$R/.claude/fluxpoint/cex/$DID.dfy.draft" ] \
  && ok "the draft carries the prover's own extension" "dfy" \
  || bad "the draft carries the prover's own extension" "missing"
dpin 'var a := new int[1237]; M.Fill(a, 1237, 196, 1236); expect a[0] == 0;'
check "a tracked .dfy test carrying the literals in order pins" 0 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
check "and --check is green" 0 "$(rc_of --check)"

mkdafny; dafny3 | dcex --exit 4 >/dev/null 2>&1; DID="$(dcexid)"
dpin '// Fill(a, 1237, 196, 1236)
  expect 1 == 1;'
check "the values in a COMMENT are refused" 1 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
dpin 'var a := new int[1237]; M.Fill(a, 1237, 196, 1236); assume false; expect a[0] == 9;'
check "an assume in the body is refused (anything verifies under it)" 1 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
printf 'include "../src/example.dfy"\n\nmethod {:test} {:verify false} %s() {\n  var a := new int[1237]; M.Fill(a, 1237, 196, 1236); expect a[0] == 0;\n}\n' "$DID" >"$R/test/reg.dfy"
commit v >/dev/null
check "{:verify false} on the test is refused" 1 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
dpin 'assert true;'
check "an assert-true body is refused" 1 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
dpin 'var a := new int[1237]; M.Fill(a, 1237, 196, 1238); expect a[0] == 0;'
check "a retyped value is refused" 1 \
  "$(rc_of --pin "$DID" --file test/reg.dfy --test-name "$DID")"
printf 'test %s() {\n  pred(1237, 196, 1236)\n}\n' "$DID" >"$R/lib/reg.ak"; commit ak >/dev/null
check "a pin in another prover's language is refused" 1 \
  "$(rc_of --pin "$DID" --file lib/reg.ak --test-name "$DID")"

# ----- failures without a model, and runs that record nothing
mkdafny
printf 'src/example.dfy(3,12): Error: a postcondition could not be proved on this return path\nsrc/example.dfy(3,12): Related location: this is the postcondition that could not be proved\n\nDafny program verifier finished with 0 verified, 1 error\n' \
  | dcex --exit 4 >/dev/null 2>&1
check "an error with no model is recorded as an assertion" 1 "$(rows)"
DID="$(dcexid)"
check "and stays open rather than failing --check" 0 "$(rc_of --check)"
dpin 'expect 1 == 1;'
err="$(cex --pin "$DID" --file test/reg.dfy --test-name "$DID" 2>&1 >/dev/null)"
case "$err" in *"no literal"*) ok "it cannot be pinned mechanically, and says so" "refused" ;;
  *) bad "it cannot be pinned mechanically, and says so" "${err:0:60}" ;; esac

mkdafny
printf 'src/example.dfy(4,9): Error: unresolved identifier: Lenght\n1 resolution/type errors detected in example.dfy\n' \
  | dcex --exit 2 >/dev/null 2>&1
check "a resolution error is not a counterexample" 0 "$(rows)"
check "and files nothing" 0 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
dafny3 | dcex --exit 0 >/dev/null 2>&1
check "exit 0 records nothing whatever was printed" 0 "$(rows)"

# ----- drift is loud and never red
mkdafny
printf 'src/example.dfy(8,16): Error: This loop invariant might not be maintained by the loop.\n\nDafny program verifier finished with 1 verified, 1 error\nCounterexample for first failing assertion:\nsrc/example.dfy(2,2): initial state:\n    <opaque model dump>\n' \
  | dcex --exit 4 >/dev/null 2>&1
check "a model heading with nothing readable under it is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
check "and it is not red" 0 "$(printf 'x\n' | dcex --exit 4 >/dev/null 2>&1; echo $?)"
mkdafny
printf 'Unhandled exception: System.Something\n' | dcex --exit 3 >/dev/null 2>&1
check "output with no Error line and no summary is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
out="$("$FPL_PY" "$INBOX" --root "$R" --list 2>/dev/null || cat "$R/.claude/fluxpoint/inbox.jsonl")"
case "$out" in *"dafny"*) ok "the inbox row names the prover that drifted" "named" ;;
  *) bad "the inbox row names the prover that drifted" "${out:0:60}" ;; esac
check "an unknown tool is refused" 1 "$(printf 'x\n' | cex --ingest --tool nosuch --exit 1 >/dev/null 2>&1; echo $?)"

# ----- the scaffolded harness captures dafny the way it captures aiken
mkdafny
mkdir -p "$R/scripts" "$R/bin"
cp "$PLUGIN/templates/harness.sh" "$R/scripts/harness.sh"; chmod +x "$R/scripts/harness.sh"
printf '#!/usr/bin/env bash\nif [ "$1" = "--version" ]; then echo "Dafny 4.9.0"; exit 0; fi\ncat <<'"'"'EOF'"'"'\n%s\nEOF\nexit 4\n' "$(dafny3)" >"$R/bin/dafny"
chmod +x "$R/bin/dafny"
cp "$R/src/example.dfy" "$R/example.dfy"   # the template globs one level down
git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm shim
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT PATH="$R/bin:$PATH" FPL_PLUGIN_ROOT="$PLUGIN" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
[ "$hrc" -ne 0 ] && ok "the harness still fails on the prover's exit" "rc=$hrc" \
  || bad "the harness still fails on the prover's exit" "rc=0"
check "and the counterexample was recorded on the way" 1 "$(rows)"
[ "$(rows)" = 1 ] || { echo "      harness output:"; tail -15 "$ROOT/harness.log" | sed 's/^/      /'; }

# ================= 10. a third prover: kani ==============================
# Neither `cargo kani` nor the Kani toolchain is installed where this suite
# was written, so — unlike the dafny fixtures above — these are RECONSTRUCTED
# from Kani's documented output (the per-check RESULTS block, the Failed
# Checks summary, the `VERIFICATION:- FAILED` verdict, the ``Concrete
# playback unit test for `h`:`` block that `-Z concrete-playback
# --concrete-playback=print` emits, and the closing Summary), not captured
# from a run. The parser is kept tolerant for that reason, and any shape it
# does not recognise files INGEST-FAILED rather than reading as green.
mkkani() {
  mkrepo
  mkdir -p "$R/src" "$R/tests"
  printf '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n' >"$R/Cargo.toml"
  cat >"$R/src/lib.rs" <<'EOF'
pub fn bumps(x: u8, y: u8) -> bool {
    x.wrapping_add(y) > x
}

#[cfg(kani)]
#[kani::proof]
fn my_harness() {
    let x: u8 = kani::any();
    let y: u8 = kani::any();
    kani::assume(y > 0);
    assert!(x + 1 > x);
}
EOF
  git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm kani
}
kani_fail() {
  cat <<'EOF'
Kani Rust Verifier 0.56.0 (cargo plugin)
Checking harness my_harness...
CBMC 6.1.1 (cbmc-6.1.1)
CBMC version 6.1.1 (cbmc-6.1.1) 64-bit x86_64 linux
Reading GOTO program from file /tmp/kani/my_harness.out

RESULTS:
Check 1: my_harness.unwrap.1
	 - Status: SUCCESS
	 - Description: "called `Option::unwrap()` on a `None` value"
	 - Location: src/lib.rs:11:13 in function my_harness

Check 2: my_harness.assertion.1
	 - Status: FAILURE
	 - Description: "assertion failed: x + 1 > x"
	 - Location: src/lib.rs:12:5 in function my_harness


SUMMARY:
 ** 1 of 2 failed
Failed Checks: assertion failed: x + 1 > x
 File: "src/lib.rs", line 12, in my_harness

VERIFICATION:- FAILED
Verification Time: 0.0312s

Concrete playback unit test for `my_harness`:
```
/// Test generated for harness `my_harness`
///
/// Check for `assertion`: "assertion failed: x + 1 > x"
#[test]
fn kani_concrete_playback_my_harness_1234567890123456789() {
    let concrete_vals: Vec<Vec<u8>> = vec![
        // 255
        vec![255],
        // 1
        vec![1],
    ];
    kani::concrete_playback_run(concrete_vals, my_harness);
}
```
INFO: To automatically add the concrete playback unit test `kani_concrete_playback_my_harness_1234567890123456789` to the src code, run Kani with `--concrete-playback=inplace`.

Summary:
Verification failed for - my_harness
Complete - 0 successfully verified harnesses, 1 failures, 1 total.
EOF
}
kani_green() {
  cat <<'EOF'
Kani Rust Verifier 0.56.0 (cargo plugin)
Checking harness my_harness...
CBMC 6.1.1 (cbmc-6.1.1)

RESULTS:
Check 1: my_harness.assertion.1
	 - Status: SUCCESS
	 - Description: "assertion failed: x + 1 > x"
	 - Location: src/lib.rs:12:5 in function my_harness


SUMMARY:
 ** 0 of 1 failed

VERIFICATION:- SUCCESSFUL
Verification Time: 0.0201s

Complete - 1 successfully verified harnesses, 0 failures, 1 total.
EOF
}
# The same failure without `-Z concrete-playback`: a check, no values.
kani_noplay() { kani_fail | sed '/^Concrete playback unit test/,/^INFO:/d'; }
kcex() { cex --ingest --tool kani "$@"; }
kcexid() { "$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2'
import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r["tool"] == "kani": print(r["cexId"]); break
PY2
}

mkkani
kani_green | kcex --exit 0 >/dev/null 2>&1
check "a green kani run records nothing" 0 "$(rows)"
kani_fail | kcex --exit 0 >/dev/null 2>&1
check "exit 0 records nothing whatever was printed" 0 "$(rows)"
kani_fail | kcex --exit 1 >/dev/null 2>&1
check "a failing harness records one counterexample" 1 "$(rows)"
out="$(cex --list)"
case "$out" in *"[kani] src/lib.rs:my_harness"*) ok "the row names the tool, the file and the harness" "selector" ;;
  *) bad "the row names the tool, the file and the harness" "${out:0:80}" ;; esac
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "the playback comment values are the recorded input, in order" "255, 1" \
  || bad "the playback comment values are the recorded input, in order" "wrong payload"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
# Kani does not name its inputs: the `// value` lines above each byte
# vector, in harness order, are the record. The raw bytes ride along as the
# prover's own second spelling of the same value.
assert r["input"] == "255, 1", r["input"]
assert r["containment"] == r["input"]
assert r["containmentAlt"] == "vec![255], vec![1]", r["containmentAlt"]
assert r["signature"] == "counterexample" and r["inputForm"] == "kani-concrete-playback"
assert r["kind"] == "harness" and r["module"] == "src/lib.rs" and r["title"] == "my_harness"
assert "assertion failed: x + 1 > x" in r["assertion"] and "src/lib.rs:12:5" in r["assertion"], r["assertion"]
PY2
kani_fail | kcex --exit 1 >/dev/null 2>&1
check "re-ingesting the same playback appends nothing" 1 "$(rows)"

# ----- what a kani pin has to survive
kpin() { # $1 = attribute lines above #[test] (may be empty), $2 = body
  printf 'use x::bumps;\n\n%s#[test]\nfn %s() {\n  %s\n}\n' "$1" "$KID" "$2" >"$R/tests/reg.rs"
  commit p >/dev/null
}
mkkani; kani_fail | kcex --exit 1 >/dev/null 2>&1; KID="$(kcexid)"
cex --pin "$KID" >/dev/null 2>&1
[ -f "$R/.claude/fluxpoint/cex/$KID.rs.draft" ] \
  && ok "the draft carries the prover's own extension" "rs" \
  || bad "the draft carries the prover's own extension" "missing"
kpin '' 'assert!(!bumps(255, 1));'
check "a tracked #[test] carrying the values in order pins" 0 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
check "and --check is green" 0 "$(rc_of --check)"

mkkani; kani_fail | kcex --exit 1 >/dev/null 2>&1; KID="$(kcexid)"
kpin $'#[should_panic]\n' 'assert!(!bumps(255, 1));'
check "#[should_panic] is refused (it inverts the oracle)" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin $'#[ignore]\n' 'assert!(!bumps(255, 1));'
check "#[ignore] is refused (it never runs)" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
printf 'use x::bumps;\n\nfn %s() {\n  assert!(!bumps(255, 1));\n}\n' "$KID" >"$R/tests/reg.rs"; commit plain >/dev/null
check "a plain fn with neither #[test] nor #[kani::proof] is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'assert!(true);'
check "an assert!(true) body is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'let a: u8 = 255; let b: u8 = 1;'
check "a body that binds the values and calls nothing is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' '// bumps(255, 1)
  assert!(bumps(2, 3));'
check "the values in a COMMENT are refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'let s = "255, 1"; assert!(!s.is_empty());'
check "the values in a STRING literal are refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'assert!(!bumps(255, 0));'
check "a body missing one literal is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'assert!(!bumps(254, 1));'
check "a retyped value is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
printf 'method {:test} %s() {\n  expect bumps(255, 1);\n}\n' "$KID" >"$R/tests/reg.dfy"; commit dfy >/dev/null
check "a pin in another prover's language is refused" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.dfy --test-name "$KID")"

# A proof harness is as legal a pin as a unit test: cargo kani runs it.
printf '\n#[cfg(kani)]\n#[kani::proof]\n#[kani::unwind(3)]\nfn %s() {\n    assert!(!bumps(255, 1));\n}\n' "$KID" >>"$R/src/lib.rs"
commit proof >/dev/null
check "a #[kani::proof] harness carrying the values pins" 0 \
  "$(rc_of --pin "$KID" --file src/lib.rs --test-name "$KID")"

# The test Kani itself writes under --concrete-playback=inplace, renamed to
# carry the cexId: the values are in `// value` comments (stripped) AND in
# the byte vectors, which are the prover's own second spelling.
mkkani; kani_fail | kcex --exit 1 >/dev/null 2>&1; KID="$(kcexid)"
kani_fail | sed -n '/^\/\/\/ Test generated/,/^}$/p' | sed "s/kani_concrete_playback_my_harness_1234567890123456789/$KID/" \
  >"$R/tests/reg.rs"; commit inplace >/dev/null
check "Kani's own inplace playback test pins" 0 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
# A value wider than a byte: `// 300` above `vec![44, 1]`. Either spelling
# pins; the bytes only as the whole vector Kani wrote, never as bare leaves.
mkkani
kani_fail | sed -e 's|// 255|// 300|' -e 's|vec!\[255\]|vec![44, 1]|' | kcex --exit 1 >/dev/null 2>&1
KID="$(kcexid)"
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "a multi-byte value records its interpreted form" "300, 1" \
  || bad "a multi-byte value records its interpreted form" "wrong payload"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
assert r["input"] == "300, 1", r["input"]
assert r["containmentAlt"] == "vec![44, 1], vec![1]", r["containmentAlt"]
PY2
kpin '' 'assert!(!wide(300, 1));'
check "and pins by that form" 0 "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'kani::concrete_playback_run(vec![vec![44, 1], vec![1]], my_harness);'
check "or by the byte vectors Kani wrote" 0 "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"
kpin '' 'assert!(!wide(44, 1));'
check "but not by the bytes retyped as plain arguments" 1 \
  "$(rc_of --pin "$KID" --file tests/reg.rs --test-name "$KID")"

# ----- failures without values, and runs that record nothing
mkkani
kani_noplay | kcex --exit 1 >/dev/null 2>&1
check "a failure without playback values is recorded as an assertion" 1 "$(rows)"
KID="$(kcexid)"
check "and stays open rather than failing --check" 0 "$(rc_of --check)"
kpin '' 'assert!(!bumps(255, 1));'
err="$(cex --pin "$KID" --file tests/reg.rs --test-name "$KID" 2>&1 >/dev/null)"
case "$err" in *"no literal"*) ok "it cannot be pinned mechanically, and says so" "refused" ;;
  *) bad "it cannot be pinned mechanically, and says so" "${err:0:60}" ;; esac
mkkani
printf 'error[E0425]: cannot find value `z` in this scope\n --> src/lib.rs:9:5\n\nerror: could not compile `x` (lib) due to 1 previous error\n' \
  | kcex --exit 1 >/dev/null 2>&1
check "a build error is not a counterexample" 0 "$(rows)"
check "and files nothing" 0 "$("$FPL_PY" "$INBOX" --root "$R" --count)"

# ----- drift is loud and never red
mkkani
check "a summary counting a failure the parser could not extract exits 0" 0 \
  "$(kani_fail | sed 's/1 failures, 1 total/2 failures, 2 total/' | kcex --exit 1 >/dev/null 2>&1; echo $?)"
check "and files INGEST-FAILED" 1 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkkani
printf 'RESULTS:\nVERIFICATION:- FAILED\n' | kcex --exit 1 >/dev/null 2>&1
check "a FAILED verdict with no harness name is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkkani
printf 'thread main panicked at kani-driver\n' | kcex --exit 101 >/dev/null 2>&1
check "output with no verdict and no rustc error is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
out="$("$FPL_PY" "$INBOX" --root "$R" --list 2>/dev/null || cat "$R/.claude/fluxpoint/inbox.jsonl")"
case "$out" in *"kani"*) ok "the inbox row names the prover that drifted" "named" ;;
  *) bad "the inbox row names the prover that drifted" "${out:0:60}" ;; esac

# ----- --check: red on real weakening
mkkani; kani_fail | kcex --exit 1 >/dev/null 2>&1; KID="$(kcexid)"
kpin '' 'assert!(!bumps(255, 1));'
cex --pin "$KID" --file tests/reg.rs --test-name "$KID" >/dev/null 2>&1
git -C "$R" rm -q tests/reg.rs; commit rm >/dev/null
check "a deleted kani regression is red" 1 "$(rc_of --check)"

# ----- the scaffolded harness captures kani the way it captures dafny
mkkani
mkdir -p "$R/scripts" "$R/bin"
cp "$PLUGIN/templates/harness.sh" "$R/scripts/harness.sh"; chmod +x "$R/scripts/harness.sh"
kani_fail >"$R/bin/kani.out"
# `cargo` stands in for the whole toolchain: fmt passes, clippy is absent
# (its --version fails, so the harness skips it), test passes, and `kani`
# prints the fixture and fails the way the real plugin does.
cat >"$R/bin/cargo" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  fmt) exit 0 ;;
  clippy) exit 1 ;;
  test) exit 0 ;;
  kani) cat "$(dirname "$0")/kani.out"; exit 1 ;;
  *) echo "cargo shim: unexpected $*" >&2; exit 2 ;;
esac
EOF
printf '#!/usr/bin/env bash\necho "cargo-kani 0.56.0"\nexit 0\n' >"$R/bin/cargo-kani"
chmod +x "$R/bin/cargo" "$R/bin/cargo-kani"
git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm shim
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT PATH="$R/bin:$PATH" FPL_PLUGIN_ROOT="$PLUGIN" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
check "the harness fails with the prover's exit" 1 "$hrc"
check "and the counterexample was recorded on the way" 1 "$(rows)"
[ "$(rows)" = 1 ] || { echo "      harness output:"; tail -15 "$ROOT/harness.log" | sed 's/^/      /'; }

# ================= 11. a fourth prover: apalache =========================
# `apalache-mc` is not installed where this suite was written either, so the
# stdout lines and the ITF trace are RECONSTRUCTED from the documented
# output (`State N: state invariant 0 violated.`, `Check the trace in: …`
# naming counterexample1.tla/.json/.itf.json, `EXITCODE: ERROR (12)`) and
# from the published ITF format (`#meta`, `vars`, `states`, `#bigint`,
# `#set`, `#map`, `#tup`, records, `#unserializable`), not captured from a
# run. Same rule as kani: tolerant parser, unrecognised shape is a loud
# INGEST-FAILED, never a silent green.
mkapalache() {
  mkrepo
  cat >"$R/Spec.tla" <<'EOF'
---- MODULE Spec ----
EXTENDS Integers
VARIABLES x, y

Init == x = 1 /\ y = {1, 2}
Next == x' = x + 1 /\ y' = y \cup {x + 2}
Inv == x < 3
====
EOF
  git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm tla
}
itf_fixture() {
  cat <<'EOF'
{
  "#meta": {
    "format": "ITF",
    "format-description": "https://apalache-mc.org/docs/adr/015adr-trace.html",
    "source": "Spec.tla",
    "description": "Created by Apalache on Thu Sep 10 12:00:00 UTC 2026",
    "varTypes": { "x": "Int", "y": "Set(Int)" }
  },
  "vars": [ "x", "y" ],
  "states": [
    { "#meta": { "index": 0 }, "x": { "#bigint": "1" }, "y": { "#set": [ { "#bigint": "1" }, { "#bigint": "2" } ] } },
    { "#meta": { "index": 1 }, "x": { "#bigint": "2" }, "y": { "#set": [ { "#bigint": "1" }, { "#bigint": "2" }, { "#bigint": "3" } ] } },
    { "#meta": { "index": 2 }, "x": 3, "y": { "#set": [] } }
  ]
}
EOF
}
# Every documented value encoding in one state, for the renderer.
itf_rich() {
  cat <<'EOF'
{
  "#meta": { "format": "ITF", "source": "Rich.tla" },
  "vars": [ "n", "s", "b", "r", "t", "m", "u", "q" ],
  "states": [
    { "#meta": { "index": 0 }, "n": { "#bigint": "-5" }, "s": "abc", "b": true,
      "r": { "a": 1, "b": "x" }, "t": { "#tup": [ 1, 2 ] },
      "m": { "#map": [ [ 1, "a" ], [ 2, "b" ] ] }, "u": { "#unserializable": "Nat" },
      "q": [ 7, 8 ] }
  ]
}
EOF
}
AP_DIR="_apalache-out/Spec.tla/2026-09-10T12-00-00_1"
apalache_out() { # $1 = directory prefix as the checker would print it
  printf 'PASS #13: BoundedChecker                                          I@12:00:00.100\nState 2: Checking 1 state invariants                              I@12:00:00.200\nState 2: state invariant 0 violated.                              E@12:00:00.300\nCheck the trace in: %s/counterexample1.tla, %s/MC1.out, %s/counterexample1.json, %s/counterexample1.itf.json E@12:00:00.400\nTotal time: 1.234 sec                                             I@12:00:00.500\nEXITCODE: ERROR (12)\n' "$1" "$1" "$1" "$1"
}
write_itf() { mkdir -p "$R/$AP_DIR"; itf_fixture >"$R/$AP_DIR/counterexample1.itf.json"; }
acex() { cex --ingest --tool apalache "$@"; }
acexid() { "$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2'
import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r["tool"] == "apalache": print(r["cexId"]); break
PY2
}

mkapalache
printf 'Checker reports no error up to computation length 10       I@12:00:00.100\nEXITCODE: OK\n' | acex --exit 0 >/dev/null 2>&1
check "a green apalache run records nothing" 0 "$(rows)"
write_itf
apalache_out "$R/$AP_DIR" | acex --exit 0 >/dev/null 2>&1
check "exit 0 records nothing whatever was printed" 0 "$(rows)"
apalache_out "$R/$AP_DIR" | acex --exit 12 >/dev/null 2>&1
check "a violated invariant records one trace" 1 "$(rows)"
out="$(cex --list)"
case "$out" in *"[apalache] Spec.tla:3-state-trace"*) ok "the row names the tool, the module and the trace length" "selector" ;;
  *) bad "the row names the tool, the module and the trace length" "${out:0:80}" ;; esac
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "every state is rendered in order; the first is what a pin must carry" "x = 1, y = {1, 2}" \
  || bad "every state is rendered in order; the first is what a pin must carry" "wrong payload"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
assert r["input"] == "x = 1, y = {1, 2} ; x = 2, y = {1, 2, 3} ; x = 3, y = {}", r["input"]
assert r["containment"] == "x = 1, y = {1, 2}", r["containment"]
assert r["signature"] == "counterexample" and r["inputForm"] == "itf"
assert r["kind"] == "trace" and r["module"] == "Spec.tla" and r["title"] == "Spec (3 states)"
assert r["assertion"] == "invariant violated after 2 transition(s)", r["assertion"]
assert r["trace"].endswith("counterexample1.itf.json"), r["trace"]
PY2
apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1
check "a relative trace path resolves against the root, and dedupes" 1 "$(rows)"

mkapalache
itf_rich >"$R/rich.itf.json"
acex --from "$R/rich.itf.json" --exit 12 >/dev/null 2>&1
check "--from pointed at the ITF file itself is read as the trace" 1 "$(rows)"
"$FPL_PY" - "$R/.fluxpoint-cex.jsonl" <<'PY2' && ok "bigint, set, map, tuple, record, sequence and unserializable all render" "TLA-ish" \
  || bad "bigint, set, map, tuple, record, sequence and unserializable all render" "wrong rendering"
import json, sys
r = json.loads(open(sys.argv[1]).readline())
assert r["input"] == ('n = -5, s = "abc", b = TRUE, r = [a |-> 1, b |-> "x"], t = <<1, 2>>, '
                      'm = SetAsFun({<<1, "a">>, <<2, "b">>}), u = Nat, q = <<7, 8>>'), r["input"]
assert r["module"] == "Rich.tla" and r["selector"] == "Rich.tla:1-state-trace", r["selector"]
assert r["assertion"] == "invariant violated after 0 transition(s)"
PY2

# ----- what an apalache pin has to survive
apin() { # $1 = the definition text (may span lines)
  printf -- '---- MODULE Reg ----\nEXTENDS Integers\nVARIABLES x, y\n\n%s\n====\n' "$1" >"$R/Reg.tla"
  commit p >/dev/null
}
mkapalache; write_itf; apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1; AID="$(acexid)"
cex --pin "$AID" >/dev/null 2>&1
[ -f "$R/.claude/fluxpoint/cex/$AID.tla.draft" ] \
  && ok "the draft carries the prover's own extension" "tla" \
  || bad "the draft carries the prover's own extension" "missing"
case "$(head -1 "$R/.claude/fluxpoint/cex/$AID.tla.draft")" in '\*'*) ok "and speaks TLA+ down to its comment marker" '\*' ;;
  *) bad "and speaks TLA+ down to its comment marker" "$(head -c 20 "$R/.claude/fluxpoint/cex/$AID.tla.draft")" ;; esac
apin "$AID == x = 1 /\\ y = {1, 2}"
check "a tracked .tla operator carrying the first state pins" 0 \
  "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
check "and --check is green" 0 "$(rc_of --check)"
apin "$AID ==
  /\\ x = 1
  /\\ y = {1, 2}

Other == y = {1, 2}"
check "a bulleted multi-line body pins, and stops at the next definition" 0 \
  "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"

mkapalache; write_itf; apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1; AID="$(acexid)"
apin "ASSUME $AID == x = 1 /\\ y = {1, 2}"
check "an ASSUME is refused (the checker takes it as given)" 1 \
  "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == TRUE"
check "a TRUE body is refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == x = 1

Other == y = {1, 2}"
check "a body missing a literal is refused, even with it in the NEXT definition" 1 \
  "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == x = 1 /\\ y = {1, 3}"
check "a retyped value is refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == \\* x = 1 /\\ y = {1, 2}
  x = 0"
check "the values in a line COMMENT are refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == (* x = 1 /\\ y = {1, 2} *) x = 0"
check "the values in a block COMMENT are refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == s = \"x = 1 /\\ y = {1, 2}\""
check "the values in a STRING are refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
printf '#[test]\nfn %s() { assert!(f(1, 1, 2)); }\n' "$AID" >"$R/reg.rs"; commit rs >/dev/null
check "a pin in another prover's language is refused" 1 \
  "$(rc_of --pin "$AID" --file reg.rs --test-name "$AID")"

# A string-valued state is pinnable: string literals are digested rather
# than blanked, so `"abc"` matches `"abc"` and nothing else can hide in one.
mkapalache; itf_rich >"$R/rich.itf.json"; acex --from "$R/rich.itf.json" --exit 12 >/dev/null 2>&1
AID="$(acexid)"; cex --pin "$AID" >/dev/null 2>&1
apin "$AID ==
  $(tail -1 "$R/.claude/fluxpoint/cex/$AID.tla.draft")"
check "the draft's first-state conjunction, pasted into a tracked module, pins" 0 \
  "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"
apin "$AID == n = -5 /\\ s = \"abd\" /\\ b = TRUE /\\ r = [a |-> 1, b |-> \"x\"] /\\ t = <<1, 2>> /\\ m = SetAsFun({<<1, \"a\">>, <<2, \"b\">>}) /\\ u = Nat /\\ q = <<7, 8>>"
check "with one string retyped it is refused" 1 "$(rc_of --pin "$AID" --file Reg.tla --test-name "$AID")"

# ----- runs that record nothing, and drift that is loud but never red
mkapalache
printf 'Parsing file Spec.tla                                        I@12:00:00.100\nParsing error: Spec.tla:6:1 unexpected token\nEXITCODE: ERROR (255)\n' \
  | acex --exit 255 >/dev/null 2>&1
check "a parse error is not a counterexample" 0 "$(rows)"
check "and files nothing" 0 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkapalache
check "a named trace file that does not exist exits 0" 0 \
  "$(apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1; echo $?)"
check "and files INGEST-FAILED" 1 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkapalache
printf 'State 2: state invariant 0 violated.\nEXITCODE: ERROR (12)\n' | acex --exit 12 >/dev/null 2>&1
check "a violation naming no .itf.json trace is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkapalache; mkdir -p "$R/$AP_DIR"; printf '{"vars": ["x"]}\n' >"$R/$AP_DIR/counterexample1.itf.json"
apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1
check "a trace file with no states is a parser break" 1 "$("$FPL_PY" "$INBOX" --root "$R" --count)"
mkapalache
printf 'java.lang.OutOfMemoryError: Java heap space\n' | acex --exit 1 >/dev/null 2>&1
check "output with no EXITCODE and no trace is a parser break" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"
out="$("$FPL_PY" "$INBOX" --root "$R" --list 2>/dev/null || cat "$R/.claude/fluxpoint/inbox.jsonl")"
case "$out" in *"apalache"*) ok "the inbox row names the prover that drifted" "named" ;;
  *) bad "the inbox row names the prover that drifted" "${out:0:60}" ;; esac

# ----- --check: red on real weakening
mkapalache; write_itf; apalache_out "$AP_DIR" | acex --exit 12 >/dev/null 2>&1; AID="$(acexid)"
apin "$AID == x = 1 /\\ y = {1, 2}"
cex --pin "$AID" --file Reg.tla --test-name "$AID" >/dev/null 2>&1
git -C "$R" rm -q Reg.tla; commit rm >/dev/null
check "a deleted apalache regression is red" 1 "$(rc_of --check)"

# ----- the scaffolded harness captures apalache the way it captures dafny
mkapalache
mkdir -p "$R/scripts" "$R/bin"
cp "$PLUGIN/templates/harness.sh" "$R/scripts/harness.sh"; chmod +x "$R/scripts/harness.sh"
itf_fixture >"$R/bin/cex.itf.json"
# The shim writes the trace where the checker would and prints the lines
# that name it, then exits 12 the way `apalache-mc check` does.
cat >"$R/bin/apalache-mc" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "version" ]; then echo "0.47.2"; exit 0; fi
d="\$PWD/$AP_DIR"
mkdir -p "\$d"
cp "\$(dirname "\$0")/cex.itf.json" "\$d/counterexample1.itf.json"
printf 'PASS #13: BoundedChecker\nState 2: state invariant 0 violated.\nCheck the trace in: %s/counterexample1.tla, %s/MC1.out, %s/counterexample1.json, %s/counterexample1.itf.json E@12:00:00.400\nEXITCODE: ERROR (12)\n' "\$d" "\$d" "\$d" "\$d"
exit 12
EOF
chmod +x "$R/bin/apalache-mc"
git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm shim
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT PATH="$R/bin:$PATH" FPL_PLUGIN_ROOT="$PLUGIN" \
    FPL_APALACHE_ARGS="--inv=Inv Spec.tla" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
check "the harness fails with the checker's exit" 12 "$hrc"
check "and the trace was recorded on the way" 1 "$(rows)"
[ "$(rows)" = 1 ] || { echo "      harness output:"; tail -15 "$ROOT/harness.log" | sed 's/^/      /'; }
mkapalache
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT PATH="$R/bin:$PATH" FPL_PLUGIN_ROOT="$PLUGIN" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
check "without FPL_APALACHE_ARGS the checker is not invoked at all" 0 "$(rows)"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
