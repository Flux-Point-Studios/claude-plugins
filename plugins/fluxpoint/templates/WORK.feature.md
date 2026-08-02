# GRAPH: ship one feature slice, council-designed and independently gated

STATUS: DESIGN

Second canonical campaign: a council designs, one loop-engineered node
implements, and a node that did NOT write the code re-runs the harness
before red-team sees it. Copy over `WORK.md` to use it, or keep both and
point `graph-run` at this file.

Uses the loop side of the plugin: the gate resolves `red-team-reviewer` via
`agentType`, and the implement node assumes `scripts/harness.sh` exists.

## Org graph
| Role | Zone owned | Binding | Notes |
|---|---|---|---|
| architect | design proposals | inline prompt | one per angle, no repo writes |
| builder | implementation | inline prompt, worktree-isolated | works one loop slice |
| red-team | adversarial diff review | `agentType: red-team-reviewer` | verdict gates the merge |

## Work graph

```json graph-ir
{
  "version": 1,
  "name": "feature-campaign",
  "campaign": "Council-designed, loop-implemented, independently gated feature slice",
  "budget": { "maxNodes": 20, "verifyFloorTokens": 50000 },
  "defaults": { "effort": "medium" },
  "requiredArgs": ["goal"],
  "argDefaults": { "constraints": "none beyond WORK.md" },
  "roles": {
    "architect": { "effort": "medium" },
    "builder": { "effort": "high" },
    "red-team": { "agentType": "red-team-reviewer", "effort": "high" }
  },
  "lists": {
    "angles": [
      { "key": "smallest-diff", "brief": "the minimum slice that ships observable value" },
      { "key": "risk-first", "brief": "neutralize the scariest failure mode before anything else" },
      { "key": "contract-first", "brief": "pin the tests and interfaces before any implementation" }
    ]
  },
  "nodes": [
    {
      "id": "design",
      "phase": "Council",
      "role": "architect",
      "foreach": "angles",
      "prompt": "Design an implementation for the goal \"{{A.goal}}\" from exactly this angle: {{item.brief}}. Constraints: {{A.constraints}}. Read the repo first; ground every plan step in real files. Plan steps must be TDD-shaped: each names the failing test before the code.",
      "contract": "DesignV1",
      "verify": "schema-only",
      "onRed": "drop+log"
    },
    {
      "id": "choose",
      "phase": "Council",
      "after": "design",
      "prompt": "Judge these candidate designs side by side for the goal \"{{A.goal}}\" on TDD-ability, blast radius, fit with the Definition of Done in WORK.md, and honesty of their risks. Candidates: {{prev}}. Return the single strongest design, grafting in the best ideas from the runners-up. Barrier justified: judging requires all candidates at once.",
      "contract": "DesignV1",
      "effort": "high",
      "verify": "schema-only",
      "onRed": "halt"
    },
    {
      "id": "build",
      "phase": "Implement",
      "role": "builder",
      "after": "choose",
      "mutates": true,
      "prompt": "Implement exactly this design as one loop slice: {{prev}}. Goal: \"{{A.goal}}\". Constraints: {{A.constraints}}. Work TDD strictly per WORK_PROMPT.md: failing test first, minimum code to green, scripts/harness.sh --changed <file> after each edit. Create and commit on a branch named claude/graph-<short-slug-of-goal>, test and code together. Run scripts/harness.sh --full and report its real exit code; never weaken the harness or delete tests to reach green. Evidence entries are command + observed result.",
      "contract": "SliceV1",
      "verify": "schema-only",
      "onRed": "halt"
    },
    {
      "id": "gate",
      "phase": "Gate",
      "after": "build",
      "independent": true,
      "verifies": "build",
      "prompt": "Independently verify the branch named in this slice: {{prev}}. Check it out into a fresh worktree (git worktree add), run scripts/harness.sh --full YOURSELF, and return the real integer exit code, the exact command, and the last ~20 lines. Do not trust any prior claim about whether it passed — run it and report what you observe.",
      "contract": "HarnessCheckV1",
      "haltWhen": "exit != 0",
      "haltReason": "independent harness re-run disagrees with the implementer; the graph never argues with the harness",
      "onRed": "halt"
    },
    {
      "id": "red-team",
      "phase": "Gate",
      "role": "red-team",
      "after": "gate",
      "prompt": "Red-team the diff of the branch implemented in this campaign against the default branch. Apply your full adversarial checklist. Context: {{prev}}",
      "contract": "RedTeamV1",
      "verify": "schema-only",
      "onRed": "halt"
    }
  ]
}
```

## Verification map
- `design` / `choose`: schema-only. Designs are cheap and the next node
  re-reads the repo anyway; a panel here buys nothing.
- `build`: mutates, so it is worktree-isolated and may not certify itself.
- `gate`: the only node whose exit code the campaign trusts. It never
  wrote the code. `haltWhen: exit != 0` stops the campaign before
  red-team burns tokens on a red branch.
- `red-team`: `VERDICT: BLOCK` is harness-red; fix findings before merge.
- Terminal: merge happens outside the graph, per WORK.md Merge policy,
  with the harness as a required CI check.

## Failure policy
- Any single node returning nothing halts (`onRed: halt`) — an unverified
  gate never passes by default.
- Dead council seats drop and log; the campaign proceeds if at least one
  design survives.
- Budget: 20 planned agent calls, verification floor 50k tokens.
- Halt condition a human can name: independent harness exit != 0.

## Evidence
Appended automatically by `scripts/record-run.py` — do not hand-edit.

| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |
|---|---|---|---|---|---|---|

## Notes for the next run
<current state, dead nodes, targeted repairs planned, resume point>
