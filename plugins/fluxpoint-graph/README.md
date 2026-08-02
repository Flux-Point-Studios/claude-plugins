# fluxpoint-graph

Graph Engineering harness for Claude Code — the layer above fluxpoint-loop.
Loops made one agent's cycle programmable; graphs make the organization of
agents programmable.

A campaign is specified once, as a declarative IR inside `GRAPH.md`, and
compiled to a Workflow script deterministically — no model transcribes the
spec, so the spec cannot drift from what runs.

- `scripts/compile-graph.py` — IR → validated → Workflow script. Rejects
  unsound graphs at compile time (missing contracts, even panels,
  self-certifying mutators, dangling edges, budget overruns).
- `scripts/record-run.py` — writes the run's provenance artifact and
  appends the GRAPH.md Evidence row. Evidence is a build artifact.
- `contracts/` — versioned named schemas (`FindingsV1`, `VerdictV1`,
  `HarnessCheckV1`, `DesignV1`, `SliceV1`, `RedTeamV1`) referenced by name
  from IR nodes.
- `commands/` — `/fluxpoint-graph:graph-init`, `:graph-design`,
  `:graph-run`, `:graph-audit`, `:graph-status`.
- `agents/graph-auditor.md` — semantic adversarial review of an IR
  (stakes-vs-tier, context scoping, hidden coupling); structure is the
  compiler's job.
- `skills/graph-engineering/` — loop-vs-graph rule, the five primitives
  and their bindings, tier selection, canonical shapes.
- `templates/` — `GRAPH.md` (fan-out/verify campaign), `GRAPH.feature.md`
  (council → build → independently gated), settings snippet.
- `tests/compile-test.py` — 22 invariant tests; every unsound-graph class
  must stay rejected.

Compiled `.graph.js` files are generated into `.claude/workflows/` at run
time. They are build output: never hand-edit them, never commit them as
templates.

No hooks of its own: fluxpoint-loop already owns SessionStart, PostToolUse,
and Stop. This plugin composes with that gate instead of contending with
it — every mutating node is a loop-engineered slice, and
`scripts/harness.sh` stays the only authority on done.

Requires `python3` (standard library only) and a Claude Code version with
the Workflow tool.

Full documentation lives in the repository root README.
