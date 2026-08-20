---
description: Regenerate SUBSTRATE.md from every repo's substrate.json manifest — exit 1 means a manifest problem that CI treats as red.
argument-hint: [none]
---

Regenerate the substrate registry.

1. Run `node "${CLAUDE_PLUGIN_ROOT}/scripts/substrate-graph.mjs" --emit`.
   If `${CLAUDE_PLUGIN_ROOT}` expands empty in your shell, locate the plugin
   with `find ~/.claude/plugins -type d -path '*substrate/scripts' | head -1`.
   Add `--root <dir>` when the repos do not live under the current project
   directory.
2. On exit 1, every `problem:` line on stderr is a manifest defect —
   unparseable JSON, a duplicate primitive id, a `consumes` entry naming a
   primitive that does not exist. `SUBSTRATE.md` is still written so the
   damage is inspectable, but the run is red. Fix the named
   `substrate.json` files and re-run until it exits 0.
3. Report the summary line (repos, primitives, edges, orphans, hubs,
   alarms) and anything that changed in `SUBSTRATE.md` since the last
   generation.
4. `SUBSTRATE.md` is generated output — never hand-edit it. If this emit
   accompanies a shipped primitive, the `substrate.json` edit belongs in
   the same commit as the code it describes; a manifest updated in a later
   commit is exactly the drift the staleness alarm exists to catch.
