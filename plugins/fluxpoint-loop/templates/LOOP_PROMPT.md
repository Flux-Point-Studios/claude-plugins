Read LOOP.md in the repo root. It is the loop's single source of state.

Each iteration of this loop starts with a fresh context. Work exactly one
slice per iteration:

1. Pick the first unchecked item under Plan. If Plan is empty, derive the
   next smallest slice from the Definition of Done and add it first.
2. TDD, strictly: write the failing test, run it, confirm the exact
   expected failure, then write the minimum code to flip it green.
3. Run scripts/harness.sh --full. Red output is the work list; fix it.
4. Append one row to Evidence for anything claimed: the command run and its
   result, a tx hash, a log excerpt. Unproven claims do not count.
5. Check the finished Plan item off. Commit test and code together with a
   message naming the slice.
6. Update Notes for the next iteration: current state, blockers, and if
   blocked, two alternative paths and which one to attack next.

Set `STATUS: DONE` on line 3 of LOOP.md only when every Definition of Done
line is checked with proof recorded. Never edit the Definition of Done or
delete tests to get there. If the same approach has failed three iterations
in a row, write a checkpoint summary in Notes instead of retrying it a
fourth time.
