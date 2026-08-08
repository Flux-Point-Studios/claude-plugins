#!/usr/bin/env bash
# Execution attestation: the exit code is minted by the hook, not typed by
# an agent.
#
# The property under test is narrow and load-bearing: a row exists if and
# only if a command the repo *declared* as a gate ran through the Bash tool,
# and the exit it records is the runtime's, not anyone's report of it. The
# laundering cases matter most — `|| true`, a pipe, a different flag — since
# each one changes the exit code the runtime reports and so must not be able
# to borrow a gate's name.
#
# Executed end to end: every case drives attest.py with a real hook payload
# and reads the resulting log, and the cross-check cases run record-run.py
# over a real run summary.
set -uo pipefail

if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
export PYTHONIOENCODING=utf-8

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
ATTEST="$PLUGIN/scripts/attest.py"
RECORD="$PLUGIN/scripts/record-run.py"
INBOX="$PLUGIN/scripts/inbox.py"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

newrepo() {
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/scripts"; cd "$ROOT/r" || exit 1
  git init -q -b main
  printf '#!/usr/bin/env bash\nexit 0\n' >scripts/harness.sh
  chmod +x scripts/harness.sh
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
}

gates() { printf '{"version":1,"gates":{"harness":"scripts/harness.sh --full"}}' \
  >"$ROOT/r/.fluxpoint-gates.json"; }

# A PostToolUse payload for the Bash tool, exactly as the runtime shapes it.
payload() { # $1 = command, $2 = exit code, $3 = stdout (optional)
  "$FPL_PY" - "$1" "$2" "${3:-}" <<'PY'
import json, sys
print(json.dumps({
    "session_id": "s1", "cwd": ".", "hook_event_name": "PostToolUse",
    "tool_name": "Bash", "tool_use_id": "toolu_x",
    "tool_input": {"command": sys.argv[1]},
    "tool_response": {"exit_code": int(sys.argv[2]), "stdout": sys.argv[3],
                      "stderr": ""},
}))
PY
}

rows() { [ -f "$ROOT/r/.claude/fluxpoint/attest.jsonl" ] \
  && grep -c . "$ROOT/r/.claude/fluxpoint/attest.jsonl" || echo 0; }
field() { # $1 = jsonl field of the last row
  "$FPL_PY" - "$ROOT/r/.claude/fluxpoint/attest.jsonl" "$1" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(rows[-1].get(sys.argv[2], "") if rows else "")
PY
}
rec() { payload "$@" | "$FPL_PY" "$ATTEST" --root "$ROOT/r" --record; }

# --- 1. dormant without a manifest --------------------------------------
newrepo
rec "scripts/harness.sh --full" 0 >/dev/null 2>&1
check "no manifest: nothing is attested" 0 "$(rows)"
check "no manifest: exits clean" 0 "$?"

# --- 2. a declared gate is attested with the runtime's exit -------------
newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
check "a declared gate is attested" 1 "$(rows)"
check "the recorded exit is the runtime's" 0 "$(field exit)"
check "the row names the gate" "harness" "$(field gate)"
[ -n "$(field attestId)" ] && ok "the row carries a citable attestId" "$(field attestId)" \
  || bad "the row carries a citable attestId" "empty"
[ -n "$(field headSha)" ] && ok "the row records the tree it judged" "headSha present" \
  || bad "the row records the tree it judged" "empty"

# A red gate is attested exactly as loudly as a green one.
rec "scripts/harness.sh --full" 1 >/dev/null
check "a red gate is attested too" 2 "$(rows)"
check "the red exit is recorded, not smoothed" 1 "$(field exit)"

# --- 3. invocation spelling that means the same thing -------------------
newrepo; gates
rec "bash scripts/harness.sh   --full" 0 >/dev/null
check "an interpreter prefix still matches" 1 "$(rows)"
rec "./scripts/harness.sh --full" 0 >/dev/null
check "a ./ prefix still matches" 2 "$(rows)"

# --- 4. laundering: anything that changes the exit must not match -------
# This is the case the whole file exists for. `|| true` reports 0 for a red
# harness; a pipe reports the last stage's exit. If either borrowed the
# gate's name, the log would attest a green that never happened.
newrepo; gates
for evil in \
  "scripts/harness.sh --full || true" \
  "scripts/harness.sh --full | tail -1" \
  "scripts/harness.sh --full 2>/dev/null" \
  "scripts/harness.sh --changed README.md" \
  "scripts/harness.sh --full; true" \
  "true # scripts/harness.sh --full"; do
  rec "$evil" 0 >/dev/null 2>&1
done
check "no laundered invocation is attested" 0 "$(rows)"

# --- 5. an unreadable exit code is said out loud, never assumed ---------
newrepo; gates
err="$("$FPL_PY" - <<'PY' | "$FPL_PY" "$ATTEST" --root "$ROOT/r" --record 2>&1 >/dev/null
import json
print(json.dumps({"session_id": "s1", "tool_name": "Bash",
                  "tool_input": {"command": "scripts/harness.sh --full"},
                  "tool_response": "ran it"}))
PY
)"
case "$err" in *"no integer tool_response.exit_code"*)
  ok "an unreadable exit is reported, not invented" "said" ;;
  *) bad "an unreadable exit is reported, not invented" "got: ${err:0:40}" ;; esac
check "and nothing is attested for it" 0 "$(rows)"

# --- 6. a malformed manifest disarms loudly, never silently -------------
newrepo
printf '{"version":1,"gates":{"harness":' >"$ROOT/r/.fluxpoint-gates.json"
err="$(rec "scripts/harness.sh --full" 0 2>&1 >/dev/null)"
case "$err" in *DISARMED*) ok "a malformed manifest says it is disarmed" "said" ;;
  *) bad "a malformed manifest says it is disarmed" "got: ${err:0:40}" ;; esac

printf '{"version":1,"gates":{"harness":"x"},"gate":"typo"}' >"$ROOT/r/.fluxpoint-gates.json"
err="$(rec "x" 0 2>&1 >/dev/null)"
case "$err" in *"unknown field 'gate'"*) ok "a misspelled manifest field is fatal" "rejected" ;;
  *) bad "a misspelled manifest field is fatal" "got: ${err:0:40}" ;; esac

printf '{"version":2,"gates":{"harness":"x"}}' >"$ROOT/r/.fluxpoint-gates.json"
err="$(rec "x" 0 2>&1 >/dev/null)"
case "$err" in *"version must be 1"*) ok "an unknown manifest version is fatal" "rejected" ;;
  *) bad "an unknown manifest version is fatal" "got: ${err:0:40}" ;; esac

printf '{"version":1,"gates":{"Harness":"x"}}' >"$ROOT/r/.fluxpoint-gates.json"
err="$(rec "x" 0 2>&1 >/dev/null)"
case "$err" in *"lowercase kebab-case"*) ok "a gate name must be referenceable" "rejected" ;;
  *) bad "a gate name must be referenceable" "got: ${err:0:40}" ;; esac

# --- 7. a corrupted log is a hard error, never a quiet shrink -----------
newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
printf 'not json\n' >>"$ROOT/r/.claude/fluxpoint/attest.jsonl"
err="$("$FPL_PY" "$ATTEST" --root "$ROOT/r" --list 2>&1 >/dev/null)"; rc=$?
case "$err$rc" in *"is not valid JSON"*) ok "a corrupted log fails hard on read" "hard error" ;;
  *) bad "a corrupted log fails hard on read" "rc=$rc ${err:0:40}" ;; esac

# --- 8. the cross-check, end to end through record-run.py --------------
summary() { # $1 = claimed exit
  "$FPL_PY" - "$1" <<'PY'
import json, sys
print(json.dumps({
    "campaign": "c", "outcome": "COMPLETE",
    "results": {"gate": {"exit": int(sys.argv[1]),
                         "command": "scripts/harness.sh --full", "tail": "ok"}},
    "contracts": {"gate": "HarnessCheckV1"},
    "provenance": [{"node": "gate", "status": "OK", "detail": ""}],
}))
PY
}
runrec() { summary "$1" | "$FPL_PY" "$RECORD" --run-id "$2" --graph WORK.md \
  --root "$ROOT/r" --state-dir "$ROOT/r/.claude/fluxpoint/runs" 2>&1; }

newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
out="$(runrec 0 wf-a)"
case "$out" in *"[ATTESTED]"*) ok "a claim matching the log is ATTESTED" "attested" ;;
  *) bad "a claim matching the log is ATTESTED" "got: ${out:0:60}" ;; esac

# The case this layer exists for: the gate really exited 1, the node says 0.
newrepo; gates
rec "scripts/harness.sh --full" 1 >/dev/null
out="$(runrec 0 wf-b)"
case "$out" in *"[MISMATCH]"*) ok "claiming green over an attested red is caught" "caught" ;;
  *) bad "claiming green over an attested red is caught" "got: ${out:0:60}" ;; esac
case "$out" in *"most recent exited 1"*) ok "the mismatch names the real exit" "named" ;;
  *) bad "the mismatch names the real exit" "got: ${out:0:60}" ;; esac
check "a mismatch raises an inbox item" 1 \
  "$("$FPL_PY" "$INBOX" --root "$ROOT/r" --count)"
# Warn mode: loud and filed, but the run is not rewritten yet. The corpus
# earns the enforcing version; a check that starts by failing runs gets
# switched off before it has established what normal looks like.
case "$out" in *"| COMPLETE |"*) ok "warn mode leaves the outcome alone" "COMPLETE" ;;
  *) bad "warn mode leaves the outcome alone" "got: ${out:0:60}" ;; esac
case "$out" in *"CONTRADICT the attested"*) ok "but the Evidence row says so" "in the row" ;;
  *) bad "but the Evidence row says so" "got: ${out:0:60}" ;; esac

# No attestation at all is distinct from a contradiction, and is not a failure.
newrepo; gates
out="$(runrec 0 wf-c)"
case "$out" in *"[UNATTESTED]"*) ok "an unattested gate claim is its own state" "unattested" ;;
  *) bad "an unattested gate claim is its own state" "got: ${out:0:60}" ;; esac
check "and raises nothing for a person" 0 \
  "$("$FPL_PY" "$INBOX" --root "$ROOT/r" --count)"

# A repo that declared no gates is not nagged about attestation at all.
newrepo
out="$(runrec 0 wf-d)"
case "$out" in *ATTEST*|*UNATTESTED*) bad "no manifest: record-run stays quiet" "spoke" ;;
  *) ok "no manifest: record-run stays quiet" "silent" ;; esac

# The artifact carries the tally, so provenance can be read after the fact.
newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
runrec 0 wf-e >/dev/null
t="$("$FPL_PY" - "$ROOT/r/.claude/fluxpoint/runs/wf-e.json" <<'PY'
import json, sys
print((json.load(open(sys.argv[1])).get("attestation") or {}).get("tally", {}).get("attested"))
PY
)"
check "the run artifact records the attestation tally" 1 "$t"

# ================= 9. prove: tiers are held to the log ===================
# A node declaring `verify: prove:<gate>` opted into being checked against
# the hook's own record. Nodes that merely happen to match a declared gate
# stay observed rather than enforced — the warn-mode lesson still holds for
# everyone who did not opt in.
prove_summary() { # $1 = claimed exit, $2 = cited attestId
  "$FPL_PY" - "$1" "$2" <<'PYEOF'
import json, sys
print(json.dumps({
    "campaign": "c", "outcome": "COMPLETE",
    "results": {"gate": {"gate": "harness", "exit": int(sys.argv[1]),
                         "attestId": sys.argv[2]}},
    "contracts": {"gate": "ExecutionV1"},
    "prove": {"gate": "harness"},
    "provenance": [{"node": "gate", "status": "OK", "detail": ""}],
}))
PYEOF
}
runprove() { prove_summary "$1" "$2" | "$FPL_PY" "$RECORD" --run-id "$3" \
  --graph WORK.md --root "$ROOT/r" --state-dir "$ROOT/r/.claude/fluxpoint/runs" 2>&1; }

newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
ATT="$(field attestId)"
out="$(runprove 0 "$ATT" wf-p1)"
case "$out" in *"[ATTESTED]"*) ok "a prove: node citing a real attestation passes" "attested" ;;
  *) bad "a prove: node citing a real attestation passes" "${out:0:56}" ;; esac
case "$out" in *"| COMPLETE |"*) ok "and the run stays COMPLETE" "COMPLETE" ;;
  *) bad "and the run stays COMPLETE" "${out:0:56}" ;; esac

# The case this layer exists for: the gate really exited 1, the node claims
# 0, and it cites the very attestation that says otherwise.
newrepo; gates
rec "scripts/harness.sh --full" 1 >/dev/null
ATT="$(field attestId)"
out="$(runprove 0 "$ATT" wf-p2)"
case "$out" in *TAMPERED-EXECUTION*)
  ok "claiming green over the attestation it cites is TAMPERED" "caught" ;;
  *) bad "claiming green over the attestation it cites is TAMPERED" "${out:0:56}" ;; esac
case "$out" in *"| COMPLETE |"*)
  bad "and the run may not be filed COMPLETE" "filed clean" ;;
  *) ok "and the run may not be filed COMPLETE" "rewritten" ;; esac

# An attestId nobody minted.
newrepo; gates
rec "scripts/harness.sh --full" 0 >/dev/null
out="$(runprove 0 att_deadbeef1234 wf-p3)"
case "$out" in *TAMPERED-EXECUTION*)
  ok "citing an attestation that does not exist is TAMPERED" "caught" ;;
  *) bad "citing an attestation that does not exist is TAMPERED" "${out:0:56}" ;; esac

# No citation at all: the declared verification did not happen. Not
# tampering — an executor that never routes through the Bash tool leaves no
# rows — but not a clean run either.
newrepo; gates
out="$(runprove 0 "" wf-p4)"
case "$out" in *"[UNATTESTED]"*) ok "a prove: node citing nothing is UNATTESTED" "unattested" ;;
  *) bad "a prove: node citing nothing is UNATTESTED" "${out:0:56}" ;; esac
case "$out" in *"| INCOMPLETE |"*)
  ok "and the run is INCOMPLETE — neither tampered nor clean" "INCOMPLETE" ;;
  *) bad "and the run is INCOMPLETE — neither tampered nor clean" "${out:0:56}" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
