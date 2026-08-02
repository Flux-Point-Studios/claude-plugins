# GRAPH: <one-line campaign goal>

STATUS: DESIGN
BUDGET: <max nodes> nodes, <token target if any>, <wall-clock bound>

## Org graph (stable roles this repo keeps)
| Role | Zone owned | Binding | Notes |
|---|---|---|---|
| red-team-reviewer | adversarial diff review | fluxpoint-loop agent, `agentType: "red-team-reviewer"` | verdict shares merge authority with the harness |
| graph-auditor | graph specs and scripts | fluxpoint-graph agent | audits this file before STATUS: READY |
| <role> | <zone> | <agents/*.md name> | |

## Work graph (this campaign)
Nodes — one row each; a node without a contract does not run:
| # | Node | Context packet (may believe) | Contract (must produce) | Verified by | On red |
|---|---|---|---|---|---|
| 1 | <name> | <exact inputs: spans, prior contracts, commands> | <schema summary> | <harness cmd \| refuter majority \| schema-only + why acceptable> | <retry N \| repair via # \| drop+log \| halt> |

Edges — explicit and deterministic; more than ten lines means split the campaign:
- 1 → 2 when <condition>
- 2 → {3a..3n} fan-out over <work-list>, pipeline, no barrier
- {3*} ⇒ 4 barrier because <cross-item reason: dedup / zero-count exit / side-by-side judging>

## Verification map
- Inner: every mutating node works a fluxpoint-loop slice —
  `scripts/harness.sh --changed <file>` green per edit, `--full` before
  the node returns its contract.
- Edge: <which edges run --full, which use refuter majorities, which are
  schema-only and why that is acceptable there>.
- Terminal: `scripts/harness.sh --full` exit 0 plus red-team
  `VERDICT: SHIP` before merge. The graph never overrides the DoD gate.

## Failure policy
- Node death (null return): <drop item and log | repair node and resume | halt>
- Budget floor: stop fan-out when remaining < <N>; log what was left undone.
- Dry rule: <K> consecutive empty rounds ends any discovery loop.
- Halt condition a human can name: <what stops this graph unconditionally>

## Evidence
| When (UTC) | runId | Nodes green/red | Harness | Red-team | Outcome |
|---|---|---|---|---|---|

## Notes for the next run
<current state, dead nodes, targeted repairs planned, resume point>
