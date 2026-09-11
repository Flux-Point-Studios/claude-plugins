# Cardano attack surfaces

Read for Aiken/Plutus validators, transaction builders and their deployment
parameters. Map each applicable class to the actual redeemers and invariants;
record why an omitted class cannot reach this protocol. A transaction builder
is not a security boundary when an attacker can construct the transaction.

| Class | Attack |
|---|---|
| Double satisfaction | Reuse one payout for multiple script inputs, owners or fees; batch unlike actions. |
| Value and fees | Underpay, exploit floor/ceiling direction, zero/negative quantities, unit errors or partial-fill accounting. |
| Mint integrity | Wrong asset names/policies, extra mint, missing burn, net-zero mint tricks or escaped state tokens. |
| Authority | Missing signer, owner/operator confusion, key/script credential confusion or permissionless stake-script authorization. |
| Index binding | Reorder inputs/outputs; target a different entry or out-of-range index; validate what the selected entry represents. |
| Continuation | Change destination, immutable datum fields, value, asset identity or state-token cardinality. |
| Economic terms | Change price or terms while preserving owner/pair/value, then exploit another permitted action. |
| Receipt integrity | Forge, duplicate or replay a receipt; mismatch claimed and enforced amounts or attributes. |
| Reference scripts | Substitute script/policy references, exploit input/reference disjointness or parameter/hash mismatch. |
| Composed transactions | Mix redeemers, collide indices, share payouts or shift losses between positions. |
| Direction and reserves | Reverse a trade, exploit asymmetric pricing/rounding, drain one reserve or bypass a minimum. |
| Datum decoding | Wrong types/version, inline/hash confusion, malformed continuation and fail-open handling. |
| Time | Open bounds, inclusive/exclusive edges, expired actions, partial fills and replay across windows. |
| Oracle and premium | Wrong/stale feed/network, replay, signature/rotation gaps, wrong vault or rounded-away payment. |
| Liveness and cost | Dust/token bundles, datum growth, contention, minimum ADA, execution budgets and recovery from partial failure. |

For each PoC, preserve the real spent UTxO and datum constraints; enumerate
the transaction components the attacker can actually choose. A synthetic
datum or impossible ledger state cannot establish a production exploit.
Use valid controls and identify the actual rejecting condition. Check script
hashes, parameters, network and supported deployment credentials separately
from permissionless protocol attacks. Test restrictions used to exclude a
configuration. Multi-transaction and cross-redeemer composition remain a
separate pass even after all individual classes have results.

Method and taxonomy adapted from Flux Point Studios'
[aiken-validator-redteam](https://github.com/Flux-Point-Studios/cardano-agent-skills/blob/c4dc5de6/skills/aiken-validator-redteam/SKILL.md)
and [audit framework](https://github.com/Flux-Point-Studios/cardano-agent-skills/blob/c4dc5de6/skills/aiken-dex-security-audit/references/audit-framework.md).
The source's [MIT notice](cardano-agent-skills-LICENSE.txt) is retained.
This adaptation does not use that workflow's verdict reducer. Missing agents,
unchecked cases and unresolved reproduction block; build failures and generic
crashes do not establish that an attack was defended.
