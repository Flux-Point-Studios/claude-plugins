# LOOP: <one-line goal>

STATUS: ACTIVE

## Definition of Done
Every line must be provably true, with the proof named. The Stop gate and
the outer loop only trust `scripts/harness.sh --full`; everything else
needs a row in Evidence.

- [ ] `scripts/harness.sh --full` exits 0
- [ ] <acceptance criterion> — proof: <command | tx hash | screenshot>
- [ ] Exercised in the real runtime (browser / device / preview net) — proof recorded
- [ ] Diff carries no single-caller abstractions and no dead code

## Plan
- [ ] <failing test for the first behavior>
- [ ] <minimum code to flip it green>
- [ ] <next slice>

## Constraints
- <what must not change on the way there>

## Evidence
| When (UTC) | Claim | Proof |
|---|---|---|

## Notes for the next iteration
<current state, blockers, two alternative paths, which one is being attacked>
