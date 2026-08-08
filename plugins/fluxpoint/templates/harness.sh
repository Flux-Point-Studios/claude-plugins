#!/usr/bin/env bash
# Flux Point harness contract, the single source of "done":
#   scripts/harness.sh --changed <file>   fast scoped checks after one edit
#   scripts/harness.sh --full             everything the DoD requires
# Exit 0 is green; anything else blocks the Stop-hook gate and the outer
# loop. The auto-detection below is a floor: wire this file to the repo's
# real build, tests, property tests, formal checks, and preview-net
# exercises.
set -euo pipefail

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

# Locate a script shipped with the plugin. Prints nothing when the plugin is
# not installed, so a repo carrying this harness stays runnable without it.
plugin_script() {
  if [ -n "${FPL_PLUGIN_ROOT:-}" ] && [ -f "$FPL_PLUGIN_ROOT/scripts/$1" ]; then
    printf '%s\n' "$FPL_PLUGIN_ROOT/scripts/$1"
  else
    # `|| true` is load-bearing: find exits 1 when ~/.claude/plugins does not
    # exist, `pipefail` propagates that through the pipe, and `set -e` then
    # killed --full on its first plugin lookup — in exactly the repos this
    # function exists to support, the ones carrying the harness without the
    # plugin installed. It failed with no output at all.
    { find "$HOME/.claude/plugins" -type f -name "$1" 2>/dev/null || true; } | head -1
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
    # `aiken check` has no --json flag: it emits structured JSON whenever
    # stdout is not a TTY and sends every diagnostic to stderr, so a plain
    # redirect buys the machine form for free and the operator still sees
    # the Compiling/Summary lines.
    #
    # Why a file and not `aiken check | cex.py --ingest`: this script runs
    # under `set -euo pipefail`, where a pipeline reports the last non-zero
    # status. A parser bug in the recorder would then be indistinguishable
    # from a failed proof, and the pipeline would abort before anything
    # downstream ran. Capture, record, re-raise — the prover's exit code
    # stays the gate and the recorder never gets a vote.
    #
    # The seed is fixed so shrinking is reproducible: aiken draws a random
    # u32 per run otherwise, and the same bug then shrinks to a different
    # value each time, which would file a new counterexample per run.
    aiken_out="$(mktemp)"
    aiken_rc=0
    aiken check --seed "${FPL_AIKEN_SEED:-1}" >"$aiken_out" || aiken_rc=$?
    if [ "$aiken_rc" -ne 0 ]; then
      # Unconditionally, before anything else can drop it: in a repo that
      # carries this harness without the plugin installed, this file is the
      # only record of which test failed and why.
      cat "$aiken_out" >&2
    fi
    cx="$(plugin_script cex.py)"
    if [ -n "$cx" ]; then
      "$FPL_PY" "$cx" --ingest --tool aiken --from "$aiken_out" \
        --exit "$aiken_rc" || true
    fi
    rm -f "$aiken_out"
    if [ "$aiken_rc" -ne 0 ]; then return "$aiken_rc"; fi
    # `check` typechecks and runs tests; `build` is what actually produces the
    # on-chain artifact, and it can fail where check passes. A validator that
    # will not build is not done.
    aiken build
    # Correct and submittable are different properties. A validator larger
    # than maxTxSize cannot go on chain at all, and the prover has nothing
    # to say about it. Protocol limits are enforced unconditionally; set a
    # headroom target in .fluxpoint-budget.json when you want one.
    pb="$(plugin_script plutus-budget.py)"
    [ -n "$pb" ] && "$FPL_PY" "$pb" --check ${FPL_PROTOCOL_PARAMS:+--params "$FPL_PROTOCOL_PARAMS"}
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
  pg="$(plugin_script proof-guard.py)"
  [ -n "$pg" ] && "$FPL_PY" "$pg" --check
  # Statement ratchet. The hatch counts above police proof bodies; this
  # polices what is being proved, because dropping a conjunct from an
  # `ensures` or deleting a property test moves no count and keeps every
  # checker green. Dormant until armed with --baseline.
  sg="$(plugin_script spec-guard.py)"
  [ -n "$sg" ] && "$FPL_PY" "$sg" --check
  # Counterexample ledger. A prover's shrunk failing input is the most
  # reusable thing it produces and it lives in a log the next command
  # overwrites. This fails when a pinned counterexample has lost the
  # regression test that carries it. Dormant with nothing recorded.
  cc="$(plugin_script cex.py)"
  [ -n "$cc" ] && "$FPL_PY" "$cc" --check
  # Mutation score. Every check above asks whether the tests pass; this asks
  # whether they can fail. Cheap here on purpose — it re-runs nothing and
  # only asks whether a measurement exists and still describes this tree.
  # The expensive `--measure` belongs off-session, on a Routine.
  mg="$(plugin_script mutation-guard.py)"
  [ -n "$mg" ] && "$FPL_PY" "$mg" --check
  # Relation gate. Every check above measures one artifact; the defects that
  # cost the most are relationships between two, and a suite stays green
  # because each half is individually correct. Dormant without a manifest.
  #
  # `if`, not `[ -n "$x" ] && cmd`: as the LAST statement of a function under
  # `set -e`, that form returns 1 when the variable is empty, so a repo whose
  # plugin is not installed failed --full for no reason at all.
  pr="$(plugin_script pair-guard.py)"
  if [ -n "$pr" ]; then
    "$FPL_PY" "$pr" --check ${FPL_PAIR_AGAINST:+--against "$FPL_PAIR_AGAINST"}
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
