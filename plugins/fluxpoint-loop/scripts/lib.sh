#!/usr/bin/env bash
# Shared helpers for fluxpoint-loop hooks. Sourced, never executed.
# JSON handling prefers jq and falls back to python3 so the hooks work on
# machines without jq installed.

fpl_state_dir() {
  printf '%s/.claude/fluxpoint-loop' "${CLAUDE_PROJECT_DIR:-$PWD}"
}

# fpl_json_get <dotted.key>  — reads JSON on stdin, prints the value or
# nothing. Booleans print as true/false to match jq -r semantics.
fpl_json_get() {
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg k "$1" 'getpath($k / ".") // empty' 2>/dev/null
  else
    python3 -c '
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
    python3 -c '
import json, sys
a = sys.argv[1:]
print(json.dumps(dict(zip(a[::2], a[1::2]))))' "$@"
  fi
}

_fpl_skip_path() {
  case "$1" in
    *.md|*.svg|*.min.js|*.lock|package-lock.json|pnpm-lock.yaml|yarn.lock) return 0 ;;
    .claude/*|*/.claude/*|node_modules/*|*/node_modules/*) return 0 ;;
    target/*|*/target/*|dist/*|*/dist/*|build/*|*/build/*|.git/*) return 0 ;;
  esac
  return 1
}

# Hygiene scan per the Flux Point Definition of Done. Scans lines added in
# uncommitted work plus untracked files; committed history is CI's job.
# Emits one "file: line" per finding.
fpl_scan_hygiene() {
  local pat='TODO|FIXME|\bXXX\b|for now|\.unwrap\(\)|\.(skip|only)\(|\bxit\(|#\[ignore\]'
  local catch='catch[[:space:]]*(\([^)]*\))?[[:space:]]*\{[[:space:]]*\}'
  local f added
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      _fpl_skip_path "$f" && continue
      added="$(git diff HEAD -U0 --no-color -- "$f" | grep -E '^\+[^+]' || true)"
      [ -n "$added" ] || continue
      printf '%s\n' "$added" | grep -EI "$pat" | sed "s|^+|$f: |" || true
      printf '%s\n' "$added" | grep -EI "$catch" | sed "s|^+|$f: |" || true
    done < <(git diff HEAD --name-only --diff-filter=ACMR 2>/dev/null)
  fi
  while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    _fpl_skip_path "$f" && continue
    grep -nEIH "$pat" "$f" || true
    grep -nEIH "$catch" "$f" || true
  done < <(git ls-files --others --exclude-standard 2>/dev/null)
}
