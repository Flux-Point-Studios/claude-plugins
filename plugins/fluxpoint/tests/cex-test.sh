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

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
