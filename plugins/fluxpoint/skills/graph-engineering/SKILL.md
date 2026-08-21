---
name: graph-engineering
description: How to run Graph Engineering at Flux Point — deciding when a loop must become a graph, specifying multi-agent campaigns as a declarative IR in WORK.md with typed contracts and verification tiers, compiling it deterministically to a Claude Code Workflow script, and repairing runs with targeted resume. Use whenever the user mentions graphs, graph engineering, multi-agent work, orchestration, councils, judge panels, fan-out, swarms, wiring or organizing subagents, WORK.md, workflow scripts, or asks to parallelize verified work across agents, even if they never say "graph engineering".
---

# Graph Engineering

Loops made one agent's behavior programmable; graphs make the organization
of agents programmable. The graph is the artifact: a declarative IR
(`WORK.md`), a compiler that turns it into a Workflow script with no model
in the loop, and the same deterministic harness deciding green. A node is
not a prompt — it is a loop-engineered unit with a scoped context packet in
and a typed contract out. The rule that survives every upgrade: the agent
never decides "done"; the harness does.

## Loop or graph

Stay in loop mode when the work is one zone, one contract, serial
evidence. Escalate to a graph when two or more hold: independent subtasks
that fail independently; a work-list to fan out over; claims that need
adversarial verification from independent context; cross-zone ownership
(validators vs off-chain vs infra); a budget worth isolating per node. A
work graph of more than ten nodes is scope creep — split the campaign;
the compiler warns past ten. Never build a graph as ceremony around a
single slice.

Two stay-in-loop cases that look graph-shaped and are not. Work whose
*decomposition* is still unknown is loop work: a discovery `repeat`
covers unknown size (known angles, unknown count), never unknown
structure — you cannot fan out over subtasks you cannot yet name, so
explore in loop mode first and graph what the exploration finds. And
work a human steers step-by-step is loop work: the graph's human
machinery (`actor: human`, `confirm`) models permission boundaries
inside an autonomous run, not a person redirecting each move — a graph
whose every edge waits on a person is a loop wearing ceremony.

## The six primitives and their bindings

| Primitive | Meaning | Binding |
|---|---|---|
| Node | one responsibility, its own context | an IR `nodes[]` entry → one `agent()` call; stable roles via `roles` → `agentType` |
| Edge | explicit routing | IR `after` / `foreach`; the compiler derives `pipeline()`/`parallel()` — never a model-improvised hand-off |
| Contract | what a node must produce | `contract: "NameV1"` → `contracts/NameV1.schema.json`, validated structured output |
| Context packet | what a node may believe | the IR `prompt`, with `{{A.x}}` inputs, `{{item.*}}` fan-out, `{{prev}}` predecessor contract or `{{prev.<field>}}` one projected field — never the transcript |
| Reducer | deterministic code between agents | a `reduce` node: `{from, over, dedupeBy, sortBy, order, topK}` → emitted JS, zero spawns. Models for ambiguity, code for plumbing |
| Verification | who decides an edge is green | the `verify` tier, plus `independent: true` nodes that re-derive gates |

**Compress before you reason.** A synthesis node handed every raw fan-out
item pays a reasoning model to do a Set's job. Put a `reduce` node between
fan-out and judgment (dedupe → rank → cut, in that order, each cut named
in the log — no silent caps), or project just the field a consumer needs
with `{{prev.<field>}}` instead of pasting the whole contract. Both are
validated at compile time: a projected field must exist in the
predecessor's contract, and projecting off a node that yields an item
array (a verified sweep, a fan-out, another reduce) is rejected — consume
`{{prev}}` whole or reduce it first.

Durable coordination rides on the executor: every run has a `runId`,
`journal.jsonl` records each node's actual return, resume replays the
longest unchanged prefix, and `record-run.py` writes the provenance
artifact and the Evidence row.

## The IR is the source of truth

One ```json graph-ir fenced block in WORK.md. `graph-run` compiles it
mechanically, so the spec cannot drift from the executor. **Never
hand-edit a compiled `.graph.js`** — edit the IR and recompile. The
compiler rejects, at compile time:

- a node with no contract, or an unknown contract name
- `verifyOver` that is not a field of the node's contract
- an even panel (majority undefined) or a panel with no `verifyOver`
- `verify: harness` — removed; it compiled to nothing while the spec
  claimed the node was checked. Gate on the harness the honest way:
  `mutates: true` on the producer plus an `independent` node that runs it
- an unknown field at any level (a misspelled `verifyOver` used to disable
  verification silently), or `after` whose prompt never uses `{{prev}}` —
  declaration order already sequences nodes, so a consumed-nothing edge is
  a phantom
- `mutates: true` with no `independent: true` node that `verifies` it
- a node verifying itself, or a verifier not marked independent
- `after`/`foreach`/`role` pointing at things that do not exist
- `{{prev}}` with no `after` edge, or `{{seen}}` with no `repeat` block
  (hidden coupling)
- `{{prev.<field>}}` naming a field the predecessor's contract lacks,
  reaching deeper than one hop, or projecting off a node that yields an
  item array — it used to compile clean and die at launch on a
  ReferenceError, which is the exact failure the scope check exists for
- `onRed` outside `halt | drop+log` — a misspelling used to fall back to
  a default silently, weakening the declared failure policy in the
  permissive direction — and `onRed` on a parked node, where nothing can
  die. On fan-out and discovery nodes the field is now real, too: `halt`
  ends the campaign on a dead worker, and a round that lost ANY worker
  never counts toward the dry rule — a finder that keeps crashing must
  not end a sweep looking converged. A spawn the node ceiling declined
  is a different sentence from a death: it files SKIPPED, and under
  `halt` the run ends `BUDGET-EXHAUSTED`, never `NODE-DEAD`
- a `reduce` node with no operation, an `over` that is not a field of its
  source's contract, `over` on a source that already yields items, a
  dedupe/sort key outside the item schema — or over items that declare no
  fields at all, where every key would read `String(undefined)` and
  dedupe would collapse distinct items to one — `order` without `sortBy`,
  `topK` without a ranking, or combined with any agent-node field
- a `repeat` block missing its dry rule, round ceiling, or dedup key, or
  whose dry rule can never fire; a dedup key that is not a field of the
  contract's items
- planned agent calls exceeding `budget.maxNodes`, or no ceiling at all —
  and rounds are priced in, so a four-round discovery loop is costed at
  four rounds, not one
- `irreversible: true` without `confirm` in `requiredArgs`, without an
  earlier `independent` node carrying a `haltWhen`, or combined with
  `foreach`/`repeat`

Those are structural. `/fluxpoint:graph-audit` judges what is left:
scoping, tier-vs-stakes, prompt quality.

## Work nobody on the graph can do

The engine used to have two answers for a node it could not complete: halt
the whole campaign, or drop the item and continue with a `null`. Neither is
*"this one is blocked, work the other branches"* — and real deliveries
guarantee that third case. 2-of-3 hardware signing. A withdrawal only an
external counterparty can perform. A 72-hour governance timelock. An
operator wallet with nothing spendable until someone tops it up.

**Park last, not first.** Most work that feels human-only is not: a CLI, an
API, a headless browser, a read-only query, or a generated file the person
only has to sign. Genuine blockers are narrow — key material an agent must
not hold, legal authority, physical possession, another party's own action.
So `release.whyNotAgent` is required and has to name what was ruled out;
`graph-auditor` treats a reason that does not survive contact with the
repo's own tooling as HIGH. Every unnecessary park is a person waiting on
work that could have been finished.

**A block never arrives empty.** When a node does park, the graph spawns one
advisor first, contracted to `DecisionV1` — so a bare "ask the operator"
cannot satisfy it. The advisor is told to attack `whyNotAgent` before
accepting it, and if the step turns out to be automatable its recommendation
*is* that concrete path and the tooling it needs. Otherwise it returns the
best available course of action with the alternatives it rejected and the
strongest objection to each, including to the one it recommends. That lands
in the provenance, the inbox row, and `/fluxpoint:status`, so what reaches a
person is a recommendation with reasoning attached, not a hand-off. The
advisor costs one agent call per parked node and is priced into
`budget.maxNodes`; if the floor declines it, the block says so explicitly
rather than quietly arriving bare.

Mark those `actor: human` or `actor: third-party` with a `release` block:

```json
{ "id": "sign", "actor": "human", "contract": "HarnessCheckV1",
  "prompt": "The campaign has built an unsigned transaction body and needs 2-of-3 hardware signatures before it can submit. Attack the claim that this needs a person before accepting it, then recommend how the operator should proceed.",
  "release": { "instructions": "Sign with 2 of the 3 hardware keys and paste the cardano-cli output.",
               "whyNotAgent": "the keys live on hardware devices held by three people; an agent may never hold them. The unsigned body IS built headlessly by the previous node.",
               "proofContract": "HarnessCheckV1" },
  "wake": { "check": "cardano-cli query tip --mainnet", "everyMinutes": 30 } }
```

The compiler emits **no spawn** for that node. It reads a release file; if
there is none it reports `BLOCKED` with those instructions, sets the run
`INCOMPLETE`, and keeps going. `instructions` is the entire message the
blocked person gets, so write it for someone with no context — the
compiler rejects an empty one, and rejects a `proofContract` that differs
from the node's contract, because the node yields exactly what was pasted.

Four consequences worth knowing:

- **Blocked is inherited.** A node whose `after` is blocked is blocked too,
  not handed the `null` that reads like a failure. A dependent chain parks
  as a unit; unrelated branches finish.
- **A parked run can never read COMPLETE.** It is `INCOMPLETE`, and the
  Evidence row names the blocked nodes.
- **Releases are proof, not assent.** `/fluxpoint:release` validates what
  the operator pastes against `proofContract` and refuses an adjective. The
  campaign resumes on the strength of that file; one that resumes on
  recollection will eventually resume on a mistake. Never write a release
  on someone's behalf — a fabricated release is strictly worse than a
  stalled campaign, because the stall is visible.
- **Nothing is waiting silently.** Every block, expired wake, and refused
  confirmation lands in `.claude/fluxpoint/inbox.jsonl`;
  `/fluxpoint:status` leads with it and SessionStart injects the count.

`wake` parks a predicate rather than a person: `scripts/wake-check.sh`,
driven by a Routine or by `loop.sh`, runs the due checks and reports which
campaigns can resume. It deliberately does not resume them itself —
re-invoking a graph spends budget and may sit upstream of an irreversible
node, so a human or an explicitly configured Routine makes that call.

In loop mode the same idea is a Plan marker: `- [~] <item> — blockedOn:
<who>`, which the loop skips. Without it, step 1's "pick the first
unchecked item" re-picks a human-blocked slice every iteration and a
72-hour wait spends the entire iteration budget in minutes.

**This is not a scheduler, on purpose.** There is no ready-set, no
topological sort, no `needs`/`priority`. Declaration order plus
park-and-skip-dependents covers a largely serial critical path with
independent slices hanging off it, which is what campaigns actually look
like. A DAG scheduler is the right answer to a problem no graph here has
had yet; build it when one does, not before.

## What a worktree is, and what it is not

`mutates: true` compiles to `isolation: 'worktree'`. Three things about that
are worth knowing before you depend on it, because each has cost a real
campaign a wrong answer rather than an error:

- **You do not choose the base.** The compiler emits the literal string and
  the runtime cuts the worktree; nothing in this plugin selects the commit.
  A node given isolation has been observed reading a tree cut from the
  default branch rather than the campaign's, which makes a file an earlier
  node committed simply ABSENT. Nothing inside says so: `git status` is
  clean and a missing file looks like a missing file, so the node returns a
  confident, well-evidenced, wrong report. **Have any node that measures or
  verifies the tree state its base** — `git rev-parse HEAD` and
  `git log --oneline -1` in its evidence — so a wrong-base finding can be
  told apart from a true one. A downstream node that must read what an
  upstream node committed should fetch or check out that branch by name
  rather than assuming it is there.

- **Isolation is opt-in, and a fan-out that measures needs it.**
  `parallel()` and `pipeline()` read as isolated units and, for pure
  reasoning, effectively are. The moment a node's output is a *measurement*
  of the tree — a build size, a byte delta, a benchmark — a shared working
  tree makes that number a function of what every sibling is doing, and the
  failure is silent: every node exits 0 with a confident figure. Declare
  isolation on anything that builds, compiles, or measures, not only on
  things that write.

- **A node that dirties the shared tree invalidates what ran before it.**
  "Throw the branch away" in a prompt is prose to a model, not a cleanup
  contract; nothing evaluates it. A gate that already passed was judging a
  tree that no longer exists. Until the runtime offers a scratch mode, a
  measuring node should either declare isolation or restore what it touched
  as part of its own contract, and a gate should re-assert the tree it is
  judging rather than trusting a green from before the mutation.

**Prove the harness in a worktree before you trust a `mutates` node.** Green
in the primary checkout is not green under the isolation the campaign
imposes: `git worktree add` checks out tracked files only, so a gitignored
build artifact or an installed `node_modules` is absent, and a test that
pins an absolute path is false there by construction. Run
`git worktree add --detach` and `bash scripts/harness.sh --full` inside it
once. Otherwise the campaign halts at its verification node blaming the
implementer, which is both wrong and pointed at an innocent node — and the
repair it invites is weakening the assertion that was right.

## Effects that cannot be undone

`mutates: true` buys `isolation: 'worktree'`. That is real containment for
a filesystem write and none whatsoever for a chain write, a published
release, or a destructive migration — the same marker on both reads as
protection it does not provide.

Mark those `irreversible: true`. Three things follow, and the second is
the one that matters:

1. The node refuses to fire unless a human named it in `confirm`. Naming
   the campaign is not naming the effect, so a blanket "yes" carries
   nothing along with it, and refusal is its own outcome
   (`CONFIRM-REQUIRED`), never a warning in a log.
2. **Resume stops double-firing.** Repair-one-node-and-resume is the
   recovery path this skill prescribes, and it is also the operation that
   mints twice: every node after the repair re-runs. Before each
   irreversible spawn the compiled graph checks a ledger keyed by campaign,
   node, and prompt hash; a hit restores the recorded result and logs
   `REPLAYED-FROM-LEDGER` instead of performing the effect. A replayed node
   is filed `REPLAYED`, never `OK` — a ceremony that did not happen must not
   read like one that did.

   **The ledger is local, not committed, and that bounds what it can
   promise.** It lives at `.claude/fluxpoint/irreversible.jsonl`, inside the
   directory `init` and `migrate` add to `.gitignore`, so a fresh clone
   starts with none: `--load` returns `{}` and exits 0, which the guard reads
   as "first run" and the effect fires again. Within one checkout the
   once-only property holds; across checkouts it does not, and no amount of
   care in the graph changes that. If an effect must be once-only for a
   ceremony that more than one machine can reach, the receipt has to outlive
   the working copy — a committed receipt log, or a lock the effect's own
   service holds. Do not read this ledger as that guarantee.
3. The gate must be *ordered before* the effect. The compiler requires an
   earlier `independent` node with a `haltWhen`, because a verifier that
   runs afterwards cannot un-mint an NFT.

Two limits, stated rather than papered over. The sandbox running the
compiled graph has no filesystem, so the ledger row is written from the run
summary afterwards — a crash between the effect landing and the run ending
leaves no record, and the confirm gate is what stands in that window.
And editing a ceremony's prompt changes its key, which re-arms it; that is
deliberate (a different effect deserves a different record) and is the
second reason a human has to name the node every time.

## Choosing a verification tier

By stakes, never by habit. `schema-only` for cheap mechanical output whose
consumer re-reads the source anyway. `skeptic:1` for low-severity claims. `panel:3` for findings that
will cost someone real time. `panel:5` only for CRITICAL. Panels are odd
so majority is defined; refuters are prompted to *refute*, default to
refuted when uncertain, re-read the underlying code themselves, and run at
low effort — cheap skeptics beat expensive believers.

The expensive lesson: verification fan-out dominates cost. A three-finder
review with `panel:3` on every finding is ~30 agent calls. Tier down and
the same campaign costs a fraction with the same guarantees where they
matter.

## Never trust a self-report

A node that writes to the tree may not certify its own work. Mark it
`mutates: true` (which also worktree-isolates it) and give the gate to a
node with `independent: true`, `verifies: "<id>"`, and a `haltWhen` on the
real exit code. That node checks out the branch and re-runs the harness
itself. This is a compiler-enforced invariant because it shipped as a bug
once: the graph trusts exit codes it re-derived, not adjectives it was
told.

Independence of *context* is what the compiler can enforce; fidelity of
*execution* it cannot, because the verifier still types the exit code into
its contract by hand. So declare the commands that decide things in
`.fluxpoint-gates.json`, and a PostToolUse hook records the runtime's own
exit for every one of those runs. `record-run.py` then cross-checks each
claimed gate exit against that log and reports `ATTESTED`, `UNATTESTED`, or
`MISMATCH`. Three things to know: only the exact declared invocation is
attested (a pipe or a trailing `|| true` reports a different exit and is
credited to nothing); an absent attestation is `UNATTESTED`, never a
failure — an executor that does not route through the Bash tool must not
read as guilt; and PostToolUse has been measured not to fire when a Bash
call fails, so the log binds passes and may hold no reds at all. The third
bounds the second: a node claiming exit 0 with no row should have left one,
and `record-run.py` lists exactly those separately as the unverified ones.

**`verify: "prove:<gate>"` turns that observation into enforcement.** The
node returns `ExecutionV1` — `{gate, exit, attestId}` — and cites the
attestation its run produced. The gate name is resolved against the
manifest *at compile time*, so a tier naming nothing refuses to compile;
that is the `verify: harness` lesson, which once priced a tier into the
budget and emitted no check at all. At record time a cited attestation that
does not exist, attests a different gate, or recorded a different exit
files the whole run `TAMPERED-EXECUTION` — a campaign does not get to
report clean when its own verification says its exit codes are not what
happened. A `prove:` node citing nothing is `INCOMPLETE` instead: the
declared verification did not run, which is not the same accusation.

Nodes that merely happen to match a declared gate stay observed rather than
enforced. Opting in is what earns the stricter reading, and a check that
starts by failing runs is a check people switch off.

And where a repo declares gates, an `irreversible` node's mandatory earlier
guard **must** use `prove:`. The ordering invariant — gate before effect —
was always sound in structure and hollow in fidelity while the guard typed
its own exit code. An effect nobody can undo may not rest on a number the
node that ran it wrote by hand.

## Canonical shapes

- **Fan-out/verify** (`templates/WORK.md`): dimensions → finders →
  per-finding refuter panel. The default review campaign.
- **Council → build → gate** (`templates/WORK.feature.md`): independent
  designs from stated angles → one node judges them side by side
  (`{{prev}}`, a justified barrier) → a mutator implements → an
  independent node re-runs the harness → red-team.
- **Advisor–orchestrator**: a planner node emits the work-list as a typed
  contract; worker nodes consume it. The planner never grades its own plan.
- **Zone defense** (org graph): stable `agents/*.md` roles own domains —
  `red-team-reviewer` owns the adversarial pass — referenced by
  `agentType`, not re-prompted inline. An agent file may declare the contract
  it answers in (`contract: RedTeamV1` in its frontmatter, or `contract: prose`
  for one that reports rather than returns a schema). Binding a node to a
  resolved agent whose declared contract is not the node's is rejected at
  compile time: the name being real is not evidence the answer fits, and that
  mismatch otherwise surfaces as a dead gate late in the run. An `agentType`
  the compiler cannot resolve is only ever a **warning** — plugins and
  built-in types are outside its view, so refusing would reject valid IR.
- **Loop-until-dry** (`templates/WORK.discovery.md`): a `repeat` block on a
  finder turns fixed fan-out into unknown-size discovery. Use it when "how
  many are there" is the question rather than an input — audits, sweeps,
  exhaustive reviews.
- **Pipeline of loops**: each mutating node works one loop slice
  — TDD, `--changed` green per edit, `--full` before returning. The graph
  sequences slices; it never replaces the gate.
- **Escalation ladder** (approximated): most items are cheap to judge and
  a few deserve expensive reasoning. Build it as two stages sharing an
  edge — a `skeptic:1` first pass, then a downstream node (or a second
  campaign seeded by `memory`) that re-judges only the survivors at
  `panel:3`/high `effort` — with a `reduce` node between them cutting to
  the items worth escalating. Runtime uncertainty-routing (an item's own
  confidence deciding its tier mid-run) is deliberately not an IR
  construct yet; see ROADMAP before building it ad hoc.

## Inputs, failure, budget

Inputs are normalized by the generated code (object, JSON string, or bare
string) and logged; required args throw. Declare optional inputs in
`argDefaults` so a default is a stated decision, not a silent substitution
— a wrong-target run looks exactly like a successful one, which makes it
the most expensive failure a graph has.

Failure is local: `onRed: drop+log` drops one item and says so; `halt`
stops the campaign. Dead nodes land in provenance with a reason.

Budget is enforced twice, and neither check is advisory. At compile time
`budget.maxNodes` is a hard ceiling on planned agent calls, priced at the
worst case including discovery rounds. At run time two floors apply:
`verifyFloorTokens` stops verification fan-out (claims left unchecked are
logged UNVERIFIED) and `nodeFloorTokens` stops *work* fan-out before a
node or another discovery round starts — recorded as `SKIPPED` in
provenance and carried into the Evidence row as incomplete coverage. A
campaign that ran out of budget says so; it never reads as a clean sweep.

## Discovery loops

A fixed fan-out finds what one pass happens to find. When the size of the
work is unknown, add `repeat` to the finder:

```json
"repeat": { "untilDryRounds": 2, "maxRounds": 4, "dedupeBy": ["file", "line", "claim"] }
```

Rounds re-run until `untilDryRounds` consecutive rounds surface nothing
new, bounded by `maxRounds`. Use `{{seen}}` in the prompt so each round is
told what earlier rounds found and spends itself on new ground.

**`maxRounds` is a backstop, not a thoroughness dial.** The dry rule is
what should end a healthy sweep; the ceiling exists for the loop that
never converges. Set it with headroom — `untilDryRounds + 3` or more — or
the ceiling ends every run and every run reports INCOMPLETE, which trains
readers to ignore the word. The compiler warns when the headroom is under
two rounds.

A generous ceiling is cheap, because it prices risk rather than spend:
`budget.maxNodes` must cover the worst case (rounds are priced in), but
the loop exits the moment it goes dry, so rounds that never run cost
nothing. Raising `maxRounds` from 4 to 6 raises the declared ceiling by
50% and typical spend by roughly zero. Let the token floors, not the round
count, be what actually caps cost.

## Sweeps that compound

A `repeat` block makes one sweep exhaustive; it does nothing for the next
one. Without memory, run two re-finds everything run one found, and pays a
fresh panel to reach verdicts that already exist — which is where the
"verification fan-out dominates cost" lesson actually bites.

`memory` closes that loop:

```json
"memory": { "seed": "defect-sweep", "emit": "defect-sweep" }
```

`emit` files every judged item as a `LessonV1` row in
`.claude/fluxpoint/memory.jsonl` — survivors *and* the panel's kills with
the objection that killed them, which is the half `verifyItems` used to
discard. `seed` hands the next run that frontier. Rows are written by
`record-run.py` from the run's own summary, never by an agent, and a row
whose `provenance.runId` names no recorded run is refused — the opening a
fabricated summary would use to plant durable knowledge.

The compiler rejects: a `memory` block declaring neither side; `seed`
without `repeat` (the seed feeds a sweep's seen-list); `emit` without a
verification tier (filing unjudged output would promote a well-formed guess
to institutional knowledge) or without `verifyOver`, or on a contract whose
items carry no `claim`; and `memory.key` alongside `repeat.dedupeBy`, since
two spellings of one identity is how they drift apart.

**A seed is advisory and never suppressive.** It reaches the finder's
prompt; it never enters the dedup set. Seeding the dedup set would silently
drop a re-found item — and a finding that comes back is evidence the lesson
went stale, exactly the regression a sweep is run to catch. So a re-found
item is judged again on its merits, and the cost saving comes from a finder
that knows where the frontier was, not from a loop that refuses to look.

Two rules the compiler enforces because getting them wrong is subtle:

- **Dedup against everything seen, not everything confirmed.** Items enter
  the seen-set before verification. Dedup against survivors instead and
  every judge-rejected finding reappears next round — the loop never
  converges and the panel re-judges the same rejects forever.
- **Hitting `maxRounds` is not exhaustion.** Ending on the ceiling while
  still finding new items is logged `discovery INCOMPLETE, not exhausted`.
  A sweep that stopped early and a sweep that finished are different
  claims and never get blurred into one.

Seeds are loaded ranked, not raw: `scripts/recall.py --format seedmap`
serves the same `{tag: {keys, killed}}` shape as `memory.py --load`, but
ordered by a hybrid of BM25, the memory graph (provenance, kill events,
touched files, campaign membership walked with personalized PageRank),
and — when an embedder key is present — semantic similarity to the
campaign line. Relevance decides what reaches the prompt *first* under a
budget; it never decides what gets judged. Lessons whose tag never
matches the sweep's still surface at SessionStart, ranked against the
work file, so knowledge filed under one campaign reaches the next one
without anyone guessing the tag.

The killed half has its own channel. Declaring

```json
"memory": { "seed": "defect-sweep", "emit": "defect-sweep", "priors": true }
```

hands every refuter the node spawns the seed tag's killed claims with the
objections that killed them — capped at 5 items of 400 chars, framed
explicitly as priors the panel may overturn. The finder's prompt never
carries them: priors are addressed to judges, whose job is to not
re-derive an argument the store already holds, not to the search, whose
job is to look everywhere. The compiler rejects `priors` without a `seed`
(nothing to load) or without a verification tier (nobody to tell).

## Running, repairing, evidence

`/fluxpoint:graph-run` compiles, runs, and records. Watch with
`/workflows`; never poll with sleep. Repair is targeted: fix the one red
node in the IR, recompile, then re-invoke with `resumeFromRunId` — the
unchanged prefix returns from cache and only the repaired node onward
re-runs. Never restart a mostly-green graph from zero. Before diagnosing
an empty result, read `journal.jsonl`. There is deliberately no per-node
retry knob: recovery is cached-prefix resume plus the once-only ledger,
because an in-run retry loop is a second failure policy hiding inside the
first.

## Observe the graph, not the chat

Tune campaigns against numbers, not transcripts. Every run already
records them: the summary carries `spawned` (agent calls that really went
out) and `declined` (calls the node ceiling refused) against `planned`
(the compile-time worst case) and `spent` (the runtime's own token
meter); discovery rounds carry structured found/fresh/kept tallies with
per-worker unique-new counts keyed by worker identity (fan-out
efficiency: a worker at zero is width without coverage, and a dead
worker reads null, never a shifted neighbor's count); reduces carry
before/after (compression). `scripts/metrics.py` folds `runs/*.json`,
`memory.jsonl`, and `inbox.jsonl` into per-campaign rates — node death
and skip rates, sweep endings split four ways (dry rule, ceiling,
budget-truncated, halted — only the dry rule is convergence), panel kill
rate, inbox pressure — and `/fluxpoint:status` reports the trend block. A
kill rate near 0% means the panels may be decoration; near 100% means
the finders are badly scoped. One number is knowingly absent: per-node
wall-clock (the executor forbids `Date` in workflow scripts to keep
resume deterministic), so the serialization cost of an undeclared edge is
caught at compile time by a warning, not measured at run time — the
compiler flags adjacent top-level nodes that declare no dependency.

Graph green is not done. The campaign still exits through the
loop-engineering ship pipeline — `harness.sh --full`, red-team
`VERDICT: SHIP`, the Merge policy in WORK.md — and the Stop-hook DoD gate
keeps final authority.

## Where the Workflow tool is unavailable

Same IR, same compiled script: run its nodes as parallel subagent calls
with the same contracts, sequencing stages yourself, then record with
`--executor degraded-subagents`. Do not silently downgrade — the Evidence
row says which executor ran the graph.
