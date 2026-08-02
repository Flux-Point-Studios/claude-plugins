# fluxpoint-graph

Graph Engineering harness for Claude Code — the layer above fluxpoint-loop.
Loops made one agent's cycle programmable; graphs make the organization of
agents programmable. Components:

- `skills/graph-engineering/` — the loop-vs-graph decision rule, the five
  graph primitives and their Claude Code bindings, GRAPH.md → Workflow
  script compile rules, canonical graph shapes.
- `commands/` — `/fluxpoint-graph:graph-init`, `:graph-design`,
  `:graph-run`, `:graph-audit`.
- `agents/graph-auditor.md` — adversarial reviewer of graph specs and
  compiled workflow scripts.
- `templates/` — `GRAPH.md` spec contract, `review.graph.js`,
  `feature.graph.js`, settings snippet; copied into repos by `graph-init`.

No hooks of its own: fluxpoint-loop already owns SessionStart, PostToolUse,
and Stop. This plugin composes with that gate instead of contending with
it — every mutating node is a loop-engineered slice, and
`scripts/harness.sh` stays the only authority on done.

Full documentation lives in the repository root README.
