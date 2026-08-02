---
description: Adversarial semantic pass over the GRAPH.md IR — context scoping, tier-vs-stakes, prompt quality, hidden coupling — ending VERDICT SOUND or REWIRE. Structure is the compiler's job.
argument-hint: [path to a GRAPH.md; defaults to the repo root one]
---

Audit the graph before anything runs. The compiler already enforces
structure; this pass judges what it cannot.

1. Resolve targets: "$ARGUMENTS" if provided, otherwise `GRAPH.md` plus
   any sibling `GRAPH.*.md` campaigns.
2. Run the structural check first and require it clean —
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-graph.py" <graph> --check`.
   If it fails, report those findings and stop: there is no point judging
   semantics of a graph that cannot compile.
3. Launch the `graph-auditor` agent on the IR. It reports only findings
   with a concrete failure path, in its fixed table format, ending
   `VERDICT: SOUND` or `VERDICT: REWIRE`. What it judges that the
   compiler cannot:
   - contracts that are structurally valid but semantically vacuous for
     this campaign (a schema no useful answer would fail either).
   - verification tiers that do not match the stakes — `schema-only` on a
     claim that will cost someone a day, `panel:5` on a rename.
   - context packets that paste transcripts or whole files where spans
     and prior contracts suffice, or prompts that leak the desired
     answer to a verifier.
   - nodes whose prompts assume state no `after` edge delivers.
   - budget ceilings set so high they are not really ceilings.
4. Treat REWIRE findings as the work list, highest severity first. Fix
   the IR, recompile, re-audit until SOUND. Never weaken a contract, drop
   a verifier, or raise a ceiling to reach SOUND — that is the graph
   equivalent of deleting tests.
5. Record the final verdict in GRAPH.md Notes. A graph runs only from
   `STATUS: READY`, and READY requires both a clean compile and a SOUND
   audit on the current IR.
