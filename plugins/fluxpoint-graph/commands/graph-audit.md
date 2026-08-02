---
description: Adversarial pass over GRAPH.md and its compiled .graph.js scripts — contracts, edges, verification, failure policy, runtime hazards — ending VERDICT SOUND or REWIRE.
argument-hint: [path to a GRAPH.md or .graph.js; defaults to both]
---

Audit the graph artifacts before anything runs.

1. Resolve targets: "$ARGUMENTS" if provided, otherwise `GRAPH.md` in the
   repo root plus every `.claude/workflows/*.graph.js`.
2. Launch the `graph-auditor` agent on those targets. It applies the full
   checklist — contract, verification, context, edge, failure, runtime
   layers — and reports only findings with a concrete failure path, in its
   fixed table format, ending `VERDICT: SOUND` or `VERDICT: REWIRE`.
3. Treat REWIRE findings as the work list, highest severity first. Fix
   the spec or the script, then re-audit until SOUND. Never weaken a
   contract, drop a verifier, or delete a failure policy to reach SOUND —
   that is the graph equivalent of deleting tests.
4. Record the final verdict in GRAPH.md Notes. A graph runs only from
   `STATUS: READY`, and READY requires a SOUND audit on the current spec.
