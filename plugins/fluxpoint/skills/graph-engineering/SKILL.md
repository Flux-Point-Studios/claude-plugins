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
work graph of more than ten nodes is scope creep — split the campaign.
Never build a graph as ceremony around a single slice.

## The five primitives and their bindings

| Primitive | Meaning | Binding |
|---|---|---|
| Node | one responsibility, its own context | an IR `nodes[]` entry → one `agent()` call; stable roles via `roles` → `agentType` |
| Edge | explicit routing | IR `after` / `foreach`; the compiler derives `pipeline()`/`parallel()` — never a model-improvised hand-off |
| Contract | what a node must produce | `contract: "NameV1"` → `contracts/NameV1.schema.json`, validated structured output |
| Context packet | what a node may believe | the IR `prompt`, with `{{A.x}}` inputs, `{{item.*}}` fan-out, `{{prev}}` predecessor contract — never the transcript |
| Verification | who decides an edge is green | the `verify` tier, plus `independent: true` nodes that re-derive gates |

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
   irreversible spawn the compiled graph checks a committed ledger keyed by
   campaign, node, and prompt hash; a hit restores the recorded result and
   logs `REPLAYED-FROM-LEDGER` instead of performing the effect. A replayed
   node is filed `REPLAYED`, never `OK` — a ceremony that did not happen
   must not read like one that did.
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
  `agentType`, not re-prompted inline.
- **Loop-until-dry** (`templates/WORK.discovery.md`): a `repeat` block on a
  finder turns fixed fan-out into unknown-size discovery. Use it when "how
  many are there" is the question rather than an input — audits, sweeps,
  exhaustive reviews.
- **Pipeline of loops**: each mutating node works one loop slice
  — TDD, `--changed` green per edit, `--full` before returning. The graph
  sequences slices; it never replaces the gate.

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

Two rules the compiler enforces because getting them wrong is subtle:

- **Dedup against everything seen, not everything confirmed.** Items enter
  the seen-set before verification. Dedup against survivors instead and
  every judge-rejected finding reappears next round — the loop never
  converges and the panel re-judges the same rejects forever.
- **Hitting `maxRounds` is not exhaustion.** Ending on the ceiling while
  still finding new items is logged `discovery INCOMPLETE, not exhausted`.
  A sweep that stopped early and a sweep that finished are different
  claims and never get blurred into one.

## Running, repairing, evidence

`/fluxpoint:graph-run` compiles, runs, and records. Watch with
`/workflows`; never poll with sleep. Repair is targeted: fix the one red
node in the IR, recompile, then re-invoke with `resumeFromRunId` — the
unchanged prefix returns from cache and only the repaired node onward
re-runs. Never restart a mostly-green graph from zero. Before diagnosing
an empty result, read `journal.jsonl`.

Graph green is not done. The campaign still exits through the
loop-engineering ship pipeline — `harness.sh --full`, red-team
`VERDICT: SHIP`, the Merge policy in WORK.md — and the Stop-hook DoD gate
keeps final authority.

## Where the Workflow tool is unavailable

Same IR, same compiled script: run its nodes as parallel subagent calls
with the same contracts, sequencing stages yourself, then record with
`--executor degraded-subagents`. Do not silently downgrade — the Evidence
row says which executor ran the graph.
