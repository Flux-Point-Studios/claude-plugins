---
description: Report the current Loop Engineering state — gate counters, last harness verdict, LOOP.md progress — and recommend the single next action.
---

Report loop state for this repo, concisely:

1. Read `.claude/fluxpoint-loop/`: `last-harness`, any `*.blocks` counters,
   any `*.dirty` markers, and the newest file under `logs/` if present.
2. Read `LOOP.md` if present: the STATUS line, checked vs unchecked counts
   for Definition of Done and Plan, and the last Evidence row.
3. Summarize in a few lines: harness verdict and its age, gate pressure
   (blocks used out of the max, default 3), loop progress, and the single
   most useful next action. If anything is red, quote the exact failing
   lines rather than paraphrasing them.
