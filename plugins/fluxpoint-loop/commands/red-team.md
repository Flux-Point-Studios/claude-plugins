---
description: Adversarial review of the current diff with the red-team-reviewer agent — validator, oracle, keeper, key-handling and infra attack surface.
argument-hint: [optional focus, e.g. "the withdraw path"]
---

Use the red-team-reviewer subagent to attack the uncommitted work in this
repo: `git diff HEAD` plus untracked files. "$ARGUMENTS" narrows the focus
if provided. Have it report only exploitable findings with a concrete
attack path, then decide SHIP or BLOCK. If it reports BLOCK, treat the
findings as harness-red: fix them before stopping.
