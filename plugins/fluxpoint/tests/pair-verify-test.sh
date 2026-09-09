#!/usr/bin/env bash
# Relation gate, second half: a parity that cannot fail is not a parity.
#
# From a real campaign (issue #62): seven review rounds on a mainnet keeper
# produced the same defect class every round — a check exists, an assertion
# names it, and the assertion cannot observe it. A hand-written reader missed
# 51 of 282 single-field edits, in one direction only; a docstring asserted
# it was "the same code" and was false for six checks; a parity that compared
# two byte-identical strings could never have failed. What settled it was a
# differential: thousands of generated inputs, both implementations, bytes
# compared including error text and key order.
#
# This suite pins the three primitives that turn that campaign's evidence
# into one manifest entry: the differential tier, the bite (--verify mutates
# the reader and requires the parity to redden, with every trap the campaign
# hit refused by name), and --scan for the mirror nobody declared.
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
PG="$FPL_PY $PLUGIN/scripts/pair-guard.py --root ."
ROOT="$(mktemp -d)"
R="$ROOT/r"
pass=0; fail=0

ok()  { printf 'PASS  %-58s -> %s\n' "$1" "$2"; pass=$((pass+1)); }
bad() { printf 'FAIL  %-58s -> %s\n' "$1" "$2"; fail=$((fail+1)); }
check(){ [ "$2" = "$3" ] && ok "$1" "$3" || bad "$1" "$3 (wanted $2)"; }
has()  { case "$2" in *"$3"*) ok "$1" "reported" ;; *) bad "$1" "${2:0:70}" ;; esac; }
hasnt(){ case "$2" in *"$3"*) bad "$1" "present" ;; *) ok "$1" "absent" ;; esac; }

commit() { git -C "$R" add -A; git -C "$R" -c user.email=t@t -c user.name=t commit -qm "${1:-x}"; }

# The pair under test: a funds-moving parser (source) and a hand-written
# reader of the same document (mirror). Both read one JSON line on stdin.
mkrepo() {
  rm -rf "$R"; mkdir -p "$R/src" "$R/scripts"; cd "$R" || exit 1
  git init -q -b main
  cat >src/source.py <<'EOF'
import json, sys
doc = json.loads(sys.stdin.readline())
fee = doc.get("fee", 0)
if fee < 0:
    sys.stderr.write("fee must be non-negative\n")
    sys.exit(2)
print(json.dumps({"ok": True, "fee": fee}))
EOF
  cp src/source.py src/mirror.py
  cat >src/gen.py <<'EOF'
import json, os, random
seed = int(os.environ.get("FPL_PAIR_SEED", "1"))
n = int(os.environ.get("FPL_PAIR_CASES", "10"))
r = random.Random(seed)
for i in range(n):
    fee = -(i + 1) if i % 4 == 0 else r.randint(0, 50)
    print(json.dumps({"fee": fee, "seed": seed, "i": i}))
EOF
  printf 'x\n' >unrelated.txt
  commit base
}
manifest() { cat >.fluxpoint-pairs.json; }
diff_manifest() { # $1 = extra JSON fields (may be empty)
  manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "differential":{"generator":"$FPL_PY src/gen.py",
                  "sourceRun":"$FPL_PY src/source.py",
                  "mirrorRun":"$FPL_PY src/mirror.py",
                  "cases":40,"seed":7}${1:-}}]
EOF
}
drop_check() { # the reader stops refusing negative fees
  "$FPL_PY" - <<'PY'
p = "src/mirror.py"; s = open(p).read()
open(p, "w").write(s.replace("if fee < 0:", "if False:"))
PY
}

# ================= 1. the differential tier ===============================
mkrepo; diff_manifest; commit m
out="$($PG --check 2>&1)"; rc=$?
check "two agreeing implementations: green" 0 "$rc"
has "the run is reported with its size and seed" "$out" "differential ok — 40 cases, seed 7"

drop_check
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "the reader dropping a check: RED" 1 "$rc"
has "the divergence names the count and seed" "$err" "DIVERGED on 10 of 40 cases (seed 7)"
has "and shows the input that split them" "$err" '"fee": -1'
has "and both exit codes" "$err" "source rc=2"
has "  (the reader's too)" "$err" "mirror rc=0"
has "and a re-run line a human can paste" "$err" "re-run: pair-guard.py --check --seed 7 --cases 40"

# Error TEXT alone is a divergence: the campaign's mirror duplicated a message
# verbatim from the code it claimed to delegate to, and got it wrong later.
mkrepo; diff_manifest; commit m
sed -i 's/fee must be non-negative/fee must be >= 0/' src/mirror.py
$PG --check >/dev/null 2>&1
check "a different error message alone is a divergence" 1 "$?"

# Map key order alone is a divergence: byte for byte means byte for byte.
mkrepo; diff_manifest; commit m
sed -i 's/{"ok": True, "fee": fee}/{"fee": fee, "ok": True}/' src/mirror.py
$PG --check >/dev/null 2>&1
check "a different key order alone is a divergence" 1 "$?"

# ================= 2. authority: the reader diverged, not "they differ" ====
mkrepo; diff_manifest ',"authority":"source"'; commit m
drop_check
err="$($PG --check 2>&1 >/dev/null)"
has "with authority, the failure names the reader" "$err" "the reader (src/mirror.py) diverged from the authority (src/source.py)"
mkrepo; diff_manifest; commit m
drop_check
err="$($PG --check 2>&1 >/dev/null)"
hasnt "without authority the wording stays neutral" "$err" "diverged from the authority"
mkrepo; diff_manifest ',"authority":"reader"'; commit m
$PG --check >/dev/null 2>&1
check "an authority that names neither side is refused" 1 "$?"

# ================= 3. a generator that compares nothing is refused ========
mkrepo; diff_manifest; commit m
printf 'import sys\n' >src/gen.py
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a generator emitting nothing: RED" 1 "$rc"
has "and says how short it fell" "$err" "produced 0 of 40 cases"
printf 'import os\nfor _ in range(int(os.environ["FPL_PAIR_CASES"])): print("{\\"fee\\": 1}")\n' >src/gen.py
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "one input repeated 40 times: RED" 1 "$rc"
has "because it compares nothing" "$err" "distinct"
printf 'import sys\nsys.exit(3)\n' >src/gen.py
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a generator that fails: RED" 1 "$rc"
has "naming the generator" "$err" "the generator exited 3"

# The generator sees the seed and count it was asked for, and the CLI
# overrides the manifest so a reported divergence is reproducible.
mkrepo; diff_manifest; commit m
out="$($PG --check --cases 5 --seed 99 2>&1)"
has "--cases and --seed reach the run" "$out" "5 cases, seed 99"
out="$(FPL_PAIR_CASES=6 $PG --check 2>&1)"
has "FPL_PAIR_CASES caps the run for a harness" "$out" "6 cases"

# A side that cannot run is not agreement.
mkrepo; diff_manifest; commit m
sed -i 's|"mirrorRun":"[^"]*"|"mirrorRun":"no-such-command-9000"|' .fluxpoint-pairs.json
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a mirror command that is not found: RED" 1 "$rc"
has "and it is reported as not found, never as a divergence" "$err" "not found"
hasnt "  (no divergence claimed)" "$err" "DIVERGED"

mkrepo; diff_manifest; commit m
drop_check
$PG --check --no-parity >/dev/null 2>&1
check "--no-parity skips the differential too" 0 "$?"

# ================= 4. the bite: a parity must be shown to fail =============
# compare.sh is a REAL parity: it runs both readers on one negative fee and
# compares the exit codes.
real_parity() {
  cat >scripts/compare.sh <<EOF
#!/bin/sh
a=\$(printf '{"fee": -3}\n' | $FPL_PY src/source.py >/dev/null 2>&1; echo \$?)
b=\$(printf '{"fee": -3}\n' | $FPL_PY src/mirror.py >/dev/null 2>&1; echo \$?)
[ "\$a" = "\$b" ]
EOF
  chmod +x scripts/compare.sh
}
bite_manifest() { # $1 = parity command, $2 = extra bite fields, $3 = extra pair fields
  manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "parity":"$1",
  "bite":{"file":"src/mirror.py","find":"if fee < 0:","replace":"if False:"${2:-}}${3:-}}]
EOF
}

mkrepo; real_parity; bite_manifest "sh scripts/compare.sh"; commit m
before="$(cat src/mirror.py)"
out="$($PG --verify 2>&1)"; rc=$?
check "a real parity with a bite: --verify green" 0 "$rc"
has "the intact run is reported" "$out" "intact   -> checks green"
has "the mutated run reddened" "$out" "the parity bites"
has "and the restore was read back" "$out" "restore  -> verified byte-for-byte"
[ "$(cat src/mirror.py)" = "$before" ] && ok "the reader is byte-identical afterwards" "restored" \
  || bad "the reader is byte-identical afterwards" "MUTANT LEFT ON DISK"
[ -e .fluxpoint-pairs-restoring ] && bad "no sentinel is left behind" "present" \
  || ok "no sentinel is left behind" "clean"

# THE REGRESSION: a parity that compares two identical strings can never
# fail, and --check reads it as green forever.
mkrepo; bite_manifest "test x = x"; commit m
$PG --check >/dev/null 2>&1
check "a parity that compares nothing is green under --check" 0 "$?"
err="$($PG --verify 2>&1 >/dev/null)"; rc=$?
check "and RED under --verify" 1 "$rc"
has "because it passed with the reader mutated" "$err" "PASSED with the reader mutated"

# Anchor discipline: exactly once, or the mutation is a silent no-op that
# reads as a survivor (or lands on the wrong site).
mkrepo; real_parity; bite_manifest "sh scripts/compare.sh"; commit m
printf '\ndef other(fee):\n    if fee < 0:\n        return 1\n' >>src/mirror.py; commit twice
before="$(cat src/mirror.py)"
err="$($PG --verify 2>&1 >/dev/null)"; rc=$?
check "an anchor matching twice is refused" 1 "$rc"
has "and says how many times" "$err" "anchor matched 2 time(s)"
[ "$(cat src/mirror.py)" = "$before" ] && ok "  and the file is untouched" "untouched" \
  || bad "  and the file is untouched" "CHANGED"
mkrepo; real_parity; bite_manifest "sh scripts/compare.sh" ',"find":"if fee <= 0:"'; commit m
err="$($PG --verify 2>&1 >/dev/null)"; rc=$?
check "an anchor matching nowhere is refused" 1 "$rc"
has "with the count" "$err" "anchor matched 0 time(s)"

# The compile exit is printed on its own line, every time, and a mutant that
# does not compile is refused rather than read as a survivor.
mkrepo; real_parity
compile_probe="$FPL_PY -c \\\"import sys; sys.exit(3 if 'if False' in open('src/mirror.py').read() else 0)\\\""
bite_manifest "sh scripts/compare.sh" ",\"compile\":\"$compile_probe\""; commit m
out="$($PG --verify 2>&1)"; rc=$?
check "a mutant that does not compile: --verify RED" 1 "$rc"
has "the intact compile exit is printed" "$out" "compile  -> rc=0 (intact)"
has "the mutated compile exit is printed separately" "$out" "compile  -> rc=3 (mutated)"
has "and named as the reason" "$out" "does not compile"
hasnt "never credited as a bite" "$out" "the parity bites"
has "and the compile runs again after the restore" "$out" "compile  -> rc=0 (restored)"

# A parity that is red INTACT proves nothing about mutation.
mkrepo; bite_manifest "false"; commit m
err="$($PG --verify 2>&1 >/dev/null)"; rc=$?
check "a parity red on the healthy tree cannot testify" 1 "$rc"
has "and says so" "$err" "RED with the reader INTACT"

# SIGTERM mid-window: the mutant is restored and the sentinel removed.
mkrepo
cat >scripts/compare.sh <<EOF
#!/bin/sh
if grep -q 'if False' src/mirror.py; then sleep 30; fi
exit 0
EOF
chmod +x scripts/compare.sh
bite_manifest "sh scripts/compare.sh"; commit m
before="$(cat src/mirror.py)"
$PG --verify >/dev/null 2>&1 &
vpid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -e .fluxpoint-pairs-restoring ] && break; sleep 0.5
done
[ -e .fluxpoint-pairs-restoring ] && ok "the sentinel is on disk while the reader is mutated" "present" \
  || bad "the sentinel is on disk while the reader is mutated" "absent"
grep -q 'if False' src/mirror.py && ok "  and the mutant is in place" "mutated" \
  || bad "  and the mutant is in place" "not mutated"
kill -TERM "$vpid" 2>/dev/null; wait "$vpid" 2>/dev/null
[ "$(cat src/mirror.py)" = "$before" ] && ok "SIGTERM mid-window restores the reader" "restored" \
  || bad "SIGTERM mid-window restores the reader" "MUTANT LEFT ON DISK"
[ -e .fluxpoint-pairs-restoring ] && bad "  and clears the sentinel" "present" \
  || ok "  and clears the sentinel" "cleared"
printf 'fee-reader\nsrc/mirror.py\n' >.fluxpoint-pairs-restoring
$PG --check >/dev/null 2>&1
check "a leftover sentinel fails --check until inspected" 1 "$?"
rm -f .fluxpoint-pairs-restoring

# ================= 5. what a bite may not name ============================
mkrepo; real_parity
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],"authority":"source",
  "parity":"sh scripts/compare.sh",
  "bite":{"file":"src/source.py","find":"if fee < 0:","replace":"if False:"}}]
EOF
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a bite on the authority side is refused" 1 "$rc"
has "because it would disable the funds path" "$err" "authority side"
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "parity":"sh scripts/compare.sh",
  "bite":{"file":"src/gen.py","find":"seed","replace":"sead"}}]
EOF
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a bite outside the pair's globs is refused" 1 "$rc"
has "as proving nothing about this relation" "$err" "not part of this pair"
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "parity":"sh scripts/compare.sh",
  "bite":{"file":"../outside.py","find":"a","replace":"b"}}]
EOF
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a bite escaping the repo is refused" 1 "$rc"
has "by name" "$err" "outside the repo"
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "bite":{"file":"src/mirror.py","find":"if fee < 0:","replace":"if False:"}}]
EOF
err="$($PG --check 2>&1 >/dev/null)"; rc=$?
check "a bite with nothing to bite is half-declared" 1 "$rc"
has "and says so" "$err" "half-declared"
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],
  "parity":"sh scripts/compare.sh",
  "bite":{"file":"src/mirror.py","find":"if fee < 0:","replace":"if fee < 0:"}}]
EOF
$PG --check >/dev/null 2>&1
check "a bite whose replace equals its find is refused" 1 "$?"

# ================= 6. loud where it cannot know ===========================
mkrepo; real_parity
manifest <<EOF
[{"id":"fee-reader","source":"src/source.py","mirror":["src/mirror.py"],"parity":"sh scripts/compare.sh"}]
EOF
commit m
out="$($PG --check 2>&1)"; rc=$?
check "parity with no bite stays green under --check" 0 "$rc"
has "but is named as unproven" "$out" "no bite — nothing has shown it can fail"
out="$($PG --list 2>&1)"
has "--list says the same" "$out" "no bite"
err="$($PG --verify 2>&1 >/dev/null)"; rc=$?
check "and --verify fails it" 1 "$rc"
has "naming the pair" "$err" "fee-reader: parity declared, no bite"
manifest <<'EOF'
[{"id":"old-style","source":"src/source.py","mirror":["src/mirror.py"]}]
EOF
out="$($PG --list 2>&1)"
has "an old-style manifest lists as before" "$out" "no parity command"
hasnt "  with no bite line" "$out" "bite"
$PG --check >/dev/null 2>&1
check "  and checks as before" 0 "$?"

# ================= 7. --scan: the mirror nobody declared ==================
mkrepo
mkdir -p keeper deploy tests
cat >keeper/config.py <<'EOF'
def parse(doc):
    if doc.get("fee", 0) < 0:
        raise ValueError("fee must be non-negative for a quoted book")
    if not doc.get("venue"):
        raise ValueError("a book needs a venue before it can be quoted")
    return doc
EOF
cat >deploy/gate.py <<'EOF'
"""Deploy gate. Checks the config the same way the keeper does, because it
is a mirror of keeper/config.py — see parse() there."""
from keeper import config

def would_refuse(doc):
    if doc.get("fee", 0) < 0:
        raise ValueError("fee must be non-negative for a quoted book")
    return False
EOF
cat >tests/test_gate.py <<'EOF'
def test_refuses():
    raise ValueError("fee must be non-negative for a quoted book")
EOF
out="$($PG --scan 2>/dev/null)"; rc=$?
check "--scan exits 0" 0 "$rc"
"$FPL_PY" - "$out" <<'PY' && ok "--scan proposes the undeclared mirror as a manifest entry" "json" \
  || bad "--scan proposes the undeclared mirror as a manifest entry" "wrong shape"
import json, sys
entries = json.loads(sys.argv[1])
assert isinstance(entries, list) and entries, entries
e = next(x for x in entries if {x["source"], x["mirror"]} == {"keeper/config.py", "deploy/gate.py"})
assert e["source"] == "keeper/config.py" and e["mirror"] == "deploy/gate.py", e
assert e["authority"] == "source", e
assert "shared-throw" in e["why"] and "claims" in e["why"], e["why"]
assert not any("tests/" in x["source"] or "tests/" in x["mirror"] for x in entries), entries
PY
manifest <<'EOF'
[{"id":"config-gate","source":"keeper/config.py","mirror":["deploy/gate.py"]}]
EOF
out="$($PG --scan 2>"$ROOT/scan.err")"
check "a declared pair covers its candidate" "[]" "$out"
has "  and the header says so" "$(cat "$ROOT/scan.err")" "1 covered by declared pairs"
manifest <<'EOF'
[{"id":"typo","source":"a","mirror":["b"],"parrity":"x"}]
EOF
$PG --scan >/dev/null 2>&1
check "--scan still refuses a malformed manifest" 1 "$?"

# ================= 8. the scaffolded harness runs the differential ========
mkrepo; diff_manifest; commit m
cp "$PLUGIN/templates/harness.sh" scripts/harness.sh; chmod +x scripts/harness.sh
drop_check; commit drift
( env -u CLAUDE_PLUGIN_ROOT FPL_PLUGIN_ROOT="$PLUGIN" bash scripts/harness.sh --full >"$ROOT/h.log" 2>&1 ); hrc=$?
[ "$hrc" -ne 0 ] && ok "harness --full fails on a diverging reader" "rc=$hrc" \
  || bad "harness --full fails on a diverging reader" "rc=0"
has "  naming the divergence" "$(cat "$ROOT/h.log")" "DIVERGED"
git -C "$R" checkout -q -- src/mirror.py 2>/dev/null; cp src/source.py src/mirror.py; commit fixed
( env -u CLAUDE_PLUGIN_ROOT FPL_PLUGIN_ROOT="$PLUGIN" bash scripts/harness.sh --full >"$ROOT/h.log" 2>&1 ); hrc=$?
check "and passes once the reader agrees" 0 "$hrc"

cd /; rm -rf "$ROOT"
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
