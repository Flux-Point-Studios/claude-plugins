# fluxpoint

The Flux Point engineering harness for Claude Code. One Definition of Done,
one Evidence discipline, two drivers over the same contract.

**Loop mode** makes one agent's cycle programmable: session bootstrap,
per-edit verification, and a Stop-hook Definition-of-Done gate that blocks
the stop while `scripts/harness.sh --full` or the hygiene scan is red.

**Graph mode** makes the organization of agents programmable: a declarative
`graph-ir` block in `WORK.md` compiles — deterministically, no model in the
loop — into a Workflow script of typed-contract nodes with verification
tiers, so the spec cannot drift from what executes.

The rule both modes share: the agent never decides "done"; the harness
does.

## Layout

- `hooks/` — SessionStart state injection, PostToolUse scoped
  verification, Stop-hook DoD gate.
- `scripts/` — `lib.sh`, `inject-state.sh`, `verify-changed.sh`,
  `dod-gate.sh` (loop); `compile-graph.py`, `record-run.py` (graph);
  `migrate.py` (pre-1.0 migration, plan/apply/finalize).
- `contracts/` — versioned named schemas (`FindingsV1`, `VerdictV1`,
  `HarnessCheckV1`, `DesignV1`, `SliceV1`, `RedTeamV1`).
- `commands/` — `/fluxpoint:init`, `:status`, `:migrate`, `:red-team`,
  `:graph-design`, `:graph-run`, `:graph-audit`.
- `agents/` — `red-team-reviewer` (adversarial diff review),
  `graph-auditor` (semantic review of a campaign IR).
- `skills/` — `loop-engineering` (driver selection, conditions, the gate),
  `graph-engineering` (escalation rule, primitives, tiers, shapes).
- `templates/` — `harness.sh` contract, `WORK.md`, `WORK.feature.md`,
  `WORK.discovery.md`, `WORK_PROMPT.md`, `loop.sh`, settings snippet.
- `tests/` — `compile-test.py` (compiler invariants), `emission-test.py`
  (field-effect probes), `gate-test.sh` (Stop-gate bypass cases),
  `hooks-test.sh` (hooks.json command strings + PostToolUse behavior),
  `migrate-test.sh` (migration against real pre-1.0 fixtures),
  `unify-test.sh` (state model and compatibility). All of it runs from
  `scripts/harness.sh --full` in CI.

## The two contracts

**Repo side:** `scripts/harness.sh` supporting `--changed <file>` (fast,
scoped) and `--full` (everything the DoD requires), exit 0 = green. The
gate stays dormant in repos that lack it.

**Spec side:** `WORK.md` — goal, `MODE`, Definition of Done, Plan (loop
slices), Campaign (the graph IR), Constraints, Merge policy, and one
Evidence table both modes append to.

## Invariants worth naming

- A node that writes to the tree may not certify its own work. Mark it
  `mutates: true`; a later node with `independent: true` must re-derive
  the verdict. The compiler refuses to build a graph that breaks this.
- The DoD gate arms on two independent signals — the PostToolUse marker
  and dirtiness re-derived from `git` — because the marker cannot see
  source written through the Bash tool.
- Compiled `.graph.js` files are build output. Never hand-edit them; edit
  the IR and recompile.
- Budget is enforced twice and neither check is advisory: `maxNodes` is a
  compile-time ceiling priced at the worst case (discovery rounds
  included), and run-time floors stop verification and work fan-out
  separately. Anything declined is recorded `SKIPPED` and surfaces in
  Evidence as incomplete coverage.
- Ending a discovery sweep on its round ceiling is logged
  `discovery INCOMPLETE, not exhausted` — stopping early and finishing are
  different claims.
- Destructive migration is code, not prose. `migrate.py` separates
  plan/apply/finalize so nothing is deleted before the result is checked,
  and `--finalize` refuses when `WORK.md` carries fewer Evidence rows than
  the sources did.
- `budget.maxNodes` is counted at run time as well as compile time. Every
  emitted agent call goes through one `spawn()` helper, because the
  compile-time estimate leans on `expectItems`, which is a guess.
- Every field the IR accepts must demonstrably change what the compiler
  produces. `tests/emission-test.py` probes each one and fails on a field
  that changes nothing, because the recurring defect here was never a wrong
  output — it was a silent one. Unknown fields are compile errors for the
  same reason.

## Migrating from the split plugins

`/fluxpoint:migrate` folds `LOOP.md` and `GRAPH.md` into one `WORK.md`,
moves local state under `.claude/fluxpoint/`, and rewires settings. Until
then the hooks still honor `LOOP.md`, so nothing breaks mid-migration.

Requires `git` and `python3`; `jq` preferred with a python3 fallback built
into the hooks. Graph mode additionally needs a Claude Code version with
the Workflow tool.

Full documentation lives in the repository root README.
