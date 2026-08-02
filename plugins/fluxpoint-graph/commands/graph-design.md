---
description: Turn a campaign goal into a validated GRAPH.md IR — nodes with named contracts, verification tiers chosen by stakes, a budget ceiling — compiling clean and audited SOUND before it may run.
argument-hint: [campaign goal]
---

Design the graph. The IR is the deliverable; nothing executes from this
command.

1. Read `GRAPH.md`; if absent, run `/fluxpoint-graph:graph-init` first.
   Set the `campaign` field from "$ARGUMENTS" if provided, otherwise ask
   before writing.
2. Apply the loop-or-graph rule from the graph-engineering skill. If the
   goal fits one fluxpoint-loop slice — one zone, one contract, serial
   evidence — say exactly that, recommend `/goal` or LOOP.md, and stop.
   Never build a graph as ceremony.
3. Interrogate the repo until every planned node is grounded: which files
   each prompt names, which commands each verifier runs, which
   `agents/*.md` roles already exist to reference by `agentType`.
4. Write the `graph-ir` block:
   - one node per responsibility, each with a `contract` from
     `contracts/` (add a new `*.schema.json` there if none fits — make it
     one a lazy output cannot satisfy: `required`, `minLength`, enums).
   - `verify` tier chosen by stakes, not habit: `schema-only` for cheap
     mechanical output, `harness` for anything claiming green,
     `skeptic:1` for low-severity claims, `panel:3` for findings that
     cost someone real time, `panel:5` only for CRITICAL. Panels are odd
     so majority is defined; every panel needs `verifyOver`.
   - any node that writes to the tree gets `mutates: true`, and a later
     node with `independent: true` and `verifies: "<id>"` must re-derive
     the verdict. The compiler enforces this; do not try to talk it out
     of the rule.
   - `budget.maxNodes` set to the real worst-case ceiling, `foreach`
     lists named, `onRed` policy per node, `haltWhen` where a numeric
     result should stop the campaign.
5. Compile-check until clean:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compile-graph.py" GRAPH.md --check`
   Its findings are structural. Fix the IR — never weaken a contract,
   drop a verifier, or raise the budget just to silence it.
6. Run `/fluxpoint-graph:graph-audit` for the semantic pass the compiler
   cannot do: are the prompts scoped to what the node may believe, do the
   tiers match the stakes, does any edge exist only because it looked
   tidy. Apply every REWIRE finding and re-audit until `VERDICT: SOUND`.
7. Set `STATUS: READY` and report: node count, planned agent calls from
   `--check`, the verification tiers in one line, the audit verdict, and
   the run command (`/fluxpoint-graph:graph-run`).
