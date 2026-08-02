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

check_json() { "$FPL_PY" -m json.tool "$1" >/dev/null; }
check_sh()   { bash -n "$1"; }
check_py()   { "$FPL_PY" -m py_compile "$1"; }

compile_templates() {
  local t n=0
  for t in "$PLUGIN"/templates/WORK*.md; do
    [ -f "$t" ] || continue
    grep -q '```json graph-ir' "$t" || continue
    n=$((n + 1))
    "$FPL_PY" "$PLUGIN/scripts/compile-graph.py" "$t" -o /tmp/fpl-compiled.js || return 1
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

# Commands and agents are run as literal instructions, so a `python3` written
# into one is reached by no shell resolver — and `python3` is absent from a
# standard Windows install, which made every slash command a no-op there.
# Invocations go through scripts/py.sh, which also pins UTF-8 stdio.
portable_invocations() {
  local hits
  hits=$(grep -rn 'python3[^`]*\.py' --include='*.md' "$PLUGIN" || true)
  if [ -n "$hits" ]; then
    echo "$hits" >&2
    echo "hardcoded python3 invocation; route it through scripts/py.sh" >&2
    return 1
  fi
  [ -x "$PLUGIN/scripts/py.sh" ] || {
    echo "scripts/py.sh missing or not executable" >&2
    return 1
  }
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
    step "portable interpreter invocations" portable_invocations
    step "templates compile + emit valid JS" compile_templates
    step "compiler invariants" "$FPL_PY" "$PLUGIN/tests/compile-test.py"
    step "emission coverage" "$FPL_PY" "$PLUGIN/tests/emission-test.py"
    step "codegen injection + red-team regressions" "$FPL_PY" "$PLUGIN/tests/security-test.py"
    step "stop-gate regression" bash "$PLUGIN/tests/gate-test.sh"
    step "hook wiring + PostToolUse" bash "$PLUGIN/tests/hooks-test.sh"
    step "migration against pre-1.0 fixtures" bash "$PLUGIN/tests/migrate-test.sh"
    step "proof-strength ratchet" bash "$PLUGIN/tests/proof-guard-test.sh"
    step "on-chain budget gate" bash "$PLUGIN/tests/budget-test.sh"
    step "once-only ledger (executed)" "$FPL_PY" "$PLUGIN/tests/ledger-test.py"
    step "relation gate" bash "$PLUGIN/tests/pair-test.sh"
    step "park layer (executed)" "$FPL_PY" "$PLUGIN/tests/park-test.py"
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
