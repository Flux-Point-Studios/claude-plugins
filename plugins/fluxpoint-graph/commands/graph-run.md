---
description: Compile GRAPH.md into a Workflow script, execute it, and record evidence — targeted node repair and cached resume on partial failure, never a restart from zero.
argument-hint: [campaign name, or blank for the repo GRAPH.md]
---

Run the designed graph. This command is the explicit authorization the
Workflow tool requires.

1. Preflight: `GRAPH.md` exists and reads `STATUS: READY` (or `RUNNING`
   with a resume point in Notes). Every command the verification map
   names exists (`scripts/harness.sh` if referenced). Any `agentType` the
   spec names resolves (red-team-reviewer requires fluxpoint-loop). Stop
   and report exactly what is missing; do not improvise around it.
2. Compile per the graph-engineering skill's compile rules into
   `.claude/workflows/<campaign>.graph.js` — or use a shipped template
   when the work graph matches its shape, passing args
   (`review.graph.js` takes `{target}`, `feature.graph.js` takes
   `{goal, constraints}`). The script must mirror GRAPH.md exactly: same
   nodes, same edges, same verifiers. A divergence is a spec bug — fix
   GRAPH.md or the script before running.
3. Set `STATUS: RUNNING`. Invoke the Workflow tool with
   `{scriptPath: ".claude/workflows/<campaign>.graph.js", args: {...}}`.
   Watch with `/workflows`; never poll with sleep.
4. On completion, read the returned contracts. Before diagnosing an empty
   or surprising result, read the run's `journal.jsonl` — it records what
   each node actually returned, including cached ones. Append the
   Evidence row to GRAPH.md: UTC time from `date -u` (never from inside
   the script), runId, nodes green/red, harness exit, red-team verdict,
   outcome.
5. Partial failure is a targeted repair, not a restart: fix the one red
   node — its prompt, its schema, or the code it touched — stop the run if
   still live, then re-invoke with `{scriptPath, resumeFromRunId}`. The
   unchanged prefix returns from cache; only the repaired node onward
   re-runs. Restarting a mostly-green graph from zero is a finding.
6. Graph green is not done. The campaign still exits through the
   loop-engineering ship pipeline — `scripts/harness.sh --full`, red-team
   `VERDICT: SHIP`, the Merge policy in LOOP.md — and the Stop-hook DoD
   gate keeps final authority. Set `STATUS: DONE` only when the Evidence
   row shows the terminal gate green; otherwise back to `READY` with the
   repair plan in Notes.
7. If the Workflow tool is unavailable in this session, degrade per the
   skill: compile the same edges to parallel subagent calls with the same
   schema-shaped prompts, sequence stages yourself, and record the
   degraded executor in the Evidence row.
