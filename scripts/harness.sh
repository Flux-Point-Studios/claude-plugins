#!/usr/bin/env bash
# Definition-of-Done contract for this repository.
#
# The plugin in here exists to make "done" a deterministic check rather than
# an agent's self-report. It should be subject to its own rule, so this is
# the same contract fluxpoint:init scaffolds into any other repo.
#
#   --changed <file>   fast, scoped checks for one file
#   --full             everything the DoD requires
#
# exit 0 = green.
set -uo pipefail
cd "$(dirname "$0")/.."
PLUGIN="plugins/fluxpoint"
fail=0

step() { # name, then command
  local name="$1"; shift
  if "$@" >/tmp/fpl-step.log 2>&1; then
    printf '  ok    %s\n' "$name"
  else
    printf '  FAIL  %s\n' "$name"
    sed 's/^/        /' /tmp/fpl-step.log | tail -25
    fail=1
  fi
}

check_json() { python3 -m json.tool "$1" >/dev/null; }
check_sh()   { bash -n "$1"; }
check_py()   { python3 -m py_compile "$1"; }

compile_templates() {
  local t n=0
  for t in "$PLUGIN"/templates/WORK*.md; do
    [ -f "$t" ] || continue
    grep -q '```json graph-ir' "$t" || continue
    n=$((n + 1))
    python3 "$PLUGIN/scripts/compile-graph.py" "$t" -o /tmp/fpl-compiled.js || return 1
    # Generated scripts must be syntactically valid under the runtime's
    # async wrapper, or the graph fails at launch instead of at compile.
    {
      echo 'const agent=0,parallel=0,pipeline=0,log=0,phase=0,args=0,budget=0,workflow=0;(async () => {'
      sed 's/^export const meta/const meta/' /tmp/fpl-compiled.js
      echo '})()'
    } >/tmp/fpl-wrapped.mjs
    node --check /tmp/fpl-wrapped.mjs || return 1
  done
  # A green that compiled nothing is not a green: if the templates are ever
  # renamed or moved, this check must fail rather than silently pass.
  if [ "$n" -lt 2 ]; then
    echo "expected at least 2 campaign templates with an IR block, compiled $n" >&2
    return 1
  fi
}

case "${1:---full}" in
  --changed)
    f="${2:?usage: harness.sh --changed <file>}"
    case "$f" in
      *.json) step "json: $f" check_json "$f" ;;
      *.sh)   step "bash -n: $f" check_sh "$f" ;;
      *.py)   step "py_compile: $f" check_py "$f" ;;
      *)      : ;;
    esac
    # Any change under the plugin can break compilation; keep it cheap but
    # not blind.
    case "$f" in
      "$PLUGIN"/scripts/*.py | "$PLUGIN"/contracts/*.json | "$PLUGIN"/templates/WORK*.md)
        step "templates compile" compile_templates ;;
    esac
    ;;
  --full)
    echo "fluxpoint harness --full"
    for f in .claude-plugin/marketplace.json "$PLUGIN"/.claude-plugin/plugin.json \
             "$PLUGIN"/contracts/*.json "$PLUGIN"/hooks/hooks.json \
             "$PLUGIN"/templates/settings.snippet.json; do
      [ -f "$f" ] && step "json: ${f#"$PLUGIN"/}" check_json "$f"
    done
    for f in "$PLUGIN"/scripts/*.sh "$PLUGIN"/templates/*.sh scripts/*.sh; do
      [ -f "$f" ] && step "bash -n: $(basename "$f")" check_sh "$f"
    done
    for f in "$PLUGIN"/scripts/*.py; do
      [ -f "$f" ] && step "py_compile: $(basename "$f")" check_py "$f"
    done
    step "manifests validate" claude plugin validate .
    step "templates compile + emit valid JS" compile_templates
    step "compiler invariants" python3 "$PLUGIN/tests/compile-test.py"
    step "emission coverage" python3 "$PLUGIN/tests/emission-test.py"
    step "codegen injection + red-team regressions" python3 "$PLUGIN/tests/security-test.py"
    step "stop-gate regression" bash "$PLUGIN/tests/gate-test.sh"
    step "hook wiring + PostToolUse" bash "$PLUGIN/tests/hooks-test.sh"
    step "migration against pre-1.0 fixtures" bash "$PLUGIN/tests/migrate-test.sh"
    step "proof-strength ratchet" bash "$PLUGIN/tests/proof-guard-test.sh"
    step "on-chain budget gate" bash "$PLUGIN/tests/budget-test.sh"
    step "once-only ledger (executed)" python3 "$PLUGIN/tests/ledger-test.py"
    step "unified state + compatibility" bash "$PLUGIN/tests/unify-test.sh"
    ;;
  *)
    echo "usage: harness.sh --changed <file> | --full" >&2
    exit 2
    ;;
esac

if [ "$fail" -ne 0 ]; then
  echo "harness: RED"
  exit 1
fi
echo "harness: green"
exit 0
