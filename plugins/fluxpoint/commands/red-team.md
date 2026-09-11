---
description: Adversarial review of a working change or PR with attack coverage, executable evidence and skeptical reproduction.
argument-hint: [PR URL, base ref, or optional attack focus]
---

Review the change named by the user with `agents/red-team-reviewer.md`.
Claude uses its `red-team-reviewer` subagent; Codex passes that file to a
subagent. Run the same procedure inline when delegation is unavailable and
state that limitation. `$ARGUMENTS` supplies the target or attack focus.

1. Resolve the review target before delegating. For a PR, read its actual
   base/head commits and inspect their merge-base diff, even when the checkout
   is clean. For a user-supplied base ref, resolve it to a commit and compare
   against the current head. Otherwise review `git diff HEAD` plus relevant
   untracked files. Record repository, base/head, changed paths and a content
   fingerprint for uncommitted or untracked inputs. An empty working diff is
   not evidence that a committed PR is safe. Confirm the scratch snapshot
   contains the reviewed changes; never substitute origin/main.
2. Give the reviewer the target, requirement packet/invariants, deployment
   configuration and trust boundaries. Have it enumerate applicable attack
   surfaces and use the shared agent procedure. A focus narrows attack effort;
   excluded surfaces stay visible. Keep experiments in separate scratch copies
   or verified worktrees containing the target state.
3. For a broad change, divide independent surfaces among attackers. Keep a
   separate composition pass that combines operations, identities, timing or
   failure modes. Use available concurrency; serial execution has the same
   coverage obligations. Scheduled agents and token consumption are not
   evidence of completed verification.
4. Give every claimed exploit, including composition claims, to a skeptic
   for fresh reproduction against the same snapshot. The skeptic checks the
   valid control, attack preconditions and impact. An unavailable skeptic
   leaves the claim unresolved; it cannot erase the claim. If no exploit is
   claimed, inspect coverage evidence and sample defended cases for failures
   that occurred before reaching the intended boundary.
5. Synthesize using the agent's verdict rule. Preserve incomplete coverage and
   unresolved claims as blockers even when findings is empty. Recheck the
   reviewed identity before SHIP. After a fix, reproduce the original attack
   against it, rerun affected coverage and composition, and bind a new verdict.

Return scope, coverage, findings, blockers and the final SHIP/BLOCK verdict.
Treat BLOCK as harness-red: fix confirmed issues and complete missing checks
within the user's authorized scope. Review does not authorize deployment,
fund transfers or destructive actions.
