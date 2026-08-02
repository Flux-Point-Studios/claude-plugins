---
description: Turn a campaign goal into a validated WORK.md IR — nodes with named contracts, verification tiers chosen by stakes, a budget ceiling — compiling clean and audited SOUND before it may run.
argument-hint: [campaign goal]
---

Design the graph. The IR is the deliverable; nothing executes from this
command.

1. Read `WORK.md`; if absent, run `/fluxpoint:graph-init` first.
   Set the `campaign` field from "$ARGUMENTS" if provided, otherwise ask
   before writing.
2. Apply the loop-or-graph rule from the graph-engineering skill. If the
   goal fits one loop slice — one zone, one contract, serial
   evidence — say exactly that, recommend `/goal` or WORK.md, and stop.
   Never build a graph as ceremony.
3. Interrogate the repo until every planned node is grounded: which files
   each prompt names, which commands each verifier runs, which
   `agents/*.md` roles already exist to reference by `agentType`.
4. Write the `graph-ir` block:
   - one node per responsibility, each with a `contract` from
     `contracts/` (add a new `*.schema.json` there if none fits — make it
     one a lazy output cannot satisfy: `required`, `minLength`, enums).
   - `verify` tier chosen by stakes, not habit: `schema-only` for cheap
     mechanical output, `skeptic:1` for low-severity claims, `panel:3` for
     findings that cost someone real time, `panel:5` only for CRITICAL.
     Panels are odd so majority is defined; every panel needs `verifyOver`.
     To gate on the harness, do not reach for a tier — mark the producer
     `mutates: true` and add an `independent` node that runs it.
   - any node that writes to the tree gets `mutates: true`, and a later
     node with `independent: true` and `verifies: "<id>"` must re-derive
     the verdict. The compiler enforces this; do not try to talk it out
     of the rule.
   - `budget.maxNodes` set to the real worst-case ceiling (the compiler
     prices discovery rounds into it), `nodeFloorTokens` where work
     fan-out should stop before exhaustion, `foreach` lists named,
     `onRed` policy per node, `haltWhen` where a numeric result should
     stop the campaign.
   - where the size of the work is unknown rather than given, a `repeat`
     block instead of a fixed fan-out: `untilDryRounds`, `maxRounds`, and
     a `dedupeBy` key that is neither so coarse it collapses distinct
     items nor so fine that a reworded restatement reads as new. Put
     `{{seen}}` in the prompt so each round works new ground. See
     `templates/WORK.discovery.md`.
5. Compile-check until clean:
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" compile-graph.py WORK.md --check`
   Its findings are structural. Fix the IR — never weaken a contract,
   drop a verifier, or raise the budget just to silence it.
6. Run `/fluxpoint:graph-audit` for the semantic pass the compiler
   cannot do: are the prompts scoped to what the node may believe, do the
   tiers match the stakes, does any edge exist only because it looked
   tidy. Apply every REWIRE finding and re-audit until `VERDICT: SOUND`.
7. Set `STATUS: READY` and report: node count, planned agent calls from
   `--check`, the verification tiers in one line, the audit verdict, and
   the run command (`/fluxpoint:graph-run`).
