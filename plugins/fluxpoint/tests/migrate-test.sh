#!/usr/bin/env bash
# Migration against real pre-1.0 fixtures.
#
# This is the operation that deletes a repo's work-state files, so it gets
# built fixtures rather than a description: a v0.1 loop-only repo, a v0.2
# loop+graph repo with an IR block, and a pre-0.2 repo whose GRAPH.md is
# prose. Evidence rows are counted before and after, because the one thing a
# migration must never do is lose the record.
set -uo pipefail
PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
MIG="python3 $PLUGIN/scripts/migrate.py --root"
ROOT="$(mktemp -d)"
pass=0; fail=0

ok()  { printf 'PASS  %-54s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-54s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }
has() { grep -qF "$2" "$1" && ok "$3" "found" || bad "$3" "MISSING: $2"; }

mkrepo() { rm -rf "$ROOT/r"; mkdir -p "$ROOT/r/.claude"; cd "$ROOT/r" || exit 1; git init -q -b main; }

write_loop() {
  cat >LOOP.md <<'EOF'
# LOOP: ship the settlement path

STATUS: ACTIVE

## Definition of Done
- [ ] `scripts/harness.sh --full` exits 0
- [ ] settlement exercised on preview — proof: tx hash

## Plan
- [x] failing test for partial fills
- [ ] oracle staleness guard

## Constraints
- No change to the datum schema.

## Merge policy
- Auto-merge: yes. Method: squash.

## Evidence
| When (UTC) | Claim | Proof |
|---|---|---|
| 2026-01-02 09:00 | harness green | exit 0 |
| 2026-01-03 11:30 | settlement on preview | tx abc123 |

## Notes for the next iteration
Blocked on oracle feed rotation.
EOF
  cat >LOOP_PROMPT.md <<'EOF'
Read LOOP.md in the repo root. It is the loop's single source of state.
Work one slice per iteration per LOOP.md.
EOF
}

write_graph_ir() {
  cat >GRAPH.md <<'EOF'
# GRAPH: audit the validators

STATUS: READY

## Work graph

```json graph-ir
{
  "version": 1,
  "name": "audit",
  "campaign": "Audit the validators for eUTxO defects",
  "budget": { "maxNodes": 12 },
  "lists": { "dims": [{ "key": "eutxo", "brief": "double satisfaction" }] },
  "nodes": [
    {
      "id": "find",
      "phase": "Find",
      "foreach": "dims",
      "prompt": "Audit for {{item.brief}}",
      "contract": "FindingsV1",
      "verify": "skeptic:1",
      "verifyOver": "findings",
      "expectItems": 2
    }
  ]
}
```

## Evidence
| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |
|---|---|---|---|---|---|---|
| 2026-01-04 08:15 | wf_aaa111 | COMPLETE | 2/0 | 5 | 0 | SHIP |

## Notes for the next run
Ceiling was generous; sweep went dry.
EOF
}

setup_common() {
  printf '.claude/fluxpoint-loop/\n.claude/fluxpoint-graph/\nnode_modules/\n' >.gitignore
  printf '{\n  "enabledPlugins": {\n    "fluxpoint-loop@fluxpoint": true,\n    "fluxpoint-graph@fluxpoint": true\n  },\n  "env": { "KEEP": "me" }\n}\n' >.claude/settings.json
  mkdir -p .claude/fluxpoint-loop .claude/fluxpoint-graph/runs .claude/workflows
  printf 'PASS 2026-01-03T11:30:00Z\n' >.claude/fluxpoint-loop/last-harness
  printf 'stale\n' >.claude/fluxpoint-loop/oldsession.dirty
  printf '{"runId":"wf_aaa111"}\n' >.claude/fluxpoint-graph/runs/wf_aaa111.json
  printf '// generated\n' >.claude/workflows/audit.graph.js
}

# ================= 1. v0.2 repo: LOOP.md + GRAPH.md with an IR =============
mkrepo; write_loop; write_graph_ir; setup_common
out="$($MIG "$ROOT/r" --plan 2>&1)"
case "$out" in *"3 Evidence row(s) carried"*) ok "plan: counts all evidence rows" "3" ;;
  *) bad "plan: counts all evidence rows" "$(printf '%s' "$out" | grep Evidence)" ;; esac
[ -f LOOP.md ] && [ ! -f WORK.md ] && ok "plan: changes nothing on disk" "untouched" \
  || bad "plan: changes nothing on disk" "mutated"

$MIG "$ROOT/r" --apply >/dev/null 2>&1
check "apply: exits 0" 0 "$?"
has WORK.md "MODE: both"                       "apply: MODE both when loop+graph"
has WORK.md "# WORK: ship the settlement path" "apply: goal line carried from LOOP.md"
has WORK.md "oracle staleness guard"           "apply: Plan carried"
has WORK.md "No change to the datum schema."   "apply: Constraints carried"
has WORK.md "Auto-merge: yes"                  "apply: Merge policy carried"
has WORK.md '```json graph-ir'                 "apply: IR block carried into Campaign"
has WORK.md "Blocked on oracle feed rotation." "apply: loop notes carried"
has WORK.md "Ceiling was generous"             "apply: graph notes carried"
has WORK.md "| tx abc123 |"                    "apply: loop evidence proof carried"
has WORK.md "wf_aaa111"                        "apply: graph evidence runId carried"
has WORK.md "harness 0; red-team SHIP"         "apply: graph row mapped to unified proof"
rows=$(sed -n '/^| When (UTC) | Source/,/^$/p' WORK.md | grep -c '^| 2026')
check "apply: all 3 evidence rows present" 3 "$rows"

[ -f LOOP.md ] && [ -f GRAPH.md ] && ok "apply: sources left in place for review" "kept" \
  || bad "apply: sources left in place for review" "deleted too early"
[ -f WORK_PROMPT.md ] && [ ! -f LOOP_PROMPT.md ] && ok "apply: prompt renamed" "WORK_PROMPT.md" \
  || bad "apply: prompt renamed" "not renamed"
has WORK_PROMPT.md "Read WORK.md"              "apply: prompt references updated"
[ -f .claude/fluxpoint/last-harness ] && ok "apply: loop state moved" "moved" \
  || bad "apply: loop state moved" "missing"
[ -f .claude/fluxpoint/runs/wf_aaa111.json ] && ok "apply: run artifacts moved" "moved" \
  || bad "apply: run artifacts moved" "missing"
[ ! -f .claude/fluxpoint/oldsession.dirty ] && ok "apply: stale session markers not carried" "skipped" \
  || bad "apply: stale session markers not carried" "carried"
[ ! -f .claude/workflows/audit.graph.js ] && ok "apply: compiled build output removed" "removed" \
  || bad "apply: compiled build output removed" "kept"
grep -q '.claude/fluxpoint/' .gitignore && ! grep -q 'fluxpoint-loop' .gitignore \
  && ok "apply: .gitignore rewired" "updated" || bad "apply: .gitignore rewired" "stale entries"
grep -q '.claude/worktrees/' .gitignore \
  && ok "apply: worktrees ignored (mutating nodes)" "added" \
  || bad "apply: worktrees ignored (mutating nodes)" "missing"
python3 -c "
import json;d=json.load(open('.claude/settings.json'))
assert d['enabledPlugins']=={'fluxpoint@fluxpoint':True}, d
assert d['env']=={'KEEP':'me'}, d" 2>/dev/null \
  && ok "apply: settings rewired, other keys preserved" "ok" \
  || bad "apply: settings rewired, other keys preserved" "wrong"

# WORK.md must actually compile now.
python3 "$PLUGIN/scripts/compile-graph.py" WORK.md --check >/dev/null 2>&1
check "apply: migrated WORK.md compiles" 0 "$?"

$MIG "$ROOT/r" --apply >/dev/null 2>&1
check "apply: refuses to overwrite an existing WORK.md" 1 "$?"

$MIG "$ROOT/r" --finalize >/dev/null 2>&1
check "finalize: exits 0" 0 "$?"
[ ! -f LOOP.md ] && [ ! -f GRAPH.md ] && ok "finalize: sources removed" "removed" \
  || bad "finalize: sources removed" "still present"

# ================= 2. evidence loss is refused =============================
mkrepo; write_loop; write_graph_ir; setup_common
$MIG "$ROOT/r" --apply >/dev/null 2>&1
python3 - <<'PY'
import re
t = open('WORK.md').read()
t = re.sub(r'^\| 2026.*\n', '', t, count=2, flags=re.M)   # drop rows by hand
open('WORK.md','w').write(t)
PY
$MIG "$ROOT/r" --finalize >/dev/null 2>&1
check "finalize: refuses when evidence rows were lost" 1 "$?"
[ -f LOOP.md ] && ok "finalize: sources survive a refused finalize" "kept" \
  || bad "finalize: sources survive a refused finalize" "deleted anyway"

# ================= 3. v0.1 repo: loop only =================================
mkrepo; write_loop; setup_common
$MIG "$ROOT/r" --apply >/dev/null 2>&1
has WORK.md "MODE: loop" "loop-only: MODE loop"
grep -q '## Campaign' WORK.md && bad "loop-only: no empty Campaign section" "present" \
  || ok "loop-only: no empty Campaign section" "absent"

# ================= 4. pre-0.2 repo: prose GRAPH.md, no IR ==================
mkrepo; write_loop; setup_common
cat >GRAPH.md <<'EOF'
# GRAPH: prose era

STATUS: DESIGN

## Work graph
| # | Node | Contract | Verified by |
|---|---|---|---|
| 1 | find | findings | panel |

## Evidence
| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |
|---|---|---|---|---|---|---|
EOF
out="$($MIG "$ROOT/r" --plan 2>&1)"
case "$out" in *"predates the IR"*) ok "pre-0.2: plan flags the hand conversion" "flagged" ;;
  *) bad "pre-0.2: plan flags the hand conversion" "silent" ;; esac
$MIG "$ROOT/r" --apply >/dev/null 2>&1
has WORK.md "MIGRATION: the previous GRAPH.md predates the IR" "pre-0.2: marker left in Campaign"
has WORK.md "| 1 | find | findings | panel |"                 "pre-0.2: prose work graph preserved"
python3 "$PLUGIN/scripts/compile-graph.py" WORK.md --check >/dev/null 2>&1
check "pre-0.2: does not pretend to compile" 1 "$?"

# ================= 5. already-unified repo is a no-op ======================
mkrepo; printf '# WORK: done\nSTATUS: ACTIVE\nMODE: loop\n' >WORK.md
out="$($MIG "$ROOT/r" --plan 2>&1)"
case "$out" in *"nothing to migrate"*) ok "already unified: reports no-op" "no-op" ;;
  *) bad "already unified: reports no-op" "$out" ;; esac

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
