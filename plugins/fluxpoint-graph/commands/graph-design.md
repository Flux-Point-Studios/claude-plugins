---
description: Turn a campaign goal into a complete GRAPH.md work graph — nodes with typed contracts, deterministic edges, a verification map, a failure policy — audited to VERDICT SOUND before it may run.
argument-hint: [campaign goal]
---

Design the graph for this campaign. The spec is the deliverable; nothing
runs from this command.

1. Read `GRAPH.md` in the repo root; if absent, run
   `/fluxpoint-graph:graph-init` first. Set the goal line from
   "$ARGUMENTS" if provided; otherwise ask before writing.
2. Apply the loop-or-graph rule from the graph-engineering skill. If the
   goal fits one fluxpoint-loop slice — one zone, one contract, serial
   evidence — say exactly that, recommend `/goal` or LOOP.md, and stop.
   Never build a graph as ceremony.
3. Interrogate the repo until every planned node is grounded: which files
   each context packet names, which commands each verifier runs, which
   `agents/*.md` roles the org graph already provides.
4. Write the work graph into GRAPH.md:
   - one row per node — context packet, contract summary, verifier,
     on-red policy. Refuse to write a node without a contract; refuse an
     edge without a named verifier. Schema-only edges must state why that
     is acceptable there.
   - edges as explicit lines, ten or fewer; every barrier states its
     cross-item reason; every fan-out names its work-list and budget
     floor; every discovery loop names its dry rule.
   - the verification map: which edges run `scripts/harness.sh`
     (`--changed` vs `--full`), which use refuter majorities, and the
     terminal gate (harness --full + red-team verdict) untouched.
5. Choose the executor shape: if the work graph matches a shipped
   template (`review.graph.js`, `feature.graph.js`), note that in GRAPH.md
   Notes with the args to pass; otherwise sketch the phases the compiled
   script will declare.
6. Run the graph-auditor over the result (the `graph-auditor` agent, or
   `/fluxpoint-graph:graph-audit`). Apply every REWIRE finding and
   re-audit until `VERDICT: SOUND`. Never weaken a contract or drop a
   verifier to reach SOUND.
7. Set `STATUS: READY` and report: node and edge count, the verification
   map in one line each, the audit verdict, and the run command
   (`/fluxpoint-graph:graph-run`).
