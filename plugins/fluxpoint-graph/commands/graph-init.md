---
description: Scaffold Graph Engineering into the current repo — GRAPH.md spec contract, canonical .graph.js templates, settings and .gitignore wiring — then smoke the executor with a two-node ping graph.
argument-hint: [one-line campaign goal]
---

Onboard this repository onto the fluxpoint-graph pattern. Work through
every step; do not stop at copying files.

1. Locate the plugin templates. Try `${CLAUDE_PLUGIN_ROOT}/templates`
   first; if that expands empty in your shell, find them with
   `find ~/.claude/plugins -type d -path '*fluxpoint-graph/templates' | head -1`.
2. Copy, without overwriting anything that already exists:
   - `templates/GRAPH.md` → `GRAPH.md`
   - `templates/review.graph.js` → `.claude/workflows/review.graph.js`
   - `templates/feature.graph.js` → `.claude/workflows/feature.graph.js`
   If a destination exists, show a diff and propose a merge instead.
3. Ensure `.gitignore` contains the line `.claude/fluxpoint-graph/`.
4. Merge the keys from `templates/settings.snippet.json` into
   `.claude/settings.json`, creating the file if absent and preserving
   every existing key.
5. Check composition with fluxpoint-loop: if `scripts/harness.sh` is
   missing, recommend running `/fluxpoint-loop:loop-init` first — until it
   exists, mark every edge in GRAPH.md's verification map schema-only and
   say so in Notes. Confirm the Workflow tool is available in this
   session; if it is not, record in GRAPH.md Notes that runs degrade to
   parallel subagent fan-out per the graph-engineering skill.
6. Tailor GRAPH.md: fill the org graph from the repo's real zones (read
   the tree — validators, off-chain, infra, frontend, docs — and name only
   roles this repo will actually reuse). Set the goal line from
   "$ARGUMENTS" if provided; otherwise ask for it before writing.
7. Smoke the executor with a two-node ping graph via the Workflow tool
   (this command is your authorization to run it):

   ```js
   export const meta = {
     name: 'graph-smoke',
     description: 'Two-node ping proving the graph executor wiring',
     phases: [{ title: 'Ping' }],
   }
   const COUNT = {
     type: 'object', required: ['files'],
     properties: { files: { type: 'integer' } },
   }
   const prompt = 'Run: git ls-files | wc -l. Return the count as {files}.'
   const scout = await agent(prompt, { label: 'scout', phase: 'Ping', schema: COUNT })
   const verify = await agent(prompt, { label: 'verify', phase: 'Ping', schema: COUNT })
   return { agree: !!scout && !!verify && scout.files === verify.files, scout, verify }
   ```

   `agree: true` proves nodes, contracts, and the executor. Append the
   first Evidence row to GRAPH.md: UTC time from `date -u`, the runId,
   `2/2 green`, harness `n/a`, red-team `n/a`, outcome `smoke`.
8. Finish with a short report: files created, smoke verdict and runId,
   and the two commands that start a campaign
   (`/fluxpoint-graph:graph-design <goal>`, then `/fluxpoint-graph:graph-run`).
