---
description: Migrate a pre-1.0 repo onto the unified harness — fold LOOP.md and any GRAPH.md into one WORK.md, move local state, and rewire settings. Idempotent; safe to re-run.
argument-hint: [none]
---

Migrate this repository from the split fluxpoint-loop / fluxpoint-graph
plugins to the unified `fluxpoint` plugin. Nothing here is destructive
until the final step, and the harness contract does not change.

1. Survey first and report what you find before touching anything:
   `LOOP.md`, `GRAPH.md` (or `GRAPH.*.md`), `LOOP_PROMPT.md`,
   `.claude/fluxpoint-loop/`, `.claude/fluxpoint-graph/`,
   `.claude/settings.json`, `.gitignore`. If none exist, say the repo is
   already unified (or was never onboarded) and stop.
2. Build `WORK.md` from what exists, preserving content verbatim wherever
   possible — this is a merge, not a rewrite:
   - Goal line and `STATUS:` from `LOOP.md`.
   - Add `MODE:` — `loop` if only LOOP.md existed, `graph` if only a
     GRAPH.md did, `both` if both did.
   - `## Definition of Done`, `## Plan`, `## Constraints`, and
     `## Merge policy` from `LOOP.md` unchanged.
   - `## Campaign` holding the ```json graph-ir block from `GRAPH.md`.
     A pre-0.2 GRAPH.md has prose tables instead of an IR block: convert
     it per the graph-engineering skill, then compile-check it. Do not
     invent nodes the old spec did not describe.
   - `## Evidence` as the unified table
     (`| When (UTC) | Source | Outcome | Claim | Proof |`). Carry every
     existing row across: loop rows become `Source: loop`, graph rows keep
     their runId as Source. Never drop a row — evidence is the record.
   - `## Notes for the next iteration` from whichever file had it; if
     both, keep both under one heading.
3. Rename `LOOP_PROMPT.md` → `WORK_PROMPT.md` (`git mv`) and update its
   internal references from `LOOP.md` to `WORK.md`.
4. Move local state, preserving history:
   - `.claude/fluxpoint-loop/last-harness` → `.claude/fluxpoint/`
   - `.claude/fluxpoint-graph/runs/` → `.claude/fluxpoint/runs/`
   Leave stale `*.dirty` and `*.blocks` markers behind; they are
   per-session and expire on their own.
5. Rewire configuration:
   - `.gitignore`: replace `.claude/fluxpoint-loop/` and
     `.claude/fluxpoint-graph/` with `.claude/fluxpoint/`; keep
     `__pycache__/`.
   - `.claude/settings.json`: in `enabledPlugins`, replace
     `fluxpoint-loop@fluxpoint` and `fluxpoint-graph@fluxpoint` with
     `fluxpoint@fluxpoint`. Preserve every other key.
   - Any `.claude/workflows/*.graph.js`: these are build output. Delete
     them and recompile from `WORK.md` when next needed.
6. Verify before removing anything:
   - `scripts/harness.sh --full` still exits 0 (the contract is unchanged;
     if this breaks, the migration did something it should not have).
   - If `WORK.md` has a Campaign section:
     `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-graph.py" WORK.md --check`
   - Confirm the Evidence table has at least as many rows as the files it
     replaced.
7. Only after both checks pass, `git rm` the superseded `LOOP.md` and
   `GRAPH.md`. Commit the migration as one commit naming what merged.
8. Report: the new MODE, rows carried into Evidence, harness verdict,
   compile check, and anything that needed a judgment call (especially a
   pre-0.2 GRAPH.md converted to IR) so the user can review it.
