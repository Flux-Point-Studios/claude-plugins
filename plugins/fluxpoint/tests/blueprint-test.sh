#!/usr/bin/env bash
# Blueprint conformance: the builder may not hand the chain a shape the
# validator rejects.
#
# The property is narrow. A validator can be proved correct and still lose
# the funds it guards, because the agent that attacks a protocol calls the
# transaction builder rather than the validator, and the validator signs
# whatever that builder constructs. `plutus.json` is the one artifact both
# sides can be held to.
#
# PROVENANCE OF THE FIXTURES, since a conformance checker verified against
# its author's idea of the format proves nothing:
#   * The blueprints are REAL, taken from the aiken compiler's own repository
#     (`examples/hello_world`, built by Aiken v1.1.0, and the `.else` and
#     `Data` shapes from `examples/gift_card`, built by v1.1.15). Only the
#     `compiledCode` strings are trimmed, which this script never reads.
#   * Every hex payload was encoded by `cbor2`, an independent CBOR
#     implementation, and the reader in blueprint-guard.py was checked to
#     agree with it on 24 values covering constructors under all three tag
#     ranges, bignums, indefinite-length arrays, maps and chunked bytes.
#   * The list and map schemas are the one part NOT captured: these small
#     examples emit only constructor, bytes and integer, so those two are
#     written from the CIP-57 definition and are labelled where they appear.
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
BG="$PLUGIN/scripts/blueprint-guard.py"
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

ok()  { printf 'PASS  %-56s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-56s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

bg()    { "$FPL_PY" "$BG" --root "$R" "$@"; }
rc_of() { bg "$@" >/dev/null 2>&1; echo $?; }

mkrepo() { rm -rf "$R"; mkdir -p "$R"; cd "$R" || exit 1; git init -q -b main; }
commit() { git add -A; git -c user.email=t@t -c user.name=t commit -qm "${1:-x}"; }

# ---- the real hello_world blueprint (Aiken v1.1.0), compiledCode trimmed ----
write_hello() {
  cat >"$R/plutus.json" <<'EOF'
{
  "preamble": {
    "title": "aiken-lang/hello_world",
    "version": "1.0.0",
    "plutusVersion": "v3",
    "compiler": { "name": "Aiken", "version": "v1.1.0+9407b67" }
  },
  "validators": [
    {
      "title": "hello_world.hello_world.spend",
      "datum": { "title": "datum", "schema": { "$ref": "#/definitions/hello_world~1Datum" } },
      "redeemer": { "title": "redeemer", "schema": { "$ref": "#/definitions/hello_world~1Redeemer" } },
      "compiledCode": "5901",
      "hash": "167f56e1b5de377df88962340a0461158e68d4b6caaea9d27c9d71e5"
    },
    {
      "title": "hello_world.hello_world.else",
      "compiledCode": "5901",
      "hash": "167f56e1b5de377df88962340a0461158e68d4b6caaea9d27c9d71e5"
    }
  ],
  "definitions": {
    "ByteArray": { "dataType": "bytes" },
    "hello_world/Datum": {
      "title": "Datum",
      "anyOf": [ { "title": "Datum", "dataType": "constructor", "index": 0,
                   "fields": [ { "title": "owner", "$ref": "#/definitions/ByteArray" } ] } ]
    },
    "hello_world/Redeemer": {
      "title": "Redeemer",
      "anyOf": [ { "title": "Redeemer", "dataType": "constructor", "index": 0,
                   "fields": [ { "title": "msg", "$ref": "#/definitions/ByteArray" } ] } ]
    }
  }
}
EOF
}

# ---- the real gift_card shapes: a sum type, an opaque Data, an empty schema --
write_gift() {
  cat >"$R/plutus.json" <<'EOF'
{
  "preamble": { "title": "gift_card", "plutusVersion": "v3",
                "compiler": { "name": "Aiken", "version": "v1.1.15+8c55971" } },
  "validators": [
    {
      "title": "multi.redeem.spend",
      "datum": { "title": "datum", "schema": { "$ref": "#/definitions/multi~1SpendTokenName" } },
      "redeemer": { "title": "_r", "schema": { "$ref": "#/definitions/Data" } },
      "compiledCode": "5901", "hash": "aa"
    },
    {
      "title": "multi.redeem.mint",
      "redeemer": { "title": "rdmr", "schema": { "$ref": "#/definitions/multi~1Action" } },
      "compiledCode": "5901", "hash": "aa"
    },
    { "title": "multi.redeem.else", "redeemer": { "schema": {} },
      "compiledCode": "5901", "hash": "aa" }
  ],
  "definitions": {
    "ByteArray": { "dataType": "bytes" },
    "Data": { "title": "Data", "description": "Any Plutus data." },
    "Int": { "dataType": "integer" },
    "multi/SpendTokenName": { "title": "SpendTokenName", "dataType": "bytes" },
    "multi/Action": {
      "title": "Action",
      "anyOf": [
        { "title": "Mint", "dataType": "constructor", "index": 0,
          "fields": [ { "$ref": "#/definitions/Int" } ] },
        { "title": "Burn", "dataType": "constructor", "index": 1, "fields": [] }
      ]
    }
  }
}
EOF
}

# ---- list and map: CIP-57 constructs the small examples do not exercise -----
write_collections() {
  cat >"$R/plutus.json" <<'EOF'
{
  "preamble": { "title": "collections", "plutusVersion": "v3" },
  "validators": [
    { "title": "pool.spend",
      "datum": { "title": "datum", "schema": { "$ref": "#/definitions/pool~1Datum" } },
      "compiledCode": "5901", "hash": "aa" },
    { "title": "book.spend",
      "datum": { "title": "datum", "schema": { "$ref": "#/definitions/book~1Datum" } },
      "compiledCode": "5901", "hash": "aa" }
  ],
  "definitions": {
    "Int": { "dataType": "integer" },
    "ByteArray": { "dataType": "bytes" },
    "List$Int": { "dataType": "list", "items": { "$ref": "#/definitions/Int" } },
    "Map$ByteArray_Int": { "dataType": "map",
      "keys": { "$ref": "#/definitions/ByteArray" },
      "values": { "$ref": "#/definitions/Int" } },
    "pool/Datum": { "title": "Datum", "anyOf": [ { "title": "Datum",
      "dataType": "constructor", "index": 0,
      "fields": [ { "title": "amounts", "$ref": "#/definitions/List$Int" } ] } ] },
    "book/Datum": { "title": "Datum", "anyOf": [ { "title": "Datum",
      "dataType": "constructor", "index": 0,
      "fields": [ { "title": "balances", "$ref": "#/definitions/Map$ByteArray_Int" } ] } ] }
  }
}
EOF
}

# Every payload below was encoded by cbor2 and agreed with by this reader.
GOOD_DATUM=d87981581cabababababababababababababababababababababababababababab
INDEF_DATUM=d8799f581cababababababababababababababababababababababababababababff
INT_WHERE_BYTES=d87981182a
WRONG_INDEX=d87a8141ab
TWO_FIELDS=d8798241ab41cd
BARE_BYTES=41ab
MINT_42=d87981182a
BURN=d87a80
MINT_BYTES=d879814101
BURN_STRAY=d87a8101
CONSTR_7=d9050080
LIST_OK=d8798183010203
LIST_BAD=d8798182014102
MAP_OK=d87981a241aa0141bb02
MAP_BAD=d87981a141aa4101

conform() { printf '%s\n' "$2" | bg --conform --validator "$1" --purpose "${3:-datum}"; }
rc_conform() { conform "$@" >/dev/null 2>&1; echo $?; }

# ================= 1. dormant without a build ==============================
mkrepo; printf 'x\n' >app.py; commit
check "no plutus.json: check exits 0" 0 "$(rc_of --check)"
case "$(bg --scan)" in *"nothing built"*) ok "and scan says nothing was built" "dormant" ;;
  *) bad "and scan says nothing was built" "$(bg --scan)" ;; esac

# ================= 2. --scan reads a real blueprint ========================
mkrepo; write_hello; commit
out="$(bg --scan)"
case "$out" in *"hello_world.hello_world.spend"*"datum: typed"*"redeemer: typed"*)
  ok "a typed validator is reported as typed" "typed" ;;
  *) bad "a typed validator is reported as typed" "${out:0:70}" ;; esac
case "$out" in *"2 typed schema(s)"*) ok "and the tally counts both purposes" "2" ;;
  *) bad "and the tally counts both purposes" "${out##*$'\n'}" ;; esac
case "$out" in *"hello_world.hello_world.else"*"datum: none"*)
  ok "the .else handler declares no datum, and says so" "none" ;;
  *) bad "the .else handler declares no datum, and says so" "not reported" ;; esac

mkrepo; write_gift; commit
out="$(bg --scan)"
case "$out" in *"OPAQUE Data"*) ok "an untyped Data redeemer is named as opaque" "opaque" ;;
  *) bad "an untyped Data redeemer is named as opaque" "${out:0:70}" ;; esac
case "$out" in *"this gate is blind"*) ok "and the scan says the gate is blind there" "said" ;;
  *) bad "and the scan says the gate is blind there" "silent" ;; esac

# ================= 3. --conform against the real schema ====================
mkrepo; write_hello; commit
check "the datum the validator declares conforms" 0 \
  "$(rc_conform hello_world.hello_world.spend "$GOOD_DATUM")"
# Real Plutus tooling emits indefinite-length arrays; a reader that handles
# only the definite form would refuse every datum a wallet actually builds.
check "the same datum in indefinite-length form conforms" 0 \
  "$(rc_conform hello_world.hello_world.spend "$INDEF_DATUM")"

for name in INT_WHERE_BYTES WRONG_INDEX TWO_FIELDS BARE_BYTES; do
  eval "hx=\$$name"
  check "a $name datum is refused" 1 \
    "$(rc_conform hello_world.hello_world.spend "$hx")"
done
# The diagnostic is the point: every Aiken record is a one-alternative anyOf,
# so collapsing it to "matches none of 1" would throw away the only useful
# sentence in the output.
err="$(conform hello_world.hello_world.spend "$INT_WHERE_BYTES" 2>&1 >/dev/null)"
case "$err" in *"datum.owner"*"expected bytes"*)
  ok "a wrong field type names the field and what was wanted" "datum.owner" ;;
  *) bad "a wrong field type names the field and what was wanted" "${err:0:70}" ;; esac
err="$(conform hello_world.hello_world.spend "$TWO_FIELDS" 2>&1 >/dev/null)"
case "$err" in *"takes 1 field(s), got 2"*) ok "a wrong arity names both counts" "1 vs 2" ;;
  *) bad "a wrong arity names both counts" "${err:0:70}" ;; esac

# ================= 4. a sum type reports the branch it meant ===============
mkrepo; write_gift; commit
check "Mint(42) conforms" 0 "$(rc_conform multi.redeem.mint "$MINT_42" redeemer)"
check "Burn conforms" 0 "$(rc_conform multi.redeem.mint "$BURN" redeemer)"
check "Mint carrying bytes is refused" 1 "$(rc_conform multi.redeem.mint "$MINT_BYTES" redeemer)"
err="$(conform multi.redeem.mint "$MINT_BYTES" redeemer 2>&1 >/dev/null)"
case "$err" in *"expected an integer"*)
  ok "and the report is the matching branch's reason" "integer" ;;
  *) bad "and the report is the matching branch's reason" "${err:0:70}" ;; esac
err="$(conform multi.redeem.mint "$BURN_STRAY" redeemer 2>&1 >/dev/null)"
case "$err" in *"takes 0 field(s), got 1"*) ok "a variant with a stray field names it" "0 vs 1" ;;
  *) bad "a variant with a stray field names it" "${err:0:70}" ;; esac
err="$(conform multi.redeem.mint "$CONSTR_7" redeemer 2>&1 >/dev/null)"
case "$err" in *"matches none of the 2 alternative(s)"*"indices 0, 1"*)
  ok "a constructor no variant claims lists the ones offered" "0, 1" ;;
  *) bad "a constructor no variant claims lists the ones offered" "${err:0:70}" ;; esac

# An opaque schema accepts anything, and says how much rode through on that.
check "anything conforms to an opaque Data redeemer" 0 \
  "$(rc_conform multi.redeem.spend "$BURN_STRAY" redeemer)"
case "$(conform multi.redeem.spend "$BURN_STRAY" redeemer)" in *"opaque field(s) unchecked"*)
  ok "and a conforming value says what went unchecked" "counted" ;;
  *) bad "and a conforming value says what went unchecked" "silent" ;; esac

# ================= 5. lists and maps (CIP-57, not captured) ================
mkrepo; write_collections; commit
check "a list of the declared element type conforms" 0 "$(rc_conform pool.spend "$LIST_OK")"
check "a list with one wrong element is refused" 1 "$(rc_conform pool.spend "$LIST_BAD")"
err="$(conform pool.spend "$LIST_BAD" 2>&1 >/dev/null)"
case "$err" in *"amounts[1]"*) ok "and the index of the bad element is named" "amounts[1]" ;;
  *) bad "and the index of the bad element is named" "${err:0:70}" ;; esac
check "a map of the declared key and value types conforms" 0 "$(rc_conform book.spend "$MAP_OK")"
check "a map with a wrong value type is refused" 1 "$(rc_conform book.spend "$MAP_BAD")"

# ================= 6. what is not Plutus data at all =======================
mkrepo; write_hello; commit
for label in "63616263:a text string" "fb3ff8000000000000:a float" \
             "d8798141ab41cd:trailing bytes" "d879:a truncated value" \
             "d82a01:an unknown tag" "abc:odd-length hex"; do
  hx="${label%%:*}"; what="${label#*:}"
  check "$what is refused, never decoded" 1 \
    "$(rc_conform hello_world.hello_world.spend "$hx")"
done
err="$(conform hello_world.hello_world.spend 63616263 2>&1 >/dev/null)"
case "$err" in *"NOT PLUTUS DATA"*) ok "and is reported as undecodable, not as non-conforming" "named" ;;
  *) bad "and is reported as undecodable, not as non-conforming" "${err:0:60}" ;; esac
check "an empty corpus is exit 2, never a pass" 2 \
  "$(printf '' | bg --conform --validator hello_world.hello_world.spend >/dev/null 2>&1; echo $?)"
check "a validator the blueprint does not have is refused" 1 \
  "$(rc_conform nosuch.spend "$GOOD_DATUM")"

# ================= 7. --check: the gate ====================================
mkrepo; write_hello; commit
check "a blueprint with no manifest does not block" 0 "$(rc_of --check)"
err="$(bg --check 2>&1 >/dev/null)"
case "$err" in *"nothing"*"holds the builder to it"*)
  ok "but it says nothing holds the builder to it" "said" ;;
  *) bad "but it says nothing holds the builder to it" "${err:0:60}" ;; esac

arm() {  # $1 = producer command
  cat >"$R/.fluxpoint-blueprint.json" <<EOF
{ "version": 1,
  "corpora": [ { "validator": "hello_world.hello_world.spend", "purpose": "datum",
                 "produce": "$1" } ],
  "waived": { "hello_world.hello_world.else": "the else handler declares no datum or redeemer schema at all" } }
EOF
  commit armed >/dev/null
}

mkrepo; write_hello
arm "printf '%s\\n%s\\n' $GOOD_DATUM $INDEF_DATUM"
check "a corpus the blueprint accepts is green" 0 "$(rc_of --check)"
case "$(bg --check)" in *"2 encoded value(s) conform"*) ok "and it counts what it checked" "2" ;;
  *) bad "and it counts what it checked" "$(bg --check | tail -1)" ;; esac

mkrepo; write_hello
arm "printf '%s\\n%s\\n' $GOOD_DATUM $INT_WHERE_BYTES"
check "one bad datum in the corpus is RED" 1 "$(rc_of --check)"
err="$(bg --check 2>&1 >/dev/null)"
case "$err" in *"line 2"*"datum.owner"*) ok "and it names the line and the field" "line 2" ;;
  *) bad "and it names the line and the field" "${err:0:70}" ;; esac

mkrepo; write_hello
arm "exit 3"
check "a producer that fails is RED, not an empty pass" 1 "$(rc_of --check)"
mkrepo; write_hello
arm "true"
check "a producer printing nothing is RED" 1 "$(rc_of --check)"
err="$(bg --check 2>&1 >/dev/null)"
case "$err" in *"corpus of zero checks nothing"*) ok "and says why" "said" ;;
  *) bad "and says why" "${err:0:60}" ;; esac

# A manifest naming something the blueprint does not have is a corpus that
# silently checks nothing, which is the failure this whole gate exists for.
mkrepo; write_hello
cat >"$R/.fluxpoint-blueprint.json" <<EOF
{ "version": 1, "corpora": [ { "validator": "gone.spend", "purpose": "datum",
  "produce": "printf '%s\\n' $GOOD_DATUM" } ] }
EOF
commit stale
check "a manifest naming a missing validator is RED" 1 "$(rc_of --check)"
case "$(bg --check 2>&1 >/dev/null)" in *"no validator titled"*) ok "and names it" "named" ;;
  *) bad "and names it" "silent" ;; esac

mkrepo; write_hello
cat >"$R/.fluxpoint-blueprint.json" <<EOF
{ "version": 1, "corpora": [ { "validator": "hello_world.hello_world.spend",
  "purpose": "parameters", "produce": "printf '%s\\n' $GOOD_DATUM" } ] }
EOF
commit purpose
check "a purpose outside datum and redeemer is RED" 1 "$(rc_of --check)"
printf '{not json\n' >"$R/.fluxpoint-blueprint.json"; commit bad
check "a malformed manifest is RED, never a pass" 1 "$(rc_of --check)"

# ================= 8. a blueprint no reader can trust ======================
mkrepo; write_hello
"$FPL_PY" - "$R/plutus.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
del d["definitions"]["hello_world/Datum"]
json.dump(d, open(p, "w"), indent=2)
PY
arm "printf '%s\\n' $GOOD_DATUM"
check "a \$ref naming no definition is RED" 1 "$(rc_of --check)"
case "$(bg --check 2>&1 >/dev/null)" in *"names no definition"*) ok "and says which ref" "named" ;;
  *) bad "and says which ref" "silent" ;; esac

# ================= 9. declared but unchecked, and waivers ==================
# The redeemer is typed and no corpus covers it. That is a gap worth saying
# out loud every run, and a failure only once a repo asks for it: reddening
# by default would make this unadoptable in the repos that need it most.
mkrepo; write_hello
arm "printf '%s\\n' $GOOD_DATUM"
case "$(bg --check)" in *"declared but unchecked"*"hello_world.hello_world.spend/redeemer"*)
  ok "an uncovered typed schema is named every run" "named" ;;
  *) bad "an uncovered typed schema is named every run" "silent" ;; esac
check "and does not fail the gate on its own" 0 "$(rc_of --check)"
"$FPL_PY" - "$R/.fluxpoint-blueprint.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["requireTyped"] = True
json.dump(d, open(p, "w"), indent=2)
PY
commit strict
check "requireTyped turns the same gap RED" 1 "$(rc_of --check)"

mkrepo; write_hello
cat >"$R/.fluxpoint-blueprint.json" <<EOF
{ "version": 1,
  "corpora": [ { "validator": "hello_world.hello_world.spend", "purpose": "datum",
                 "produce": "printf '%s\\n' $GOOD_DATUM" } ],
  "waived": { "hello_world.hello_world.else": "n/a" } }
EOF
commit waiver
check "a waiver without a real reason is RED" 1 "$(rc_of --check)"
case "$(bg --check 2>&1 >/dev/null)" in *"characters saying why"*) ok "and says what it needs" "said" ;;
  *) bad "and says what it needs" "silent" ;; esac

# ================= 10. the scaffolded harness runs it ======================
mkrepo; write_hello
mkdir -p "$R/scripts"
cp "$PLUGIN/templates/harness.sh" "$R/scripts/harness.sh"; chmod +x "$R/scripts/harness.sh"
arm "printf '%s\\n' $INT_WHERE_BYTES"
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT FPL_PLUGIN_ROOT="$PLUGIN" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
check "the harness fails on a datum the blueprint refuses" 1 "$hrc"
case "$(cat "$ROOT/harness.log")" in *"blueprint-guard"*) ok "and the gate is what reported it" "named" ;;
  *) bad "and the gate is what reported it" "$(tail -3 "$ROOT/harness.log")" ;; esac
arm "printf '%s\\n' $GOOD_DATUM"
( cd "$R" && env -u CLAUDE_PLUGIN_ROOT FPL_PLUGIN_ROOT="$PLUGIN" \
    bash scripts/harness.sh --full >"$ROOT/harness.log" 2>&1 ); hrc=$?
check "and passes once the encoder agrees" 0 "$hrc"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
