---
name: graph-engineering
description: How to run Graph Engineering at Flux Point — deciding when a loop must become a graph, specifying multi-agent campaigns in GRAPH.md as nodes with typed contracts and deterministically verified edges, compiling the spec to a Claude Code Workflow script, and repairing runs with targeted resume. Use whenever the user mentions graphs, graph engineering, multi-agent work, orchestration, councils, judge panels, fan-out, swarms, wiring or organizing subagents, GRAPH.md, workflow scripts, or asks to parallelize verified work across agents, even if they never say "graph engineering".
---

# Graph Engineering

Loops made one agent's behavior programmable; graphs make the organization
of agents programmable. The graph is the artifact: a spec (`GRAPH.md`), a
compiled executor (a Workflow script), and the same deterministic harness
deciding green. A node is not a prompt — it is a loop-engineered unit with
a scoped context packet in and a typed contract out. The rule that survives
the upgrade unchanged: the agent never decides "done"; the harness does.

## Loop or graph

Stay on fluxpoint-loop when the work is one zone, one contract, serial
evidence. Escalate to a graph when two or more hold: independent subtasks
that fail independently; a work-list to fan out over; claims that need
adversarial verification from independent context; cross-zone ownership
(validators vs off-chain vs infra); a budget worth isolating per node. A
work graph whose edges do not fit in ten lines is scope creep — split the
campaign. Never build a graph as ceremony around a single slice.

## The five primitives and their Claude Code bindings

| Primitive | Meaning | Claude Code binding |
|---|---|---|
| Node | one responsibility, its own context | `agent()` call in a Workflow script; stable roles as `agents/*.md` subagents via `agentType` |
| Edge | explicit routing between nodes | deterministic JS in the script — `pipeline()`, `parallel()`, conditionals, loop-until-dry — never a model-improvised hand-off |
| Contract | what a node must produce | `schema:` JSON Schema → validated structured output; malformed output retries at the tool layer, not in your prose |
| Context packet | what a node may believe | the prompt you compose for it: exact spans, prior contracts, named commands — never the whole transcript |
| Verification | who decides an edge is green | `scripts/harness.sh --changed\|--full` run inside verify nodes; refuter majorities for claims; red-team verdict before merge |

Durable coordination rides on the executor: every run has a `runId`,
`journal.jsonl` records each node's actual return, and resume replays the
longest unchanged prefix from cache.

## Compile rules (GRAPH.md → Workflow script)

1. `export const meta` mirrors the work graph: pure literal, one `phases`
   entry per phase, same titles as the `phase()` calls and `opts.phase`
   strings.
2. `pipeline()` is the default wiring. A barrier — `parallel()` before a
   dependent stage — only where stage N needs ALL of stage N−1 (dedup,
   zero-count early exit, judging candidates side by side). State the
   reason in a comment or it is a finding.
3. Every node gets `schema:`. A node returning prose is a node without a
   contract. Write schemas a lazy output cannot satisfy: `required` fields,
   `minLength`, enums for verdicts, exit codes as integers.
4. Compose context packets from contracts, not transcripts: pass
   `f.file`, `f.claim`, the exact spans. A verifier re-reads the code
   itself; it never trusts the finder's summary.
5. Mutating nodes take `isolation: 'worktree'` whenever two could touch
   the same tree; merging their work is an explicit node, not an accident.
6. Verify nodes run the harness themselves with Bash and return the real
   exit code in their contract. The graph trusts exit codes, not
   adjectives.
7. Failure isolation: a dead node returns null — `.filter(Boolean)` and
   `log()` the drop. No silent caps: top-N, sampling, and no-retry each
   get a `log()` line saying what was left on the floor.
8. Bound every loop: `while (budget.total && budget.remaining() > floor)`
   or a K-consecutive-dry-rounds counter. Dedup discovery against
   everything seen, not everything confirmed, or the loop never converges.
9. Model tiers per node (`opts.model`, `opts.effort`) only when confident:
   cheap effort for mechanical stages, high effort for judges and
   refuters. Default is inherit.
10. No `Date.now()`, `Math.random()`, or argless `new Date()` in scripts —
    they break resume. Stamp Evidence rows from the shell after the run.

## Canonical shapes

- **Fan-out/verify** (review): dimensions → finders → per-finding refuter
  panel. Odd panel, majority kills; refuters are prompted to refute, never
  to confirm, and default to refuted when uncertain.
- **Council** (design): N independent attempts from stated angles → judges
  score → synthesize the winner, grafting the runners-up's best ideas.
  For wide solution spaces; beats one-attempt-iterated.
- **Advisor–orchestrator**: a planner node emits the work-list as a typed
  contract (never prose); worker nodes execute it. The planner plans; it
  does not also grade its own plan.
- **Zone defense** (org graph): stable `agents/*.md` roles own domains —
  red-team-reviewer owns the adversarial pass — and the work graph crosses
  zones via `agentType`, not by re-prompting the zone's knowledge inline.
- **Loop-until-dry**: unknown-size discovery ends after K consecutive
  rounds finding nothing new, with a budget floor underneath.
- **Pipeline of loops**: each mutating node works one fluxpoint-loop slice
  — TDD, `--changed` green per edit, `--full` before returning. The graph
  sequences slices; it never replaces the gate.

## Running, repairing, evidence

`/fluxpoint-graph:graph-run` compiles and executes; it is also the explicit
authorization the Workflow tool requires. Watch with `/workflows`; never
poll with sleep. Repair is targeted: fix the one red node (its prompt, its
schema, or the code it touched), stop the run if still live, then re-invoke
with `resumeFromRunId` — the unchanged prefix returns from cache and only
the repaired node onward re-runs. Never restart a mostly-green graph from
zero. Before diagnosing an empty result, read the run's `journal.jsonl` —
it records what each node actually returned.

Every run appends a row to GRAPH.md's Evidence table: runId, nodes
green/red, harness exit, verdicts. Graph green is not done. The campaign
still exits through the loop-engineering ship pipeline — `harness.sh
--full`, red-team `VERDICT: SHIP`, the Merge policy in LOOP.md — and the
Stop-hook DoD gate keeps final authority.

## Where the Workflow tool is unavailable

Same GRAPH.md; compile the edges to parallel Agent-tool calls in a single
message with the same schema-shaped prompts, sequence the stages yourself,
and record in Evidence that the run was degraded: no journal, no cached
resume. Do not silently downgrade — the Evidence row says which executor
ran the graph.
