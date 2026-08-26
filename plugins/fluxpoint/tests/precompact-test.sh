#!/usr/bin/env bash
# The compaction gate: the one moment a session's reasoning is discarded is
# the one moment the hooks can demand it be written down first.
#
# The contract under test:
#   - A compaction that would discard a window in which durable state changed
#     nowhere — not WORK.md's Decisions/Notes, not the memory store, not the
#     task board — is refused ONCE (exit 2), with the reason on stderr.
#   - The refusal is bounded per window, not per session: the marker written
#     by a block is cleared by the next allowed compaction, so two
#     consecutive attempts can never both block and a wedge is impossible,
#     while a marathon session's later windows are still protected.
#   - A flush to ANY durable surface clears the gate: the work file's
#     Decisions/Notes, the auto-memory directory, or the task board.
#   - Non-git roots are in scope. The old hook exited at a git guard, which
#     made it inert in exactly the kind of long non-repo session where
#     compaction hurts most. Without git there is no "did code change"
#     signal, so an unflushed window blocks regardless.
#   - FPL_COMPACT_BLOCK=0 demotes the gate to the old warn-only behavior;
#     FPL_DISABLE=1 silences it entirely.
#
# The durable side effects (marker files, the window snapshot) are the tested
# surface; stdout-into-summary remains undocumented and untrusted.
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
export CLAUDE_PLUGIN_ROOT="$PLUGIN"
PRE="$PLUGIN/scripts/precompact.sh"
INJECT="$PLUGIN/scripts/inject-state.sh"
DEC="$PLUGIN/scripts/decision.py"
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

# The suite fakes the per-user surfaces (memory store, task board) under its
# own home, and must not inherit the runner's project dir: both would scope
# the aux hash to the real machine and make every assertion here weather.
unset CLAUDE_PROJECT_DIR CLAUDE_CONFIG_DIR
export FPL_AUX_HOME="$ROOT/home"

ok()  { printf 'PASS  %-58s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-58s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }

# The projects-dir name Claude Code derives from a project path, computed by
# the same rule lib.sh uses so the fixture and the code cannot drift apart.
munge() { "$FPL_PY" -c 'import sys
print("".join(c if c.isalnum() else "-" for c in sys.argv[1]))' "$1"; }

fakehome() { # $1 = project dir whose memory store should exist
  rm -rf "$ROOT/home"
  mkdir -p "$ROOT/home/.claude/projects/$(munge "$1")/memory" \
           "$ROOT/home/.claude/tasks/s"
  printf 'seed\n' >"$ROOT/home/.claude/projects/$(munge "$1")/memory/MEMORY.md"
  printf '{"id":1}\n' >"$ROOT/home/.claude/tasks/s/1.json"
}

GOOD='{
  "question": "how should the unlock window be bounded?",
  "options": [
    {"option": "24h", "argued_by": "ops",
     "strongest_objection": "a day may be too slow for incident response"},
    {"option": "72h", "argued_by": "gov",
     "strongest_objection": "three days widens the window an attacker has"}
  ],
  "chosen": "72h",
  "rationale": "the governance timelock already binds operations to 72h, so a shorter unlock buys nothing and adds a race",
  "overturned_prior": true,
  "frozen_by": "genesis",
  "reversible": false,
  "evidence": ["scripts/harness.sh --full exit 0"]
}'

mkrepo() {
  rm -rf "$R"; mkdir -p "$R/scripts" "$R/src"; cd "$R" || exit 1
  git init -q -b main
  printf '#!/usr/bin/env bash\nexit 0\n' >scripts/harness.sh
  chmod +x scripts/harness.sh
  printf 'x = 1\n' >src/app.py
  cat >WORK.md <<'EOF'
# work

STATUS: WIP
MODE: loop

## Decisions

| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |
|---|---|---|---|---|---|

## Evidence

| When (UTC) | Source | Outcome | Claim | Proof |
|---|---|---|---|---|

## Notes for the next iteration
EOF
  git add -A; git -c user.email=t@t -c user.name=t commit -qm base
  fakehome "$R"
  printf '{"session_id":"s","cwd":"%s"}' "$R" | bash "$INJECT" >/dev/null 2>&1
}

mkplain() { # a non-git root: no repo, no WORK.md — the development-root shape
  rm -rf "$R"; mkdir -p "$R"; cd "$R" || exit 1
  fakehome "$R"
}

# The runtime's PreCompact schema carries `trigger` ("manual"|"auto"), not
# `compact_reason` — a field it has never sent. Fabricating the wrong key
# here is how the hook recorded "unknown" on every live run.
precompact() { printf '{"session_id":"s","cwd":"%s","trigger":"%s"}' \
  "$R" "${1:-auto}" | bash "$PRE"; }
inject()     { printf '{"session_id":"s","cwd":"%s"}' "$R" | bash "$INJECT"; }
dec()        { "$FPL_PY" "$DEC" --root "$R" --graph WORK.md "$@"; }
SD() { printf '%s/.claude/fluxpoint' "$R"; }

# ============ 1. an unflushed window is refused once, then never twice ====
mkrepo
[ -f "$(SD)/s.cwin" ] \
  && ok "SessionStart seeds the compaction window snapshot" "seeded" \
  || bad "SessionStart seeds the compaction window snapshot" "missing"

printf 'y = 2\n' >>src/app.py                 # code changed, nothing written
err="$(precompact auto 2>&1 >/dev/null)"; rc=$?
check "code changed + nothing flushed: compaction is blocked" 2 "$rc"
case "$err" in *"COMPACTION BLOCKED"*)
  ok "the refusal names itself on stderr" "named" ;;
  *) bad "the refusal names itself on stderr" "got: ${err:0:60}" ;; esac
case "$err" in *"never blocks twice"*)
  ok "and states its own bound" "bounded" ;;
  *) bad "and states its own bound" "unstated" ;; esac
[ -f "$(SD)/s.cblock" ] \
  && ok "a block leaves its marker" "marker" \
  || bad "a block leaves its marker" "missing"
[ -f "$(SD)/s.compacted" ] \
  && bad "a blocked attempt writes no compacted marker" "wrote one" \
  || ok "a blocked attempt writes no compacted marker" "none"

out="$(precompact auto 2>/dev/null)"; rc=$?
check "the immediate retry proceeds — a wedge is impossible" 0 "$rc"
case "$out" in *"context is being compacted"*)
  ok "the allowed pass still advises the summarizer" "advised" ;;
  *) bad "the allowed pass still advises the summarizer" "silent" ;; esac
"$FPL_PY" -c '
import json,sys
d = json.load(open(sys.argv[1]))
print(d["codeChanged"], d["flushed"], d["blocked"])' "$(SD)/s.compacted" \
  | grep -q "yes no yes" \
  && ok "the marker records work, no flush, and the override" "yes/no/yes" \
  || bad "the marker records work, no flush, and the override" \
         "$(cat "$(SD)/s.compacted")"
[ -f "$(SD)/s.cblock" ] \
  && bad "an allowed compaction clears the block marker" "still armed" \
  || ok "an allowed compaction clears the block marker" "cleared"

# The next context is told the override happened, via the injection backbone.
out="$(inject 2>&1)"
case "$out" in *"CONTEXT WAS COMPACTED"*)
  ok "the fresh context is told the reasoning is gone" "injected" ;;
  *) bad "the fresh context is told the reasoning is gone" "silent" ;; esac
case "$out" in *"blocked once"*)
  ok "and told the gate was overridden, not satisfied" "told" ;;
  *) bad "and told the gate was overridden, not satisfied" "untold" ;; esac

# ============ 2. the gate re-arms per window, not per session =============
printf 'z = 3\n' >>src/app.py                 # new work, still nothing flushed
precompact auto >/dev/null 2>&1
check "a later unflushed window blocks again" 2 "$?"
precompact auto >/dev/null 2>&1
check "and its retry proceeds — never two refusals in a row" 0 "$?"

# ============ 3. flushing any durable surface clears the gate =============
mkrepo
printf 'y = 2\n' >>src/app.py
printf '%s' "$GOOD" | dec --record --id unlock-window >/dev/null 2>&1
precompact auto >/dev/null 2>&1
check "a recorded decision clears the gate on the first try" 0 "$?"
grep -q '"flushed": *"yes"' "$(SD)/s.compacted" \
  && ok "and the marker says so" "flushed yes" \
  || bad "and the marker says so" "$(cat "$(SD)/s.compacted")"
out="$(inject 2>&1)"
case "$out" in *"CONTEXT WAS COMPACTED"*)
  bad "a session that wrote things down is not nagged" "warned anyway" ;;
  *) ok "a session that wrote things down is not nagged" "quiet" ;; esac

mkrepo
printf 'y = 2\n' >>src/app.py
printf 'new fact\n' >>"$ROOT/home/.claude/projects/$(munge "$R")/memory/MEMORY.md"
precompact auto >/dev/null 2>&1
check "a memory-store write clears the gate" 0 "$?"

mkrepo
printf 'y = 2\n' >>src/app.py
printf '{"id":2}\n' >"$ROOT/home/.claude/tasks/s/2.json"
precompact auto >/dev/null 2>&1
check "a task-board write clears the gate" 0 "$?"

mkrepo
printf 'y = 2\n' >>src/app.py
dec --none "a mechanical rename with no choice in it" --session s >/dev/null 2>&1
precompact auto >/dev/null 2>&1
check "declared silence clears the gate" 0 "$?"

# ============ 4. sessions with nothing to lose are never stopped ==========
mkrepo
precompact manual >/dev/null 2>&1
check "a read-only git session is not blocked" 0 "$?"
out="$(inject 2>&1)"
case "$out" in *"CONTEXT WAS COMPACTED"*)
  bad "and not warned about afterwards" "warned" ;;
  *) ok "and not warned about afterwards" "quiet" ;; esac

mkrepo
printf 'y = 2\n' >>src/app.py
rm -f "$(SD)/s.cwin"                          # SessionStart never ran here
precompact auto >/dev/null 2>&1
check "no window baseline: allow, never guess" 0 "$?"
[ -f "$(SD)/s.cwin" ] \
  && ok "but the pass seeds the next window" "seeded" \
  || bad "but the pass seeds the next window" "unseeded"

# ============ 5. non-git roots are finally in scope =======================
mkplain
precompact auto >/dev/null 2>&1
check "non-git first sight: allow and seed the window" 0 "$?"
precompact auto >/dev/null 2>&1
check "non-git unflushed window: blocked" 2 "$?"
precompact auto >/dev/null 2>&1
check "non-git retry: proceeds" 0 "$?"
grep -q '"codeChanged": *"unknown"' "$(SD)/s.compacted" \
  && ok "the marker is honest that git gave no work signal" "unknown" \
  || bad "the marker is honest that git gave no work signal" \
         "$(cat "$(SD)/s.compacted")"

mkplain
precompact auto >/dev/null 2>&1               # seed
printf '{"id":9}\n' >"$ROOT/home/.claude/tasks/s/9.json"
precompact auto >/dev/null 2>&1
check "non-git with a task-board flush: not blocked" 0 "$?"

# ============ 6. the demotions still work =================================
mkrepo
printf 'y = 2\n' >>src/app.py
FPL_COMPACT_BLOCK=0 bash -c 'printf "{\"session_id\":\"s\",\"cwd\":\"%s\",\"compact_reason\":\"auto\"}" "$1" | bash "$2"' _ "$R" "$PRE" >/dev/null 2>&1
check "FPL_COMPACT_BLOCK=0: warn-only, never exit 2" 0 "$?"
[ -f "$(SD)/s.compacted" ] \
  && ok "warn-only still records the marker" "recorded" \
  || bad "warn-only still records the marker" "missing"
[ -f "$(SD)/s.cblock" ] \
  && bad "warn-only never arms the block marker" "armed" \
  || ok "warn-only never arms the block marker" "unarmed"

mkrepo
printf 'y = 2\n' >>src/app.py
FPL_DISABLE=1 bash -c 'printf "{\"session_id\":\"s\",\"cwd\":\"%s\",\"compact_reason\":\"auto\"}" "$1" | bash "$2"' _ "$R" "$PRE" >/dev/null 2>&1
check "FPL_DISABLE=1: the hook is a no-op" 0 "$?"

# ============ 7. the red team's findings stay closed ======================
# A block that cannot be RECORDED must not be TAKEN: with the marker path
# unwritable, an unguarded write would block every attempt forever — the
# exact wedge the bound exists to make impossible. A directory squatting on
# the marker path makes the write fail on every platform.
mkrepo
printf 'y = 2\n' >>src/app.py
mkdir -p "$(SD)/s.cblock"
precompact auto >/dev/null 2>&1
check "unrecordable block marker: allow, never wedge" 0 "$?"
rm -rf "$(SD)/s.cblock"

# With no observable surface at all — no work file, no memory store, no task
# board — the hashes are constants and no user action could ever clear a
# block. That shape must never block.
rm -rf "$R"; mkdir -p "$R"; cd "$R" || exit 1
rm -rf "$ROOT/home"; mkdir -p "$ROOT/home"
precompact auto >/dev/null 2>&1               # first sight seeds
precompact auto >/dev/null 2>&1; rc1=$?
precompact auto >/dev/null 2>&1; rc2=$?
check "no observable surface: never blocks (attempt 2)" 0 "$rc1"
check "no observable surface: never blocks (attempt 3)" 0 "$rc2"

# Declared silence is a statement about ONE window, not a permanent disarm
# the gated agent hands itself. The allowed pass consumes it.
mkrepo
printf 'y = 2\n' >>src/app.py
dec --none "a mechanical rename with no choice in it" --session s >/dev/null 2>&1
precompact auto >/dev/null 2>&1
check "declared silence clears its own window" 0 "$?"
printf 'z = 3\n' >>src/app.py
precompact auto >/dev/null 2>&1
check "but the next unflushed window blocks again" 2 "$?"
precompact auto >/dev/null 2>&1
check "and its retry proceeds" 0 "$?"

# A hostile session id must not write outside the state dir.
mkrepo
printf '{"session_id":"../../../pwn","cwd":"%s","trigger":"auto"}' "$R" \
  | bash "$PRE" >/dev/null 2>&1
if [ -e "$R/pwn.cwin" ] || [ -e "$R/.claude/pwn.cwin" ] || [ -e "$ROOT/pwn.cwin" ]; then
  bad "a traversal session id cannot escape the state dir" "escaped"
else
  ok "a traversal session id cannot escape the state dir" "contained"
fi

# The runtime's field is honored: a manual /compact records reason "manual".
mkrepo
precompact manual >/dev/null 2>&1
grep -q '"reason": *"manual"' "$(SD)/s.compacted" \
  && ok "the marker records the runtime trigger" "manual" \
  || bad "the marker records the runtime trigger" "$(cat "$(SD)/s.compacted")"

# Warn-only in a non-git root still warns afterwards: codeChanged is
# "unknown" there, and the injection must mirror the gate's predicate
# (worked != no), not demand a literal "yes".
mkplain
precompact auto >/dev/null 2>&1               # seed
FPL_COMPACT_BLOCK=0 bash -c 'printf "{\"session_id\":\"s\",\"cwd\":\"%s\",\"trigger\":\"auto\"}" "$1" | bash "$2"' _ "$R" "$PRE" >/dev/null 2>&1
out="$(inject 2>&1)"
case "$out" in *"CONTEXT WAS COMPACTED"*)
  ok "warn-only non-git compaction still warns the next context" "warned" ;;
  *) bad "warn-only non-git compaction still warns the next context" "silent" ;; esac

# Non-git roots get the stale-file sweep too, or every scratch dir accretes
# window files forever.
mkplain
mkdir -p "$(SD)"
printf 'x\n' >"$(SD)/old.cwin"
touch -t "$(date -d '5 days ago' +%Y%m%d%H%M 2>/dev/null || date -v-5d +%Y%m%d%H%M)" "$(SD)/old.cwin"
inject >/dev/null 2>&1
[ -f "$(SD)/old.cwin" ] \
  && bad "non-git roots sweep stale window files" "still there" \
  || ok "non-git roots sweep stale window files" "swept"

# The state dir refuses to be committed: its own .gitignore covers it even
# in repos that never ran /fluxpoint:init.
mkrepo
[ -f "$(SD)/.gitignore" ] && grep -q '^\*$' "$(SD)/.gitignore" \
  && ok "the state dir ships its own gitignore" "present" \
  || bad "the state dir ships its own gitignore" "missing"

# A relocated config dir (CLAUDE_CONFIG_DIR) is honored by the aux hash.
cfg="$ROOT/cfgdir"
mkdir -p "$cfg/tasks/s"
printf '{"id":1}\n' >"$cfg/tasks/s/1.json"
a="$(cd "$R" && FPL_AUX_HOME= CLAUDE_CONFIG_DIR="$cfg" \
     bash -c '. "'"$PLUGIN"'/scripts/lib.sh"; fpl_aux_sha s')"
[ "$a" != "noaux" ] && [ -n "$a" ] \
  && ok "CLAUDE_CONFIG_DIR relocates the aux surface" "$a" \
  || bad "CLAUDE_CONFIG_DIR relocates the aux surface" "noaux"

# The aux hash keys into Claude Code's projects/<munged-path> naming, and the
# runtime munges the LAUNCH directory — CLAUDE_PROJECT_DIR — not wherever the
# hook has since cd'd. In a multi-repo workspace the hooks cd into the repo
# while the session's auto-memory store stays keyed to the workspace root;
# hashing by $PWD reads a directory that does not exist and reports the
# store's flushes as noaux, blinding the compaction window to the one surface
# it exists to protect. This suite otherwise unsets CLAUDE_PROJECT_DIR (line
# 59), which is the same blind spot that let v1.30.0 ship believing it was
# done — so this case sets it on purpose.
wsroot="$ROOT/wsroot"; mkdir -p "$wsroot/repo"
( cd "$wsroot/repo" && git init -q -b main )
rm -rf "$ROOT/home"
mkdir -p "$ROOT/home/.claude/projects/$(munge "$wsroot")/memory"
printf 'lesson\n' >"$ROOT/home/.claude/projects/$(munge "$wsroot")/memory/MEMORY.md"
a="$(cd "$wsroot/repo" && CLAUDE_PROJECT_DIR="$wsroot" \
     bash -c '. "'"$PLUGIN"'/scripts/lib.sh"; fpl_aux_sha s')"
[ "$a" != "noaux" ] && [ -n "$a" ] \
  && ok "aux hash keys by the launch dir, not the hook's cwd" "$a" \
  || bad "aux hash keys by the launch dir, not the hook's cwd" "noaux — hashed the repo instead"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
