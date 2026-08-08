#!/usr/bin/env bash
# Mutation score: the judge of the judge.
#
# Every other gate asks whether the tests pass; this one asks whether they
# can fail. The cases below are weighted toward the two failures that would
# make it useless: a ratchet that lets the suite quietly get weaker, and a
# gate that goes red because an expensive job has not been re-run — the
# second being how a gate gets switched off, taking the harness with it.
#
# cargo-mutants is not installed here and would take minutes if it were, so
# measurements are fed through `--from`, which is the same path a CI job
# takes when it ran the tool in its own step.
set -uo pipefail

if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
MG="$PLUGIN/scripts/mutation-guard.py"
PG="$PLUGIN/scripts/proof-guard.py"
INBOX="$PLUGIN/scripts/inbox.py"
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

ok()  { printf 'PASS  %-58s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-58s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

mg() { "$FPL_PY" "$MG" --root "$R" "$@"; }
rc_of() { mg "$@" >/dev/null 2>&1; echo $?; }

mkrepo() {
  rm -rf "$R"; mkdir -p "$R/src"; cd "$R" || exit 1
  git init -q -b main
  printf 'fn a() {}\n' >src/lib.rs
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
  printf '{"version":1,"tool":"cargo-mutants"}' >"$R/.fluxpoint-mutation.json"
}
commit() { git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm "${1:-x}"; }

# Shaped like cargo-mutants' own outcomes.json: a list of outcomes, each
# with a summary and a scenario carrying the mutant.
outcomes() { # $1 = caught, $2 = missed, $3 = unviable
  "$FPL_PY" - "$1" "$2" "$3" >"$R/out.json" <<'PY'
import json, sys
caught, missed, unviable = (int(x) for x in sys.argv[1:4])
o = []
for i in range(caught):
    o.append({"summary": "CaughtMutant",
              "scenario": {"Mutant": {"file": "src/lib.rs", "line": i + 1,
                                      "replacement": "-> ()"}}})
for i in range(missed):
    o.append({"summary": "MissedMutant",
              "scenario": {"Mutant": {"file": "src/lib.rs", "line": 100 + i,
                                      "replacement": "replace a with ()"}}})
for i in range(unviable):
    o.append({"summary": "Unviable",
              "scenario": {"Mutant": {"file": "src/lib.rs", "line": 200 + i}}})
json.dump({"outcomes": o}, open("/dev/stdout", "w"))
PY
}

# ================= 1. the states it must tell apart ======================
mkrepo; rm "$R/.fluxpoint-mutation.json"
out="$(mg --check)"
check "no config: dormant and green" 0 "$?"
case "$out" in *dormant*) ok "and says so rather than passing in silence" "said" ;;
  *) bad "and says so rather than passing in silence" "${out:0:40}" ;; esac

mkrepo
out="$(mg --check 2>&1)"
check "declared but never measured: not armed, still green" 0 "$?"
case "$out" in *"NOT armed"*) ok "and names the unarmed state" "named" ;;
  *) bad "and names the unarmed state" "${out:0:40}" ;; esac

mkrepo
printf '{"version":1,"tool":"mutmut"}' >"$R/.fluxpoint-mutation.json"
out="$(mg --check 2>&1)"
check "a real tool this guard cannot parse is refused" 1 "$?"
case "$out" in *"does not parse it yet"*)
  ok "and is named rather than silently skipped" "named" ;;
  *) bad "and is named rather than silently skipped" "${out:0:40}" ;; esac

mkrepo
printf '{"version":1,"tool":"cargo-mutants","typo":1}' >"$R/.fluxpoint-mutation.json"
check "an unknown config field is fatal" 1 "$(rc_of --check)"
printf 'not json' >"$R/.fluxpoint-mutation.json"
out="$(mg --check 2>&1)"
case "$out" in *DISARMED*) ok "a malformed config says it is disarmed" "said" ;;
  *) bad "a malformed config says it is disarmed" "${out:0:40}" ;; esac

# ================= 2. recording, and what is scored ======================
mkrepo
outcomes 8 2 5
mg --measure --from "$R/out.json" >/dev/null 2>&1
check "a measurement records" 0 "$?"
score="$("$FPL_PY" -c '
import json,sys; print(json.load(open(sys.argv[1]))["mutation"]["score"])' \
  "$R/.fluxpoint-proof-baseline.json")"
check "unviable mutants are not scored (8/10, not 8/15)" "0.8" "$score"

# The shared baseline file must survive its siblings.
"$FPL_PY" "$PG" --root "$R" --baseline >/dev/null 2>&1
has() { "$FPL_PY" -c '
import json,sys; print("yes" if sys.argv[2] in json.load(open(sys.argv[1])) else "no")' \
  "$R/.fluxpoint-proof-baseline.json" "$1"; }
check "re-arming proof-guard preserves the mutation section" "yes" "$(has mutation)"
outcomes 8 2 5
mg --measure --from "$R/out.json" >/dev/null 2>&1
check "and re-measuring preserves proof-guard's counts" "yes" "$(has counts)"

# ================= 3. the ratchet, both directions =======================
mkrepo
outcomes 8 2 0
mg --measure --from "$R/out.json" >/dev/null 2>&1
outcomes 9 1 0
check "a stronger suite records without ceremony" 0 \
  "$(rc_of --measure --from "$R/out.json")"

mkrepo
outcomes 9 1 0
mg --measure --from "$R/out.json" >/dev/null 2>&1
outcomes 7 3 0
out="$(mg --measure --from "$R/out.json" 2>&1)"
check "a weaker suite is RED" 1 "$?"
case "$out" in *"score:"*) ok "and the drop is named" "named" ;;
  *) bad "and the drop is named" "${out:0:40}" ;; esac
case "$out" in *"no test noticed"*)
  ok "with the survivors listed as changes nothing caught" "listed" ;;
  *) bad "with the survivors listed as changes nothing caught" "${out:0:40}" ;; esac

# The case a ratio alone cannot see: delete a well-tested module and the
# score holds while absolute coverage shrinks.
mkrepo
outcomes 90 10 0     # score 0.9, 10 survivors
mg --measure --from "$R/out.json" >/dev/null 2>&1
outcomes 180 20 0    # score 0.9 still, but twice the survivors
check "a flat ratio with more survivors is RED" 1 \
  "$(rc_of --measure --from "$R/out.json")"

# The release valve costs a written reason.
check "accepting a weaker score without a reason is refused" 1 \
  "$(rc_of --measure --from "$R/out.json" --accept)"
check "with a real one it records" 0 \
  "$(rc_of --measure --from "$R/out.json" --accept --reason \
      "the module was split, so the survivor count is not comparable")"
"$FPL_PY" -c '
import json,sys
d = json.load(open(sys.argv[1]))["mutation"]
print("yes" if d.get("acceptedWeaker",{}).get("reason") else "no")' \
  "$R/.fluxpoint-proof-baseline.json" | grep -q yes \
  && ok "and the reason is kept in the committed record" "kept" \
  || bad "and the reason is kept in the committed record" "lost"

# ================= 4. staleness is loud, not fatal by default ============
mkrepo
outcomes 9 1 0
mg --measure --from "$R/out.json" >/dev/null 2>&1
check "a fresh measurement is green" 0 "$(rc_of --check)"

for i in $(seq 1 25); do printf 'fn f%s() {}\n' "$i" >>"$R/src/lib.rs"; commit "c$i" >/dev/null; done
out="$(mg --check 2>&1)"
check "a stale measurement does not fail the build by default" 0 "$?"
case "$out" in *STALE*) ok "but says how stale, loudly" "named" ;;
  *) bad "but says how stale, loudly" "${out:0:44}" ;; esac
check "and raises it for a person to schedule" 1 \
  "$("$FPL_PY" "$INBOX" --root "$R" --count)"

printf '{"version":1,"tool":"cargo-mutants","failWhenStale":true}' \
  >"$R/.fluxpoint-mutation.json"
check "a repo that asks for staleness to be fatal gets it" 1 "$(rc_of --check)"

printf '{"version":1,"tool":"cargo-mutants","maxStaleCommits":100}' \
  >"$R/.fluxpoint-mutation.json"
check "a repo that widens the window is green again" 0 "$(rc_of --check)"

# A measurement taken at a commit this repo no longer has cannot be related
# to the tree at all, and that is different from merely being old.
mkrepo
outcomes 9 1 0
mg --measure --from "$R/out.json" >/dev/null 2>&1
"$FPL_PY" - "$R/.fluxpoint-proof-baseline.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["mutation"]["headSha"] = "0" * 40
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
PY
out="$(mg --check 2>&1)"
case "$out" in *"no longer has"*)
  ok "a measurement from a vanished commit is reported" "reported" ;;
  *) bad "a measurement from a vanished commit is reported" "${out:0:44}" ;; esac

# ================= 5. a format this parser cannot read ===================
# The adapter was written from documentation, not from a run of the real
# tool, so the property that matters most is what happens when the shape is
# wrong: a parser that quietly scored 0 mutants would record a fake number
# and ratchet everything afterwards against it.
mkrepo
printf '{"results":[{"status":"killed"}]}' >"$R/wrong.json"
out="$(mg --measure --from "$R/wrong.json" 2>&1)"
check "an unrecognized outcomes shape fails, not scores zero" 1 "$?"
case "$out" in *"no scored mutants"*) ok "and says nothing was measured" "said" ;;
  *) bad "and says nothing was measured" "${out:0:44}" ;; esac
[ -f "$R/.fluxpoint-proof-baseline.json" ] \
  && bad "and records nothing to ratchet against later" "wrote a baseline" \
  || ok "and records nothing to ratchet against later" "untouched"

# ================= 6. excusing a mutant ratchets too =====================
mkrepo
printf '#[mutants::skip]\nfn skipped() {}\n' >>"$R/src/lib.rs"
commit skip >/dev/null
out="$("$FPL_PY" "$PG" --root "$R" --scan 2>&1)"
case "$out" in *mutants_skip*)
  ok "#[mutants::skip] is counted as an escape hatch" "counted" ;;
  *) bad "#[mutants::skip] is counted as an escape hatch" "invisible" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
