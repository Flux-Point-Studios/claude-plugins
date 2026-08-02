export const meta = {
  name: 'review-graph',
  description: 'Fan-out/verify review: one finder per dimension, refuter majority on every finding',
  phases: [
    { title: 'Find', detail: 'one finder per dimension' },
    { title: 'Verify', detail: 'three refuters per finding, majority kills' },
  ],
}
// Shape: fan-out/verify (see skills/graph-engineering). pipeline() wires
// Find → Verify per dimension with no barrier: findings from a fast
// dimension verify while slow dimensions are still searching.
// args: { target?: string } — file, directory, or diff spec to review.
// No Date.now()/Math.random() here — they break resume; stamp the
// Evidence row from the shell after the run.

// The Workflow tool may hand `args` over as an object, a JSON string, or a
// bare string. Normalize all three; never silently review the wrong thing.
const A =
  args && typeof args === 'object'
    ? args
    : typeof args === 'string' && args.trim()
    ? (() => {
        try {
          return JSON.parse(args)
        } catch {
          return { target: args }
        }
      })()
    : {}

const target =
  A.target ||
  'the uncommitted diff: git diff HEAD, plus untracked files from git status'
log(`review target: ${target}`)

// Contracts. required + minLength keep lazy output from satisfying them.
const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['file', 'line', 'claim', 'failure_path', 'severity'],
        properties: {
          file: { type: 'string', minLength: 1 },
          line: { type: 'integer' },
          claim: { type: 'string', minLength: 20 },
          failure_path: { type: 'string', minLength: 20 },
          severity: { enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['refuted', 'reason'],
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string', minLength: 10 },
  },
}

const DIMENSIONS = [
  { key: 'correctness', brief: 'wrong output, broken invariants, unhandled edges, off-by-one and boundary math' },
  { key: 'security', brief: 'the fluxpoint-loop red-team surface: eUTxO, oracle, authority, numeric, off-chain, infra' },
  { key: 'tests', brief: 'behavior changed with no failing-test-first evidence, skipped or weakened assertions' },
]

const results = await pipeline(
  DIMENSIONS,
  d =>
    agent(
      `Review ${target} for ${d.brief}. Read the code yourself with Read/Grep/Bash. ` +
        `Report only findings with a concrete failure path (inputs/state -> wrong outcome); no style commentary. ` +
        `An empty findings list is a valid, welcome answer.`,
      { label: `find:${d.key}`, phase: 'Find', schema: FINDINGS }
    ),
  (found, d) => {
    if (!found) {
      log(`finder died: ${d.key} — dimension dropped, resume after repair to cover it`)
      return []
    }
    if (!found.findings.length) return []
    // Refuters get the contract fields, never the finder's transcript, and
    // must re-read the code themselves. Odd panel; majority (2 of 3) kills.
    return parallel(
      found.findings.map(f => () =>
        parallel(
          [1, 2, 3].map(i => () =>
            agent(
              `Attempt to REFUTE this ${d.key} finding. File: ${f.file} line ${f.line}. ` +
                `Claim: ${f.claim} Failure path: ${f.failure_path} ` +
                `Re-read the code yourself and hunt for the reason the claim is wrong: a guard upstream, ` +
                `a type that forbids the state, a test that pins the behavior. ` +
                `Default to refuted=true when uncertain.`,
              { label: `refute${i}:${f.file}`, phase: 'Verify', schema: VERDICT }
            )
          )
        ).then(votes => {
          const cast = votes.filter(Boolean)
          if (cast.length < 3) log(`refuter died on ${f.file}:${f.line} — ${cast.length}/3 votes cast`)
          return { ...f, dimension: d.key, kills: cast.filter(v => v.refuted).length }
        })
      )
    ).then(judged => judged.filter(Boolean).filter(f => f.kills < 2))
  }
)

const RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 }
const confirmed = results
  .filter(Boolean)
  .flat()
  .sort((a, b) => RANK[a.severity] - RANK[b.severity])
log(`${confirmed.length} findings survived the refuter majority`)

// Graph green is not done: confirmed findings become the work list for a
// fluxpoint-loop slice, and the DoD gate still owns "done".
return { target, confirmed }
