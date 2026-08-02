# fluxpoint-graph — design notes toward v0.2

These notes record what the v0.1 smoke test proved and the redesign it
points to. Not bound by the fluxpoint-loop conventions — the goal is the
best design, not symmetry with the loop plugin.

## What the smoke test established

Onboarded a scratch repo (`smoke-ledger`: a lovelace ledger, unittest
suite, real `scripts/harness.sh`), ran `graph-init`, then exercised the
executor:

- **Ping graph** — two independent nodes counted tracked files; `agree:
  true` (10 = 10). Nodes, schema contracts, and the executor wiring work.
- **`review.graph.js`** — 12 agents (3 dimension finders → 3-refuter
  panel per finding), 0 errors, empty-result handling exercised (2 finders
  found nothing), majority filtering and severity sort exercised. The
  full fan-out/verify machine ran end to end and produced **verified real
  findings** — including a HIGH fail-open in the sibling loop plugin's Stop
  gate. The approach has teeth.

Two defects surfaced, both now fixed in the templates:

1. **Silent wrong-target.** `args.target` arrived as a JSON *string*;
   `review.graph.js` fell back to its default target instead of failing
   loudly, and reviewed the wrong repo. Fix: normalize `args`
   (object | JSON string | bare string) and `log()` the resolved target.
2. **Self-report gate.** `feature.graph.js` gated red-team on
   `slice.harnessExit` — the *implementer node's own reported exit code*.
   The node that wrote the code graded its own pass. Fix: an independent
   `harness-verify` node checks out the branch and re-runs the harness
   itself; the gate keys on that.

One cost signal: **570k subagent tokens, ~25 min, 12 agents** for a single
review. Verification fan-out (9 of 12 agents were refuters) dominates both.

## The core tension

v0.1 makes `GRAPH.md` a prose spec that a model hand-compiles into a
`.graph.js` script. That transcription step is itself the source of the
bug class the `graph-auditor` exists to police ("does the script mirror
GRAPH.md?"). The highest-leverage redesign removes the compile step.

## Prioritized redesign

### 1. Declarative graph IR — single source of truth (highest leverage)
Replace prose→script transcription with a declarative block inside
GRAPH.md (fenced ```yaml/```json) that `graph-run` compiles *mechanically*
to the Workflow script. Nodes, edges, contracts, and verifiers become
data:

```yaml
nodes:
  - id: find-correctness
    role: finder            # -> agents/*.md or inline prompt
    context: [diff, src/**] # spans/refs, never the transcript
    contract: FindingV1     # named schema (see 4)
    verify: panel:3         # tier (see 2)
    onRed: drop+log
edges:
  - {from: find-*, to: verify, when: "findings>0"}
```

Kills "spec diverges from script" entirely, turns `graph-audit` into a
mostly-static check, and makes evidence/resume trivial to wire. This is
the single biggest upgrade.

### 2. Verification tiers, not one-size panels (biggest cost win)
A per-edge policy — `schema-only | harness | skeptic:1 | panel:3 |
panel:5` — chosen by stakes (severity, blast radius, reversibility).
Default cheap; escalate to a full panel only for HIGH/CRITICAL. v0.1
hardcodes `panel:3` on every finding (9 refuters for 3 findings); tiering
plus cheap models/effort for refuters (`opts.model`, `opts.effort`,
already supported, unused) is most of the 570k tokens back.

### 3. Never trust a node's self-report — a checked invariant
The `feature.graph.js` bug generalizes: every gate must be re-derived by a
node that did not produce the artifact. Make independent verification a
first-class edge type in the IR and an auditor invariant, not a convention
a template can quietly violate.

### 4. Contracts as versioned, named artifacts
`contracts/*.schema.json` (`FindingV1`, `VerdictV1`, `SliceV1`,
`DesignV1`) referenced by name from nodes. Harden once, reuse everywhere;
they become the typed interface between org-graph roles, and the auditor
checks schema strength in one place instead of per template.

### 5. Provenance as a build artifact, not a manual table
`graph-run` writes `.claude/fluxpoint-graph/runs/<runId>.json` (nodes,
contracts returned, verifier exits, verdicts, token cost) and
auto-appends the GRAPH.md Evidence row. No more hand-run shell to stamp
evidence. Feeds a `/fluxpoint-graph:graph-status` sibling of `loop-status`.

### 6. Org graph = real installed subagents
Stable roles become actual `agents/*.md` with pinned contracts; the work
graph references them by name; zone ownership becomes enforceable (a node
in the `src` zone can only be the src-owner role). Makes "zone defense"
real instead of a markdown table.

### 7. Budget as a declared, enforced input
The GRAPH.md `BUDGET` line becomes the token target the runner enforces —
scaling fan-out and verification tiers to fit, failing at design time if
the campaign cannot fit. v0.1 ignores `budget` entirely.

### 8. (Bigger call) Unify loop + graph into one plugin, two drivers
One shared harness contract, one `WORK.md` state model with a mode line,
one Evidence discipline, one review family (red-team + graph-audit).
Removes the duplicated settings snippets, Evidence tables, and
ship-pipeline prose now copied across two plugins. Presented as an option,
not a mandate — the split may be worth keeping for install granularity.

## Safety note surfaced by the smoke test (loop plugin)

The Stop gate is bypassable. `.dirty` — the marker `dod-gate.sh:22`
requires before it runs the harness — is written only by the
`Write|Edit|MultiEdit` PostToolUse hook (`verify-changed.sh:24`). Source
edits made through the **Bash tool** (`cat > f`, `sed -i`, `git apply`)
never arm it, so the gate exits 0 with the harness never run. Same disease
as the graph self-report bug: trusting a signal the agent can route
around. Fix: re-derive dirtiness from `git status --porcelain` at Stop
time instead of a hook-written marker. Recommended as a fluxpoint-loop
follow-up (v0.1.1 closed the exec-bit vector, not this one).
