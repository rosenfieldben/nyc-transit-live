export const meta = {
  name: 'adversarial-review',
  description: 'Cost-bounded adversarial review: size to the diff, triage before fan-out, batch the mechanical verification',
  whenToUse:
    'Reviewing a spec-sized change (one PR, one concern) where findings must be verified before they are reported. Pass args: {range, changedLines, focus}. Scout the diff inline first to get changedLines, then invoke this.',
  phases: [
    { title: 'Find' },
    { title: 'Triage' },
    { title: 'Verify' },
  ],
}

// WHY THIS SCRIPT EXISTS
//
// The R4 review was designed as 6 finder dimensions x 3 verifier lenses per
// finding: 123 agents, ~8.3M tokens, more than every prior review on this repo
// combined. It was killed and re-run at 29 agents / ~1.6M tokens and still caught
// both critical findings. Verification, not finding, is the cost center: 117 of
// those 123 agents were verifiers.
//
// The four rules below are that lesson made structural. They are not advisory
// defaults to be talked out of by a diff that feels important; every past review
// felt important.

// RULE 3: SIZE TO THE DIFF. One finder dimension per 150 changed lines, floor 2,
// cap 5. A 130-line monitor change does not need six independent lenses; a
// 2000-line refactor does not get twelve.
const LINES_PER_DIMENSION = 150
const MIN_DIMENSIONS = 2
const MAX_DIMENSIONS = 5

// RULE 4: BUDGET AWARE. Reserve enough headroom that the verify phase cannot be
// the thing that blows the ceiling. An agent on this repo costs ~65k output
// tokens; these are agent-count reserves expressed in tokens.
const AGENT_TOKEN_ESTIMATE = 65_000
const VERIFY_RESERVE = 4 * AGENT_TOKEN_ESTIMATE

// RULE 1 + 2: triage before fan-out, and batch the mechanical half onto a cheap
// model. Only findings triage rates critical/high get their own verifier; the
// rest are verified in batches of this size.
const BATCH_SIZE = 5
const CHEAP_MODEL = 'haiku'

const range = (args && args.range) || 'main...HEAD'
const changedLines = (args && args.changedLines) || 300
const focus = (args && args.focus) || ''

const DIMENSION_CATALOG = [
  {
    key: 'silent-failure',
    brief:
      'Paths where the change can fail, degrade, or check nothing while still reporting success. Swallowed exceptions, fallbacks that mask the condition they handle, a guard that returns the healthy value on unknown input, a status that is computed from frozen data.',
  },
  {
    key: 'removed-behavior',
    brief:
      'For every line the diff DELETES or replaces, name the invariant it enforced and find where the new code re-establishes it. A removed guard, a narrowed validation, a dropped error path, a deleted test that covered a real case.',
  },
  {
    key: 'boundary-and-type',
    brief:
      'Threshold arithmetic and value shapes: off-by-one at the band edges, null versus zero versus absent, bool as a subclass of int, negative values passing a greater-than test, non-numeric input reaching arithmetic, clock skew between two differently-stamped timestamps.',
  },
  {
    key: 'test-vacuity',
    brief:
      'Tests that pass for the wrong reason. Pick the strongest assertion in each new test and ask what mutation of the production code it would NOT catch. Fixture values that make the asserted branch unreachable, a status asserted where the branch is what matters, setup that short-circuits before the logic under test.',
  },
  {
    key: 'operator-reality',
    brief:
      'The change as an operator will actually meet it: config that does not reach the process, an error message that names nothing actionable, a documented escape hatch that is inert, whitespace or case in a pasted value, a default that differs between local and CI.',
  },
]

const dimensionCount = Math.max(
  MIN_DIMENSIONS,
  Math.min(MAX_DIMENSIONS, Math.ceil(changedLines / LINES_PER_DIMENSION))
)
const dimensions = DIMENSION_CATALOG.slice(0, dimensionCount)

log(
  changedLines +
    ' changed lines -> ' +
    dimensionCount +
    ' finder dimensions (' +
    dimensions.map((d) => d.key).join(', ') +
    ')'
)

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          summary: { type: 'string' },
          failure_scenario: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
        },
        required: ['file', 'summary', 'failure_scenario', 'severity'],
      },
    },
  },
  required: ['findings'],
}

const TRIAGE_SCHEMA = {
  type: 'object',
  properties: {
    survivors: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          summary: { type: 'string' },
          failure_scenario: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          needs_own_verifier: { type: 'boolean' },
          triage_note: { type: 'string' },
        },
        required: ['file', 'summary', 'failure_scenario', 'severity', 'needs_own_verifier'],
      },
    },
    dropped: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          summary: { type: 'string' },
          reason: { type: 'string' },
        },
        required: ['summary', 'reason'],
      },
    },
  },
  required: ['survivors', 'dropped'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          summary: { type: 'string' },
          real: { type: 'boolean' },
          verdict: { type: 'string', enum: ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'] },
          evidence: { type: 'string' },
        },
        required: ['summary', 'real', 'verdict', 'evidence'],
      },
    },
  },
  required: ['verdicts'],
}

// Every agent that touches the tree gets this. Two review agents in the R4 run
// left mutations behind (a reverted FAIL and an "if True:" guard bypass); the
// stop hook caught them, but only because it happened to fire.
const RESTORE =
  ' YOU MAY EDIT FILES ONLY TO TEST A HYPOTHESIS, AND YOU MUST RESTORE THE TREE ' +
  'EXACTLY BEFORE YOU RETURN. Use git stash or git checkout to verify you left ' +
  'nothing behind: git status must be clean of your edits. A mutation left in the ' +
  'tree is a worse outcome than an unverified finding.'

const scope =
  'Review the diff for ' +
  range +
  '. Read the enclosing function for every hunk: defects in unchanged lines of a touched function are in scope. ' +
  (focus ? 'The author flags this focus: ' + focus + '. ' : '')

phase('Find')

const found = await parallel(
  dimensions.map((d) => () =>
    agent(
      scope +
        'Your single dimension is ' +
        d.key +
        '. ' +
        d.brief +
        ' Report at most 6 findings, only ones you can name a concrete failing input or state for. ' +
        'Do not report style preferences. Rate severity honestly: critical means green would mean the ' +
        'wrong thing or a rider-visible break, low means cleanup.' +
        RESTORE,
      { label: 'find:' + d.key, phase: 'Find', schema: FINDINGS_SCHEMA }
    )
  )
)

const candidates = found.filter(Boolean).flatMap((r) => r.findings || [])
log(candidates.length + ' candidates from ' + dimensions.length + ' dimensions')

if (!candidates.length) {
  return { confirmed: [], dropped: [], note: 'no candidates surfaced' }
}

// RULE 1: TRIAGE BEFORE FAN-OUT. One agent reads every candidate together, which
// is the only vantage point from which duplicates and mutually-contradicting
// findings are visible at all, and decides which ones are worth an individual
// verifier. This single agent replaces the N-per-finding fan-out that made the
// original design cost what it did.
phase('Triage')

const triage = await agent(
  'You are triaging candidate review findings for ' +
    range +
    ' before expensive verification. Here they are as JSON:\n' +
    JSON.stringify(candidates, null, 2) +
    '\n\nDo four things. (1) Merge near-duplicates: same defect, same location, same reason becomes one entry, ' +
    'keeping the clearest statement. (2) Drop anything that is a style preference, a restatement of the code, ' +
    'or contradicted by another candidate you can see is better-argued; put every drop in the dropped list with ' +
    'a reason, because a silent drop reads as full coverage. (3) Read the actual code for each survivor and ' +
    'correct its file and line if wrong. (4) Set needs_own_verifier true ONLY for findings where being wrong is ' +
    'expensive: critical or high severity, or a claim that turns on subtle control flow you could not settle by ' +
    'reading. Everything else will be verified in batches, which is adequate for mechanical claims.' +
    RESTORE,
  { label: 'triage', phase: 'Triage', schema: TRIAGE_SCHEMA }
)

const survivors = (triage && triage.survivors) || []
const triageDropped = (triage && triage.dropped) || []
log(survivors.length + ' survivors, ' + triageDropped.length + ' dropped in triage')
for (const d of triageDropped) log('  dropped: ' + d.summary + ' (' + d.reason + ')')

// RULE 4: BUDGET AWARE. If the remaining target cannot cover individual
// verification, demote everything to batches and SAY SO. A silent cap reads as
// full coverage.
let solo = survivors.filter((s) => s.needs_own_verifier)
let batched = survivors.filter((s) => !s.needs_own_verifier)

if (budget.total && budget.remaining() < VERIFY_RESERVE + solo.length * AGENT_TOKEN_ESTIMATE) {
  log(
    'BUDGET: ' +
      Math.round(budget.remaining() / 1000) +
      'k remaining cannot cover ' +
      solo.length +
      ' individual verifiers; demoting all of them to batched verification'
  )
  batched = batched.concat(solo)
  solo = []
}

phase('Verify')

const batches = []
for (let i = 0; i < batched.length; i += BATCH_SIZE) {
  batches.push(batched.slice(i, i + BATCH_SIZE))
}

const verifyPreamble =
  'You are an adversarial verifier for the diff at ' +
  range +
  '. Your job is to REFUTE, not to agree. For each finding: read the real code, ' +
  'construct the concrete input or state the finding claims, and decide whether it ' +
  'actually produces the claimed failure. Default to REFUTED when you cannot ' +
  'demonstrate the failure; a plausible-sounding finding that does not reproduce is ' +
  'worse than no finding, because it costs the author a real investigation. Quote the ' +
  'line that settles it in evidence.'

const verdictSets = await parallel(
  [
    // Individual verifiers, session model: the findings where being wrong is expensive.
    ...solo.map((f) => () =>
      agent(
        verifyPreamble +
          '\n\nThe single finding to verify:\n' +
          JSON.stringify(f, null, 2) +
          RESTORE,
        { label: 'verify:' + (f.file || 'unknown'), phase: 'Verify', schema: VERDICT_SCHEMA }
      )
    ),
    // RULE 2: CHEAP MODEL FOR THE MECHANICAL HALF. Batched, on haiku. These are
    // claims that turn on reading a value or a signature, not on judgement.
    ...batches.map((b, i) => () =>
      agent(
        verifyPreamble +
          '\n\nVerify each of these ' +
          b.length +
          ' findings independently and return one verdict per finding:\n' +
          JSON.stringify(b, null, 2) +
          RESTORE,
        {
          label: 'verify:batch' + (i + 1),
          phase: 'Verify',
          schema: VERDICT_SCHEMA,
          model: CHEAP_MODEL,
          effort: 'low',
        }
      )
    ),
  ]
)

const verdicts = verdictSets.filter(Boolean).flatMap((v) => v.verdicts || [])
const bySummary = new Map(verdicts.map((v) => [v.summary, v]))

const confirmed = []
const refuted = []
for (const s of survivors) {
  const v = bySummary.get(s.summary)
  if (!v) {
    // No verdict came back. Report it as unverified rather than dropping it: an
    // agent that died is not evidence the finding was wrong.
    confirmed.push({ ...s, verdict: 'PLAUSIBLE', evidence: 'verifier returned no verdict' })
  } else if (v.real) {
    confirmed.push({ ...s, verdict: v.verdict, evidence: v.evidence })
  } else {
    refuted.push({ ...s, evidence: v.evidence })
  }
}

const order = { critical: 0, high: 1, medium: 2, low: 3 }
confirmed.sort((a, b) => (order[a.severity] || 9) - (order[b.severity] || 9))

log(
  confirmed.length +
    ' confirmed, ' +
    refuted.length +
    ' refuted, ' +
    triageDropped.length +
    ' dropped in triage'
)

return {
  confirmed,
  refuted,
  dropped_in_triage: triageDropped,
  cost_shape: {
    dimensions: dimensions.length,
    candidates: candidates.length,
    solo_verifiers: solo.length,
    batched_verifiers: batches.length,
    agents_total: dimensions.length + 1 + solo.length + batches.length,
  },
}
