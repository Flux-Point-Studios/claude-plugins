# fluxpoint-loop

Loop Engineering harness for Claude Code. Components:

- `hooks/` — SessionStart loop-state injection, PostToolUse scoped
  verification, Stop-hook Definition-of-Done gate.
- `scripts/` — the hook implementations (`lib.sh`, `inject-loop-state.sh`,
  `verify-changed.sh`, `dod-gate.sh`).
- `commands/` — `/fluxpoint-loop:loop-init`, `:loop-status`, `:red-team`.
- `agents/red-team-reviewer.md` — adversarial reviewer for DeFi/infra diffs.
- `skills/loop-engineering/` — driver selection and condition-writing guide.
- `templates/` — `harness.sh` contract, `LOOP.md`, `LOOP_PROMPT.md`,
  `loop.sh`, settings snippet; copied into repos by `loop-init`.

Full documentation lives in the repository root README.
