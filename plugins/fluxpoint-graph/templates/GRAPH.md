# GRAPH: <one-line campaign goal>

STATUS: DESIGN

The IR block below is the single source of truth. `graph-run` compiles it
to a Workflow script mechanically — no model transcribes it — so the spec
and the executor cannot drift. Prose sections explain intent; the IR
decides what runs.

## Org graph (stable roles this repo keeps)
| Role | Zone owned | Binding | Notes |
|---|---|---|---|
| finder | read-only review | inline prompt, default effort | one per dimension |
| red-team | adversarial diff review | `agentType: red-team-reviewer` (fluxpoint-loop) | verdict shares merge authority with the harness |
| graph-auditor | graph specs and IR | fluxpoint-graph agent | audits semantics; the compiler enforces structure |

## Work graph

```json graph-ir
{
  "version": 1,
  "name": "review-campaign",
  "campaign": "Review the working diff; every finding must survive an adversarial panel",
  "budget": { "maxNodes": 32, "verifyFloorTokens": 50000 },
  "defaults": { "effort": "medium" },
  "argDefaults": {
    "target": "the uncommitted diff: git diff HEAD, plus untracked files from git status"
  },
  "roles": {
    "finder": { "effort": "medium" },
    "red-team": { "agentType": "red-team-reviewer", "effort": "high" }
  },
  "lists": {
    "dimensions": [
      { "key": "correctness", "brief": "wrong output, broken invariants, unhandled edges, boundary math" },
      { "key": "security", "brief": "the red-team surface: eUTxO, oracle, authority, numeric, off-chain, infra" },
      { "key": "tests", "brief": "behavior changed with no failing-test-first evidence, weakened assertions" }
    ]
  },
  "nodes": [
    {
      "id": "find",
      "phase": "Find",
      "role": "finder",
      "foreach": "dimensions",
      "prompt": "Review {{A.target}} for {{item.brief}}. Read the code yourself with Read/Grep/Bash. Report only findings with a concrete failure path (inputs/state -> wrong outcome); no style commentary. An empty findings list is a valid, welcome answer.",
      "contract": "FindingsV1",
      "verify": "panel:3",
      "verifyOver": "findings",
      "expectItems": 3,
      "onRed": "drop+log"
    }
  ]
}
```

Edges are the IR's `after`/`foreach` fields; the compiler derives ordering
and fan-out from them. More than ten nodes means split the campaign.

## Verification map
- Tier per node is declared in the IR (`verify`). Pick by stakes, not by
  habit: `schema-only` for cheap mechanical output, `harness` for anything
  claiming green, `skeptic:1` for low-severity claims, `panel:3` (odd, so
  majority is defined) for findings that will cost someone real time,
  `panel:5` only for CRITICAL.
- Inner: every mutating node works a fluxpoint-loop slice —
  `scripts/harness.sh --changed <file>` green per edit.
- Terminal: `scripts/harness.sh --full` exit 0 plus red-team
  `VERDICT: SHIP` before merge. The graph never overrides the DoD gate.
- A node with `mutates: true` MUST have a matching node with
  `independent: true` and `verifies: "<id>"`. The compiler rejects the
  graph otherwise: the node that wrote the code may not certify it.

## Failure policy
- `onRed` per node: `halt` (default for single nodes) or `drop+log`.
- Budget: `budget.maxNodes` is a hard compile-time ceiling on planned agent
  calls; `verifyFloorTokens` stops verification fan-out before exhaustion
  and logs every claim left unverified. No silent caps.
- Halt condition a human can name: <what stops this graph unconditionally>

## Evidence
Appended automatically by `scripts/record-run.py` — do not hand-edit.

| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |
|---|---|---|---|---|---|---|

## Notes for the next run
<current state, dead nodes, targeted repairs planned, resume point>
