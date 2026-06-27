// understand — リポジトリ(または指定領域)を複数の読み手で分担(fan-out)して読み、要点を1枚の「コードベース地図(map)」にまとめる read-only ワークフロー。
// 流れ: Survey(見るべき主要領域を最大8件 洗い出す)→ Read(各領域を並行に読んで要約)→ Synthesize(各要約を1つに統合)。
// 3段とも実際の読み込みはサブエージェントへ委譲し、本体は段取り(phase)と結果の受け渡しだけを行う。
export const meta = {
  name: 'understand',
  description: 'Fan out readers across the codebase (or a given area) and synthesize a distilled map: subsystems, entry points, key files, conventions, risks. Read-only. Pass an optional focus path/area as args.',
  phases: [
    { title: 'Survey', detail: 'identify the major areas to map' },
    { title: 'Read', detail: 'parallel Explore over each area' },
    { title: 'Synthesize', detail: 'merge into one distilled map' },
  ],
}

const FOCUS = (typeof args === 'string' && args.trim()) ? args.trim() : 'the whole repository (cwd)'

const AREAS_SCHEMA = {
  type: 'object',
  properties: {
    areas: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          path: { type: 'string' },
          why: { type: 'string' },
        },
        required: ['name', 'path'],
      },
    },
  },
  required: ['areas'],
}

const SUMMARY_SCHEMA = {
  type: 'object',
  properties: {
    area: { type: 'string' },
    summary: { type: 'string' },
    key_files: { type: 'array', items: { type: 'string' } },
    entry_points: { type: 'array', items: { type: 'string' } },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['area', 'summary'],
}

// phase(名)=実行環境が注入する「進捗の段」の宣言(上の meta.phases と対応)。
// 第1段 Survey: 全体構造をざっと見て、把握すべき主要領域(最大8件)を AREAS_SCHEMA の形で受け取る。
// agent(prompt, opts)=サブエージェントを起動し結果を返す。schema=返り値を AREAS_SCHEMA に沿った JSON にする。agentType:'Explore'=読み取り専用の探索エージェント。
phase('Survey')
const survey = await agent(
  `Map the top-level structure of ${FOCUS}. List the major subsystems/areas worth understanding ` +
  `(at most 8), each with a representative path and why it matters. Read-only; fast and high-level.`,
  { label: 'survey', phase: 'Survey', schema: AREAS_SCHEMA, agentType: 'Explore' }
)
const areas = ((survey && survey.areas) || []).slice(0, 8)

// 第2段 Read: 洗い出した各領域を並行に読む。parallel(fns)=複数の () => Promise を同時に走らせ、結果配列を返す。
// 各領域は SUMMARY_SCHEMA で要約を返し(生コードでなく結論だけ)、本体はそれを集める。
phase('Read')
const summaries = (await parallel(areas.map((a) => () =>
  agent(
    `Read and distill the area "${a.name}" (path: ${a.path}). Return: a 1-paragraph summary, ` +
    `key files (file:line where useful), entry points, and any risks/gotchas. Read only what's needed; ` +
    `return conclusions, NOT raw file contents.`,
    { label: `read:${a.name}`, phase: 'Read', schema: SUMMARY_SCHEMA, agentType: 'Explore' }
  )
))).filter(Boolean)

// 第3段 Synthesize: 各領域の要約を1つの distilled map に統合する(概要 / subsystem の関係 / entry point / 規約 / リスク)。
phase('Synthesize')
const map = await agent(
  `Synthesize a single distilled "codebase map" for ${FOCUS} from these area summaries:\n` +
  JSON.stringify(summaries) +
  `\nProduce: (1) a short overview, (2) the subsystems and how they connect, (3) key entry points, ` +
  `(4) notable conventions, (5) the top risks/unknowns. Concrete (file:line) and concise. ` +
  `人が読む散文(概要・説明・リスク)は日本語で書く。コード・識別子・パス・file:line・スキーマのキー名は原語のまま。`,
  { label: 'synthesize', phase: 'Synthesize', agentType: 'Explore' }
)

return { focus: FOCUS, areas: areas.map((a) => a.name), map }
