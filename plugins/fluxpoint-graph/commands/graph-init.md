---
description: Scaffold Graph Engineering into the current repo — GRAPH.md IR spec, contracts, settings and .gitignore wiring — then prove the toolchain with a compile check and a two-node ping graph.
argument-hint: [one-line campaign goal]
---

Onboard this repository onto the fluxpoint-graph pattern. Work through
every step; do not stop at copying files.

1. Resolve the plugin root: try `${CLAUDE_PLUGIN_ROOT}`, else
   `find ~/.claude/plugins -type d -name fluxpoint-graph | head -1`.
2. Copy, without overwriting anything that already exists:
   - `templates/GRAPH.md` → `GRAPH.md` (the fan-out/verify campaign)
   - `templates/GRAPH.feature.md` → `GRAPH.feature.md` (council →
     implement → independently gated campaign; requires fluxpoint-loop)
   If a destination exists, show a diff and propose a merge instead.
   Compiled scripts are generated into `.claude/workflows/` at run time —
   never commit them as templates; they drift.
3. Ensure `.gitignore` contains `.claude/fluxpoint-graph/` and
   `__pycache__/`.
4. Merge the keys from `templates/settings.snippet.json` into
   `.claude/settings.json`, creating the file if absent and preserving
   every existing key.
5. Check composition and record what you find in GRAPH.md Notes:
   - `python3 --version` must work; the compiler needs python3 only.
   - If `scripts/harness.sh` is missing, recommend
     `/fluxpoint-loop:loop-init` first — until it exists, no node may
     declare `verify: harness`, and say so in Notes.
   - Confirm the Workflow tool is available in this session; if not, note
     that runs degrade to subagent fan-out per the graph-engineering
     skill.
6. Tailor `GRAPH.md`: fill the org-graph table from the repo's real zones
   (read the tree — validators, off-chain, infra, frontend, docs — and
   name only roles this repo will actually reuse). Set the IR `campaign`
   from "$ARGUMENTS" if provided; otherwise ask for it before writing.
7. Prove the toolchain, in this order:
   ```
   python3 "$ROOT/scripts/compile-graph.py" GRAPH.md --check
   ```
   It must print a node count and planned agent calls. Then run the
   two-node ping via the Workflow tool (this command is your
   authorization) to prove nodes, contracts, and the executor:

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
   const prompt = 'Run: git ls-files | wc -l in the repo root. Return the count as {files}.'
   const scout = await agent(prompt, { label: 'scout', phase: 'Ping', schema: COUNT })
   const verify = await agent(prompt, { label: 'verify', phase: 'Ping', schema: COUNT })
   const agree = !!scout && !!verify && scout.files === verify.files
   return { campaign: 'graph-init smoke', outcome: agree ? 'COMPLETE' : 'DISAGREE',
            results: {}, provenance: [
              { node: 'scout', status: scout ? 'OK' : 'DEAD', detail: String(scout && scout.files) },
              { node: 'verify', status: verify ? 'OK' : 'DEAD', detail: String(verify && verify.files) }] }
   ```

   Then record it, which also proves the provenance path:
   `echo '<the return value>' | python3 "$ROOT/scripts/record-run.py" --run-id <runId>`
8. Finish with a short report: files created, compile-check output, smoke
   verdict and runId, and the two commands that start a campaign
   (`/fluxpoint-graph:graph-design <goal>`, then `/fluxpoint-graph:graph-run`).
