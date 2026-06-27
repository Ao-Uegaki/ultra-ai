// design-panel — 1つの設計課題に対し、視点(angle)の違う N 案を独立に作り、採点基準(rubric)で評価し、上位案を土台に良い所を接ぎ木して1案にまとめるワークフロー。
// 流れ: Generate(4つの ANGLES から各1案を並行生成)→ Judge(各案を correctness/simplicity/robustness で並行採点)→ Synthesize(最高得点を起点に次点の良案を取り込み1案を推奨)。
// 案の生成・採点はサブエージェントへ委譲し、本体は angle の割り振り・採点の集約・順位付けだけを行う。
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

// phase(名)=実行環境が注入する「進捗の段」の宣言(上の meta.phases と対応)。
// 第1段 Generate: 4つの ANGLES それぞれに1案を並行生成する。parallel(fns)=複数の () => Promise を同時実行。
// agent(prompt, opts)=サブエージェントを起動し結果を返す。schema=返り値を APPROACH_SCHEMA に沿わせる。agentType:'Explore'=読み取り専用の探索エージェント。各案には後の集約用に angle を付け直す。
phase('Generate')
const approaches = (await parallel(ANGLES.map((angle, i) => () =>
  agent(
    `Design an approach for: ${QUESTION}\nAngle/bias: ${angle}\n` +
    `Give a concrete approach: summary, key steps, tradeoffs, risks. Read the codebase as needed (read-only). ` +
    `Commit to this angle — don't hedge toward the others.`,
    { label: `approach:${i}`, phase: 'Generate', schema: APPROACH_SCHEMA, agentType: 'Explore' }
  ).then((a) => (a ? { ...a, angle } : null))
))).filter(Boolean)

// 第2段 Judge: 各案を独立した審査役が rubric(採点基準)で並行採点する(各 0-10、total 0-30)。SCORE_SCHEMA で構造化して受け取る。
phase('Judge')
const judged = (await parallel(approaches.map((a) => () =>
  agent(
    `Score this design approach for "${QUESTION}" on correctness/simplicity/robustness (0-10 each, total 0-30) ` +
    `with a one-paragraph rationale. Be a tough, independent judge.\nApproach:\n${JSON.stringify(a)}`,
    { label: `judge:${a.angle}`, phase: 'Judge', schema: SCORE_SCHEMA, agentType: 'Explore' }
  ).then((s) => (s ? { approach: a, score: s } : null))
))).filter(Boolean)

// total の高い順に並べ替え(ranked の先頭が最有力案)。
const ranked = judged.sort((x, y) => (y.score.total || 0) - (x.score.total || 0))

// 第3段 Synthesize: 最高得点の案を起点に、次点(runner-up)の良案を接ぎ木して1案を推奨する(主要な決定 / 却下案と理由 / 主なリスク)。
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
