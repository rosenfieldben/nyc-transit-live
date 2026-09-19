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

/* RULE 0: EVERY AGENT RUNS IN ITS OWN WORKTREE, AND THIS ONE IS PAID FOR IN BLOOD.
   A review that verifies a finding the way this repository does, by applying the
   mutation and seeing whether a spec dies, WRITES TO THE TREE. Run over the shared
   checkout, those writes race whatever the caller is doing. Measured, on stage MR2 of
   the map redesign: two verifier mutations landed in frontend/systems/subway.js and
   frontend/map.js while the caller was running that round's gates over the same files,
   one of them reached a commit, and that commit's gate numbers described a tree that was
   never the one committed. The agent had already reverted its own edit by the time
   anyone noticed, which is precisely what makes this class of bug invisible: the
   evidence deletes itself.

   So isolation is not a tuning knob here. It is ~200-500ms and a little disk per agent,
   and the alternative is a review that can corrupt the thing it is reviewing.

   AND THE CALLER OWES ONE CHECK BACK: before any commit, confirm the working tree is
   what the gates ran on (`git status --short` and a diff against the last commit you
   controlled). Isolation makes the race impossible for agents this script spawns; the
   check is what catches anything else that writes while you are not looking.

   THIS IS THE RULE FOR EVERY REVIEW AND PROBE WORKFLOW IN THIS REPOSITORY, not only
   this one. .claude/workflows/README.md says so in one place; this file is where it is
   enforced. */

/* RULE 0b: AND THE WORKTREE MUST BE AT THE COMMIT UNDER REVIEW, ASSERTED BY EVERY AGENT.
   This half was missing and it cost two false refutations on stage MR3 of the map redesign.

   WHAT HAPPENED. A worktree is created at the repository's default branch unless something
   puts it somewhere else, and nothing here did. Two verifiers on MR3 therefore read
   origin/main, where the branch under review does not exist, and returned evidence of the
   form "the diff is empty" and "bindRailStationLabel does not exist" as REFUTATIONS. Both
   findings were real: one was a canvas casing stroked with the literal string "var(--paper)",
   a silent no-op that draws in whatever colour the 2D context held last, and the other was a
   bearing computed from the wrong pair of points. Both were re-verified by hand and both were
   confirmed. The only reason they were not lost is that the caller distrusted the shape of the
   evidence; a refutation that reads "the code you describe is not there" is indistinguishable
   from a correct refutation of a hallucinated finding, and that is the whole danger.

   SO A REFUTATION IS ONLY ADMISSIBLE FROM THE RIGHT COMMIT. Every agent is told the sha, told
   to detach its worktree at it if it is not already there, told to prove the diff is non-empty,
   and REQUIRED BY ITS SCHEMA to echo back the sha it actually read. The script compares that
   echo against the expected sha, and a mismatch makes the finding UNVERIFIED rather than
   refuted: an agent that read the wrong tree has produced no evidence about this one, in either
   direction. A "REFUTED from the wrong commit" is the single worst output this tool can
   produce, because it deletes a real defect and looks like diligence doing it. */

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

// Coerce a JSON-STRING args into an object. The Workflow contract says to pass args
// as a real JSON value, but a caller who passes the JSON-encoded string instead gets
// a silent downgrade rather than an error: every field reads undefined, so the run
// falls back to MIN_DIMENSIONS and drops the caller's focus text without a word. That
// happened on the first real run of this script (2 dimensions instead of 5, focus
// never delivered), and a review that quietly reviews less than asked is the worst
// failure mode available to a review tool.
function resolveArgs(raw) {
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch {
      return {}
    }
  }
  return raw || {}
}

const input = resolveArgs(args)
const range = input.range || 'main...HEAD'
const changedLines = input.changedLines || 300
const focus = input.focus || ''
/* THE COMMIT UNDER REVIEW (RULE 0b). The caller passes the full sha of the tip it wants
   reviewed: `git rev-parse HEAD` on the branch, taken AFTER the last commit the caller made and
   BEFORE the review is launched. Without it every agent's worktree lands wherever the harness
   puts it, which on this repository is the default branch, and a review of the default branch
   refutes every finding about the branch under review.

   NOT OPTIONAL, AND THE FALLBACK SAYS SO RATHER THAN GUESSING. A caller who omits it gets a
   review that still runs (refusing outright would make the tool unusable from a context that
   cannot shell out) but every agent is told to derive the sha from the range and to report what
   it found, and the script logs the omission at the top of the run so a thin review is traceable
   to it instead of being read as a clean one. */
const commit = typeof input.commit === 'string' ? input.commit.trim() : ''
const branch = typeof input.branch === 'string' ? input.branch.trim() : ''

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
// Say out loud whether the focus arrived, so a dropped or mistyped args field is
// visible in the run rather than inferred later from thin findings.
log(focus ? 'focus: ' + focus : 'focus: (none supplied)')
if (commit) {
  log('commit under review: ' + commit + (branch ? ' (' + branch + ')' : ''))
} else {
  log(
    'WARNING: no commit sha supplied (RULE 0b). Every worktree defaults to the repository default ' +
      'branch, which is how stage MR3 got two false refutations. Agents will report the sha they ' +
      'actually read and mismatches will be surfaced, but pass {commit: "<full sha>"} next time.'
  )
}

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
    // RULE 0b: the sha this agent's worktree was actually at when it read the code, from
    // `git rev-parse HEAD`. Required, so it cannot be omitted by an agent that did not check.
    head_commit: { type: 'string' },
    diff_files: { type: 'integer' },
  },
  required: ['findings', 'head_commit', 'diff_files'],
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
    head_commit: { type: 'string' },
    diff_files: { type: 'integer' },
  },
  required: ['survivors', 'dropped', 'head_commit', 'diff_files'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          // REQUIRED, and the schema enforces it: this is how a verdict is matched to
          // the finding it judges. Matching on `summary` broke whenever a verifier
          // reworded the claim, which is every time.
          finding_id: { type: 'string' },
          summary: { type: 'string' },
          real: { type: 'boolean' },
          verdict: { type: 'string', enum: ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'] },
          evidence: { type: 'string' },
        },
        required: ['finding_id', 'summary', 'real', 'verdict', 'evidence'],
      },
    },
    /* RULE 0b, AND ON THIS SCHEMA IT IS LOAD-BEARING RATHER THAN INFORMATIONAL. A verdict set
       whose head_commit is not the commit under review is discarded whole: its findings become
       UNVERIFIED, never refuted. An agent that read the default branch has produced no evidence
       about this branch in either direction, and the two false refutations on stage MR3 both
       arrived as confident REFUTED with evidence that the code "does not exist". */
    head_commit: { type: 'string' },
    diff_files: { type: 'integer' },
  },
  required: ['verdicts', 'head_commit', 'diff_files'],
}

// Every agent that touches the tree gets this. Two review agents in the R4 run
// left mutations behind (a reverted FAIL and an "if True:" guard bypass); the
// stop hook caught them, but only because it happened to fire.
const RESTORE =
  ' YOU MAY EDIT FILES ONLY TO TEST A HYPOTHESIS, AND YOU MUST RESTORE THE TREE ' +
  'EXACTLY BEFORE YOU RETURN. Use git stash or git checkout to verify you left ' +
  'nothing behind: git status must be clean of your edits. A mutation left in the ' +
  'tree is a worse outcome than an unverified finding.'

/* RULE 0b's PREFLIGHT, prepended to EVERY agent's prompt. First three commands, before reading
   any code: put the worktree at the commit under review, prove it, and prove the diff is there.

   DETACHED, NOT A BRANCH CHECKOUT. Several agents share one repository's object store and each
   has its own worktree; `git checkout <branch>` from two of them at once fails, and one that
   succeeded would move the branch ref the caller is committing to. `--detach` at a sha is the
   one form that is safe to run from N worktrees simultaneously and cannot move any ref.

   AND THE DIFF IS COUNTED, because "I checked out the right sha" and "I am looking at the change"
   are different claims. A sha that exists but whose merge base with main is itself gives an empty
   diff, and an empty diff is exactly the state that produced MR3's two false refutations. */
const PREFLIGHT =
  'PREFLIGHT, BEFORE YOU READ ANY CODE. You are in your own git worktree, and it does NOT ' +
  'start at the commit under review: it starts at the repository default branch, which is how ' +
  'two verifiers on a previous review "refuted" two real defects with the evidence that the ' +
  'code did not exist. So: ' +
  (commit
    ? '(1) run `git rev-parse HEAD`. If it is not ' +
      commit +
      ', run `git fetch origin ' +
      (branch || '--all') +
      ' || true` and then `git checkout --detach ' +
      commit +
      '`, and run `git rev-parse HEAD` again. It MUST print ' +
      commit +
      ' exactly. Use --detach and never `git checkout <branch>`: other agents share this ' +
      'repository and a branch checkout either fails or moves the ref the caller is committing to. '
    : '(1) run `git rev-parse HEAD` and record it. No sha was supplied for this run, so you ' +
      'cannot correct your position; report what you actually read and say so in your findings. ') +
  '(2) run `git diff --stat ' +
  range +
  '` and count the files it lists. If it lists ZERO files you are NOT looking at the change: say ' +
  'so, report no findings and no verdicts, and stop. An empty diff is never evidence that a ' +
  'finding is wrong. (3) Report the sha from step 1 in head_commit and the file count from step 2 ' +
  'in diff_files, both verbatim and both required by your schema. A judgement reached from the ' +
  'wrong tree is discarded, so guessing these fields loses your whole contribution.\n\n'

const scope =
  PREFLIGHT +
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
      { label: 'find:' + d.key, phase: 'Find', schema: FINDINGS_SCHEMA, isolation: 'worktree' }
    )
  )
)

/* RULE 0b, ENFORCED ON THE FIND PHASE. A finder that read the wrong tree found nothing about
   this change, and its "nothing" would otherwise read as a clean dimension. So its whole
   contribution is dropped and the drop is logged with the sha it actually read. */
function atRightCommit(result, label) {
  if (!result) return false
  const seen = typeof result.head_commit === 'string' ? result.head_commit.trim() : ''
  if (commit && seen !== commit) {
    log('RULE 0b: ' + label + ' read ' + (seen || '(no sha)') + ', not ' + commit + '; its output is discarded')
    return false
  }
  if (!result.diff_files) {
    log('RULE 0b: ' + label + ' reported an EMPTY diff for ' + range + '; its output is discarded')
    return false
  }
  return true
}

const usableFinds = found.filter((r, i) => atRightCommit(r, 'find:' + (dimensions[i] ? dimensions[i].key : i)))
const lostDimensions = dimensions.length - usableFinds.length
if (lostDimensions > 0) {
  log(
    'WARNING: ' +
      lostDimensions +
      ' of ' +
      dimensions.length +
      ' finder dimensions produced nothing usable (wrong commit or empty diff). This review is ' +
      'NOT full coverage.'
  )
}
const candidates = usableFinds.flatMap((r) => r.findings || [])
log(candidates.length + ' candidates from ' + usableFinds.length + ' usable dimensions')

if (!candidates.length) {
  // AND IT SAYS WHICH KIND OF NOTHING THIS IS. "No candidates" from agents that read the right
  // tree is a clean review; "no candidates" from agents that read the default branch is a broken
  // run, and the two used to be the same sentence.
  return {
    confirmed: [],
    dropped: [],
    note: lostDimensions
      ? 'BROKEN RUN: no usable dimensions (' + lostDimensions + ' discarded for wrong commit or empty diff)'
      : 'no candidates surfaced',
    lost_dimensions: lostDimensions,
  }
}

// RULE 1: TRIAGE BEFORE FAN-OUT. One agent reads every candidate together, which
// is the only vantage point from which duplicates and mutually-contradicting
// findings are visible at all, and decides which ones are worth an individual
// verifier. This single agent replaces the N-per-finding fan-out that made the
// original design cost what it did.
phase('Triage')

const triage = await agent(
  PREFLIGHT +
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
  { label: 'triage', phase: 'Triage', schema: TRIAGE_SCHEMA, isolation: 'worktree' }
)

/* AND ON TRIAGE, WHERE A WRONG-TREE READ IS WORSE THAN ANYWHERE ELSE: step (3) of the triage
   prompt has it re-read the code and CORRECT each survivor's file and line, so an agent on the
   default branch would "correct" every real finding into a drop. A triage that cannot be trusted
   is discarded and the candidates go forward unmerged, which costs verifier agents and loses no
   finding; the reverse trade is not available. */
const triageUsable = atRightCommit(triage, 'triage')
if (!triageUsable) {
  log('RULE 0b: triage is discarded; every candidate goes to verification unmerged and unranked')
}
const survivors = (triageUsable && triage.survivors) || candidates.map((c) => ({ ...c, needs_own_verifier: false }))
const triageDropped = (triageUsable && triage.dropped) || []
log(survivors.length + ' survivors, ' + triageDropped.length + ' dropped in triage')
for (const d of triageDropped) log('  dropped: ' + d.summary + ' (' + d.reason + ')')

// RULE 4: BUDGET AWARE. If the remaining target cannot cover individual
// verification, demote everything to batches and SAY SO. A silent cap reads as
// full coverage.
// Stamp a STABLE ID on every survivor and make the verifier echo it back. Verdicts
// used to be matched to findings by their `summary` string, which broke the moment a
// verifier reworded the claim it was handed, and they reword constantly (they are
// summarising what they found, not copying the input). On the first real run EVERY
// verdict missed its finding, so all of them fell through to the dead-verifier
// fallback and came back PLAUSIBLE, including one the verifier had explicitly
// REFUTED. A review tool that reports a refuted finding as surviving is worse than
// one that reports nothing.
const withIds = survivors.map((s, i) => ({ ...s, finding_id: 'F' + (i + 1) }))

let solo = withIds.filter((s) => s.needs_own_verifier)
let batched = withIds.filter((s) => !s.needs_own_verifier)

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
  PREFLIGHT +
  'You are an adversarial verifier for the diff at ' +
  range +
  '. Your job is to REFUTE, not to agree. For each finding: read the real code, ' +
  'construct the concrete input or state the finding claims, and decide whether it ' +
  'actually produces the claimed failure. Default to REFUTED when you cannot ' +
  'demonstrate the failure; a plausible-sounding finding that does not reproduce is ' +
  'worse than no finding, because it costs the author a real investigation. Quote the ' +
  'line that settles it in evidence.\n\nECHO EACH FINDING\'S finding_id BACK VERBATIM ' +
  'in its verdict. That field is how your verdict is matched to the finding it judges; ' +
  'a verdict with a missing or altered finding_id is discarded as unverified, and you ' +
  'may reword the summary freely as long as the id is exact.'

const verdictSets = await parallel(
  [
    // Individual verifiers, session model: the findings where being wrong is expensive.
    ...solo.map((f) => () =>
      agent(
        verifyPreamble +
          '\n\nThe single finding to verify:\n' +
          JSON.stringify(f, null, 2) +
          RESTORE,
        { label: 'verify:' + (f.file || 'unknown'), phase: 'Verify', schema: VERDICT_SCHEMA, isolation: 'worktree' }
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
          isolation: 'worktree',
          model: CHEAP_MODEL,
          effort: 'low',
        }
      )
    ),
  ]
)

/* RULE 0b, ON THE VERDICTS, WHICH IS THE ONE THAT COST US. A verdict set from the wrong tree is
   thrown away WHOLE rather than per verdict: the agent's reading of every finding in its batch
   came from the same wrong files. The findings it judged fall through to UNVERIFIED below, which
   is the honest bucket for them, and never to refuted. On stage MR3 two such sets came back as
   confident REFUTED on two real critical defects, with "the diff is empty" and "this function
   does not exist" as their evidence. */
const usableVerdictSets = verdictSets.filter((v, i) => atRightCommit(v, 'verify#' + (i + 1)))
const discardedSets = verdictSets.filter(Boolean).length - usableVerdictSets.length
if (discardedSets > 0) {
  log(
    'RULE 0b: ' +
      discardedSets +
      ' verdict set(s) discarded for reading the wrong commit or an empty diff; their findings are UNVERIFIED, not refuted'
  )
}
const verdicts = usableVerdictSets.flatMap((v) => v.verdicts || [])
const byId = new Map(verdicts.filter((v) => v.finding_id).map((v) => [v.finding_id, v]))
const unmatched = verdicts.length - byId.size
if (unmatched > 0) {
  // Loud, because this is the failure that silently mislabels a whole review.
  log('WARNING: ' + unmatched + ' verdict(s) carried no usable finding_id and were discarded')
}

const confirmed = []
const refuted = []
const unverified = []
for (const s of withIds) {
  const v = byId.get(s.finding_id)
  if (!v) {
    // No verdict came back. Kept rather than dropped (an agent that died is not
    // evidence the finding was wrong), but in its OWN bucket, never mixed into
    // confirmed. Unverified findings used to be pushed into confirmed carrying a
    // PLAUSIBLE label, so when the id matching broke, a whole review of unjudged
    // findings read exactly like a review of confirmed ones and the breakage was
    // invisible in the result. A separate bucket makes that impossible to miss.
    unverified.push({
      ...s,
      verdict: 'UNVERIFIED',
      evidence: discardedSets
        ? 'verifier returned no usable verdict (a verdict set was discarded for reading the wrong commit)'
        : 'verifier returned no verdict',
    })
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
    unverified.length +
    ' UNVERIFIED, ' +
    triageDropped.length +
    ' dropped in triage'
)
if (unverified.length && !confirmed.length && !refuted.length) {
  // Nothing was judged at all: treat it as a broken run, not a clean review.
  log('WARNING: the verify phase produced NO usable verdicts; findings below are unjudged')
}

return {
  confirmed,
  refuted,
  unverified,
  dropped_in_triage: triageDropped,
  cost_shape: {
    dimensions: dimensions.length,
    changed_lines: changedLines,
    focus_supplied: Boolean(focus),
    candidates: candidates.length,
    solo_verifiers: solo.length,
    batched_verifiers: batches.length,
    verdicts_returned: verdicts.length,
    verdicts_unmatched: unmatched,
    // RULE 0b's counters, in the result rather than only in the log, so a caller reading the
    // return value alone can tell a clean review from a partly blind one.
    commit_under_review: commit || null,
    lost_dimensions: lostDimensions,
    triage_discarded: !triageUsable,
    verdict_sets_discarded: discardedSets,
    agents_total: dimensions.length + 1 + solo.length + batches.length,
  },
}
