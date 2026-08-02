# fluxpoint

The Flux Point engineering harness for Claude Code. One Definition of Done,
one Evidence discipline, two drivers over the same contract.

**Loop mode** makes one agent's cycle programmable: session bootstrap,
per-edit verification, and a Stop-hook Definition-of-Done gate that blocks
the stop while `scripts/harness.sh --full` or the hygiene scan is red.

**Graph mode** makes the organization of agents programmable: a declarative
`graph-ir` block in `WORK.md` compiles — deterministically, no model in the
loop — into a Workflow script of typed-contract nodes with verification
tiers, so the spec cannot drift from what executes.

The rule both modes share: the agent never decides "done"; the harness
does.

## Layout

- `hooks/` — SessionStart state injection, PostToolUse scoped
  verification, Stop-hook DoD gate.
- `scripts/` — `lib.sh`, `inject-state.sh`, `verify-changed.sh`,
  `dod-gate.sh` (loop); `compile-graph.py`, `record-run.py` (graph);
  `proof-guard.py` (proof-strength ratchet); `plutus-budget.py` (on-chain
  size and execution-unit limits); `pair-guard.py` (relation gate over
  declared artifact pairs); `ledger.py` (once-only guard for
  irreversible nodes); `release.py`, `inbox.py`, `wake-check.sh`
  (the park layer); `migrate.py` (pre-1.0 migration,
  plan/apply/finalize).
- `contracts/` — versioned named schemas (`FindingsV1`, `VerdictV1`,
  `HarnessCheckV1`, `DesignV1`, `SliceV1`, `RedTeamV1`).
- `commands/` — `/fluxpoint:init`, `:status`, `:migrate`, `:red-team`,
  `:proof-audit`, `:graph-design`, `:graph-run`, `:graph-audit`,
  `:release`.
- `agents/` — `red-team-reviewer` (adversarial diff review),
  `graph-auditor` (semantic review of a campaign IR), `proof-auditor`
  (did the verification get weaker, not just greener).
- `skills/` — `loop-engineering` (driver selection, conditions, the gate),
  `graph-engineering` (escalation rule, primitives, tiers, shapes).
- `templates/` — `harness.sh` contract, `WORK.md`, `WORK.feature.md`,
  `WORK.discovery.md`, `WORK_PROMPT.md`, `loop.sh`, settings snippet.
- `tests/` — `compile-test.py` (compiler invariants), `emission-test.py`
  (field-effect probes), `gate-test.sh` (Stop-gate bypass cases),
  `hooks-test.sh` (hooks.json command strings + PostToolUse behavior),
  `security-test.py` (codegen injection and red-team regressions),
  `migrate-test.sh` (migration against real pre-1.0 fixtures),
  `proof-guard-test.sh` (the ratchet, per prover), `budget-test.sh`
  (on-chain limits), `ledger-test.py` (the once-only guard, executed
  rather than grepped), `pair-test.sh` (relation gate),
  `unify-test.sh` (state model and compatibility). All of it runs from
  `scripts/harness.sh --full` in CI.

## The two contracts

**Repo side:** `scripts/harness.sh` supporting `--changed <file>` (fast,
scoped) and `--full` (everything the DoD requires), exit 0 = green. The
gate stays dormant in repos that lack it.

**Spec side:** `WORK.md` — goal, `MODE`, Definition of Done, Plan (loop
slices), Campaign (the graph IR), Constraints, Merge policy, and one
Evidence table both modes append to.

## Invariants worth naming

- A node that writes to the tree may not certify its own work. Mark it
  `mutates: true`; a later node with `independent: true` must re-derive
  the verdict. The compiler refuses to build a graph that breaks this.
- The DoD gate arms on two independent signals — the PostToolUse marker
  and dirtiness re-derived from `git` — because the marker cannot see
  source written through the Bash tool.
- Compiled `.graph.js` files are build output. Never hand-edit them; edit
  the IR and recompile.
- Budget is enforced twice and neither check is advisory: `maxNodes` is a
  compile-time ceiling priced at the worst case (discovery rounds
  included), and run-time floors stop verification and work fan-out
  separately. Anything declined is recorded `SKIPPED` and surfaces in
  Evidence as incomplete coverage.
- Ending a discovery sweep on its round ceiling is logged
  `discovery INCOMPLETE, not exhausted` — stopping early and finishing are
  different claims.
- Destructive migration is code, not prose. `migrate.py` separates
  plan/apply/finalize so nothing is deleted before the result is checked,
  and `--finalize` refuses when `WORK.md` carries fewer Evidence rows than
  the sources did.
- `budget.maxNodes` is counted at run time as well as compile time. Every
  emitted agent call goes through one `spawn()` helper, because the
  compile-time estimate leans on `expectItems`, which is a guess.
- **WORK.md is untrusted input.** The compiler turns it into JavaScript that
  is then executed, so every interpolated value is an injection surface.
  Free text goes through `js_str`/`js_template`; anything emitted as a JS
  *identifier* (node ids, `lists` keys) is constrained by `IDENT` at
  validation; halt literals are re-emitted from their parsed value rather
  than pasted from the source. `tests/security-test.py` compiles real
  payloads and executes the output to prove they stay inert.
- Every field the IR accepts must demonstrably change what the compiler
  produces. `tests/emission-test.py` probes each one and fails on a field
  that changes nothing, because the recurring defect here was never a wrong
  output — it was a silent one. Unknown fields are compile errors for the
  same reason.

## Relations, not just artifacts

Every gate above measures one artifact. The defects that cost the most are
relationships between two: an on-chain predicate tightened without its
off-chain builder, a migration and the schema it assumes, both ends of a
wire format. Each side passes its own tests — the suite is green *because*
each half is individually correct — and the pair is what breaks.

`scripts/pair-guard.py` checks declared pairs from a repo-owned
`.fluxpoint-pairs.json`, in two tiers. **Co-change** reports a diff that
moves one side and not the other; it is a smoke alarm and proves only that
somebody touched both files. **Parity** runs a declared command whose job
is to evaluate the two together, making the relation itself an exit code —
that is the real check, and `--list` names every pair that lacks one rather
than letting a half-checked relation read as covered. Only the consuming
repo can write parity vectors; the plugin supplies the slot.

## Blocked is a state

A node no agent can run — 2-of-3 signing, a third party's withdrawal, a
72h timelock — used to leave the engine two options: halt everything, or
drop it and continue with a `null`. Neither is "work the other branches".

Parking is a last resort: `release.whyNotAgent` must name what makes the
step impossible for an agent, and `graph-auditor` attacks that reason,
because a CLI, an API or a headless browser covers most of what feels
human-only. `actor: human` / `actor: third-party` emits no worker spawn.
Absent a release the node reports BLOCKED — but never empty: one advisor
runs first, contracted to `DecisionV1` so a bare "ask the operator" cannot
satisfy it, told to challenge `whyNotAgent` before accepting it and to
return the concrete automatable path if one exists, otherwise the
recommended course with the rejected alternatives and the strongest
objection to each. The run goes INCOMPLETE and continues, and dependents
inherit BLOCKED rather than a null that reads like a failure. `/fluxpoint:release`
validates the operator's proof against the node's `proofContract` and
refuses an adjective — the campaign resumes on that file, so it is not a
formality. Every block, expired `wake` deadline and refused confirmation
lands in an inbox that `/fluxpoint:status` leads with and SessionStart
counts, because a campaign that parks quietly is a worse failure than one
that halts loudly. Deliberately not a DAG scheduler.

## Verified work

A prover behind this harness is the best case for it: `aiken check`,
`dafny verify`, `lake build`, `coqc` give a crisp exit code. But exit 0 is
weaker than it looks — every prover ships a way to discharge a goal without
proving it, and a checker exits 0 on an assumed lemma exactly as on a
proved one.

- **The prover runs in `--full`.** Per-file checking on edit is feedback;
  the gate that decides done must invoke the prover over the project.
- **`scripts/proof-guard.py` ratchets escape hatches.** `--baseline`
  records `todo`/`expect` (Aiken), `assume`/`{:axiom}` (Dafny), `sorry`
  (Lean/Isabelle), `Admitted` (Coq), `#[verifier::external_body]` (Verus),
  `ASSUME` (TLA+), and verification-disabling CLI flags into a committed
  file. `--check` fails when a category rises. Falling is always allowed.
- **`scripts/plutus-budget.py` gates submittability.** Correct and
  submittable are different properties and only one has a prover:
  `aiken check` is green on a validator too large to go on chain. It reads
  `compiledCode` out of the plutus.json blueprint, folds the entries a
  multi-purpose validator repeats, and fails on the protocol's `maxTxSize`
  unconditionally — that limit is a fact, not a preference. Headroom is the
  part you configure (`.fluxpoint-budget.json`), and `--params` takes real
  `cardano-cli query protocol-parameters` output so the constants here are a
  fallback rather than the authority. Execution units cannot be derived from
  a compiled artifact — they are a property of evaluating a script against a
  transaction — so measured values go in the same file and an absent
  measurement is reported as unmeasured rather than passed.
- **`/fluxpoint:proof-audit`** runs the ratchet, checks the prover is
  actually in `--full`, then the `proof-auditor` agent for what a count
  cannot see: vacuity, specification drift, assumption laundering, tests
  that cannot fail, negative tests that fail for the wrong reason, unproved
  surface, on-chain budgets, solver `unknown` read as success.

It also counts two weakenings that add no hatch for a line scan to find:
Aiken tests that cannot fail (a body that is a bare boolean, a
self-comparison, or empty) and predicates that decide nothing (`fn
credential_matches(..) -> Bool { True }` — the call site still reads as a
checked conjunction). Both catch the same trade: swapping a `todo` for
`True` lowers `aiken.todo` to zero and looks like progress. Only
unambiguous cases are flagged, and a pre-existing one is absorbed by the
baseline rather than indicting the repo; a false positive would train
people to ignore the ratchet. The ratchet remains a floor, not a ceiling: a
theorem that lost a conjunct, or a `fail` test that trips an earlier guard
than the one it is named for, still needs the semantic pass.

## Migrating from the split plugins

`/fluxpoint:migrate` folds `LOOP.md` and `GRAPH.md` into one `WORK.md`,
moves local state under `.claude/fluxpoint/`, and rewires settings. Until
then the hooks still honor `LOOP.md`, so nothing breaks mid-migration.

Requires `git` and `python3`; `jq` preferred with a python3 fallback built
into the hooks. Graph mode additionally needs a Claude Code version with
the Workflow tool.

Full documentation lives in the repository root README.
