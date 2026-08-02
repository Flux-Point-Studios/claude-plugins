#!/usr/bin/env bash
# Hook wiring and PostToolUse behavior.
#
# The other suites call the hook scripts directly, which never exercises the
# thing that actually runs in a session: the command strings in hooks.json,
# with ${CLAUDE_PLUGIN_ROOT} expanded. This suite reads those strings out of
# hooks.json and runs them, so a broken path or a renamed script fails here
# instead of silently disarming the harness in someone's session.
#
# It also covers verify-changed.sh, which had no test at all.
set -uo pipefail
PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
export CLAUDE_PLUGIN_ROOT="$PLUGIN"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()   { printf 'PASS  %-52s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()  { printf 'FAIL  %-52s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

# The command strings the runtime actually executes.
hook_cmd() {
  python3 - "$PLUGIN/hooks/hooks.json" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for group in d["hooks"].get(sys.argv[2], []):
    for h in group["hooks"]:
        print(h["command"])
        raise SystemExit
PY
}
hook_matcher() {
  python3 - "$PLUGIN/hooks/hooks.json" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for group in d["hooks"].get(sys.argv[2], []):
    print(group.get("matcher", ""))
    raise SystemExit
PY
}

newrepo() { # $1 = harness exit code, or "none" for no harness
  rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/scripts" "$ROOT/r/src"; cd "$ROOT/r" || exit 1
  git init -q -b main
  if [ "$1" != "none" ]; then
    printf '#!/usr/bin/env bash\nexit %s\n' "$1" >scripts/harness.sh
    chmod +x scripts/harness.sh
  fi
  printf 'x = 1\n' >src/app.py
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
}

post_input() { printf '{"session_id":"s","cwd":"%s","tool_input":{"file_path":"%s"}}' "$ROOT/r" "$1"; }

# --- 1. every hook command in hooks.json resolves and runs ---
for ev in SessionStart PostToolUse Stop; do
  cmd="$(hook_cmd "$ev")"
  [ -n "$cmd" ] || { bad "$ev: command present in hooks.json" "missing"; continue; }
  # Expand ${CLAUDE_PLUGIN_ROOT} exactly as the runtime does, then check the
  # target exists before running it.
  target="$(eval "printf '%s' \"$(printf '%s' "$cmd" | sed 's/^bash //')\"")"
  if [ -f "$target" ]; then ok "$ev: script exists at plugin root" "$(basename "$target")"
  else bad "$ev: script exists at plugin root" "missing $target"; fi
done

# --- 2. the hook commands execute without error on a clean repo ---
newrepo 0
out="$(post_input src/app.py | eval "$(hook_cmd SessionStart)" 2>&1)"; rc=$?
check "SessionStart: runs via its hooks.json command" 0 "$rc"
case "$out" in *"Flux Point work context"*) ok "SessionStart: injects context" "yes" ;;
  *) bad "SessionStart: injects context" "no" ;; esac

# --- 3. PostToolUse matcher covers exactly the file-writing tools ---
m="$(hook_matcher PostToolUse)"
check "PostToolUse: matcher is Write|Edit|MultiEdit" "Write|Edit|MultiEdit" "$m"

# --- 4. verify-changed.sh: the previously untested hook ---
newrepo 0
printf 'y = 2\n' >>src/app.py
post_input src/app.py | eval "$(hook_cmd PostToolUse)" >/dev/null 2>&1
check "verify-changed: green harness exits 0" 0 "$?"
[ -f .claude/fluxpoint/s.dirty ] && ok "verify-changed: marks the session dirty" "marker written" \
  || bad "verify-changed: marks the session dirty" "no marker"

newrepo 1
printf 'y = 2\n' >>src/app.py
err="$(post_input src/app.py | eval "$(hook_cmd PostToolUse)" 2>&1 >/dev/null)"; rc=$?
check "verify-changed: red harness exits 2 (feeds Claude)" 2 "$rc"
case "$err" in *"harness --changed RED"*) ok "verify-changed: red output names the file" "yes" ;;
  *) bad "verify-changed: red output names the file" "got: ${err:0:40}" ;; esac

# A red harness must still arm the gate, or a failed edit could be walked away
# from by never touching Write again.
[ -f .claude/fluxpoint/s.dirty ] && ok "verify-changed: arms the gate even when red" "marker written" \
  || bad "verify-changed: arms the gate even when red" "no marker"

# --- 5. skipped paths do not arm the gate or run the harness ---
for skip in README.md .claude/settings.json docs/notes.md; do
  newrepo 1
  mkdir -p "$(dirname "$skip")" 2>/dev/null
  printf 'text\n' >"$skip"
  post_input "$skip" | eval "$(hook_cmd PostToolUse)" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 0 ] && [ ! -f .claude/fluxpoint/s.dirty ]; then
    ok "verify-changed: skips $skip" "no run, no marker"
  else
    bad "verify-changed: skips $skip" "rc=$rc marker=$([ -f .claude/fluxpoint/s.dirty ] && echo yes || echo no)"
  fi
done

# --- 6. no harness in the repo: mark dirty, stay silent, never fail the edit ---
newrepo none
printf 'y = 2\n' >>src/app.py
post_input src/app.py | eval "$(hook_cmd PostToolUse)" >/dev/null 2>&1
check "verify-changed: harmless when no harness exists" 0 "$?"
[ -f .claude/fluxpoint/s.dirty ] && ok "verify-changed: still arms the gate" "marker written" \
  || bad "verify-changed: still arms the gate" "no marker"

# --- 7. the kill switch disarms every hook ---
newrepo 1
printf 'y = 2\n' >>src/app.py
FPL_DISABLE=1 bash -c "$(hook_cmd PostToolUse)" <<<"$(post_input src/app.py)" >/dev/null 2>&1
check "FPL_DISABLE=1: PostToolUse becomes a no-op" 0 "$?"
printf '{"session_id":"s","cwd":"%s"}' "$ROOT/r" >"$ROOT/stopin"
out="$(FPL_DISABLE=1 bash -c "$(hook_cmd Stop)" <"$ROOT/stopin" 2>&1)"
[ -z "$out" ] && ok "FPL_DISABLE=1: Stop gate becomes a no-op" "silent" \
  || bad "FPL_DISABLE=1: Stop gate becomes a no-op" "spoke"

# --- 8. non-git directory: hooks must not explode ---
rm -rf "$ROOT/plain"; mkdir -p "$ROOT/plain"; cd "$ROOT/plain"
printf '{"session_id":"s","cwd":"%s","tool_input":{"file_path":"a.py"}}' "$ROOT/plain" \
  | eval "$(hook_cmd PostToolUse)" >/dev/null 2>&1
check "non-git dir: PostToolUse exits cleanly" 0 "$?"
printf '{"session_id":"s","cwd":"%s"}' "$ROOT/plain" | eval "$(hook_cmd Stop)" >/dev/null 2>&1
check "non-git dir: Stop gate exits cleanly" 0 "$?"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
