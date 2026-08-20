#!/usr/bin/env bash
# On-chain budget gate, against real blueprint shapes.
#
# The property under test: a proof of correctness says nothing about whether
# the script can be submitted, so the two failure modes that matter are a
# limit that is not enforced and a limit that is silently assumed met. Both
# get cases here.
set -uo pipefail

# Interpreter name differs by platform: `python3` on Linux/macOS, `python` on a
# standard Windows install. Resolve once rather than hardcoding either.
if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
# Force UTF-8 on every embedded interpreter's stdio. Without it Windows writes
# cp1252, so a header like "## Plan --" emitted with an em-dash comes back as
# 0x97 and every consumer that greps for the UTF-8 bytes silently misses it.
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
PB=""$FPL_PY" $PLUGIN/scripts/plutus-budget.py --root"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

# A blueprint with one validator of exactly N bytes of compiled code.
blueprint() { # bytes [second_bytes]
  "$FPL_PY" - "$@" <<'PY'
import json, sys
def v(title, nbytes, fill="ab"):
    return {"title": title, "compiledCode": fill * nbytes,
            "hash": "00" * 28, "redeemer": {"title": "_r"}}
vs = [v("vault.spend", int(sys.argv[1]))]
if len(sys.argv) > 2:
    vs.append(v("mint.receipt", int(sys.argv[2]), "cd"))
json.dump({"preamble": {"title": "t/v", "plutusVersion": "v3"},
           "validators": vs}, open("plutus.json", "w"))
PY
}

mkrepo() { rm -rf "$ROOT/r"; mkdir -p "$ROOT/r"; cd "$ROOT/r" || exit 1; }

# ================= 1. dormant with nothing built =========================
mkrepo
out="$($PB . --check 2>&1)"
check "no plutus.json: exits 0" 0 "$?"
case "$out" in *"nothing built to measure"*) ok "no plutus.json: says why" "dormant" ;;
  *) bad "no plutus.json: says why" "$out" ;; esac

# ================= 2. a script that comfortably fits =====================
mkrepo; blueprint 4000
out="$($PB . --check 2>&1)"; rc=$?
check "a 4000 B script passes" 0 "$rc"
case "$out" in *"24.4% of maxTxSize"*) ok "reports the percentage of the limit" "24.4%" ;;
  *) bad "reports the percentage of the limit" "${out:0:70}" ;; esac

# ================= 3. a script that cannot be submitted ==================
# The whole point: aiken check is green on this, and it can never go on chain.
mkrepo; blueprint 17000
err="$($PB . --check 2>&1 >/dev/null)"; rc=$?
check "a script over maxTxSize fails" 1 "$rc"
case "$err" in *"maxTxSize 16384"*) ok "names the protocol limit it broke" "reported" ;;
  *) bad "names the protocol limit it broke" "${err:0:70}" ;; esac
case "$err" in *vault.spend*) ok "names the validator" "reported" ;;
  *) bad "names the validator" "${err:0:70}" ;; esac

# The protocol limit is not a preference, so it holds with no budget file.
[ -f .fluxpoint-budget.json ] && bad "no budget file was needed" "one exists" \
  || ok "protocol limit enforced with no budget configured" "unconditional"

# ================= 4. one script, several purposes =======================
# Aiken repeats the same compiledCode per purpose. Counting those separately
# would report a validator twice and imply a size that never goes on chain.
mkrepo
"$FPL_PY" - <<'PY'
import json
code = "ab" * 5000
vs = [{"title": f"vault.{p}", "compiledCode": code, "hash": "00" * 28}
      for p in ("spend", "mint", "withdraw")]
json.dump({"preamble": {"plutusVersion": "v3"}, "validators": vs},
          open("plutus.json", "w"))
PY
out="$($PB . --report 2>&1)"
n="$(printf '%s\n' "$out" | grep -c ' B  ')"
check "a multi-purpose validator is counted once" 1 "$n"
case "$out" in *"spend / vault.mint / vault.withdraw"*)
    ok "the shared script lists every purpose" "reported" ;;
  *) bad "the shared script lists every purpose" "${out:0:80}" ;; esac

# ================= 5. project headroom, over but legal ===================
mkrepo; blueprint 9000
printf '{"maxScriptBytes": 8000}\n' >.fluxpoint-budget.json
err="$($PB . --check 2>&1 >/dev/null)"; rc=$?
check "under maxTxSize but over project budget fails" 1 "$rc"
case "$err" in *"project budget 8000"*) ok "budget failure is named as a budget" "reported" ;;
  *) bad "budget failure is named as a budget" "${err:0:70}" ;; esac

printf '{"maxScriptBytes": 10000}\n' >.fluxpoint-budget.json
$PB . --check >/dev/null 2>&1
check "within the project budget passes" 0 "$?"

# ================= 6. unmeasured is not met ==============================
# An ex-unit budget nobody measured must not read as one that was met.
mkrepo; blueprint 4000
out="$($PB . --check 2>&1)"
case "$out" in *"execution units NOT measured"*) ok "unmeasured ex-units are called out" "reported" ;;
  *) bad "unmeasured ex-units are called out" "${out:0:70}" ;; esac
case "$out" in *"16,500,000"*) ok "says what to measure against" "limits printed" ;;
  *) bad "says what to measure against" "${out:0:70}" ;; esac
case "$out" in *"no maxScriptBytes"*) ok "missing headroom target is called out" "reported" ;;
  *) bad "missing headroom target is called out" "${out:0:70}" ;; esac

# ================= 7. measured ex-units are checked ======================
mkrepo; blueprint 4000
printf '{"exUnits": {"mem": 17000000, "steps": 1000}}\n' >.fluxpoint-budget.json
err="$($PB . --check 2>&1 >/dev/null)"; rc=$?
check "measured mem over the protocol limit fails" 1 "$rc"
case "$err" in *maxTxExMem*) ok "names the ex-unit limit" "reported" ;;
  *) bad "names the ex-unit limit" "${err:0:70}" ;; esac

printf '{"exUnits": {"mem": 8250000, "steps": 5000000000}}\n' >.fluxpoint-budget.json
out="$($PB . --check 2>&1)"; rc=$?
check "measured ex-units within budget pass" 0 "$rc"
case "$out" in *"50.0% of 16,500,000"*) ok "reports ex-unit headroom" "50.0%" ;;
  *) bad "reports ex-unit headroom" "${out:0:70}" ;; esac

# ================= 8. real protocol parameters win =======================
# The constants in this file are a fallback, not the authority: a repo
# targeting a network with different limits passes its own params.
mkrepo; blueprint 9000
printf '{"maxTxSize": 8000, "maxTxExecutionUnits": {"memory": 100, "steps": 100}}\n' \
  >params.json
err="$($PB . --check --params params.json 2>&1 >/dev/null)"; rc=$?
check "a stricter maxTxSize from --params is honored" 1 "$rc"
case "$err" in *"maxTxSize 8000"*) ok "the supplied limit is the one enforced" "reported" ;;
  *) bad "the supplied limit is the one enforced" "${err:0:70}" ;; esac
out="$($PB . --report --params params.json 2>&1)"
case "$out" in *"limits from params.json"*) ok "names where the limits came from" "reported" ;;
  *) bad "names where the limits came from" "${out:0:70}" ;; esac

# cardano-cli has spelled the ex-unit fields both ways across eras.
mkrepo; blueprint 100
printf '{"maxTxExecutionUnits": {"exUnitsMem": 500, "exUnitsSteps": 900}}\n' >params.json
printf '{"exUnits": {"mem": 600}}\n' >.fluxpoint-budget.json
err="$($PB . --check --params params.json 2>&1 >/dev/null)"; rc=$?
check "the older exUnitsMem spelling is read" 1 "$rc"

# ================= 9. --report never gates ===============================
mkrepo; blueprint 17000
$PB . --report >/dev/null 2>&1
check "--report exits 0 on a script that cannot be submitted" 0 "$?"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
