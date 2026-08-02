---
description: Report the harness state — gate counters, last harness verdict, WORK.md progress, recorded graph runs — and recommend the single next action.
argument-hint: [none]
---

Report the state of this repo's work, concisely. Read-only; change
nothing.

1. Read `.claude/fluxpoint/`: `last-harness`, any `*.blocks` counters, any
   `*.dirty` markers, and every `runs/*.json` (newest first). If the repo
   still has `.claude/fluxpoint-loop/` or `.claude/fluxpoint-graph/`, note
   that `/fluxpoint:migrate` has not been run.
2. Read the work file — `WORK.md`, or `LOOP.md` in a repo that has not
   migrated: the STATUS and MODE lines, checked vs unchecked counts for
   Definition of Done and Plan, and the last few Evidence rows.
3. In `graph` or `both` mode, add:
   - `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-graph.py" WORK.md --check`
     — valid or invalid, node count, planned agent calls, budget ceiling.
   - the newest recorded run: runId, outcome, nodes OK/dead, findings,
     harness exit, red-team verdict, and any node whose status is `DEAD`
     with its detail.
4. Summarize in a few lines: harness verdict and its age, gate pressure
   (blocks used out of the max, default 3), plan progress, campaign state,
   and the single most useful next action — fix what is red, work the next
   slice, compile-fix the IR, run the campaign, resume from a runId, or
   ship per the Merge policy. If anything is red, quote the exact failing
   lines rather than paraphrasing them.
5. Never infer that a campaign ran from the presence of a compiled script;
   only a recorded run in `runs/` counts.
