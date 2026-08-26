---
description: Scaffold the Flux Point harness into the current repo — harness contract, WORK.md, outer loop runner, settings and .gitignore wiring — then tailor the harness and prove it green.
argument-hint: [one-line goal]
---

Onboard this repository onto the fluxpoint harness. Work through every
step; do not stop at copying files.

1. Locate the plugin templates. Try `${CLAUDE_PLUGIN_ROOT}/templates`
   first; if that expands empty in your shell, find them with
   `find ~/.claude/plugins -type d -path '*fluxpoint/templates' | head -1`.
2. Copy, without overwriting anything that already exists:
   - `templates/harness.sh` → `scripts/harness.sh` (then `chmod +x`)
   - `templates/loop.sh` → `scripts/loop.sh` (then `chmod +x`)
   - `templates/WORK.md` → `WORK.md`
   - `templates/WORK_PROMPT.md` → `WORK_PROMPT.md`
   If a destination exists, show a diff and propose a merge instead. If
   the repo has a pre-1.0 `LOOP.md`, stop and run `/fluxpoint:migrate`
   instead of writing a second state file.
3. Ensure `.gitignore` contains `.claude/fluxpoint/`,
   `.claude/worktrees/`, and `__pycache__/`. The worktrees entry
   matters as soon as any campaign has a `mutates` node: those run in
   isolated trees under `.claude/worktrees/`, which must never be
   committed.
4. Merge the keys from `templates/settings.snippet.json` into
   `.claude/settings.json`, creating the file if absent and preserving
   every existing key.
5. Tailor `scripts/harness.sh`: inspect the repo's real stack and replace
   the auto-detection floor with the exact commands the Definition of Done
   requires (build, unit and property tests, lint, typecheck, formal
   checks, preview-net exercises). Then run `scripts/harness.sh --full` and
   iterate until it exits 0, or report precisely what is red and why.

   **Then prove it once in a worktree.** A graph node marked `mutates: true`
   runs worktree-isolated, and green in the primary checkout is not green
   there: `git worktree add` checks out tracked files only, so a gitignored
   build artifact or an installed `node_modules` is absent, and a test that
   pins an absolute path is false in a worktree by construction. Under
   `set -euo pipefail` the first such failure aborts the rest of `--full`,
   including gates a later campaign node was told to read.

   ```sh
   git worktree add --detach ../.fpl-probe
   (cd ../.fpl-probe && bash scripts/harness.sh --full); probe=$?
   git worktree remove --force ../.fpl-probe
   [ "$probe" -eq 0 ] || echo "worktree probe FAILED (rc=$probe)" >&2
   ( exit "$probe" )
   ```

   The status is captured before cleanup and re-raised after it. Written as
   one `&&` chain ending in `git worktree remove`, the whole thing exits with
   the REMOVE's status — so a red harness reports success, and this is a step
   an agent runs and reads the exit code of.

   Fix what that finds now. A harness only ever proven in the primary
   checkout is not proven for the isolation a campaign imposes, and the
   failure surfaces later as a verification node blaming the implementer —
   wrong, and pointed at an innocent node.

   While you are reading the stack, ask the one question the harness cannot
   answer for itself: **which artifacts have to agree with each other?** An
   on-chain predicate and the off-chain builder that constructs
   transactions for it; a migration and the schema it assumes; a wire
   format and both ends of it. Each side has its own tests and passes them;
   the pair is what breaks. Write them into `.fluxpoint-pairs.json` with a
   `parity` command wherever one can be written — a co-change rule only
   proves somebody touched both files, never that they agree. The manifest
   is worthless if it is not written at onboarding, because nobody adds a
   pair after the incident it would have caught.
6. Fill in the WORK.md goal line. Use "$ARGUMENTS" if provided; otherwise
   ask for the goal before writing.
7. Set `MODE`. Default to `loop` and delete the Campaign section — most
   repos start there. Choose `graph` or `both` only when the work already
   meets the escalation rule in the graph-engineering skill (independent
   subtasks, a work-list to fan out over, claims needing adversarial
   verification, cross-zone ownership). If the Campaign section stays,
   verify it compiles:
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" compile-graph.py WORK.md --check`
8. Arm the ratchets that share `.fluxpoint-proof-baseline.json`. In ANY
   repo with tests — proof languages or not — arm the seam ratchet:
   ```
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" seam-guard.py --baseline
   ```
   If the repo also tracks proof-language files (`.ak`, `.dfy`, `.lean`,
   `.v`, `.thy`, `.tla`, or verified Rust), arm both proof ratchets:
   ```
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" proof-guard.py --baseline
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" spec-guard.py --baseline
   ```
   Each preserves the others' sections of the shared file. Commit it — it
   belongs in review, because a rise in the seam counts is someone walling
   a module off behind a mock, a rise in the hatch counts is someone
   making a proof obligation disappear, and a change in the statements is
   someone making a theorem claim less. Read
   `spec-guard.py --scan` before arming: it lists exactly what is being
   treated as an obligation, and names any tracked proof language it does
   not parse yet, so an unarmed corner never reads as a covered one.
   Confirm `harness.sh --full` actually invokes the prover; per-file
   checking on edit is not a Definition-of-Done gate.
   For Aiken repos the scaffolded harness also captures `aiken check`'s
   JSON and records any counterexample it finds to `.fluxpoint-cex.jsonl`.
   That file and `.fluxpoint-cex/` are committed artifacts like the
   baselines — a ratchet only anyone else can see is one that lives in the
   tree, so do not add them to `.gitignore`.
9. Declare this repo's gates so their exit codes stop being self-reported.
   Write `.fluxpoint-gates.json` naming each command whose verdict decides
   something — at minimum the harness — exactly as it is invoked:
   ```json
   {"version": 1, "gates": {"harness": "scripts/harness.sh --full"}}
   ```
   A PostToolUse hook then records the runtime's own exit code for every one
   of those runs to `.claude/fluxpoint/attest.jsonl`, and `record-run.py`
   cross-checks any campaign node that claims a gate exit against it. Match
   the declared string to how the command is actually run: a gate invoked
   with a pipe, a redirect, or a trailing `|| true` reports a different exit
   and is deliberately not attested, so it shows up as UNATTESTED rather
   than being credited to the gate. Commit the manifest; it is part of the
   trust base.
10. Fill in the Merge policy block, asking the user once: may green + SHIP
   PRs merge autonomously in this repo, and does merging trigger a deploy?
   If auto-merge is on, verify `gh` is authenticated and record the
   required CI check names the merge will wait on.
11. Finish with a short report: files created, harness verdict, MODE, merge
   policy, and the one command that starts an outer loop
   (`scripts/loop.sh`) or a campaign (`/fluxpoint:graph-design`, which flips
   the work file to READY, then `/fluxpoint:graph-run`).
