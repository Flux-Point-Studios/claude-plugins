---
name: substrate-init
description: Onboard a multi-repo workspace onto the substrate registry — scaffold substrate.config.json, seed a starter substrate.json in a named repo, generate SUBSTRATE.md, and install the sweep doctrine. Codex entry point for the substrate `init` command.
user-invocable: false
disable-model-invocation: true
---

This skill is how Codex reaches the `init` command of the substrate
plugin; on Claude Code the same command is `/substrate:init`, and the two
Claude-only keys above keep this entry point out of that runtime's menus so
nothing is listed twice.

1. Read `../../commands/init.md`, relative to this file. Under an installed
   plugin that is `${PLUGIN_ROOT}/commands/init.md`; Codex also sets
   `CLAUDE_PLUGIN_ROOT` to the same directory, which is what the command's
   own shell snippets use.
2. Carry out its steps exactly as written, in order, and report as it says.
   The text of the user's request stands in for `$ARGUMENTS` ([repo dirname to seed first]).
3. Where the command names a subagent from `agents/`, run one with that
   file's contents as its instructions, or perform the pass inline when no
   subagent can be spawned. Where it names the Workflow tool, note that graph
   execution is Claude Code only and follow the command's own fallback.
