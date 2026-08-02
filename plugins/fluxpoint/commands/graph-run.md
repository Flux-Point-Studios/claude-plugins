---
description: Compile WORK.md's IR to a Workflow script, execute it, record provenance and the Evidence row — targeted node repair and cached resume on partial failure, never a restart from zero.
argument-hint: [path to WORK.md, plus any campaign args as JSON]
---

Run the designed graph. This command is the explicit authorization the
Workflow tool requires.

1. Resolve the plugin root: try `${CLAUDE_PLUGIN_ROOT}`, else
   `find ~/.claude/plugins -type d -name fluxpoint | head -1`.
   The graph file is "$ARGUMENTS" if it names one, else `WORK.md`.
2. Preflight, all deterministic — stop and report exactly what is missing
   rather than improvising around it:
   - `python3 "$ROOT/scripts/compile-graph.py" <graph> --check` exits 0.
     Its findings are the work list; fix the IR, never the compiler.
   - `STATUS:` reads `READY` (or `RUNNING` with a resume point in Notes).
   - Every command the verification map names exists (`scripts/harness.sh`
     if referenced) and every `agentType` resolves — `red-team-reviewer`
     ships with this plugin.
3. Compile:
   `python3 "$ROOT/scripts/compile-graph.py" <graph> -o .claude/workflows/<name>.graph.js`
   The output is generated code. Never hand-edit it; edit the IR and
   recompile, or the spec and the executor start lying to each other.
4. Load whatever state the graph refuses to start without.
   If any node has `actor: human` or `actor: third-party`:
   ```
   python3 "$ROOT/scripts/release.py" --load --campaign "<the IR's campaign line>"
   ```
   into `args._releases`. A node with no release parks: the run reports it
   BLOCKED with its instructions, marks itself INCOMPLETE, and works the
   branches that do not depend on it. That is the expected outcome, not a
   failure — clear it with `/fluxpoint:release <node>`, never by inventing
   a release.
   If the IR has an `imports` block, resolve each frozen decision from
   `.claude/fluxpoint/runs/*.json` — `'latest'` means the newest run whose
   summary carries that decision id, otherwise the named runId — and pass
   the map as `args._decisions`. The graph throws at launch on a missing
   one rather than re-deciding it by accident. If a required decision
   genuinely does not exist yet, the campaign that makes it has to run
   first; do not invent the record to get past the throw.
   If the IR contains any `irreversible` node, load the once-only ledger —
   the compiled graph refuses to start without it:
   ```
   python3 "$ROOT/scripts/ledger.py" --load --campaign "<the IR's campaign line>"
   ```
   Put its output in `args._ledger`. Run `--list` and show the operator what
   has already fired before asking for anything. `confirm` is theirs to
   supply and yours never to invent: it is a comma-separated list of the
   node ids a human authorized this run, and a node not named in it refuses
   to fire. Never pass `confirm` because a previous run used it, because
   the campaign obviously intends it, or because the user said "go" — only
   when they have named the effect.
5. Set `STATUS: RUNNING`. Invoke the Workflow tool with
   `{scriptPath: ".claude/workflows/<name>.graph.js", args: {...}}`.
   Pass args as a real JSON object, not a stringified one. Watch with
   `/workflows`; never poll with sleep.
6. On completion, record the run — evidence is a build artifact, not
   something you remember to write:
   ```
   echo '<the workflow return value as JSON>' | python3 "$ROOT/scripts/record-run.py" \
     --run-id <runId> --graph <graph> [--harness <exit>] [--red-team SHIP|BLOCK]
   ```
   That also appends any irreversible effect to the ledger, so the next
   run replays it instead of repeating it. Before diagnosing an empty or
   surprising result, read the run's `journal.jsonl` — it records what
   each node actually returned, including cached ones.
7. Partial failure is a targeted repair, not a restart: fix the one red
   node (its IR prompt, its contract, or the code it touched), recompile,
   stop the run if still live, then re-invoke with
   `{scriptPath, resumeFromRunId}` — passing `_ledger` again, freshly
   loaded. The unchanged prefix returns from cache; only the repaired
   node onward re-runs, and any irreversible node among them replays
   from the ledger rather than firing twice. Restarting a mostly-green
   graph from zero is a finding.
8. Graph green is not done. The campaign still exits through the
   loop-engineering ship pipeline — `scripts/harness.sh --full`, red-team
   `VERDICT: SHIP`, the Merge policy in WORK.md — and the Stop-hook DoD
   gate keeps final authority. Set `STATUS: DONE` only when the Evidence
   row shows the terminal gate green; otherwise back to `READY` with the
   repair plan in Notes.
9. If the Workflow tool is unavailable, degrade per the graph-engineering
   skill: run the compiled script's nodes as parallel subagent calls with
   the same contracts, then record with `--executor degraded-subagents` so
   the Evidence row says which executor ran the graph.
