# fluxpoint — roadmap: compounding, memory, and proof

This document answers three questions asked of the v1.12 system: what would
make the loop and graph layers compound rather than merely coexist; what is
missing for a graph-engineered system that outlives any one context window;
and how far formal verification can be pushed so that "done" means proved,
not asserted. It was produced the way this plugin says work should be
produced — a fan-out of independent subsystem reviews, adversarially read,
then designed against — and it records the gaps honestly, including the
ones that are open today.

The one-sentence diagnosis: **the primitives are right and the circuits are
open.** Nearly every layer produces something a later layer could consume
— findings, refuter objections, counterexamples, seen-sets, decisions,
attested exit codes — and almost none of it flows back. Closing those
circuits is worth more than any new capability, because each closure
multiplies work the system already does instead of adding work it doesn't.

## Part 1 — the open circuits

Verified against the code, these are the places where one layer's output is
another layer's missing input:

| # | Circuit | Today | Cost of the gap |
|---|---|---|---|
| 1 | `runs/*.json` → future campaigns | Write-only. Refuter objections, killed findings with their kill reasons, and advisor recommendations are recorded and never re-injected; `inject-state.sh` shows one summary line and 5 Evidence rows. | Every campaign re-derives what a prior panel already established. |
| 2 | Discovery seen-sets → the next sweep | `emit_repeat`'s seen-set is an in-run local. | A re-run sweep re-finds and re-judges everything from round one; verification fan-out — the measured dominant cost — is spent re-litigating settled claims. |
| 3 | Loop-mode work → Evidence | The agent hand-writes its own rows (`WORK_PROMPT.md` step 4) and checks its own DoD boxes; nothing re-executes a Proof cell. | "A claim without a row is false" has no converse: a fabricated row is injected as ground truth into every future session. |
| 4 | Loop-mode work → Decisions | The Decisions table is populated only by graph `DecisionV1` nodes; loop mode has no capture step and `hooks.json` wires no PreCompact. | The named failure — "a fresh context silently re-decides the other way" — is still fully open for loop work, which is most work. |
| 5 | Prover output → anywhere | The shrunk counterexample from `aiken check` (or any prover) lives in `full.log`, clobbered per run. | The single most valuable artifact a prover produces evaporates; a fixed bug carries no pinned regression. |
| 6 | Frozen decisions → the next campaign | ~~`imports` resolved by the orchestrating agent by hand; the emitted guard checked key presence only.~~ **Closed**: the compiler resolves `imports` from recorded runs and embeds the records (see Part 2, slice 1). | — |
| 7 | Red-team / proof-audit verdicts → the gate | `SHIP/BLOCK` is typed (`RedTeamV1`) but `proof-auditor`'s `SOUND/WEAKENED` is prose; neither reaches `dod-gate.sh`. | "Treat WEAKENED as harness-red" is policy, not mechanism. |
| 8 | Evidence → freshness | Rows are never replayed; a row true at write time is injected unchanged after the code it describes was rewritten. | Memory rots silently, and the bootstrap presents rot as fact. |

## Part 2 — the trust chain

The enforcement chain is strong at the session boundary (Stop gate,
deterministic harness, hygiene scan) and weakest in the middle, where an
LLM sits between a real verdict and its record. In order of blast radius:

**1. Imports were agent-couriered — closed.** `ledger.py --load` and
`release.py --load` existed, but decisions — the artifact class the system
protects hardest — were resolved by the orchestrating agent by hand into
`args._decisions`, and the emitted guard checked only that *some* record
arrived. A fabricated or stale record satisfied it. As of this roadmap's
first slice, `compile-graph.py` resolves each import from
`.claude/fluxpoint/runs` at compile time (`'latest'` = newest recorded run
carrying the decision, else the named runId), shallow-validates the record
against `DecisionV1`'s required fields, and **embeds the record and its
source runId in the generated script**. No hand-assembled map exists for a
launch to trust; a missing or gutted record fails `--check` in preflight; a
malformed run artifact is a hard error (what `'latest'` names must not
depend on which artifacts happened to parse); a campaign that `decides` an
id it also `imports` is rejected (re-deciding a frozen decision is the
exact overturn imports exist to prevent); and `record-run.py` no longer
re-files imported decisions as fresh rows — the Evidence claim names them
as `honors <id>@<runId>` instead.

**2. Exit codes inside graphs are transcribed, not captured.** The
"independent harness gate" node runs `scripts/harness.sh` itself and then
*writes* `{"exit": 0}` into `HarnessCheckV1` — and that self-reported
integer is what `haltWhen` consumes before an irreversible effect. The fix
is attestation: a PostToolUse hook on `Bash` that, when the command matches
a gate named in a committed `.fluxpoint-gates.json` allowlist, appends
`{attestId, gate, commandSha, exit, logSha256, ts}` to an append-only
`.claude/fluxpoint/attest.jsonl` (ledger read discipline: malformed rows
are hard errors). Then: a `verify: "prove:<gate>"` tier whose gate name
must resolve in the manifest at compile time (the removed-`harness`-tier
lesson: a tier must emit something real), an `ExecutionV1` contract
(`{gate, exit, attestId, logSha256}`, additionalProperties false), and a
`record-run.py` cross-check that rewrites any harness/execution result
whose attestId or exit does not match the attest log to
`TAMPERED-EXECUTION` (the `BLOCKED-REDTEAM` override pattern, reused). An
agent can still trim prose in a summary; it can no longer mint a green
exit. First slice: the hook plus the manifest plus the cross-check in
warn mode — no compiler changes, and the attestation corpus starts
accruing immediately.

**3. Loop-mode evidence is self-certified.** `evidence.py --mint` takes a
claim and a command, **executes the command itself** from an allowlist
declared in a committed `.fluxpoint-tools.json`, records the actual exit
and HEAD sha, and splices the row. The agent requests evidence; the script
manufactures it — the loop-mode twin of `record-run.py`. The allowlist is
the same idiom that must eventually contain `wake-check.sh`'s `sh -c`
exposure, so this closes one arbitrary-shell channel while refusing to
open another. Follow-ons, in order: `--replay` (re-execute proof cells of
current rows on a bounded schedule; append REVERIFIED / STALE / REFUTED
status rows, never mutate — supersession, not erasure), validity-graded
SessionStart injection (each row rides in with its replay status and age),
and DoD lines with a `— proof: <cmd>` tail that `dod-gate.sh` re-runs
before honoring a checked box.

**Known bypasses to close while the above lands.** (a) Write-via-Bash then
commit in the same session: `fpl_code_dirty` diffs against HEAD, so a
committed tree plus a never-fired PostToolUse marker leaves the Stop gate
disarmed with the harness never run — compare against the branch point or
the last-gated sha instead. (b) The hygiene scan has the same HEAD blind
spot for committed markers. (c) `fpl_harness_modified` warns on a green
pass under an edited `scripts/harness.sh` but guards none of the wider
trust base — `package.json` scripts, `.fluxpoint-proof-baseline.json`,
`.fluxpoint-pairs.json`, `.fluxpoint-budget.json` — and an agent can
re-run `proof-guard.py --baseline` to absorb a rise; baselines deserve
the same modified-contract warning, or a PreToolUse guard. (d)
`wake-check.sh` runs `sh -c` on predicates that originate in the IR;
predicates need the allowlist treatment.

## Part 3 — the memory layer

The context strategy already matches where the field converged: durable
state in versioned files, a curated always-present injection at
SessionStart, isolated node contexts whose typed contracts define what
survives the boundary. What is missing is the tier discipline and the
consolidation process. In the standard taxonomy: SessionStart injection is
core memory, `runs/` is episodic memory, Evidence/Decisions are semantic
memory — and nothing performs consolidation, so episodic never becomes
semantic without an agent volunteering it.

**3a. Lessons: `memory.jsonl` + `LessonV1` + IR `memory:`.** A new
append-only `.claude/fluxpoint/memory.jsonl` owned by `scripts/memory.py`
(ledger read discipline; identity `tag|dedupeKey`, latest-state-wins like
the inbox). `record-run.py` grows a filing pass symmetric to its
ledger/inbox passes: every `FindingsV1` node's items become `LessonV1`
rows — survivors with the objections the panel already keeps, killed items
with their kill count and top objection (today discarded by
`verifyItems`' filter) — mechanically, keyed by the node's own `dedupeBy`,
provenance `{runId, node}` required and checked against `runs/`. The IR
gains `memory: {seed: <tag>, emit: <tag>}` (closed-registry, emission-test
probe like every field): `seed` pre-loads a discovery node's seen-set and
`{{seen}}` from disk via `memory.py --load`, and killed-lesson claims ride
into refuter prompts as priors. The Evidence row gains "seeded N prior
keys, M new beyond the prior frontier", so an incremental sweep is
distinguishable from a fresh one. This is the single most multiplicative
change available: `untilDryRounds` converges against cumulative knowledge,
so the same ceiling buys monotonically deeper coverage every run, and
panels stop re-litigating what earlier panels killed. Seeds are advisory
(they suppress re-reporting cost, never auto-kill a re-found item), and
`--since <git-rev>` drops lessons older than the last touch of the files
they name, so a stale kill cannot hide a regression.

**3b. Counterexamples: `cex.jsonl`.** `cex.py --ingest` parses prover
output tee'd from the harness (aiken's shrunk counterexample block first;
Kani traces and Apalache ITF later — parser drift is a loud INGEST-FAILED
inbox row, never a silent drop). `--pin <id>` generates a concrete
regression test from the *recorded* input (never from agent prose) and
`--check` in `--full` fails when a pinned counterexample has lost its test
file. A pinned cex is the one memory artifact that re-verifies itself
forever, because its regression runs in every future `harness.sh --full` —
the "claim without a row is false" axiom finally given its converse for
prover claims. Repair campaigns get their canonical shape: gate red → cex
ingested → the implementer's context packet *is* the minimal failing
input → gate re-run → pin on green.

**3c. Loop-mode decision capture + flush-before-death.**
`decision.py --record` validates a piped `DecisionV1` against the existing
schema and splices it under the Decisions header with `Source: loop` — the
schema's anti-lazy floors (two options with real objections, rationale ≥40)
now police loop mode too; `--none <reason>` makes silence explicit. A new
PreCompact hook tells the summarizer exactly what is already durable
("WORK.md and .claude/fluxpoint/* are authoritative; decisions and failed
approaches not yet there must be preserved verbatim"), which turns
compaction from the largest memory-loss event into a non-event for
anything that matters. Optionally, behind `FPL_DISTILL=1` until the
false-positive rate is measured: the Stop gate blocks once when code
changed but the Decisions/Notes hash didn't and no `--none` marker exists —
"done" extends from code-green to memory-flushed.

**3d. Consolidation as an ordinary campaign.** A shipped
`WORK.consolidate.md` template — read recent runs and `memory.jsonl` →
propose merges/supersessions → `skeptic:1` refutation → a `mutates` node
appends via `memory.py`, verified by an `independent` node re-reading the
store — run by a scheduled Routine. Memory curation thereby produces
provenance, spends declared budget, and passes independent verification
like any other work; no bespoke daemon. Supersession is bi-temporal
(append an invalidating row; never mutate), which preserves the
append-only property everywhere. Cross-repo federation comes last and
allowlisted: a committed `.fluxpoint-federation.json` maps aliases to repo
paths (the compiler rejects an alias not in the manifest — the wake-check
lesson applied in advance), org-scope lessons are injected as advisory
context only, never as gate inputs, and promotion to org scope happens
only through the consolidation campaign's refute-and-verify path.

## Part 4 — formal verification

A prover's exit code is the strongest gate this harness can hold, and
agents gaming provers is measured behavior, not speculation. There are
four escape routes; `proof-guard.py` closes the first. The roadmap closes
the rest, and every mechanism lands as an exit code the existing gate
already consumes.

**4a. `spec-guard.py` — the statement ratchet (route 2: weaken the
theorem).** proof-guard polices proof *bodies*; the statements are
unguarded — a dropped `ensures` conjunct, a widened `requires`, or a
deleted property keeps every hatch count flat and every prover green, and
the proof-auditor's own checklist calls this the most common way verified
code regresses. `--scan` extracts every obligation statement per tool
(Dafny requires/ensures/invariant per method, Lean/Coq theorem signatures,
Aiken test + property names with their fuzzer types), normalizes and
hashes each into a `spec` section of the existing baseline file. `--check`
fails when a baselined statement changed or disappeared without a
Decisions row whose evidence names the obligation id — weakening a spec
becomes a frozen, re-injected, justified decision or a red gate. Adding
obligations is always free. `--axioms` runs the prover's own assumption
audit (`#print axioms`, `Print Assumptions`, Dafny's listing) over
baselined headline theorems: a *new* axiom in a dependency set is red even
when counts are flat, closing assumption laundering; where the toolchain
is absent the output says "axiom audit not run" rather than reading clean.
With both ratchets armed, the LLM auditor's residue shrinks to what only
judgment can see — properties proved about unreachable states — which is
the correct division of labor.

**4b. Attestation (route 3: transcribe the verdict).** Part 2, item 2.
Composes: `prove:spec-guard` as the ordered pre-merge gate makes
spec-freeze a graph-shape property, and an irreversible ceremony's
mandatory earlier `haltWhen` guard finally consumes a machine-minted exit.

**4c. `mutation-guard.py` — the judge of the judge (route 4: gut the
tests).** In loop-mode TDD the same agent writes the code and its judge;
proof-guard's structural scans catch only the unambiguous `{ True }`
vacuities. Mutation score is the one metric that cannot be faked by merely
executing code. Dispatch by toolchain (cargo-mutants, mutmut, Stryker; for
Aiken a first-party operator set — negate comparisons, swap `&&`/`||`,
replace an arm body with `True` — as source-rewrite plus `aiken check` per
mutant, feasible because proof-guard already brace-parses Aiken).
`--measure` stamps `{score, survivors, headSha, ts}` into the baseline;
`--check` fails on a score below the ratcheted floor *or* a stamp more
than N commits stale — the plutus-budget honesty rule: unmeasured is loud,
never a silent pass. Expensive measurement runs ride the existing
wake/Routine layer off-session. Survivors are counted absolutely as well
as by ratio (deleting tests to shrink the mutant surface must not raise
the score), and a `mutants-allow` annotation is itself a proof-guard hatch
category, so allowlisting ratchets too.

**4d. The attack taxonomy as scaffolded obligations (route 0: never
specify the property).** For Cardano work, `/fluxpoint:init` on an Aiken
repo should scaffold the known validator vulnerability classes as named
property tests over `aiken/fuzz` — double satisfaction (count validation
tokens across *all* inputs and outputs), datum hijack (output address
checks), token-name confusion, unbounded-value — whose names *are*
spec-guard obligation ids. A Cardano campaign then starts with the attack
taxonomy already baselined as hashed, prover-checked obligations, and the
red-team agent audits the residue instead of re-deriving the taxonomy. The
assurance ladder maps onto the existing tiers cleanly — automated tooling
= exit-code gates in `--full`; in-depth audit = skeptic/panel tiers plus
the reviewer agents; full FV = proof against the Plutus metatheory — and
the attested chain (statement hash → prover exit → mutation score →
Evidence row) is reconstructible certification evidence rather than a
vibe.

**Spec-first campaign shape.** A canonical `WORK.verified.md`: a spec node
authors obligations (frozen via spec-guard, adversarially refuted by a
panel — once implementation cannot move the target, the spec *is* the
attack surface); the implementer `mutates` against them; a `prove:` gate
attests the prover's exit; red-team closes. The verification condition
becomes the Definition of Done before any implementation exists — "the
harness decides done" taken to its logical end. Stated honestly: formal
verification moves trust to the spec rather than eliminating it, which is
why the ratchet, the adversarial spec review, and the mutation floor are
the load-bearing pieces, not the prover invocation.

## Part 5 — build order

Ordered by leverage over effort; every slice lands green through the
existing harness and each is independently shippable.

| # | Slice | Status |
|---|---|---|
| 1 | Compile-time `imports` resolution + embedding (`resolve_imports`, `DECISIONS_IMPORTED`, record-run dedup, re-decide invariant) | **shipped** with this document |
| 2 | `exec-attest.sh` + `.fluxpoint-gates.json` + record-run cross-check in warn mode | next |
| 3 | `memory.py` + record-run filing of findings/objections + `memory.seed` seen-set seeding | |
| 4 | `spec-guard.py` for Aiken + Dafny statements, wired into `templates/harness.sh --full` | |
| 5 | `cex.py --ingest/--pin/--check` for `aiken check` output | |
| 6 | `evidence.py --mint` + `WORK_PROMPT.md` requiring minted rows for harness-checkable claims | |
| 7 | Stop-gate branch-point dirtiness + baseline/trust-base modified-contract warnings | |
| 8 | `decision.py` + PreCompact hook (distill gate behind `FPL_DISTILL=1`) | |
| 9 | `mutation-guard.py` wrapping cargo-mutants, floor + staleness stamp | |
| 10 | `ExecutionV1` + `verify: "prove:<gate>"` tier + TAMPERED-EXECUTION enforcement | |
| 11 | `WORK.consolidate.md` + consolidation Routine; evidence `--replay` + graded injection | |
| 12 | Attack-taxonomy scaffolding in `/fluxpoint:init` for Aiken repos; `WORK.verified.md` | |
| 13 | Cross-repo federation (`.fluxpoint-federation.json`, org-scope lessons) | |

Every proposal above follows the house idiom — closed field registries
with emission probes, append-only stores whose reads hard-fail on
corruption, deterministic scripts deciding green, incompleteness that
names itself — so each one strengthens the invariants it touches instead
of adding a parallel mechanism beside them.
