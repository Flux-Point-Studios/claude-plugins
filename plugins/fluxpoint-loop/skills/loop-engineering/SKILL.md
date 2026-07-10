---
name: loop-engineering
description: How to run Loop Engineering at Flux Point — choosing between /goal, native /loop, /schedule Routines, and the outer scripts/loop.sh runner, writing verifiable completion conditions, and working with the Stop-hook DoD gate. Use this whenever the user mentions loops, Ralph, /goal, /loop, /schedule, routines, watching or monitoring a build, deploy, or transaction, autonomous or overnight runs, "keep going until green", iteration budgets, or asks Claude to work unattended on a task, even if they never say "loop engineering".
---

# Loop Engineering

The loop is the artifact. Every loop has three parts: a state file
(`LOOP.md`), a deterministic harness (`scripts/harness.sh`), and a driver.
The agent never decides "done"; the harness does.

## Choosing the driver

- **`/goal <condition>`** — the default for interactive work with a
  verifiable end state. The evaluator model only reads the transcript and
  runs nothing itself, so the condition must name its proof command and the
  proof output must be surfaced in conversation: "scripts/harness.sh --full
  exits 0 and the run is shown" works; "the code is production ready" does
  not. Always append a bound: "or stop after N turns and summarize gaps".
- **`/loop`** (native, v2.1.72+) — in-session grinding and polling. With
  an interval (`/loop 5m <prompt>`) it re-fires on a timer; with no
  interval it self-paces, choosing each delay itself and ending the loop
  once the stop condition in the prompt provably holds. Loop-work form:
  `/loop work the next slice per LOOP_PROMPT.md; stop only when
  scripts/harness.sh --full exits 0 and LOOP.md reads STATUS: DONE`.
  It is a scheduler, not a Stop hook, so the DoD gate stays the sole stop
  authority: every iteration must end green before the next fires.
  Session-scoped; Esc cancels a pending iteration; loops expire after
  seven days. Prefer it over the older ralph-loop plugin, whose second
  Stop hook contends with the gate. For pure watching (CI, a preview-net
  tx, the outer loop's logs), ask for a dynamic watch and Claude may run
  the Monitor tool — a background script whose output streams into the
  session — which beats interval polling on tokens and latency. There is
  no /monitor slash command; Monitor is a tool Claude reaches for.
- **`/schedule` (Routines)** — cloud-hosted standing guardrails that run
  with the laptop closed. Triggers: a schedule (cron floor one hour, daily
  run caps by plan), a per-routine HTTPS endpoint, or GitHub events,
  combinable on one routine. Each run clones the repo fresh and pushes
  only to claude/-prefixed branches; create conversationally with
  /schedule in-session (API and GitHub triggers are added at
  claude.ai/code/routines), manage with /schedule list|update|run. Right
  jobs: nightly harness --full drift checks, PR-triggered red-team passes,
  docs drift. Routines run autonomously under the account's identity, so
  prompts must be self-contained and fail loudly — open an issue on red,
  never end silent. No key material, no mainnet paths, no governed repos.
- **`scripts/loop.sh`** — the outer Ralph for multi-hour unattended runs:
  fresh context per iteration, state in LOOP.md and git, promotion gated by
  the harness. Unattended means a sandboxed container with allow-listed
  egress, zero reachable key material, and preview networks only. Choose
  it over Routines when the campaign needs the exact local toolchain
  (aiken, dafny, preview-net egress) or the repo cannot leave self-hosted
  infrastructure.

## Writing conditions that hold up

Four parts, always: one measurable end state, a stated proof command, the
constraints that must not change, and a turn or time bound. Example:
"scripts/harness.sh --full exits 0, no test file deleted or skipped, diff
touches at most 15 files, or stop after 30 turns and report gaps."

## Working with the DoD gate

The Stop hook blocks stops while `scripts/harness.sh --full` or the hygiene
scan is red, up to FPL_MAX_BLOCKS (default 3) consecutive times, then
yields with a checkpoint notice. The correct responses, in order: fix what
is red; never weaken the harness, delete tests, or edit LOOP.md's
Definition of Done to reach green; on the third block, write a checkpoint —
what failed, what was attempted, two alternative paths — and hand control
back to the user.

## Evidence discipline

Every completion claim gets a row in LOOP.md's Evidence table: the command
and its exit status, the tx hash on preview or preprod, the log excerpt. A
claim without evidence is treated as false.
