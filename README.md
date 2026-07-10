# Flux Point Claude Plugins

Private Claude Code plugin marketplace for Flux Point Studios. One plugin
today: **fluxpoint-loop**, the Loop Engineering harness that makes every
Claude Code session — interactive or autonomous — run against a
deterministic Definition-of-Done gate instead of the agent's self-report.

## What fluxpoint-loop enforces

| Layer | Mechanism | Behavior |
|---|---|---|
| Bootstrap | `SessionStart` hook | Injects branch state, last harness verdict, gate status, and the head of `LOOP.md` at every session start, resume, clear, and post-compaction. |
| Inner loop | `PostToolUse` hook on `Write\|Edit\|MultiEdit` | Runs `scripts/harness.sh --changed <file>`; failures feed straight back to Claude for immediate correction. |
| DoD gate | `Stop` hook | When files were edited this session, runs `scripts/harness.sh --full` plus a hygiene scan of uncommitted/new code (TODO, FIXME, XXX, "for now", `.unwrap()`, skipped or focused tests, empty `catch {}`). Red blocks the stop with the failures as the work list, up to `FPL_MAX_BLOCKS` (default 3) consecutive times, then yields with a checkpoint notice: three failed paths is a user checkpoint, not a retreat. |
| Review | `red-team-reviewer` agent, `/fluxpoint-loop:red-team` | Adversarial pass over the diff: eUTxO, oracle, authority, numeric, off-chain, and infra attack surface. Ends `VERDICT: SHIP` or `VERDICT: BLOCK`. |
| Drivers | `loop-engineering` skill + templates | `/goal` for interactive convergence, native `/loop` (self-paced) for in-session grinding, `/schedule` Routines for cloud standing guardrails, `scripts/loop.sh` for multi-hour outer Ralph runs with fresh context per iteration. |

The repo-side contract is a single file: `scripts/harness.sh` supporting
`--changed <file>` (fast, scoped) and `--full` (everything the DoD
requires), exit 0 = green. The gate stays dormant in repos that lack it.

## Install

Publish this repo (see below), then either path:

**One-time, per developer**

```
/plugin marketplace add flux-point-studios/claude-plugins
/plugin install fluxpoint-loop@fluxpoint
```

**Automatic, per repo** — commit this to each repo's
`.claude/settings.json` (it is `templates/settings.snippet.json`):

```json
{
  "extraKnownMarketplaces": {
    "fluxpoint": {
      "source": { "source": "github", "repo": "flux-point-studios/claude-plugins" }
    }
  },
  "enabledPlugins": {
    "fluxpoint-loop@fluxpoint": true
  }
}
```

Anyone who trusts the repo folder gets prompted to install; every session
in that repo then boots with the loop context injected and the gate armed.
For CI and containers, use `forcedPlugins` in managed settings so the
install needs no interaction.

## Onboard a repo

```
/fluxpoint-loop:loop-init <one-line loop goal>
```

This copies the harness contract, `LOOP.md`, `LOOP_PROMPT.md`, and
`scripts/loop.sh` into the repo, wires `.gitignore` and settings, then
tailors `harness.sh` to the repo's real stack and iterates until `--full`
exits 0.

## Drive a loop

Interactive convergence (evaluator-checked, gate as deterministic backstop):

```
/goal scripts/harness.sh --full exits 0 and the run is shown; no test
deleted or skipped; or stop after 30 turns and summarize gaps
```

In-session grinding (native `/loop`, self-paced — re-fires when the session
goes idle, ends itself once the stop condition provably holds; requires
Claude Code v2.1.72+, self-ending v2.1.202+; Esc cancels):

```
/loop work the next slice per LOOP_PROMPT.md; stop only when
scripts/harness.sh --full exits 0 and LOOP.md reads STATUS: DONE
```

Standing guardrails (cloud Routines — run with the laptop closed; fresh
clone per run, pushes only to `claude/`-prefixed branches; cron floor one
hour, daily run caps by plan; create conversationally in-session, add API
or GitHub triggers at claude.ai/code/routines):

```
/schedule nightly at 02:00, run scripts/harness.sh --full; if it exits
non-zero, open an issue titled "DoD drift" with the last 40 log lines;
if green, end without output
```

A GitHub-triggered routine fits the red-team pass: on every opened PR,
review the diff against the adversarial checklist and post a review ending
`VERDICT: SHIP` or `VERDICT: BLOCK`. Validate the routine environment's
setup script can install the repo's toolchain before trusting a routine to
run the harness. For pure in-session watching (CI, a preview-net tx), ask
Claude to watch it and it may use the Monitor tool — a background script
streaming output into the session — instead of interval polling.

Outer Ralph, fresh context per iteration:

```
scripts/loop.sh                       # attended, acceptEdits
MAX_ITER=50 PERMISSION_ARGS="--dangerously-skip-permissions" scripts/loop.sh
                                      # sandboxed container ONLY
```

Halt an outer loop any time: `touch .claude/fluxpoint-loop/STOP`

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `FPL_DISABLE=1` | off | Kill switch: all three hooks become no-ops. |
| `FPL_MAX_BLOCKS` | 3 | Consecutive Stop blocks before the gate yields with a checkpoint. |
| `MAX_ITER` / `MAX_TURNS` | 25 / 40 | Outer loop budgets. |
| `PERMISSION_ARGS` | `--permission-mode acceptEdits` | Outer loop permission flags. |

Dependencies: `git` required; `jq` preferred with a `python3` fallback
built into the hooks.

## Security posture

- Plugins execute arbitrary code with user privileges. Treat THIS repo as
  production infrastructure: protected default branch, required review,
  signed commits.
- Unattended loops run in a sandboxed container with allow-listed egress.
  No path from that environment to mainnet key material, ever. Harness
  exercises settle on preview/preprod; the resulting tx hash is the
  evidence artifact the DoD demands.
- Routines run on Anthropic-managed cloud under your identity — commits
  and posts appear as you. Guardrail jobs only: no key material, no
  mainnet paths, and repos under external data-governance constraints stay
  on the self-hosted outer loop.
- The hygiene scan covers uncommitted and untracked code only; committed
  history is CI's job. Run the same harness script in CI.

## Publishing this repo

1. Create the GitHub repo (private is fine) — the snippet above assumes
   `flux-point-studios/claude-plugins`; edit both the snippet and this
   README if the org or name differs.
2. Push, then validate locally: `claude plugin validate .`
3. Smoke it end to end in a scratch repo:
   `/plugin marketplace add <org>/claude-plugins`, install, run
   `/fluxpoint-loop:loop-init`, make an edit containing `FIXME`, try to
   stop, and watch the gate block.

## Layout

```
.claude-plugin/marketplace.json
plugins/fluxpoint-loop/
├── .claude-plugin/plugin.json
├── hooks/hooks.json
├── scripts/            lib.sh, inject-loop-state.sh, verify-changed.sh, dod-gate.sh
├── commands/           loop-init.md, loop-status.md, red-team.md
├── agents/             red-team-reviewer.md
├── skills/             loop-engineering/SKILL.md
└── templates/          harness.sh, LOOP.md, LOOP_PROMPT.md, loop.sh, settings.snippet.json
```
