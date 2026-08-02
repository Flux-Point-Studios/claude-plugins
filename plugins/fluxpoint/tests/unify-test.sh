#!/usr/bin/env bash
# Tests for the unified state model and the pre-1.0 compatibility path that
# /fluxpoint:migrate relies on.
set -uo pipefail
PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(mktemp -d)"
pass=0; fail=0

check() { # name expected actual
  if [ "$2" = "$3" ]; then printf 'PASS  %-50s -> %s\n' "$1" "$3"; pass=$((pass+1))
  else printf 'FAIL  %-50s -> %s (wanted %s)\n' "$1" "$3" "$2"; fail=$((fail+1)); fi
}
contains() { # name needle file
  if grep -qF "$2" "$3"; then printf 'PASS  %-50s -> found\n' "$1"; pass=$((pass+1))
  else printf 'FAIL  %-50s -> MISSING\n' "$1"; fail=$((fail+1)); fi
}

newrepo() {
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/scripts"; cd "$ROOT/r" || exit 1
  git init -q -b main
  printf '#!/usr/bin/env bash\nexit 0\n' >scripts/harness.sh; chmod +x scripts/harness.sh
  printf 'x\n' >f.txt; git add -A
  git -c user.email=t@t -c user.name=t commit -qm base
}
RESULT='{"campaign":"c","outcome":"COMPLETE","results":{"find":[{"a":1},{"a":2}]},"provenance":[{"node":"find","status":"OK"},{"node":"g","status":"DEAD"}]}'

# --- 1. state-file resolution: WORK.md wins, LOOP.md still honored ---
newrepo
. "$PLUGIN/scripts/lib.sh"
printf '# WORK\n' >WORK.md; printf '# LOOP\n' >LOOP.md
check "state file prefers WORK.md" "WORK.md" "$(fpl_state_file)"
rm WORK.md
check "state file falls back to LOOP.md" "LOOP.md" "$(fpl_state_file)"
rm LOOP.md
fpl_state_file >/dev/null 2>&1
check "no state file -> non-zero" "1" "$?"

# --- 2. unified 5-column Evidence table gets the graph row ---
newrepo
cp "$PLUGIN/templates/WORK.md" WORK.md
printf '%s' "$RESULT" | python3 "$PLUGIN/scripts/record-run.py" --run-id wf_u1 --harness 0 --red-team SHIP >/dev/null
contains "unified table: row appended" "| wf_u1 | COMPLETE |" WORK.md
contains "unified table: claim column" "1 node(s) OK, 1 dead, 2 produced item(s)" WORK.md
contains "unified table: proof column" "harness exit 0; red-team SHIP" WORK.md
check "provenance artifact written" "0" "$([ -f .claude/fluxpoint/runs/wf_u1.json ] && echo 0 || echo 1)"

# --- 2b. budget-skipped nodes are reported, never filed as success ---
newrepo
cp "$PLUGIN/templates/WORK.md" WORK.md
SKIPPED='{"campaign":"c","outcome":"COMPLETE","results":{},"provenance":[{"node":"a","status":"OK"},{"node":"b","status":"SKIPPED","detail":"budget floor"}]}'
printf '%s' "$SKIPPED" | python3 "$PLUGIN/scripts/record-run.py" --run-id wf_sk1 >/dev/null
contains "skipped node: flagged in Evidence claim" "1 SKIPPED on budget — coverage incomplete" WORK.md
check "skipped node: not counted as OK" "1" \
  "$(python3 -c "import json;print(json.load(open('.claude/fluxpoint/runs/wf_sk1.json'))['nodesOk'])")"
check "skipped node: counted in artifact" "1" \
  "$(python3 -c "import json;print(json.load(open('.claude/fluxpoint/runs/wf_sk1.json'))['nodesSkipped'])")"

# --- 3. legacy 7-column GRAPH.md table still works (mid-migration repo) ---
newrepo
{ printf '## Evidence\n\n'
  printf '| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |\n'
  printf '|---|---|---|---|---|---|---|\n'; } >GRAPH.md
printf '%s' "$RESULT" | python3 "$PLUGIN/scripts/record-run.py" --run-id wf_l1 --graph GRAPH.md --harness 0 >/dev/null
contains "legacy table: 7-column row appended" "| wf_l1 | COMPLETE | 1/1 | 2 | 0 |" GRAPH.md

# --- 4. no Evidence table: artifact still written, warning emitted, exit 0 ---
newrepo
printf '# WORK\nno table here\n' >WORK.md
err="$(printf '%s' "$RESULT" | python3 "$PLUGIN/scripts/record-run.py" --run-id wf_n1 2>&1 >/dev/null)"
check "missing table -> still exit 0" "0" "$?"
check "missing table -> artifact exists" "0" "$([ -f .claude/fluxpoint/runs/wf_n1.json ] && echo 0 || echo 1)"
case "$err" in *"no Evidence table header"*) check "missing table -> warns" "warn" "warn" ;;
  *) check "missing table -> warns" "warn" "silent" ;; esac

# --- 5. SessionStart injection reflects the unified world ---
newrepo
cp "$PLUGIN/templates/WORK.md" WORK.md
printf '%s' "$RESULT" | python3 "$PLUGIN/scripts/record-run.py" --run-id wf_s1 --harness 0 --red-team SHIP >/dev/null
mkdir -p .claude/fluxpoint; printf 'PASS 2026-01-01T00:00:00Z\n' >.claude/fluxpoint/last-harness
out="$(printf '{"session_id":"s","cwd":"%s"}' "$ROOT/r" | bash "$PLUGIN/scripts/inject-state.sh")"
printf '%s' "$out" >"$ROOT/inject.txt"
contains "inject: harness verdict" "Last recorded harness verdict: PASS" "$ROOT/inject.txt"
contains "inject: last graph run" "Last graph run: wf_s1 COMPLETE" "$ROOT/inject.txt"
contains "inject: WORK.md head" "WORK.md (first 80 lines)" "$ROOT/inject.txt"

# --- 6. legacy repo: LOOP.md injected AND migrate hint shown ---
newrepo
printf '# LOOP: legacy goal\nSTATUS: ACTIVE\n' >LOOP.md
mkdir -p .claude/fluxpoint-loop; printf 'FAIL 2026-01-01T00:00:00Z\n' >.claude/fluxpoint-loop/last-harness
out="$(printf '{"session_id":"s","cwd":"%s"}' "$ROOT/r" | bash "$PLUGIN/scripts/inject-state.sh")"
printf '%s' "$out" >"$ROOT/inject2.txt"
contains "legacy: reads pre-1.0 state dir" "Last recorded harness verdict: FAIL" "$ROOT/inject2.txt"
contains "legacy: injects LOOP.md" "LOOP.md (first 80 lines)" "$ROOT/inject2.txt"
contains "legacy: suggests migrate" "/fluxpoint:migrate" "$ROOT/inject2.txt"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
