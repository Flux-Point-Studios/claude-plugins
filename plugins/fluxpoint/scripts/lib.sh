#!/usr/bin/env bash
# Shared helpers for fluxpoint hooks. Sourced, never executed.
# JSON handling prefers jq and falls back to "$FPL_PY" so the hooks work on
# machines without jq installed.


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

fpl_state_dir() {
  printf '%s/.claude/fluxpoint' "${CLAUDE_PROJECT_DIR:-$PWD}"
}

# fpl_state_file — the repo's work-state file. WORK.md is current; LOOP.md is
# still honored so repos that have not run /fluxpoint:migrate keep working.
# Prints nothing when neither exists.
fpl_state_file() {
  for f in WORK.md LOOP.md; do
    [ -f "$f" ] && { printf '%s' "$f"; return 0; }
  done
  return 1
}

# fpl_legacy_state_dir — pre-1.0 state lived under .claude/fluxpoint-loop.
fpl_legacy_state_dir() {
  printf '%s/.claude/fluxpoint-loop' "${CLAUDE_PROJECT_DIR:-$PWD}"
}

# fpl_run_summary <runs/xxx.json> — one line describing a recorded graph run.
fpl_run_summary() {
  if command -v jq >/dev/null 2>&1; then
    jq -r '"\(.runId) \(.outcome), nodes \(.nodesOk)/\(.nodesDead) dead, \(.findings) finding(s), harness \(.harnessExit), red-team \(.redTeam)"' "$1" 2>/dev/null
  else
    "$FPL_PY" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print("%s %s, nodes %s/%s dead, %s finding(s), harness %s, red-team %s" % (
    d.get("runId"), d.get("outcome"), d.get("nodesOk"), d.get("nodesDead"),
    d.get("findings"), d.get("harnessExit"), d.get("redTeam")))' "$1" 2>/dev/null
  fi
}

# fpl_json_get <dotted.key>  — reads JSON on stdin, prints the value or
# nothing. Booleans print as true/false to match jq -r semantics.
fpl_json_get() {
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg k "$1" 'getpath($k / ".") // empty' 2>/dev/null
  else
    "$FPL_PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for part in sys.argv[1].split("."):
    d = d.get(part) if isinstance(d, dict) else None
    if d is None:
        sys.exit(0)
print(d if isinstance(d, str) else json.dumps(d))' "$1" 2>/dev/null
  fi
}

# fpl_json_obj key value [key value ...]  — emits a flat JSON object with
# string values, safely escaped.
fpl_json_obj() {
  if command -v jq >/dev/null 2>&1; then
    local args=() filter='{' i=0
    while [ "$#" -gt 0 ]; do
      args+=(--arg "k$i" "$1" --arg "v$i" "$2")
      filter="${filter}(\$k$i): \$v$i,"
      shift 2
      i=$((i + 1))
    done
    jq -cn "${args[@]}" "${filter%,}}"
  else
    "$FPL_PY" -c '
import json, sys
a = sys.argv[1:]
print(json.dumps(dict(zip(a[::2], a[1::2]))))' "$@"
  fi
}

# fpl_memory_sha — a hash of the work file's Decisions and Notes sections.
#
# Those two sections are where reasoning becomes durable: everything else a
# session knows lives in a transcript that compaction summarizes and a
# process exit discards. Hashing them lets a hook answer one question
# deterministically — did anything this session learned reach the disk? —
# without a model reading anything.
fpl_memory_sha() {
  local state
  state="$(fpl_state_file || true)"
  [ -n "$state" ] || { printf 'nostate'; return 0; }
  "$FPL_PY" - "$state" <<'PY' 2>/dev/null || printf 'unknown'
import hashlib, re, sys
try:
    text = open(sys.argv[1], errors="replace").read()
except OSError:
    print("unknown"); raise SystemExit
keep = []
for name in ("Decisions", "Notes"):
    for m in re.finditer(r"^##\s+" + name + r"[^\n]*$(.*?)(?=^##\s|\Z)",
                         text, re.S | re.M | re.I):
        keep.append(m.group(1))
print(hashlib.sha256("".join(keep).encode("utf-8", "replace")).hexdigest()[:12])
PY
}

_fpl_skip_path() {
  case "$1" in
    *.md|*.svg|*.min.js|*.lock|package-lock.json|pnpm-lock.yaml|yarn.lock) return 0 ;;
    .claude/*|*/.claude/*|node_modules/*|*/node_modules/*) return 0 ;;
    # The proof ratchet's own baseline NAMES every marker the scan hunts —
    # "lean.sorry": 0 is a count of zero holes, and reading it as a hole makes
    # the guard flag the evidence that it is clean.
    .fluxpoint-proof-baseline.json|*/.fluxpoint-proof-baseline.json) return 0 ;;
    target/*|*/target/*|dist/*|*/dist/*|build/*|*/build/*|.git/*) return 0 ;;
    # A Python repo that has not gitignored its virtualenv otherwise has the
    # hygiene scan walk site-packages.
    .venv/*|*/.venv/*|venv/*|*/venv/*|__pycache__/*|*/__pycache__/*) return 0 ;;
  esac
  return 1
}

# fpl_base_file <session_id> — where this session's gate baseline lives.
fpl_base_file() {
  printf '%s/%s.base' "$(fpl_state_dir)" "${1:-nosession}"
}

# fpl_base_ref <session_id> — the commit the gate compares the tree against.
#
# HEAD is the wrong baseline and it was a real bypass: an agent that writes
# source through the Bash tool (which fires no PostToolUse marker), commits,
# and stops leaves a tree identical to HEAD, so the gate found nothing to
# judge and the harness never ran. The work file's own prompt tells the loop
# to commit every slice, which made that the normal path rather than a
# clever one.
#
# So the baseline is the commit this session started from — or the last one
# the gate passed — and work committed mid-session is still work the gate
# has not seen.
#
# Falls back to HEAD whenever the recorded base cannot describe the current
# branch: a stale sha, a rebase, or a branch switch would otherwise diff the
# tree against unrelated history, arming the gate and flooding the hygiene
# scan with findings nobody introduced. A gate that cries wolf gets turned
# off, which is a worse outcome than the bypass it was closing.
fpl_base_ref() {
  local base
  base="$(cat "$(fpl_base_file "$1")" 2>/dev/null)"
  if [ -n "$base" ] \
     && git rev-parse --verify -q "${base}^{commit}" >/dev/null 2>&1 \
     && git merge-base --is-ancestor "$base" HEAD 2>/dev/null; then
    printf '%s' "$base"
    return 0
  fi
  printf 'HEAD'
}

# fpl_set_base <session_id> [ref] — record a baseline. Written at
# SessionStart only when absent (so compaction and resume cannot silently
# forgive commits made before them) and refreshed by the gate on green,
# because a tree that just passed is the right thing to measure the next one
# against.
fpl_set_base() {
  local sd f
  sd="$(fpl_state_dir)"
  f="$(fpl_base_file "$1")"
  git rev-parse --verify -q HEAD >/dev/null 2>&1 || return 0
  mkdir -p "$sd" 2>/dev/null || return 0
  git rev-parse "${2:-HEAD}" 2>/dev/null >"$f"
}

# fpl_code_dirty — succeeds when the tree carries code the DoD gate should
# judge: anything added, modified or renamed since the session baseline, or
# untracked, that survives _fpl_skip_path. Re-derived from git rather than
# read from a marker, so source edits made through the Bash tool (cat >,
# sed -i, git apply, tee) arm the gate exactly like Write/Edit/MultiEdit do —
# committed or not.
fpl_code_dirty() {
  local f base="${FPL_DIFF_BASE:-HEAD}"
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      _fpl_skip_path "$f" && continue
      return 0
    done < <(git diff "$base" --name-only --diff-filter=ACMR 2>/dev/null)
  fi
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    _fpl_skip_path "$f" && continue
    return 0
  done < <(git ls-files --others --exclude-standard 2>/dev/null)
  return 1
}

# The files whose contents decide what "green" means. A verdict is only as
# trustworthy as the contract that produced it, and every one of these can
# turn a red run green without touching a line of the code under test.
FPL_TRUST_BASE="scripts/harness.sh
.fluxpoint-hygiene-ignore
.fluxpoint-proof-baseline.json
.fluxpoint-pairs.json
.fluxpoint-gates.json
.fluxpoint-budget.json
.fluxpoint-cex.jsonl
package.json
Makefile"

# fpl_trust_base_modified — prints each trust-base file changed since the
# session baseline or untracked. Empty output means the contract that
# produced this verdict is the committed one.
#
# Diffing against the baseline rather than HEAD is what makes this real: an
# agent that re-records a proof baseline to absorb a rise, or edits the
# harness to exit 0, and then commits it, would otherwise show a clean tree.
fpl_trust_base_modified() {
  local f base="${FPL_DIFF_BASE:-HEAD}" hit
  printf '%s\n' "$FPL_TRUST_BASE" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    hit=""
    if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
      hit="$(git diff "$base" --name-only -- "$f" 2>/dev/null)"
    fi
    [ -n "$hit" ] || hit="$(git ls-files --others --exclude-standard -- "$f" 2>/dev/null)"
    [ -n "$hit" ] && printf '%s\n' "$hit"
  done
}

# fpl_harness_modified — kept as the narrow question ("is the harness itself
# changed"), still asked by name in the gate's regression suite.
fpl_harness_modified() {
  local base="${FPL_DIFF_BASE:-HEAD}"
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    [ -n "$(git diff "$base" --name-only -- scripts/harness.sh 2>/dev/null)" ] && return 0
  fi
  [ -n "$(git ls-files --others --exclude-standard -- scripts/harness.sh 2>/dev/null)" ] && return 0
  return 1
}

# Hygiene scan per the Flux Point Definition of Done. Scans lines added since
# the session baseline — committed this session or not — plus untracked
# files. History older than the session is CI's job; work this session
# committed is not, or committing would launder a marker past the gate.
# Emits one "file: line" per finding.
# _fpl_hygiene_ignored — is this path excluded from the HYGIENE SCAN only?
#
# Read by fpl_scan_hygiene and deliberately NOT by _fpl_skip_path, because
# _fpl_skip_path is shared with fpl_code_dirty, the predicate that ARMS the
# gate. A repo-extensible skip list wired into that predicate would be a
# gate-disarming primitive: `src/**` in a file, and the DoD gate silently
# stops firing. That is a far worse failure than the one being fixed, so the
# arming predicate stays untouchable and this narrows only what the scan
# reads.
#
# .fluxpoint-hygiene-ignore is one glob per line, # for comments. It is in
# FPL_TRUST_BASE, so widening the scan's blind spot is itself reported on a
# green run rather than being a silent edit.
_fpl_hygiene_ignored() {
  local pat
  [ -f .fluxpoint-hygiene-ignore ] || return 1
  while IFS= read -r pat; do
    case "$pat" in ""|\#*) continue ;; esac
    # shellcheck disable=SC2254 - the pattern is a glob on purpose
    case "$1" in $pat) return 0 ;; esac
  done < .fluxpoint-hygiene-ignore
  return 1
}

fpl_scan_hygiene() {
  # The last group are proof holes: a checker exits 0 on an assumed lemma
  # exactly as it does on a proved one. Only unambiguous markers live here
  # — `assume` and `todo` are too common in prose for a scan that cannot
  # strip comments; proof-guard.py ratchets those with comment stripping.
  local pat='TODO|FIXME|\bXXX\b|for now|\.unwrap\(\)|\.(skip|only)\(|\bxit\(|#\[ignore\]'
  pat="$pat"'|\bsorry\b|\bAdmitted\b|\{:axiom\}|\{:verify false\}|verifier::external_body|--skip-tests|--no-verify'
  local catch='catch[[:space:]]*(\([^)]*\))?[[:space:]]*\{[[:space:]]*\}'
  local f added base="${FPL_DIFF_BASE:-HEAD}"
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      _fpl_skip_path "$f" && continue
      added="$(git diff "$base" -U0 --no-color -- "$f" | grep -E '^\+[^+]' || true)"
      [ -n "$added" ] || continue
      printf '%s\n' "$added" | grep -EI "$pat" | sed "s|^+|$f: |" || true
      printf '%s\n' "$added" | grep -EI "$catch" | sed "s|^+|$f: |" || true
    done < <(git diff "$base" --name-only --diff-filter=ACMR 2>/dev/null)
  fi
  # Untracked files are read WHOLE, while the tracked branch above takes only
  # added lines — so a design-tool export or a vendored sample that predates
  # the session blocks every stop in the repo, on code nobody here wrote. That
  # contradicts the contract three lines up: history older than the session is
  # CI's job. .gitignore and .git/info/exclude already scope this, but a file
  # you intend to commit LATER has no correct lever among them: gitignoring it
  # is wrong and committing it early to silence the gate is worse. Hence a
  # hygiene-only list, which cannot disarm the gate.
  while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    _fpl_skip_path "$f" && continue
    _fpl_hygiene_ignored "$f" && continue
    grep -nEIH "$pat" "$f" || true
    grep -nEIH "$catch" "$f" || true
  done < <(git ls-files --others --exclude-standard 2>/dev/null)
}
