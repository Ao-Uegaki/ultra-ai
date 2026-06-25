export const meta = {
  name: 'design-panel',
  description: 'Generate N independent design approaches from distinct angles, judge them on a rubric, and synthesize a recommended design grafting the best ideas. Pass the design question/requirements as args.',
  phases: [
    { title: 'Generate', detail: 'N independent approaches from distinct angles' },
    { title: 'Judge', detail: 'score each approach on a rubric' },
    { title: 'Synthesize', detail: 'recommend the best, graft runner-up ideas' },
  ],
}

const QUESTION = (typeof args === 'string' && args.trim())
  ? args.trim()
  : 'the design problem described in the current conversation/context'

const ANGLES = [
  'simplest thing that works (MVP, least moving parts)',
  'robustness & failure modes first',
  'long-term maintainability & extensibility',
  'performance & scale first',
]

const APPROACH_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    steps: { type: 'array', items: { type: 'string' } },
    tradeoffs: { type: 'string' },
    risks: { type: 'string' },
  },
  required: ['summary', 'tradeoffs'],
}

const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    correctness: { type: 'number' },
    simplicity: { type: 'number' },
    robustness: { type: 'number' },
    total: { type: 'number' },
    rationale: { type: 'string' },
  },
  required: ['total', 'rationale'],
}

phase('Generate')
const approaches = (await parallel(ANGLES.map((angle, i) => () =>
  agent(
    `Design an approach for: ${QUESTION}\nAngle/bias: ${angle}\n` +
    `Give a concrete approach: summary, key steps, tradeoffs, risks. Read the codebase as needed (read-only). ` +
    `Commit to this angle — don't hedge toward the others.`,
    { label: `approach:${i}`, phase: 'Generate', schema: APPROACH_SCHEMA, agentType: 'Explore' }
  ).then((a) => (a ? { ...a, angle } : null))
))).filter(Boolean)

phase('Judge')
const judged = (await parallel(approaches.map((a) => () =>
  agent(
    `Score this design approach for "${QUESTION}" on correctness/simplicity/robustness (0-10 each, total 0-30) ` +
    `with a one-paragraph rationale. Be a tough, independent judge.\nApproach:\n${JSON.stringify(a)}`,
    { label: `judge:${a.angle}`, phase: 'Judge', schema: SCORE_SCHEMA, agentType: 'Explore' }
  ).then((s) => (s ? { approach: a, score: s } : null))
))).filter(Boolean)

const ranked = judged.sort((x, y) => (y.score.total || 0) - (x.score.total || 0))

phase('Synthesize')
const recommendation = await agent(
  `You are the lead designer. Scored design approaches for "${QUESTION}" (best first):\n` +
  JSON.stringify(ranked) +
  `\nRecommend ONE design: start from the top approach, graft the best ideas from the runners-up, ` +
  `and state the key decisions, the rejected alternatives + why, and the main risks. Concrete and concise. ` +
  `人が読む散文(推奨・理由・リスク)は日本語で書く。コード・識別子・パス・スキーマのキー名は原語のまま。`,
  { label: 'recommend', phase: 'Synthesize', agentType: 'Explore' }
)

return {
  question: QUESTION,
  ranking: ranked.map((r) => ({ angle: r.approach.angle, total: r.score.total })),
  recommendation,
}
