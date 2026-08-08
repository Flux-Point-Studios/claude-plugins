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
| 1 | `runs/*.json` → future campaigns | ~~Write-only.~~ **Closed for findings**: a node's judged items — survivors and the panel's kills with their objections — are filed as `LessonV1` rows by `record-run.py` (Part 3a). Advisor recommendations and per-node objections beyond the top one remain unindexed. | — |
| 2 | Discovery seen-sets → the next sweep | ~~`emit_repeat`'s seen-set is an in-run local.~~ **Closed**: `memory: {seed, emit}` carries the frontier across runs; run two opens where run one stopped. | — |
| 3 | Loop-mode work → Evidence | **Partly closed**: the Stop gate now writes its own `Source: gate` rows and SessionStart labels agent-written rows as assertions (Part 2, slice 6). Still open: `WORK.md` itself is unguarded — `verify-changed.sh` and the hygiene scan both skip `*.md` — so a row can still be edited after the fact, and nothing re-executes a Proof cell. | A forged row is now a *visible* anomaly rather than an invisible one; making it impossible needs the work file guarded. |
| 4 | Loop-mode work → Decisions | **Closed**: `decision.py --record`/`--none` gives loop mode the bus with the `DecisionV1` floors enforced, and a PreCompact hook plus SessionStart tell the post-compaction context when reasoning was lost (Part 3c, slice 8). | — |
| 5 | Prover output → anywhere | The shrunk counterexample from `aiken check` (or any prover) lives in `full.log`, clobbered per run. | The single most valuable artifact a prover produces evaporates; a fixed bug carries no pinned regression. |
| 6 | Frozen decisions → the next campaign | ~~`imports` resolved by the orchestrating agent by hand; the emitted guard checked key presence only.~~ **Closed**: the compiler resolves `imports` from recorded runs and embeds the records (see Part 2, slice 1). | — |
| 7 | Real exit codes → claimed exit codes | ~~A gate node typed its own `{"exit": 0}` and nothing could contradict it.~~ **Closed in warn mode**: a PostToolUse hook mints the runtime's exit for declared gates and `record-run.py` cross-checks every claim (see Part 2, slice 2). | — |
| 8 | Red-team / proof-audit verdicts → the gate | `SHIP/BLOCK` is typed (`RedTeamV1`) but `proof-auditor`'s `SOUND/WEAKENED` is prose; neither reaches `dod-gate.sh`. | "Treat WEAKENED as harness-red" is policy, not mechanism. |
| 9 | Evidence → freshness | Rows are never replayed; a row true at write time is injected unchanged after the code it describes was rewritten. | Memory rots silently, and the bootstrap presents rot as fact. |

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

**2. Exit codes inside graphs were transcribed, not captured — warn mode
shipped.** The "independent harness gate" node runs `scripts/harness.sh`
itself and then *writes* `{"exit": 0}` into `HarnessCheckV1`, and that
self-reported integer is what `haltWhen` consumes before an irreversible
effect. As of slice 2, a PostToolUse hook on `Bash`
(`scripts/exec-attest.sh` → `scripts/attest.py --record`) records the
runtime's own `tool_response.exit_code` for every command declared in a
committed `.fluxpoint-gates.json`, appending
`{attestId, gate, command, commandSha, exit, logSha256, headSha, when,
sessionId, agent}` to `.claude/fluxpoint/attest.jsonl` — ledger read
discipline, so a malformed row is a hard error rather than a quiet shrink.
`record-run.py` cross-checks every claimed gate exit against that log and
reports `ATTESTED`, `UNATTESTED`, or `MISMATCH`; a mismatch is printed to
stderr, filed as an `attest-mismatch` inbox row, carried into the Evidence
row and the run artifact's `attestation.tally`, and surfaced at
SessionStart. Subagent Bash calls fire the hook too, so gates run inside
graph nodes are attested and the row records which agent ran them.

Two properties worth naming, both pinned by tests. Only the exact declared
invocation matches (whitespace and a leading `bash `/`./` are normalized,
nothing else): `scripts/harness.sh --full || true` reports 0 for a red
harness, so it must not be able to borrow the gate's name — it is attested
as nothing, and shows up as UNATTESTED. And absence is never guilt: an
executor that does not route through the Bash tool produces no rows, which
is reported as its own state rather than as a failure, because the
alternative is a false-red generator.

Still to come, once the corpus shows what normal looks like: an
`ExecutionV1` contract (`{gate, exit, attestId, logSha256}`,
additionalProperties false), a `verify: "prove:<gate>"` tier whose gate
name must resolve in the manifest at compile time (the
removed-`harness`-tier lesson: a tier must emit something real), and
promotion of MISMATCH from warn to `TAMPERED-EXECUTION` via the
`BLOCKED-REDTEAM` override pattern. Warn mode first is deliberate — a check
that starts by failing runs gets switched off before it has established a
baseline.

**3. Loop-mode evidence was self-certified — closed in slice 6, and not the
way this section proposed.** The design below (an `evidence.py --mint` the
agent runs, which executes a gate and manufactures a row) was written up,
adversarially reviewed from four angles, and rejected. The decisive
objection: *a subprocess an agent launches cannot outrank the agent that
launched it.* It inherits the agent's environment, PATH, and cwd, so
identical argv can produce opposite verdicts; it would have added a fourth
full harness run per iteration; and its attestId would have pointed into
gitignored state, so the committed row's only verifiable content was
unresolvable to a reviewer. It also would have laundered free prose through
a real exit code — `harness --full` green establishes exactly one
proposition, and any sentence in the Claim cell would have inherited it.

**The witness already existed; what was missing was a reader.**
`dod-gate.sh` is a *Stop hook*: the runtime invokes it, at the moment the
agent claims done, and it already re-runs `scripts/harness.sh --full`,
holds the exit code, the log, and the tree state, and already distinguishes
PASS from FAIL from TIMEOUT. So the gate now writes its own Evidence row
(`Source: gate`) through `scripts/evidence.py`, which is a *writer* — it
imports no `subprocess` and executes nothing, and a test pins that. The
proof cell names what was actually established: `harness --full exit <n>
@<sha7> tree:<sha8> dirty:<n> log:<sha8>`. The tree hash matters more than
the commit: in loop mode HEAD is the pre-work commit for the whole
iteration, so a verdict taken before the first edit and one taken after the
last would otherwise be the same row.

Rows are written only where a stop actually happens — green, checkpoint
yield, or timeout. An ordinary blocked stop is a correction, not an outcome.
An unchanged verdict is not repeated, so a long green streak is one row.

`inject-state.sh` then renders the two classes separately with reserved
budgets, so a run of agent-written rows can no longer evict every witnessed
one from the window a fresh context inherits.

**The property, stated honestly:** not "a row cannot be forged" — `WORK.md`
is skipped by both `verify-changed.sh` and the hygiene scan, so an agent can
still type anything into it, including a `gate` row. It is that **there is
now a row class an honest agent never writes**, so a `gate` row in a diff
with no corresponding gate run is a reviewable anomaly, and a fresh context
inherits assertions labelled as assertions. That is a review affordance, not
a containment boundary. Guarding `WORK.md` itself remains open (circuit 3
below).

Also fixed here, and independent: `plugin_script` in the scaffolded harness
ran `find "$HOME/.claude/plugins" … | head -1`, and `find` exits 1 when that
directory does not exist. Under `set -euo pipefail` the pipeline inherited
that status and killed `--full` on its first plugin lookup, with no output —
in exactly the repos the function exists to support, the ones carrying the
harness without the plugin installed.

The rejected design, for the record. `evidence.py --mint` takes a
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

**Known bypasses — (a), (b) and (c) closed in slice 7.** The gate now
judges the tree against a **session baseline commit** rather than HEAD.
`inject-state.sh` records the starting commit once per session (only when
absent, so resume and post-compaction cannot forgive commits made before
them), the gate exports it as `FPL_DIFF_BASE`, and it is refreshed on green
— a tree that just passed is the right thing to measure the next one
against. Red and timed-out runs leave it alone, having established nothing.

- (a) **Write-via-Bash then commit is closed.** Verified both ways: with the
  session baseline the gate blocks a red harness; forcing the old HEAD
  baseline lets the same session stop silently. The work prompt tells the
  loop to commit every slice, so this was the normal path, not a clever one.
- (b) **The hygiene scan inherits the same baseline**, so a `TODO` or
  `.unwrap()` committed mid-session is no longer laundered past the gate.
  History older than the session is still CI's job.
- (c) **The trust base is now a list, not one file**: `scripts/harness.sh`
  plus `.fluxpoint-proof-baseline.json`, `.fluxpoint-pairs.json`,
  `.fluxpoint-gates.json`, `.fluxpoint-budget.json`, `.fluxpoint-cex.jsonl`,
  `package.json`, `Makefile`. Any of them changed this session — committed
  or not — is named in the green notice, so re-recording a baseline to
  absorb a rise is said out loud. Still a warning, not a block: making it
  blocking is how a gate gets switched off.
- (d) `wake-check.sh` still runs `sh -c` on predicates that originate in the
  IR. Open; it needs the allowlist treatment `.fluxpoint-gates.json` models.

Two properties of the baseline worth knowing. It **falls back to HEAD**
whenever the recorded commit is not an ancestor of the current one — a
rebase or a branch switch would otherwise diff the tree against unrelated
history, arming the gate and flooding the hygiene scan with findings nobody
introduced; a gate that cries wolf is worse than the bypass it closed. And
it **depends on SessionStart having run**: with no recorded baseline the
gate degrades to the previous HEAD comparison, which is the old behavior
rather than a new hole, but it means `FPL_DISABLE=1` on the SessionStart
hook alone weakens the Stop gate too.

## Part 3 — the memory layer

The context strategy already matches where the field converged: durable
state in versioned files, a curated always-present injection at
SessionStart, isolated node contexts whose typed contracts define what
survives the boundary. What is missing is the tier discipline and the
consolidation process. In the standard taxonomy: SessionStart injection is
core memory, `runs/` is episodic memory, Evidence/Decisions are semantic
memory — and nothing performs consolidation, so episodic never becomes
semantic without an agent volunteering it.

**3a. Lessons: `memory.jsonl` + `LessonV1` + IR `memory:` — shipped in
slice 3.** What landed matches the design below with one deliberate
narrowing, and it is the safety-critical part: **a seed reaches the
finder's prompt and never the dedup set.** Seeding the dedup set — the
literal reading of "pre-loads the seen-set" — would silently drop a
re-found item, and a finding that comes back is evidence the lesson went
stale, which is exactly the regression a sweep exists to catch. So the cost
saving comes from a finder that knows where the frontier was, not from a
loop that refuses to look, and a re-found item is judged again on its
merits. Also enforced beyond the original sketch: `memory.emit` requires a
verification tier, because filing unjudged finder output would promote a
well-formed guess to institutional knowledge. Still open from this
proposal: killed-lesson claims are loaded (`memory.py --load` returns them)
but do not yet ride into refuter prompts as priors, and there is no
`--since <git-rev>` staleness filter.

The original design, for reference. A new
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

**3b. Counterexamples: `.fluxpoint-cex.jsonl` — shipped in slice 5.**
Research settled the parser question outright: `aiken check` has no `--json`
flag because it emits structured JSON whenever stdout is not a TTY, sending
diagnostics to stderr — so the harness captures it with a plain redirect and
nothing parses a terminal box. (The published `--show-json-schema` is wrong
in two ways that break a schema-driven parser: it declares a `kind`
discriminator the binary never writes, and names the array `test` where real
output says `tests`. Kinds are told apart by field presence.)

Four corrections to the sketch below, each from an adversarial pass and each
now pinned by tests:

- **The store is `.fluxpoint-cex.jsonl` at the repo root, not under
  `.claude/fluxpoint/`.** That directory is gitignored by this plugin's own
  installer, so a ratchet placed there exists only on the machine that found
  the bug — CI and every teammate would take the dormant path and report
  green. Every other ratchet (`.fluxpoint-proof-baseline.json`,
  `.fluxpoint-pairs.json`, `.fluxpoint-gates.json`) is already at root; only
  ephemeral run state belongs under `.claude/`.
- **A pin cannot be generated, so it is verified instead.** The JSON carries
  the counterexample but not the test's parameter type or the predicate it
  broke, so a generated `.ak` would be a guess — and a wrong guess drops a
  non-compiling file into a tree that was merely red. The agent writes the
  test; `cex.py` refuses to record it unless the recorded literals are in
  *that test's own body*, in order, on token boundaries, outside comments
  and `@"..."` strings, in a test that is neither `fail`-annotated (which
  inverts the oracle: it passes because the bug is unfixed) nor hollow (bare
  boolean, self-comparison, or a value bound then ignored). Whole-file
  containment would have been vacuous for the modal counterexample — `0`,
  `True`, `[]`.
- **Containment is over literal leaves, not the whole string.** Opaque types
  render as something unwritable: `Dict([(#"ab", True)])` must be built with
  `dict.from_list(...)`, so demanding the word `Dict` would make a
  legitimate pin impossible. The data inside it is still required.
- **The harness fixes the seed** (`--seed "${FPL_AIKEN_SEED:-1}"`). Aiken
  draws a random u32 per run, so the same bug shrinks to a different value
  each time and dedupe would never fire.

Also fixed here, found while wiring: `[ -n "$x" ] && cmd` as the **last**
statement of a function returns 1 under `set -e`, so the shipped template's
final pair-guard line failed `--full` outright in any repo without the
plugin installed. Converted to an `if`.

Not yet claimed, and named in the docstring: a pinned test is recorded as
*carrying* the counterexample, not re-run against the un-fixed code to prove
it would have caught it. That is why `signature` is already in the schema.

The original design, for reference. `cex.py --ingest` parses prover
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

**3c. Loop-mode decision capture + flush-before-death — shipped in slice 8,
with the PreCompact half redesigned.** `decision.py --record` and `--none`
landed as described, enforcing the `DecisionV1` floors rather than
describing them — including one the schema implies but never stated: a
`chosen` value that was never among the options is refused, because every
floor can pass and the decision still be incoherent.

The PreCompact hook did **not** land as sketched. The sketch had it "tell
the summarizer what is already durable", which assumes a hook's stdout is
injected into the compaction summary — and that is **undocumented**. A hook
whose entire value rested on an undocumented mechanism would be inert
without ever saying so, which is the `verify: harness` failure class this
plugin removed from its own compiler.

So the hook's value rests on a mechanism this plugin already depends on. It
answers one deterministic question at compaction time — had anything this
session learned reached the disk? — by hashing the work file's Decisions and
Notes sections against a snapshot SessionStart took at the session's real
start, and writes the answer to `<session>.compacted`. SessionStart then
reads that marker and tells the post-compaction context, in the same
injection that already carries branch state and the harness verdict, that
the reasoning behind the current diff is gone and to re-derive rather than
assume. The stdout line is still emitted: a bonus if the summarizer sees it,
costing nothing if it does not. The hook never blocks compaction — wedging a
session by refusing to free context is a worse failure than the memory it
was protecting.

`FPL_DISTILL=1` arms the Stop-gate check, and it is off by default for the
reason the slice-6 review made explicit: a check that starts by blocking
stops is one people disable, taking the rest of the gate with it.

The original design, for reference.
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
theorem) — shipped in slice 4.** What landed covers Aiken and Dafny:
Aiken test and property signatures (name, fuzzer types, `fail` polarity)
and Dafny `requires`/`ensures`/`invariant` clauses per declaration, hashed
into the `spec` section of `.fluxpoint-proof-baseline.json` (each guard now
preserves the other's section, or arming one would disarm the other).
`--check` reds on a changed or removed obligation unless a Decisions row
names its id; additions are free; reformatting, clause reordering, and file
moves are not weakenings and stay green. Lean, Coq, Isabelle and TLA+ are
reported `NOT COVERED` by name rather than passed over — that surface
remains the proof-auditor's alone. Not yet built: `--axioms`, the DoD
`— proof: <tool>:<id>` tails, and the Aiken attack-taxonomy scaffolding
(slice 12).

Building it surfaced a live bug in the sibling guard, now fixed with
regression cases: `proof-guard.py` matched Aiken parameter lists with a
`[^)]*` class, which stops at the first `)`. A property test's own fuzzer
contains one — `n: Int via bounded_int(1, 99)` — so **every parameterised
property test was invisible to the vacuous-test scan**, which is exactly
where a gutted body is least likely to be re-read. Constant predicates
taking a tuple were missed the same way. Both now match by counting parens.

The original design, for reference. proof-guard polices proof *bodies*;
the statements are unguarded — a dropped `ensures` conjunct, a widened `requires`, or a
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

**4c. `mutation-guard.py` — the judge of the judge — shipped in slice 9.**
The split that made it work is between the two halves: `--measure` is the
expensive run (minutes to hours, one compile-and-test per mutant) and is
where the ratchet lives; `--check` is cheap enough for `--full` and re-runs
nothing, asking only whether a measurement exists and still describes this
tree. `--measure` belongs on the wake/Routine layer.

Both directions ratchet, and the second is the one a ratio alone misses: a
score can hold flat while coverage shrinks, so the absolute survivor count
may fall but never rise. `--accept` re-records a weaker score and demands a
written reason that is kept in the committed record. `unviable` and
`timeout` outcomes are excluded from the denominator — a mutant that never
compiled tested nothing, and folding those in would let a build that got
slower look like a suite that got better.

`--from` records an `outcomes.json` a CI job already produced instead of
re-running the tool, which is both the common CI shape and what makes the
ratchet testable without a Rust toolchain.

Staleness is deliberately **not** fatal by default. It is named with how
stale, and raised in the inbox so somebody schedules the re-run — a gate
that reds because an expensive job has not been re-run yet is one people
switch off, and it would take the rest of the harness with it. A repo that
wants it fatal sets `failWhenStale`. A measurement taken at a commit the
repo no longer has is reported as its own state, distinct from merely old.
Toolchains this guard cannot parse are named rather than silently skipped.

The adapter was then verified against a real run of cargo-mutants 27.1.0
rather than shipped on documentation, and that caught four things
documentation alone would not have:

- The `outcomes` array **includes the baseline run**, whose `scenario` is
  the bare string `"Baseline"` while every mutant's is an object — so it is
  neither uniform nor one-entry-per-mutant. The top-level counters are the
  source of truth instead.
- A survivor's line is `span.start.line`, not a `line` field; `function.span`
  is the whole enclosing function.
- A **failed baseline still writes an `outcomes.json`**, full of zeroes.
  Read as counters that is indistinguishable from a clean sweep, and would
  publish a perfect score for a build that never ran.
- **`cargo mutants --check` exits 0 having measured nothing** — it only
  compiles mutants, filing every one as `Success` with nothing caught or
  missed. A gate trusting that exit reports a green mutation run.

All four are refused by name. The schema has also churned across releases
(`cargo_result` → `process_status`, `line` folded into a `function`
submessage plus `span`, the `failure` counter removed), so an
`outcomes.json` from a version this parser has not been read against is
refused rather than parsed on optimism. Scoring follows the convention
Stryker publishes — `detected = caught + timeout`, `valid` excludes
`unviable` — with timeouts recorded separately, since a timeout usually
means the limit is wrong rather than that a test did its job.

`#[mutants::skip]` joined `proof-guard`'s ratcheted hatch categories (along
with `mutants::exclude_re`, and matched loosely enough to catch the
`cfg_attr`-nested form the tool also honors — cargo-mutants emits no count
of skipped mutants anywhere, so grepping the source is the only way to see
them):
excusing a mutant is excusing a change no test has to notice, which is the
same move as excusing a proof obligation, and it should not be free to
sprinkle wherever this goes red.

The original design, for reference. In loop-mode TDD the same agent writes the code and its judge;
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
| 2 | `exec-attest.sh` + `.fluxpoint-gates.json` + record-run cross-check in warn mode | **shipped** |
| 3 | `memory.py` + record-run filing of findings/objections + `memory.seed` frontier seeding | **shipped** |
| 4 | `spec-guard.py` for Aiken + Dafny statements, wired into `templates/harness.sh --full` | **shipped** |
| 5 | `cex.py --ingest/--pin/--check` for `aiken check` output | **shipped** |
| 6 | Gate-authored Evidence rows + classed injection (replaced `--mint`; see Part 2) | **shipped** |
| 7 | Stop-gate branch-point dirtiness + baseline/trust-base modified-contract warnings | **shipped** |
| 8 | `decision.py` + PreCompact hook (distill gate behind `FPL_DISTILL=1`) | **shipped** |
| 9 | `mutation-guard.py` wrapping cargo-mutants, floor + staleness stamp | **shipped** |
| 10 | `ExecutionV1` + `verify: "prove:<gate>"` tier + TAMPERED-EXECUTION enforcement | |
| 11 | `WORK.consolidate.md` + consolidation Routine; evidence `--replay` + graded injection | |
| 12 | Attack-taxonomy scaffolding in `/fluxpoint:init` for Aiken repos; `WORK.verified.md` | |
| 13 | Cross-repo federation (`.fluxpoint-federation.json`, org-scope lessons) | |

Every proposal above follows the house idiom — closed field registries
with emission probes, append-only stores whose reads hard-fail on
corruption, deterministic scripts deciding green, incompleteness that
names itself — so each one strengthens the invariants it touches instead
of adding a parallel mechanism beside them.
