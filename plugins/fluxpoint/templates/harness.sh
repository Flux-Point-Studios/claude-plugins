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
    *.lean)
      if has lake; then lake build; fi ;;
    *.v)
      if has coqc; then coqc -q "$file"; fi ;;
    *.tf)
      if has terraform; then terraform fmt -check "$file"; fi ;;
    *) : ;;
  esac
}

full() {
  if [ -f aiken.toml ] && has aiken; then
    aiken fmt --check .
    aiken check
    # `check` typechecks and runs tests; `build` is what actually produces the
    # on-chain artifact, and it can fail where check passes. A validator that
    # will not build is not done.
    aiken build
    # Worth adding once you know your limits: parse plutus.json and fail if a
    # validator exceeds your script-size or ex-unit budget. A proof of
    # correctness does not help if the script cannot be submitted.
  fi
  # Provers run in --full, not only per-file. A gate that decides "done"
  # without invoking the prover is not a gate.
  if has dafny && compgen -G '**/*.dfy' >/dev/null 2>&1; then
    dafny verify .
  fi
  if [ -f lakefile.lean ] || [ -f lakefile.toml ]; then
    if has lake; then lake build; fi
  fi
  if [ -f _CoqProject ] && has coq_makefile; then
    coq_makefile -f _CoqProject -o CoqMakefile && make -f CoqMakefile
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
  # Proof-strength ratchet. A prover exits 0 on an assumed lemma exactly as it
  # does on a proved one, so the count of escape hatches may fall but never
  # rise. Dormant in repos with no proof-language files.
  if [ -n "${FPL_PLUGIN_ROOT:-}" ] && [ -f "$FPL_PLUGIN_ROOT/scripts/proof-guard.py" ]; then
    python3 "$FPL_PLUGIN_ROOT/scripts/proof-guard.py" --check
  else
    pg="$(find "$HOME/.claude/plugins" -type f -name proof-guard.py 2>/dev/null | head -1)"
    [ -n "$pg" ] && python3 "$pg" --check
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
