#!/usr/bin/env bash
# Reproduction + regression test for the fluxpoint Stop-gate bypass.
# Builds a scratch repo whose harness is RED, then drives dod-gate.sh the way
# the Stop hook does, under each arming scenario.
set -uo pipefail
GATE="$(cd "$(dirname "$0")/.." && pwd)/scripts/dod-gate.sh"
ROOT="$(mktemp -d)"
pass=0; fail=0

setup() { # $1 = harness exit code
  rm -rf "$ROOT/repo"; mkdir -p "$ROOT/repo/scripts" "$ROOT/repo/src"
  cd "$ROOT/repo" || exit 1
  git init -q -b main
  printf '#!/usr/bin/env bash\nexit %s\n' "$1" >scripts/harness.sh
  chmod +x scripts/harness.sh
  printf 'print("baseline")\n' >src/app.py
  git add -A
  git -c user.email=t@t -c user.name=t commit -qm baseline
}

run_gate() {
  printf '{"session_id":"testsid","cwd":"%s"}' "$ROOT/repo" | bash "$GATE" 2>/dev/null
}

check() { # $1=name  $2=expected(block|allow)  $3=actual output
  local got="allow"
  printf '%s' "$3" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' && got="block"
  if [ "$got" = "$2" ]; then
    printf 'PASS  %-52s -> %s\n' "$1" "$got"; pass=$((pass+1))
  else
    printf 'FAIL  %-52s -> %s (wanted %s)\n' "$1" "$got" "$2"; fail=$((fail+1))
  fi
}

# 1. THE BYPASS: source edited via Bash tool only (no Write/Edit marker), harness RED.
setup 1
printf 'print("changed via bash tool")\n' >>src/app.py   # exactly what `cat >>` does
check "bash-tool edit, harness RED" block "$(run_gate)"

# 2. Same, but the edit was committed (git-clean tree) with the marker present:
#    the marker path must still work.
setup 1
printf 'print("changed")\n' >>src/app.py
git add -A; git -c user.email=t@t -c user.name=t commit -qm work
mkdir -p .claude/fluxpoint; : >.claude/fluxpoint/testsid.dirty
check "committed work, marker present, harness RED" block "$(run_gate)"

# 3. Untracked new source file via Bash, harness RED.
setup 1
printf 'def f(): pass\n' >src/new_module.py
check "untracked new source, harness RED" block "$(run_gate)"

# 4. No changes at all -> gate must stay dormant (no false arming).
setup 1
check "clean tree, no marker (must not arm)" allow "$(run_gate)"

# 5. Docs-only change -> skipped by _fpl_skip_path, must not arm.
setup 1
printf '# notes\n' >>README.md
check "docs-only change (must not arm)" allow "$(run_gate)"

# 6. Bash-tool edit with harness GREEN -> gate runs, passes, allows stop.
setup 0
printf 'print("ok")\n' >>src/app.py
check "bash-tool edit, harness GREEN" allow "$(run_gate)"

# 7. Harness tampering is surfaced on the PASS path.
setup 0
printf 'exit 0\n' >>scripts/harness.sh    # gutting the contract itself
out="$(run_gate)"
if printf '%s' "$out" | grep -q 'harness.sh is itself modified'; then
  printf 'PASS  %-52s -> notice emitted\n' "modified harness on green"; pass=$((pass+1))
else
  printf 'FAIL  %-52s -> no notice\n' "modified harness on green"; fail=$((fail+1))
fi

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
