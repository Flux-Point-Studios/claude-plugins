#!/usr/bin/env bash
# A gate that cannot run must not read as a gate that passed.
#
# Every gate in templates/harness.sh resolved its script out of
# ~/.claude/plugins and, finding nothing, skipped and still exited 0. The
# environments where that happens are the ones that matter most — CI, cloud
# sessions, fresh clones, and the detached worktrees used for independent
# verification — so "harness green" could describe a run in which the relation
# gate, the proof ratchet, the statement ratchet, the counterexample ledger
# and the mutation ratchet were all absent.
#
# The property under test is that intent decides the direction: a repo whose
# manifest declares a gate goes RED when the gate is missing, a repo that
# declared nothing stays runnable, and the gap can only be accepted on purpose.
#
# Also covers the resolver itself: `find | head -1` guarantees no ordering and
# was observed taking an ORPHANED cached version over the active one.
set -uo pipefail

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
HARNESS="$PLUGIN/templates/harness.sh"
pass=0; fail=0
ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
EMPTY_HOME="$WORK/home"; mkdir -p "$EMPTY_HOME"

# A repo carrying the harness, with no plugin reachable from it.
mkrepo() {
  r="$WORK/$1"; rm -rf "$r"; mkdir -p "$r/scripts"
  cp "$HARNESS" "$r/scripts/harness.sh"
  ( cd "$r" && git init -q . && git config user.email t@t && git config user.name t \
    && printf 'x\n' > file.txt && git add -A && git commit -qm init )
}
run_full() {  # $1 = repo, rest = env assignments
  ( cd "$WORK/$1" && env -u FPL_PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT \
      HOME="$EMPTY_HOME" "${@:2}" bash scripts/harness.sh --full 2>&1 )
}

# ============ 1. a declared gate that cannot run is RED ==================
mkrepo declared
printf '{"pairs": []}\n' > "$WORK/declared/.fluxpoint-pairs.json"
out="$(run_full declared)"; rc=$?
check "a declared gate with no plugin fails the harness" 1 "$rc"
case "$out" in *"pair-guard.py is not installed"*)
    ok "and names the gate and the manifest" "reported" ;;
  *) bad "and names the gate and the manifest" "${out:0:60}" ;; esac
case "$out" in *"cannot report green"*)
    ok "and says why green is unavailable" "reported" ;;
  *) bad "and says why green is unavailable" "${out:0:60}" ;; esac

# ============ 2. the gap is acceptable only on purpose ===================
out="$(run_full declared FPL_ALLOW_MISSING_GATES=1)"; rc=$?
check "an explicit opt-out lets the run continue" 0 "$rc"
case "$out" in *WARNING*) ok "and still says the gate did not run" "warned" ;;
  *) bad "and still says the gate did not run" "${out:0:60}" ;; esac

# ============ 3. a repo that declared nothing stays runnable =============
# The requirement the silent skip existed to serve, kept.
mkrepo bare
out="$(run_full bare)"; rc=$?
check "a repo declaring no gates still runs without the plugin" 0 "$rc"

# ============ 4. the resolver is deterministic ===========================
# Two cached versions plus a marketplace copy, laid out as a real install.
FAKE="$WORK/fakehome"
mkdir -p "$FAKE/.claude/plugins/cache/fluxpoint/fluxpoint/1.24.1/scripts" \
         "$FAKE/.claude/plugins/cache/fluxpoint/fluxpoint/1.26.0/scripts" \
         "$FAKE/.claude/plugins/marketplaces/fluxpoint/plugins/fluxpoint/scripts"
for v in 1.24.1 1.26.0; do
  printf 'v%s\n' "$v" > "$FAKE/.claude/plugins/cache/fluxpoint/fluxpoint/$v/scripts/pair-guard.py"
done
printf 'marketplace\n' > "$FAKE/.claude/plugins/marketplaces/fluxpoint/plugins/fluxpoint/scripts/pair-guard.py"

resolve() {  # runs plugin_script from the shipped template, nothing else
  ( set -uo pipefail
    eval "$(sed -n '/^plugin_script() {/,/^}/p' "$HARNESS")"
    HOME="$1" plugin_script pair-guard.py )
}
got="$(HOME="$FAKE" resolve "$FAKE")"
case "$got" in */marketplaces/*)
    ok "a marketplace install wins over cached copies" "marketplaces" ;;
  *) bad "a marketplace install wins over cached copies" "${got##*plugins/}" ;; esac

rm -rf "$FAKE/.claude/plugins/marketplaces"
got="$(HOME="$FAKE" resolve "$FAKE")"
case "$got" in *1.26.0*) ok "otherwise the highest cached version wins" "1.26.0" ;;
  *) bad "otherwise the highest cached version wins" "${got##*fluxpoint/}" ;; esac

# The orphan must not win merely by sorting first on disk.
case "$got" in *1.24.1*) bad "the orphaned older version is not picked" "1.24.1" ;;
  *) ok "the orphaned older version is not picked" "skipped" ;; esac

# ============ 5. the runtime's own root is honored =======================
# FPL_PLUGIN_ROOT had no producer anywhere in the plugin, so the first branch
# of the resolver was dead in every scaffolded repo. CLAUDE_PLUGIN_ROOT is set
# by the runtime whenever the harness runs under a hook.
ROOT="$WORK/pluginroot"; mkdir -p "$ROOT/scripts"
printf 'from-claude-root\n' > "$ROOT/scripts/pair-guard.py"
got="$( set -uo pipefail
        eval "$(sed -n '/^plugin_script() {/,/^}/p' "$HARNESS")"
        HOME="$EMPTY_HOME" CLAUDE_PLUGIN_ROOT="$ROOT" plugin_script pair-guard.py )"
case "$got" in "$ROOT"/*) ok "CLAUDE_PLUGIN_ROOT resolves the gate" "honored" ;;
  *) bad "CLAUDE_PLUGIN_ROOT resolves the gate" "${got:-empty}" ;; esac

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
