#!/usr/bin/env bash
# Flux Point harness contract, the single source of "done":
#   scripts/harness.sh --changed <file>   fast scoped checks after one edit
#   scripts/harness.sh --full             everything the DoD requires
# Exit 0 is green; anything else blocks the Stop-hook gate and the outer
# loop. The auto-detection below is a floor: wire this file to the repo's
# real build, tests, property tests, formal checks, and preview-net
# exercises.
set -euo pipefail
mode="${1:---full}"
file="${2:-}"

has() { command -v "$1" >/dev/null 2>&1; }

pm() {
  if [ -f pnpm-lock.yaml ]; then
    echo pnpm
  elif [ -f yarn.lock ]; then
    echo yarn
  else
    echo npm
  fi
}

run_script_if_present() {
  local s="$1"
  if has jq; then
    if jq -e --arg s "$s" '.scripts[$s]' package.json >/dev/null 2>&1; then
      "$(pm)" run "$s"
    fi
  elif grep -q "\"$s\"" package.json 2>/dev/null; then
    "$(pm)" run "$s"
  fi
}

changed() {
  case "$file" in
    *.ak)
      if has aiken; then aiken check; fi ;;
    *.rs)
      if has cargo; then cargo check --quiet; fi ;;
    *.ts | *.tsx | *.js | *.jsx | *.mjs)
      if [ -f tsconfig.json ] && [ -f package.json ]; then
        npx --no-install tsc --noEmit
      fi ;;
    *.dfy)
      if has dafny; then dafny verify "$file"; fi ;;
    *.tf)
      if has terraform; then terraform fmt -check "$file"; fi ;;
    *) : ;;
  esac
}

full() {
  if [ -f aiken.toml ] && has aiken; then
    aiken fmt --check .
    aiken check
  fi
  if [ -f Cargo.toml ] && has cargo; then
    cargo fmt --all -- --check
    if cargo clippy --version >/dev/null 2>&1; then
      cargo clippy --all-targets --quiet -- -D warnings
    fi
    cargo test --quiet
  fi
  if [ -f package.json ]; then
    run_script_if_present typecheck
    run_script_if_present lint
    run_script_if_present test
  fi
  if has terraform && compgen -G '*.tf' >/dev/null; then
    terraform fmt -check -recursive
  fi
}

case "$mode" in
  --changed) changed ;;
  --full) full ;;
  *)
    echo "usage: harness.sh --changed <file> | --full" >&2
    exit 64
    ;;
esac
