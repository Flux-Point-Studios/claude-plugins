# fluxpoint — design notes

These notes record what the v0.1 smoke test proved, the redesign it
pointed to, and how that redesign landed. Not bound by prior conventions —
the goal was the best design, not symmetry with what came before.

## Status: all eight recommendations shipped (1–7 in v0.2.0, 8 in v1.0.0),
plus the two gaps v1.0 named, closed in v1.1.0

| # | Recommendation | Shipped as |
|---|---|---|
| 1 | Declarative graph IR | ```json graph-ir block in WORK.md + `scripts/compile-graph.py` |
| 2 | Verification tiers | `verify: schema-only \| harness \| skeptic:N \| panel:N`, low-effort refuters, `verifyFloorTokens` |
| 3 | No self-report gates | compile-time invariant: `mutates` requires an `independent` node that `verifies` it |
| 4 | Named versioned contracts | `contracts/*.schema.json`, referenced by name, inlined at compile |
| 5 | Provenance as build artifact | `scripts/record-run.py` → `runs/<runId>.json` + auto-appended Evidence row; `/fluxpoint:status` |
| 6 | Org graph = real subagents | IR `roles` → `agentType`; `red-team-reviewer` wired in the feature campaign |
| 7 | Budget declared and enforced | `budget.maxNodes` rejected at compile time; run-time verification floor logs what it skips |
| 8 | Unify loop + graph into one plugin | v1.0.0: one `fluxpoint` plugin, one `WORK.md`, one Evidence table, `/fluxpoint:migrate` |

Recommendation 8 shipped in v1.0.0 after being parked once. The parked
rationale was that merging changes install granularity for every consuming
repo and rewrites the public surface — true, and the reason it needed a
real migration path rather than a rename. What made it worth doing: both
plugins had grown their own Evidence table, their own settings snippet,
and their own copy of the ship-pipeline prose, and the graph side already
depended on the loop side for `red-team-reviewer` and `harness.sh`. Two
plugins that cannot be installed independently are one plugin.

What unification actually bought:
- one `WORK.md` — a single Definition of Done, Merge policy, and Evidence
  table, whether work is decomposed as loop slices (Plan) or a campaign
  (Campaign). Previously a repo running both had two DoDs that could
  disagree.
- one Evidence discipline: `| When | Source | Outcome | Claim | Proof |`,
  where `Source` is `loop` or a runId.
- one state directory, one settings snippet, one install.
- `/fluxpoint:migrate`, which folds pre-1.0 repos over without losing an
  Evidence row, and hooks that keep honoring `LOOP.md` until it runs.

## v1.1.0: the two gaps v1.0 shipped with

Both were named honestly at v1.0 rather than discovered later, and both
were real:

**Unknown-size discovery.** The IR could only express fixed fan-out, so an
audit found whatever one pass happened to find. A `repeat` block
(`untilDryRounds`, `maxRounds`, `dedupeBy`) turns a finder into a
loop-until-dry sweep, with `{{seen}}` telling each round what earlier
rounds surfaced. Two subtleties are compiler-enforced because they are
easy to get wrong and quiet when wrong: items enter the seen-set **before**
verification (dedup against survivors and every judge-rejected finding
returns next round, so the loop never converges), and ending on the round
ceiling is logged `discovery INCOMPLETE, not exhausted` rather than
reading as a finished sweep.

**Budget enforced where the tokens actually go.** `maxNodes` was a
compile-time ceiling priced at one round, and the only run-time floor
guarded verification. Now rounds are priced into the ceiling (a four-round
loop costs four rounds), and `nodeFloorTokens` stops *work* fan-out —
a node, or another discovery round — before it starts. Declined work is
recorded `SKIPPED` in provenance and surfaces in the Evidence row as
incomplete coverage, so a campaign that ran out of budget can never be
read as a clean sweep.

Verified: 45 compiler invariant tests, 7 gate-bypass regression cases,
20 unified-state and compatibility tests; all three canonical campaigns
compile to syntactically valid Workflow scripts; the provenance path
writes a real artifact and Evidence row; and compiled campaigns have been
executed end-to-end on the Workflow engine.

## What the smoke test established

*(Historical record from v0.1/v0.2. Filenames are as they were then:
`GRAPH.md` and `LOOP.md` became `WORK.md` in v1.0.0, and the hand-written
`review.graph.js` / `feature.graph.js` templates became compiled output.)*

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

v0.1 made `GRAPH.md` a prose spec that a model hand-compiled into a
`.graph.js` script. That transcription step is itself the source of the
bug class the `graph-auditor` exists to police ("does the script mirror
the spec?"). The highest-leverage redesign removes the transcription step.

## Prioritized redesign

### 1. Declarative graph IR — single source of truth (highest leverage)
Replace prose→script transcription with a declarative block inside the
work file (fenced ```yaml/```json) that `graph-run` compiles *mechanically*
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
`graph-run` writes `.claude/fluxpoint/runs/<runId>.json` (nodes,
contracts returned, verifier exits, verdicts, token cost) and
auto-appends the WORK.md Evidence row. No more hand-run shell to stamp
evidence. Feeds `/fluxpoint:status`.

### 6. Org graph = real installed subagents
Stable roles become actual `agents/*.md` with pinned contracts; the work
graph references them by name; zone ownership becomes enforceable (a node
in the `src` zone can only be the src-owner role). Makes "zone defense"
real instead of a markdown table.

### 7. Budget as a declared, enforced input
The WORK.md `BUDGET` line becomes the token target the runner enforces —
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
