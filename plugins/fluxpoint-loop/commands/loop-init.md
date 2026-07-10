---
description: Scaffold Loop Engineering into the current repo — harness contract, LOOP.md, outer loop runner, settings and .gitignore wiring — then tailor the harness and prove it green.
argument-hint: [one-line loop goal]
---

Onboard this repository onto the fluxpoint-loop pattern. Work through every
step; do not stop at copying files.

1. Locate the plugin templates. Try `${CLAUDE_PLUGIN_ROOT}/templates`
   first; if that expands empty in your shell, find them with
   `find ~/.claude/plugins -type d -path '*fluxpoint-loop/templates' | head -1`.
2. Copy, without overwriting anything that already exists:
   - `templates/harness.sh` → `scripts/harness.sh` (then `chmod +x`)
   - `templates/loop.sh` → `scripts/loop.sh` (then `chmod +x`)
   - `templates/LOOP.md` → `LOOP.md`
   - `templates/LOOP_PROMPT.md` → `LOOP_PROMPT.md`
   If a destination exists, show a diff and propose a merge instead.
3. Ensure `.gitignore` contains the line `.claude/fluxpoint-loop/`.
4. Merge the keys from `templates/settings.snippet.json` into
   `.claude/settings.json`, creating the file if absent and preserving
   every existing key.
5. Tailor `scripts/harness.sh`: inspect the repo's real stack and replace
   the auto-detection floor with the exact commands the Definition of Done
   requires (build, unit and property tests, lint, typecheck, formal
   checks, preview-net exercises). Then run `scripts/harness.sh --full` and
   iterate until it exits 0, or report precisely what is red and why.
6. Fill in the LOOP.md goal line. Use "$ARGUMENTS" if provided; otherwise
   ask for the goal before writing it.
7. Finish with a short report: files created, harness verdict, and the one
   command that starts an outer loop (`scripts/loop.sh`).
