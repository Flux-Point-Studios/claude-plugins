export const meta = {
  name: 'feature-graph',
  description: 'Council-designed, loop-implemented, harness-and-red-team-gated feature slice',
  phases: [
    { title: 'Council', detail: 'three independent designs, judged side by side' },
    { title: 'Implement', detail: 'one fluxpoint-loop slice on its own branch' },
    { title: 'Gate', detail: 'real harness exit code, then red-team verdict' },
  ],
}
// Shape: council -> pipeline of loops -> gate (see skills/graph-engineering).
// Requires fluxpoint-loop installed: the Gate phase resolves its
// red-team-reviewer agent via agentType, and Implement assumes
// scripts/harness.sh exists in the repo.
// args: { goal: string, constraints?: string[] }

if (!args || !args.goal) {
  throw new Error('feature-graph requires args.goal — one line, as it would appear in LOOP.md')
}
const constraints = (args.constraints || []).join('; ') || 'none beyond LOOP.md'

const DESIGN = {
  type: 'object',
  required: ['summary', 'plan', 'files', 'risks'],
  properties: {
    summary: { type: 'string', minLength: 20 },
    plan: { type: 'array', minItems: 1, items: { type: 'string', minLength: 10 } },
    files: { type: 'array', items: { type: 'string' } },
    risks: { type: 'array', minItems: 1, items: { type: 'string', minLength: 10 } },
  },
}

const SCORE = {
  type: 'object',
  required: ['score', 'strongest', 'weakest'],
  properties: {
    score: { type: 'integer', minimum: 1, maximum: 10 },
    strongest: { type: 'string', minLength: 10 },
    weakest: { type: 'string', minLength: 10 },
  },
}

const SLICE = {
  type: 'object',
  required: ['branch', 'diffSummary', 'testsAdded', 'harnessCommand', 'harnessExit', 'evidence'],
  properties: {
    branch: { type: 'string', minLength: 1 },
    diffSummary: { type: 'string', minLength: 20 },
    testsAdded: { type: 'array', items: { type: 'string' } },
    harnessCommand: { type: 'string', minLength: 1 },
    harnessExit: { type: 'integer' },
    evidence: { type: 'array', minItems: 1, items: { type: 'string', minLength: 10 } },
  },
}

const RED_TEAM = {
  type: 'object',
  required: ['verdict', 'findings'],
  properties: {
    verdict: { enum: ['SHIP', 'BLOCK'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'finding', 'exploit_path', 'minimal_fix'],
        properties: {
          severity: { enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
          finding: { type: 'string' },
          exploit_path: { type: 'string' },
          minimal_fix: { type: 'string' },
        },
      },
    },
  },
}

phase('Council')
const ANGLES = [
  'smallest-diff-first: the minimum slice that ships observable value',
  'risk-first: neutralize the scariest failure mode before anything else',
  'contract-first: pin the tests and interfaces before any implementation',
]
const designs = (
  await parallel(
    ANGLES.map(a => () =>
      agent(
        `Design an implementation for the goal "${args.goal}" from exactly this angle: ${a}. ` +
          `Constraints: ${constraints}. Read the repo first; ground every plan step in real files. ` +
          `Plan steps must be TDD-shaped: each names the failing test before the code.`,
        { label: `design:${a.split(':')[0]}`, phase: 'Council', schema: DESIGN }
      )
    )
  )
).filter(Boolean)
if (!designs.length) throw new Error('council produced no designs')
if (designs.length < ANGLES.length) log(`council seat died — judging ${designs.length}/${ANGLES.length} designs`)

// Barrier justified: judges need all candidate designs side by side.
const scored = (
  await parallel(
    designs.map((d, i) => () =>
      agent(
        `Judge this design for the goal "${args.goal}" on: TDD-ability, blast radius, ` +
          `fit with the Definition of Done in LOOP.md, and honesty of its risks. ` +
          `Design: ${JSON.stringify(d)}. Score 1-10; name its strongest and weakest point.`,
        { label: `judge:${i + 1}`, phase: 'Council', schema: SCORE, effort: 'high' }
      ).then(s => (s ? { design: d, score: s } : null))
    )
  )
).filter(Boolean)
if (!scored.length) throw new Error('all judges died')
const ranked = scored.slice().sort((a, b) => b.score.score - a.score.score)
const winner = ranked[0]
log(`council winner (${winner.score.score}/10): ${winner.design.summary}`)

phase('Implement')
const slice = await agent(
  `Implement exactly this design as one fluxpoint-loop slice: ${JSON.stringify(winner.design)}. ` +
    `Goal: "${args.goal}". Constraints: ${constraints}. Work TDD strictly per LOOP_PROMPT.md: ` +
    `failing test first, minimum code to green, scripts/harness.sh --changed <file> after each edit. ` +
    `Create and commit on a branch named claude/graph-<short-slug-of-goal> (test and code together). ` +
    `Before returning, run scripts/harness.sh --full and report its REAL exit code in the contract — ` +
    `report red honestly; never weaken the harness or delete tests to reach green. ` +
    `Evidence entries are command + observed result, one per claim.`,
  { label: 'implement', phase: 'Implement', schema: SLICE, isolation: 'worktree' }
)
if (!slice) throw new Error('implement node died — repair and re-run with resumeFromRunId')

phase('Gate')
if (slice.harnessExit !== 0) {
  log(`harness red (exit ${slice.harnessExit}) — halting before red-team; the graph never argues with the harness`)
  return { goal: args.goal, design: winner.design, slice, verdict: 'HARNESS-RED' }
}
const redTeam = await agent(
  `Red-team the diff of branch ${slice.branch} against the default branch ` +
    `(git diff <default>...${slice.branch}). Apply your full adversarial checklist.`,
  { label: 'red-team', phase: 'Gate', schema: RED_TEAM, agentType: 'red-team-reviewer', effort: 'high' }
)

const verdict = redTeam && redTeam.verdict === 'SHIP' ? 'SHIP' : 'BLOCK'
if (!redTeam) log('red-team node died — verdict forced to BLOCK, never SHIP by default')

// Ship per LOOP.md Merge policy happens outside the graph: push the branch,
// open the PR, let CI harness + this verdict gate the merge.
return { goal: args.goal, design: winner.design, slice, redTeam, verdict }
