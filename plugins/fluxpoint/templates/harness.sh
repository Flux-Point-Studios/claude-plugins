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
# standard Windows install. Resolve by running each candidate, because a
# name on PATH is not evidence of an interpreter.
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
# not installed, so a repo carrying this harness stays runnable without it —
# but see need_gate below: "not installed" must never be read as "passed".
#
# Resolution is ordered rather than incidental. `find` guarantees no ordering,
# so `| head -1` picked whichever cached copy the filesystem happened to yield
# first — on a machine with several installed versions that was an ORPHANED
# one, while installed_plugins.json named a newer version as active. Nothing
# misbehaved only because the scripts were byte-identical across those
# versions, which is a coincidence and not a maintained property.
plugin_script() {
  # CLAUDE_PLUGIN_ROOT is set by the runtime whenever the harness runs under a
  # hook, and is version-correct by construction. FPL_PLUGIN_ROOT stays ahead
  # of it as the repo's deliberate override.
  for _root in "${FPL_PLUGIN_ROOT:-}" "${CLAUDE_PLUGIN_ROOT:-}"; do
    if [ -n "$_root" ] && [ -f "$_root/scripts/$1" ]; then
      printf '%s\n' "$_root/scripts/$1"
      unset _root
      return 0
    fi
  done
  unset _root
  # `|| true` is load-bearing: find exits 1 when ~/.claude/plugins does not
  # exist, `pipefail` propagates that through the pipe, and `set -e` then
  # killed --full on its first plugin lookup — in exactly the repos this
  # function exists to support, the ones carrying the harness without the
  # plugin installed. It failed with no output at all.
  _cands="$( { find "$HOME/.claude/plugins" -type f -name "$1" 2>/dev/null || true; } )"
  [ -z "$_cands" ] && { unset _cands; return 0; }
  # A locally-installed marketplace carries no version directory and is the
  # only copy present for that install, so it wins outright. Otherwise take
  # the highest version: `sort -V` is meaningful here precisely because the
  # remaining candidates are all cache paths sharing a prefix up to the
  # version component, which is the comparison that was nondeterministic.
  _mk="$(printf '%s\n' "$_cands" | grep '/marketplaces/' | head -1 || true)"
  if [ -n "$_mk" ]; then
    printf '%s\n' "$_mk"
  else
    printf '%s\n' "$_cands" | sort -V | tail -1
  fi
  unset _cands _mk
}

# A gate whose script is absent is not a gate that passed.
#
# Every gate below was `x="$(plugin_script f.py)"; [ -n "$x" ] && run`, so a
# missing plugin skipped it and the harness still exited 0 — silently, with
# the old "SKIPPED" notice long since refactored away. The environments where
# that happens are the ones that matter most: CI, cloud sessions, fresh clones
# and the detached worktrees used for independent verification. A check that
# does not run reads exactly like a check that passed, which is the defect
# class this harness exists to enforce against.
#
# The repo's intent decides which way it fails. Arming config on disk means
# the repo asked for that gate, so its absence is RED. With no config the gate
# is dormant by design and the run continues, saying so once. Set
# FPL_ALLOW_MISSING_GATES=1 to downgrade the red to a warning — an opt-out
# someone chose, never an absence inferred.
need_gate() {
  FPL_GATE=""
  _script="$1"; shift
  FPL_GATE="$(plugin_script "$_script")"
  if [ -n "$FPL_GATE" ]; then unset _script; return 0; fi
  _armed=""
  for _cfg in "$@"; do
    [ -e "$_cfg" ] && { _armed="$_cfg"; break; }
  done
  if [ -z "$_armed" ]; then
    unset _script _armed _cfg
    return 1
  fi
  if [ "${FPL_ALLOW_MISSING_GATES:-}" = "1" ]; then
    echo "harness: WARNING — $_script is not installed, and $_armed declares it." >&2
    echo "harness:   Running anyway because FPL_ALLOW_MISSING_GATES=1." >&2
    unset _script _armed _cfg
    return 1
  fi
  echo "harness: $_script is not installed, but $_armed declares it." >&2
  echo "harness:   This gate cannot run, so this harness cannot report green." >&2
  echo "harness:   Install the fluxpoint plugin, or set FPL_ALLOW_MISSING_GATES=1" >&2
  echo "harness:   to accept the gap on purpose." >&2
  exit 1
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
    # Armed by aiken.toml, not contracts/aiken.toml: reaching this line already
    # required aiken.toml at the ROOT, so the contracts/ path could never arm
    # anything. Arming on the manifest that got us here is what makes the
    # sentence above true — a repo that just built a validator cannot report
    # green without someone having checked it fits on chain.
    # .fluxpoint-budget.json stays first so the more specific declaration is
    # the one named when a repo has set a headroom target.
    if need_gate plutus-budget.py .fluxpoint-budget.json aiken.toml; then
      "$FPL_PY" "$FPL_GATE" --check ${FPL_PROTOCOL_PARAMS:+--params "$FPL_PROTOCOL_PARAMS"}
    fi
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
  if need_gate proof-guard.py .fluxpoint-proof-baseline.json; then
    "$FPL_PY" "$FPL_GATE" --check
  fi
  # Statement ratchet. The hatch counts above police proof bodies; this
  # polices what is being proved, because dropping a conjunct from an
  # `ensures` or deleting a property test moves no count and keeps every
  # checker green. Dormant until armed with --baseline.
  if need_gate spec-guard.py .fluxpoint-proof-baseline.json; then
    "$FPL_PY" "$FPL_GATE" --check
  fi
  # Counterexample ledger. A prover's shrunk failing input is the most
  # reusable thing it produces and it lives in a log the next command
  # overwrites. This fails when a pinned counterexample has lost the
  # regression test that carries it. Dormant with nothing recorded.
  if need_gate cex.py .fluxpoint-cex.jsonl; then
    "$FPL_PY" "$FPL_GATE" --check
  fi
  # Mutation score. Every check above asks whether the tests pass; this asks
  # whether they can fail. Cheap here on purpose — it re-runs nothing and
  # only asks whether a measurement exists and still describes this tree.
  # The expensive `--measure` belongs off-session, on a Routine.
  if need_gate mutation-guard.py .fluxpoint-mutation.json .fluxpoint-proof-baseline.json; then
    "$FPL_PY" "$FPL_GATE" --check
  fi
  # Relation gate. Every check above measures one artifact; the defects that
  # cost the most are relationships between two, and a suite stays green
  # because each half is individually correct. Dormant without a manifest.
  #
  # `if`, not `[ -n "$x" ] && cmd`: as the LAST statement of a function under
  # `set -e`, that form returns 1 when the variable is empty, so a repo whose
  # plugin is not installed failed --full for no reason at all.
  if need_gate pair-guard.py .fluxpoint-pairs.json; then
    # Fall back to the session baseline the Stop gate already exports.
    # pair-guard diffs against HEAD by default, so an agent that committed
    # its slice — which WORK_PROMPT.md step 5 tells it to do — empties
    # `git diff HEAD`, and the co-change tier reports "no changes to
    # compare" over work sitting right there in the commit. A pair with no
    # parity command is then enforced by nothing. Every other check in the
    # gate judges against where the session started; this one opted out by
    # omission, not by design.
    pair_base="${FPL_PAIR_AGAINST:-${FPL_DIFF_BASE:-}}"
    "$FPL_PY" "$FPL_GATE" --check ${pair_base:+--against "$pair_base"}
  fi
  # Recurrence gate. Every check above judges the tree; this one judges what
  # the repo keeps re-learning. A lesson filed a second time is not a
  # duplicate to collapse, it is a missing gate — so past the threshold the
  # item stops being satisfiable by another lesson and demands a command
  # whose exit code is its verdict, executed here rather than reported.
  # Armed by the lesson store itself, so it cannot be dodged by deleting the
  # manifest, and dormant in every repo that has filed none.
  if need_gate recurrence-guard.py .fluxpoint-recurrence.json \
       .claude/fluxpoint/memory.jsonl; then
    "$FPL_PY" "$FPL_GATE" --check
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
