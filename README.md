# Flux Point Claude Plugins

Private Claude Code plugin marketplace for Flux Point Studios. Two plugins:
**fluxpoint**, the engineering harness (below), and **substrate**, a
multi-repo primitive registry (see [The substrate
plugin](#the-substrate-plugin)). fluxpoint has two drivers over one
contract:

- **Loop mode** makes one agent's cycle programmable — every session,
  interactive or autonomous, runs against a deterministic
  Definition-of-Done gate instead of the agent's self-report.
- **Graph mode** makes the *organization* of agents programmable —
  campaigns declared as an IR and compiled, deterministically, into
  verified multi-agent Workflow scripts.

Both share one work-state file (`WORK.md`), one Definition of Done, one
Evidence table, and one authority on done: `scripts/harness.sh`.

## What loop mode enforces

| Layer | Mechanism | Behavior |
|---|---|---|
| Bootstrap | `SessionStart` hook | Injects branch state, last harness verdict, gate status, and the head of `WORK.md` at every session start, resume, clear, and post-compaction. |
| Inner loop | `PostToolUse` hook on `Write\|Edit\|MultiEdit` | Runs `scripts/harness.sh --changed <file>`; failures feed straight back to Claude for immediate correction. |
| Gate-authored evidence | `Stop` hook → `scripts/evidence.py` | The gate records its own verdict as a `Source: gate` Evidence row — exit code, commit, a hash of the working tree, dirty-path count, and a hash of the log. Nobody writes that class by hand, so a `gate` row with no gate run behind it is a reviewable anomaly, and SessionStart shows witnessed rows separately from asserted ones instead of letting agent prose evict them. |
| DoD gate | `Stop` hook | When files changed this session — measured against the commit the session started from, so committing work mid-session does not hide it — runs `scripts/harness.sh --full` plus a hygiene scan of uncommitted/new code (TODO, FIXME, XXX, "for now", `.unwrap()`, skipped or focused tests, empty `catch {}`). Red blocks the stop with the failures as the work list, up to `FPL_MAX_BLOCKS` (default 3) consecutive times, then yields with a checkpoint notice: three failed paths is a user checkpoint, not a retreat. |
| Proof ratchets | `proof-guard.py` (bodies), `spec-guard.py` (statements) | A checker exits 0 on an assumed lemma exactly as on a proved one, so escape-hatch counts may fall but never rise — and because the easier move is to weaken the theorem instead, the obligations themselves are hashed too: a dropped `ensures` conjunct, a deleted or renamed property test, a flipped `fail` test, or a narrowed fuzzer is red unless a Decisions row names it. Both share one committed baseline. |
| Mutation score | `scripts/mutation-guard.py` | Every other gate asks whether the tests pass; this asks whether they can fail — break the implementation on purpose and count what the suite notices. `--measure` carries the ratchet (red when the score falls *or* the survivor count rises, since a ratio can hold flat while coverage shrinks) and runs off-session; `--check` is cheap enough for `--full` and only asks whether a measurement exists and still describes this tree. Staleness is named and raised in the inbox, fatal only if the repo asks. |
| Seam ratchet | `scripts/seam-guard.py` | Mutation asks whether the tests can fail; this asks whether they reach the code at all. `vi.mock('@/services/x')` asserts what the author believed that module does, and nothing ever checks the belief — a mutant behind that wall reports the same green either way. The count of module mocks (first-party and third-party tracked apart) may fall but never rise against a committed baseline shared with the proof ratchets. Process-boundary stubs and partial mocks via `importActual`/`importOriginal` are deliberately not counted: they are the fix, not the disease. |
| Counterexamples | `scripts/cex.py`, `.fluxpoint-cex.jsonl` | A prover's shrunk failing input is the most reusable thing it produces and it lives in a log the next command overwrites. `aiken check`'s JSON is captured, each failure recorded, and a fix pinned to a regression test — accepted only when the recorded value is physically in that test's body, outside comments and strings, in a test that is neither `fail`-annotated nor hollow. Losing a pinned test is red; releasing one costs a reason and a Decisions row. |
| Decisions | `scripts/decision.py`, `PreCompact` hook | A choice with more than one defensible answer is recorded against `DecisionV1` — the options, the best case against each including the winner, the rationale — so it survives the context that made it; `--none` makes silence a statement. At compaction the hook records whether anything reached the disk, and SessionStart tells the next context when the reasoning behind the current diff is gone. `FPL_DISTILL=1` makes the Stop gate ask for one; off by default. |
| Attestation | `PostToolUse` hook on `Bash` | For a declared command that succeeds, records the runtime's own exit code to `.claude/fluxpoint/attest.jsonl`, so a green stops depending on an agent typing it back. PostToolUse has been measured not to fire when a Bash call fails, so the log binds passes and is silent about reds. Dormant in a repo that declares no gates. |
| Proved gates | `verify: "prove:<gate>"`, `ExecutionV1` | A node can declare that its verdict must match the hook's record. The gate name resolves at compile time, so a tier naming nothing refuses to compile; at record time a citation that does not exist or disagrees files the run `TAMPERED-EXECUTION`, and citing nothing files it `INCOMPLETE`. Where a repo declares gates, an `irreversible` node's guard must be prove-gated — an effect nobody can undo may not rest on a self-reported exit code. |
| Review | `red-team-reviewer` agent, `/fluxpoint:red-team` | Adversarial pass over the diff: eUTxO, oracle, authority, numeric, off-chain, and infra attack surface. Ends `VERDICT: SHIP` or `VERDICT: BLOCK`. |
| Drivers | `loop-engineering` skill + templates | `/goal` for interactive convergence, native `/loop` (self-paced) for in-session grinding, `/schedule` Routines for cloud standing guardrails, `scripts/loop.sh` for multi-hour outer Ralph runs with fresh context per iteration. |

The repo-side contract is a single file: `scripts/harness.sh` supporting
`--changed <file>` (fast, scoped) and `--full` (everything the DoD
requires), exit 0 = green. The gate stays dormant in repos that lack it.

## What graph mode adds

Graph Engineering is the layer above the loop: loop mode makes one agent's
cycle programmable (state file, deterministic harness, driver); graph mode
makes the organization of agents programmable. A campaign
is a graph — nodes are single-responsibility agents with typed contracts,
edges are deterministic code, verification is named per edge — compiled to
a Claude Code Workflow script and repaired by targeted resume instead of
restarts.

| Piece | Mechanism | Behavior |
|---|---|---|
| Spec | ```json graph-ir block in `WORK.md` | The single source of truth: nodes with named contracts, `foreach`/`after` edges, a verification tier per node, budget ceiling, failure policy. Prose explains intent; the IR decides what runs. |
| Compiler | `scripts/compile-graph.py` (python3, stdlib) | Compiles the IR to a Workflow script **deterministically — no model transcribes it**, so spec and executor cannot drift. Rejects unsound graphs at compile time: missing/unknown contracts, even panels, `verifyOver` that is not a contract field, dangling `after`/`foreach`/`role`, `{{prev}}` without an edge, a discovery loop with no dry rule/ceiling/dedup key, planned fan-out over `budget.maxNodes` (rounds priced in), and any mutator with no independent node verifying it. Unknown fields are errors too, at every level, so a misspelled `verifyOver` fails loudly instead of silently disabling verification. It also warns (without blocking) on shapes that compile but disappoint — a discovery ceiling too tight for its dry rule to ever fire, or a sweep with no verification tier. |
| Executor | Claude Code Workflow tool | `/fluxpoint:graph-run` compiles, runs, and records. Generated code carries the guarantees: input normalization, worktree isolation for mutators, refuter panels that attack rather than confirm, a budget floor that logs whatever it leaves unverified. On partial failure, `resumeFromRunId` re-runs only the repaired node onward. |
| Reducers | `reduce` IR nodes, `{{prev.<field>}}` projection | Deterministic code between agents — dedupe, rank, cut, or project one contract field — compiled to plain JS with zero spawns, every cut named in the log. Models for ambiguity, code for plumbing: the synthesis node judges a shortlist, not a landfill. |
| Metrics | structured provenance, `scripts/metrics.py`, `/fluxpoint:status` | Every summary records agent calls spawned vs planned and the runtime's token meter; discovery rounds carry found/new/kept tallies with per-worker unique-new counts; reduces carry before/after. `metrics.py` folds runs, lessons, and inbox into per-campaign rates — death and skip rates, sweep endings split dry/ceiling/truncated/halted, panel kill rate — so campaigns are tuned against numbers, not vibes. |
| Memory | `memory` IR field, `scripts/memory.py`, `LessonV1` | A sweep's judged items — survivors *and* the panel's kills with the objection that killed them — are filed as lessons keyed by the IR's own dedupe fields, and the next run seeds from the same tag. Run two opens where run one stopped instead of re-arguing it. Seeds are advisory: they reach a prompt, never the dedup set, so a re-found item is still judged rather than silently dropped. |
| Recall | `scripts/recall.py`, `scripts/embedder.py`, `/fluxpoint:recall` | The hybrid retrieval layer over that memory (see [Hybrid memory recall](#hybrid-memory-recall-graph--embeddings)): a deterministic knowledge graph plus BM25 plus optional API embeddings, fused with weighted reciprocal-rank fusion, so lessons surface by meaning and graph proximity instead of exact tag match. Seed maps come back relevance-ordered in the same shape `memory.py --load` prints; SessionStart injects the top lessons for the work at hand. |
| Evidence | `scripts/record-run.py`, `/fluxpoint:status` | Provenance is a build artifact: `runs/<runId>.json` plus an auto-appended Evidence row (outcome, nodes OK/dead, findings, harness exit, red-team verdict). Nothing is remembered by hand. Every claimed gate exit is cross-checked against the attestation log and filed `ATTESTED`, `UNATTESTED`, or `MISMATCH` — a node claiming green over an attested red raises an inbox item and says so in the row. |
| Review | `graph-auditor` agent, `/fluxpoint:graph-audit` | Semantic adversarial pass — stakes-vs-tier mismatches, vacuous contracts, context packets that paste transcripts, hidden coupling, ceilings that are not ceilings. Structure is the compiler's job. Ends `VERDICT: SOUND` or `VERDICT: REWIRE`. |
| Method | `graph-engineering` skill + templates | Loop-vs-graph rule, five primitives → bindings, tier selection by stakes, canonical shapes: fan-out/verify (`WORK.md`), council → build → independently gated (`WORK.feature.md`), loop-until-dry discovery (`WORK.discovery.md`), advisor–orchestrator, zone defense. |
| Composition | shares the loop contract | Mutating nodes work loop slices; `scripts/harness.sh` and the Stop-hook DoD gate keep final authority. Graph green ≠ done — campaigns still exit through the ship pipeline. |

The invariant worth naming: **a node that writes to the tree may not
certify its own work.** Mark it `mutates: true` and some later node with
`independent: true` must re-derive the verdict by running the harness
itself. The compiler refuses to build a graph that breaks this — it
shipped as a real bug in v0.1, so it is now unexpressible.

## Hybrid memory recall (graph + embeddings)

`memory.jsonl` remembers what campaigns established; until v1.26 the only
way back in was an exact `tag|dedupeKey` match, so a lesson about
beacon-prefix derivation was invisible to a session working on two-way
asset beacons. `scripts/recall.py` closes that gap with the architecture
the research points at — Graphiti's read path without its write path —
while adding **no store, no writer, no dependency, and no daemon**:

- **The graph is a projection, not a store.** `recall.py --build` compiles
  a typed knowledge graph deterministically from the stores that already
  exist — lessons, run artifacts, decisions (keyed by the import id the
  compiler already resolves), pinned counterexamples, and the repo's own
  `substrate.json` primitives (sanitized as hostile input; consumes-edges
  kept walkable across the repo boundary) — into gitignored
  `.claude/fluxpoint/index/`. Every relation is schema-native (provenance,
  supersession, kill events, path components inside dedupe keys, campaign
  membership), so there is no LLM extraction step to pay for or to
  hallucinate: the evidence (LazyGraphRAG, HippoRAG 2's ablations,
  verbatim-beats-extracted) says extraction subtracts value when the data
  is already typed. Identical sources build byte-identical indexes, and a
  malformed line in a *source* store is still a hard error while a damaged
  *index* file is deleted and rebuilt out loud.
- **Bi-temporal by derivation.** Each lesson identity carries its full
  append chain, so recall serves the current version by default,
  `--as-of <ts>` serves the version that held then, and a kill stays a
  permanently valid `KILLED_BY` edge even though the claim it killed is
  not — kills are priors, never suppressors. A killed lesson whose touched
  file changed after the kill is marked `stale: <file> changed since`
  at build time — annotated, never dropped, because a finding that comes
  back after the code moved is exactly the regression a sweep exists to
  catch.
- **Hybrid retrieval, evidence-shaped.** Up to four legs — BM25 over an
  identifier-aware tokenization of the query, BM25 over path tokens from
  files touched, cosine against API embeddings, and personalized PageRank
  from tag/file/campaign seeds with hub-resistant specificity weights —
  fused with weighted reciprocal-rank fusion (k=60), then boosted by
  re-establishment count (a lesson filed by many runs outranks a one-off)
  and gentle recency decay on lessons only.
- **The embedder is quarantined.** `scripts/embedder.py` is a closed
  provider registry — `voyage` (default `voyage-code-3` at 256 dims;
  Anthropic's documented embeddings partner, and its code-tuned models
  lead code retrieval), `openai` (`text-embedding-3-small` at 512),
  `gemini` (`gemini-embedding-001` at 768), or `none` — resolved by key
  presence (`VOYAGE_API_KEY`, then `OPENAI_API_KEY`, then
  `GEMINI_API_KEY`) or forced with `FPL_EMBEDDER`; an unknown value is a
  hard error, never a silent fallback, and
  `FPL_EMBED_MODEL`/`FPL_EMBED_DIMS` tune the model.
  Vectors are cached by content hash (rebuilds re-embed only what
  changed), stored unit-normalized one file per model, and compared by
  brute-force dot product — at this corpus size a vector database would
  be a dependency, not a speedup. The API is the **single sanctioned
  non-deterministic input** in the memory layer, and it can only ever
  reorder advisory output: nothing embedded is stored as truth, gates
  anything, or suppresses anything. Keyless is a fully supported mode —
  BM25 + graph serve, and every result names the absent leg.
- **Injection is budgeted per site.** SessionStart injects the top 5
  lessons ranked against the work file and the session's touched paths —
  offline, never rebuilding, capped at 1200 bytes, elisions named,
  `FPL_RECALL_INJECT=0` to demote. `/fluxpoint:graph-run` seeds sweeps
  through `recall.py --format seedmap`, which emits exactly the shape
  `memory.py --load` prints with keys relevance-ordered — the compiled
  graph's advisory-seed contract is untouched. A node declaring
  `memory: {priors: true}` additionally hands its refuters the seed tag's
  killed claims with the objections that killed them (5 items, 400 chars
  each), framed as priors the panel may overturn — the panel stops paying
  to rediscover arguments the store already holds, and the finder's
  prompt stays clean. `/fluxpoint:recall` serves humans. A per-prompt
  UserPromptSubmit hook exists but ships **dark** behind
  `FPL_MEM_PROMPT=1`: offline, 3 items, 1200 bytes, and a precision floor
  — two independent retrieval legs, or a lexical match on at least two
  informative query tokens; the graph leg never corroborates, because its
  seeds come from the lexical top ranks — off by default because the
  strongest external result says wrongly retrieved memories cost more
  than none.
- **Curation is a campaign, not a daemon.** `templates/WORK.consolidate.md`
  reads the store, proposes merges and restatements as findings, has a
  skeptic attack each one with the killed priors in hand, and lets
  `record-run.py` file the survivors through the same single-writer path —
  supersession by append, never deletion. Run it by hand or from a
  scheduled Routine.

What it refuses, on purpose: LLM extraction or reranking anywhere in the
write or rank path; any new dependency (no numpy, faiss, sqlite-vec, or
local models); graph databases, bundled MCP servers, daemons; new writers
to `memory.jsonl`; and retrieval-driven suppression of any kind — ranking
decides what reaches a prompt first, never what gets judged.

Requires a Python 3 interpreter reachable as `python3` or `python`, and a
Claude Code version with the Workflow tool
(`/workflows` resolves); where absent, runs degrade to parallel subagent
fan-out with the same contracts, recorded as `degraded-subagents` in
Evidence.

## Install

Publish this repo (see below), then either path:

**One-time, per developer**

```
/plugin marketplace add flux-point-studios/claude-plugins
/plugin install fluxpoint@fluxpoint
```

**Automatic, per repo** — commit this to each repo's
`.claude/settings.json` (it is `templates/settings.snippet.json`):

```json
{
  "extraKnownMarketplaces": {
    "fluxpoint": {
      "source": { "source": "github", "repo": "flux-point-studios/claude-plugins" }
    }
  },
  "enabledPlugins": {
    "fluxpoint@fluxpoint": true
  }
}
```

Anyone who trusts the repo folder gets prompted to install; every session
in that repo then boots with the loop context injected and the gate armed.
For CI and containers, use `forcedPlugins` in managed settings so the
install needs no interaction.

## Onboard a repo

```
/fluxpoint:init <one-line goal>
```

This copies the harness contract, `WORK.md`, `WORK_PROMPT.md`, and
`scripts/loop.sh` into the repo, wires `.gitignore` and settings, then
tailors `harness.sh` to the repo's real stack and iterates until `--full`
exits 0.

Set `MODE: graph` (or `both`) in `WORK.md` when the work meets the
escalation rule, and fill the Campaign section's `graph-ir` block. Drive
a campaign with `/fluxpoint:graph-design <goal>` (author the IR;
compile-check clean, then audited to `VERDICT: SOUND`) followed by
`/fluxpoint:graph-run` (compile, execute, record). Check in any time
with `/fluxpoint:status`.

Compiled `.graph.js` files land in `.claude/workflows/` and are build
output — never hand-edit them; edit the IR and recompile.

## Drive a loop

Interactive convergence (evaluator-checked, gate as deterministic backstop):

```
/goal scripts/harness.sh --full exits 0 and the run is shown; no test
deleted or skipped; or stop after 30 turns and summarize gaps
```

In-session grinding (native `/loop`, self-paced — re-fires when the session
goes idle, ends itself once the stop condition provably holds; requires
Claude Code v2.1.72+, self-ending v2.1.202+; Esc cancels):

```
/loop work the next slice per LOOP_PROMPT.md; stop only when
scripts/harness.sh --full exits 0 and WORK.md reads STATUS: DONE
```

Every driver runs the same per-slice contract from `LOOP_PROMPT.md`, and
a slice ends merged-and-cleaned or explicitly parked — never at "PR
opened". Merge authority is decided once per repo in WORK.md's Merge
policy; the deterministic form is `harness.sh --full` as a required CI
check plus the red-team verdict, then
`gh pr merge --auto --squash --delete-branch`, so GitHub — not the agent's
self-report — executes "merge if green".

Standing guardrails (cloud Routines — run with the laptop closed; fresh
clone per run, pushes only to `claude/`-prefixed branches; cron floor one
hour, daily run caps by plan; create conversationally in-session, add API
or GitHub triggers at claude.ai/code/routines):

```
/schedule nightly at 02:00, run scripts/harness.sh --full; if it exits
non-zero, open an issue titled "DoD drift" with the last 40 log lines;
if green, end without output
```

A GitHub-triggered routine fits the red-team pass: on every opened PR,
review the diff against the adversarial checklist and post a review ending
`VERDICT: SHIP` or `VERDICT: BLOCK`. Validate the routine environment's
setup script can install the repo's toolchain before trusting a routine to
run the harness. For pure in-session watching (CI, a preview-net tx), ask
Claude to watch it and it may use the Monitor tool — a background script
streaming output into the session — instead of interval polling.

Outer Ralph, fresh context per iteration:

```
scripts/loop.sh                       # attended, acceptEdits
MAX_ITER=50 PERMISSION_ARGS="--dangerously-skip-permissions" scripts/loop.sh
                                      # sandboxed container ONLY
```

Halt an outer loop any time: `touch .claude/fluxpoint/STOP`

## Migrating from the split plugins

Repos onboarded before 1.0 carry `LOOP.md`, `GRAPH.md`, and two state
directories. Run:

```
/fluxpoint:migrate
```

It folds both files into one `WORK.md` (carrying every Evidence row
across), moves local state under `.claude/fluxpoint/`, rewires
`.gitignore` and `enabledPlugins`, and verifies the harness is still green
and the IR still compiles before removing anything. Until it runs, the
hooks still honor `LOOP.md`, so a half-migrated repo keeps working.

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `FPL_DISABLE=1` | off | Kill switch: every hook becomes a no-op. |
| `FPL_MAX_BLOCKS` | 3 | Consecutive Stop blocks before the gate yields with a checkpoint. |
| `FPL_ALLOW_MISSING_GATES=1` | off | Accept, on purpose, that a gate your manifest declares is not installed. Without it the harness is RED when a declared gate's script cannot be found — a check that does not run must not read as a check that passed. A repo declaring no gates is unaffected and stays runnable without the plugin. |
| `CLAUDE_PLUGIN_ROOT` | set by the runtime | Where the harness resolves gate scripts from. Version-correct by construction; `FPL_PLUGIN_ROOT` overrides it. Without either, the fallback prefers a marketplace install, then the highest cached version. |
| `.fluxpoint-hygiene-ignore` | absent | One glob per line, `#` for comments. Excludes a path from the **hygiene scan only** — for an untracked file you intend to commit later, which `.gitignore` is the wrong lever for. Deliberately not read by the predicate that arms the gate, so it can narrow what the scan reads and never switch the gate off. It is in the trust base, so widening the blind spot is reported on a green run. |
| `MAX_ITER` / `MAX_TURNS` | 25 / 40 | Outer loop budgets. |
| `PERMISSION_ARGS` | `--permission-mode acceptEdits` | Outer loop permission flags. |

Dependencies: `git` required; `jq` preferred with a Python fallback
built into the hooks.

## This repo gates itself

`scripts/harness.sh --full` is the same contract `/fluxpoint:init` scaffolds
into any other repo, and `.github/workflows/harness.yml` runs it on every
push to `main`, every pull request, and on demand. It checks every manifest and contract, syntax-checks
every script, validates the plugin, compiles all campaign templates and
`node --check`s the generated JavaScript, then runs every suite in
`plugins/fluxpoint/tests/` (35 today), among them: compiler
invariants, field-effect probes, codegen-injection regressions, Stop-gate
regression cases, hook wiring and PostToolUse behavior, migration against
real pre-1.0 fixtures, the proof-strength ratchet across seven provers, and
unified-state compatibility.

The field-effect suite exists because of the defect that kept recurring
here: not a wrong output, a *silent* one. `verify: harness` was accepted,
documented, priced into the budget, and emitted nothing; `haltWhen` on a
fan-out node compiled clean and could never fire. Reviewing for that is
unreliable, so the suite sets every field the IR accepts to a non-default
value and fails if the compiler's output does not change. A field added to
the registry without a probe fails the run.

## Migrating from the split plugins

`/fluxpoint:migrate` drives `scripts/migrate.py`, which is tested against
built pre-1.0 fixtures (a v0.1 loop-only repo, a v0.2 loop+graph repo, and a
pre-0.2 repo whose GRAPH.md is prose). It runs in three phases — `--plan`
touches nothing, `--apply` writes `WORK.md` and rewires config while leaving
the sources in place, `--finalize` deletes them — and refuses to finalize if
`WORK.md` carries fewer Evidence rows than the sources did. The one thing it
will not do is invent a `graph-ir` block for a pre-0.2 prose campaign; it
flags that for a human instead.

## Verified work (Aiken, Dafny, Lean, Coq, Verus, Isabelle, TLA+)

A prover is the ideal thing to put behind a Definition-of-Done gate,
because it answers with an exit code rather than an opinion. The catch is
that exit 0 does not mean what it looks like: every proof assistant ships a
way to make a goal go away without proving it, and an agent told to "make
it pass" will find it.

Four mechanisms, in `scripts/harness.sh`, `scripts/proof-guard.py` and
`scripts/plutus-budget.py`:

1. The scaffolded harness invokes the prover in **`--full`**, not only in
   the per-file `--changed` path. A gate that decides "done" without
   calling the prover is not a gate.
2. **The proof-strength ratchet.** `proof-guard.py --baseline` records every
   escape hatch — `todo`/`expect` in Aiken, `assume`/`{:axiom}` in Dafny,
   `sorry` in Lean and Isabelle, `Admitted` in Coq,
   `#[verifier::external_body]` in Verus, `ASSUME` in TLA+, and
   `--skip-tests`/`--no-verify` anywhere — into a committed
   `.fluxpoint-proof-baseline.json`. `--check` fails when any category
   rises. Proving something you previously assumed lowers the count and is
   always welcome; raising one becomes a diff a human has to justify.
3. **The on-chain budget gate (Cardano).** `plutus-budget.py` runs after
   `aiken build` and measures `compiledCode` out of the plutus.json
   blueprint, because correct and submittable are different properties and
   only one of them has a prover. The protocol `maxTxSize` fails
   unconditionally — a script that exceeds it cannot go on chain no matter
   how well it is proved — while headroom is yours to set in
   `.fluxpoint-budget.json`, and `--params` reads real `cardano-cli query
   protocol-parameters` output so the constants in the script are a
   fallback, not the authority. Execution units are a property of
   evaluating a script against a transaction, not of the artifact, so
   measured values live in the same file and an absent one is reported
   unmeasured rather than passed.
4. **`/fluxpoint:proof-audit`** adds the semantic pass a counter cannot do,
   via the `proof-auditor` agent: a theorem whose statement lost a
   conjunct, a property proved about an unreachable state, an Aiken `test`
   that cannot fail, a negative test that trips an earlier guard than the
   one it is named for, a validator with no test at all, a solver `unknown`
   read as success. When the prover is not installed it says so and caps
   its verdict at `UNPROVEN` rather than reporting a static read as sound.

Two weakenings add no escape hatch, so both are counted structurally
rather than by line match: Aiken tests that cannot fail (a bare boolean
body, a self-comparison, an empty body) and predicates that decide nothing
(`fn credential_matches(..) -> Bool { True }`, where the call site still
reads as a checked conjunction). They cover the same trade — replacing a
`todo` with `True` drives `aiken.todo` to zero and looks like progress.
False positives are treated as worse than misses, and a constant that
predates the baseline is absorbed rather than held against the repo.

The ratchet is still deliberately a floor. A theorem that lost a conjunct,
a property proved about an unreachable state, or a `fail` test that trips
an earlier guard than the one it is named for — `cannot_underpay` built
with the wrong signer fails on the signature check and covers nothing —
leave every count unchanged, which is why the second pass exists.

## Treating WORK.md as untrusted input

The compiler generates JavaScript that is then executed, so every value
interpolated from `WORK.md` is an injection surface — including one that
arrives from a legacy `GRAPH.md` during migration. An adversarial review of
v1.3.0 drove two fields to real code execution: `haltReason` was pasted
straight into a template literal, and a `lists` key was emitted as a bare JS
identifier. Both passed `--check` and a fully green harness.

The rule now, enforced by `tests/security-test.py`, which compiles real
payloads and runs the output to prove they stay inert:

- free text (prompts, reasons, list values) is emitted through `js_str` or
  `js_template`, which escape backticks, `${`, and backslashes;
- anything emitted as a JS *identifier* — node ids, `lists` keys — is
  constrained by `IDENT` at validation time, because escaping does not
  apply to an identifier position;
- halt literals are re-emitted from their parsed value, never pasted from
  the matched source text.

## Security posture

- Plugins execute arbitrary code with user privileges. Treat THIS repo as
  production infrastructure: protected default branch, required review,
  signed commits.
- Unattended loops run in a sandboxed container with allow-listed egress.
  No path from that environment to mainnet key material, ever. Harness
  exercises settle on preview/preprod; the resulting tx hash is the
  evidence artifact the DoD demands.
- Routines run on Anthropic-managed cloud under your identity — commits
  and posts appear as you. Guardrail jobs only: no key material, no
  mainnet paths, and repos under external data-governance constraints stay
  on the self-hosted outer loop.
- The hygiene scan covers uncommitted and untracked code only; committed
  history is CI's job. Run the same harness script in CI.
- The DoD gate arms on two independent signals: the PostToolUse marker
  (`Write|Edit|MultiEdit`) and dirtiness re-derived from `git` at Stop
  time. The second exists because the first cannot see source written
  through the Bash tool (`cat >`, `sed -i`, `git apply`) — before v0.1.3
  such a session could stop with the harness never run.
  `plugins/fluxpoint/tests/gate-test.sh` pins all ten cases.
- A green produced by a working tree in which `scripts/harness.sh` itself
  is modified or untracked is reported, not swallowed: the verdict is only
  as trustworthy as the contract that produced it.

## Publishing this repo

1. Create the GitHub repo (private is fine) — the snippet above assumes
   `flux-point-studios/claude-plugins`; edit both the snippet and this
   README if the org or name differs.
2. Push, then validate locally: `claude plugin validate .` As of v0.1.1
   the hooks are invoked via `bash`, so a stripped exec bit (GitHub web
   uploads and Windows checkouts drop it) can no longer disarm the gate;
   still, keep the bits correct for direct runs:
   `git update-index --chmod=+x $(git ls-files '*.sh')` and commit.
3. Smoke it end to end in a scratch repo:
   `/plugin marketplace add <org>/claude-plugins`, install, run
   `/fluxpoint:init`, make an edit containing `FIXME`, try to
   stop, and watch the gate block.

## The substrate plugin

The second plugin in this marketplace, **substrate**, answers a different
question than the harness: not "is this done" but "does this already
exist". Each repo in a multi-repo workspace declares its reusable
primitives in a `substrate.json` manifest; a zero-dependency Node script
compiles every manifest into one generated `SUBSTRATE.md` graph — nodes,
consumes-edges, orphans (dormant value to activate), hubs (harden first) —
and a matcher-less `SessionStart` hook injects the compact summary plus
staleness alarms into every session, post-compaction included. A manifest
that lags its repo's commits is reported as a fact, and a re-commit
touching only the manifest is deliberately not drift. Install with
`/plugin install substrate@fluxpoint`, onboard a workspace with
`/substrate:init`; the manifest schema, config reference, staleness
semantics, and honest limitations live in
[`plugins/substrate/README.md`](plugins/substrate/README.md). Its
`node:test` suites run as part of `scripts/harness.sh --full`.

## Layout

```
.claude-plugin/marketplace.json
plugins/substrate/
├── .claude-plugin/plugin.json
├── hooks/hooks.json
├── scripts/            substrate-graph.mjs, memory-lint.mjs, prose-smell.mjs
├── commands/           init.md, status.md, emit.md, smell.md
├── templates/          DOCTRINE.snippet.md, WRITING.snippet.md
└── tests/              graph.test.mjs, staleness.test.mjs
plugins/fluxpoint/
├── .claude-plugin/plugin.json
├── hooks/hooks.json
├── scripts/            lib.sh, inject-state.sh, verify-changed.sh, dod-gate.sh,
│                       compile-graph.py, record-run.py, recall.py, embedder.py,
│                       prompt-recall.sh
├── contracts/          FindingsV1, VerdictV1, HarnessCheckV1, DesignV1, SliceV1, RedTeamV1
├── commands/           init.md, status.md, migrate.md, red-team.md, recall.md,
│                       graph-design.md, graph-run.md, graph-audit.md
├── agents/             red-team-reviewer.md, graph-auditor.md
├── skills/             loop-engineering/SKILL.md, graph-engineering/SKILL.md
├── templates/          harness.sh, WORK.md, WORK.feature.md, WORK.discovery.md,
│                       WORK.consolidate.md, WORK_PROMPT.md, loop.sh,
│                       settings.snippet.json
├── tests/              gate-test.sh, compile-test.py, unify-test.sh
└── DESIGN-NOTES.md
```
