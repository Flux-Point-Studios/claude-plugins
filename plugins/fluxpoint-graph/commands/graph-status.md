---
description: Show the campaign's current state — IR validity, recorded runs, dead nodes, and what the next action is.
argument-hint: [path to GRAPH.md]
---

Report the graph's state. Read-only; change nothing.

1. Resolve the plugin root (`${CLAUDE_PLUGIN_ROOT}`, else
   `find ~/.claude/plugins -type d -name fluxpoint-graph | head -1`) and
   the graph file ("$ARGUMENTS" or `GRAPH.md`).
2. Gather, without editing anything:
   - `STATUS:` line and the campaign goal.
   - `python3 "$ROOT/scripts/compile-graph.py" <graph> --check` — report
     valid/invalid, node count, planned agent calls, budget ceiling.
   - the last five rows of the Evidence table.
   - `.claude/fluxpoint-graph/runs/*.json`, newest first: runId, outcome,
     nodes OK/dead, findings, harness exit, red-team verdict. For the
     newest run, list any node whose status is `DEAD` and its detail.
   - whether `scripts/harness.sh` exists and what the last
     `.claude/fluxpoint-loop/last-harness` verdict was, if present.
3. Print a short report in this order: campaign and STATUS; IR check
   result; last run summary; dead nodes needing targeted repair; then one
   line naming the single next action — design, compile-fix, run, resume
   from a runId, or ship per LOOP.md Merge policy.
4. If no runs are recorded, say so plainly and point at
   `/fluxpoint-graph:graph-run`. Never infer a run happened from the
   presence of a compiled script.
