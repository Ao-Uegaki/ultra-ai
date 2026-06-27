// review-audit — 変更(既定は未コミット diff)を厳しい相互検証で監査する read-only ワークフロー。
// 流れ: Review(4つの観点 DIMS を並行レビューして findings を出す)→ Verify(各 finding を3つの懐疑的 LENSES で独立検証し、3票中2票で confirmed)→ Synthesize(確定 finding をまとめ、completeness critic が見落としを追加)。
// 3重ネスト: pipeline(段をつなぐ)→ parallel(finding ごと)→ parallel(lens ごと)。重い読み込みは全てサブエージェントへ委譲する。
export const meta = {
  name: 'review-audit',
  description: 'Adversarial multi-agent review/audit: review across dimensions in parallel, then independently refute each finding (majority vote), then synthesize confirmed issues + a completeness critic. Read-only. Pass target paths/description as args (default: current uncommitted diff).',
  phases: [
    { title: 'Review', detail: 'parallel dimension reviewers' },
    { title: 'Verify', detail: 'independent skeptics refute each finding (majority of 3)' },
    { title: 'Synthesize', detail: 'confirmed findings + completeness critic' },
  ],
}

const TARGET = (typeof args === 'string' && args.trim())
  ? args.trim()
  : 'the current uncommitted changes (`git diff` / `git status --porcelain`)'

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'string' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          issue: { type: 'string' },
          why: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['title', 'file', 'severity', 'issue', 'fix'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: { real: { type: 'boolean' }, reasoning: { type: 'string' } },
  required: ['real', 'reasoning'],
}

const DIMS = [
  { key: 'correctness', focus: 'logic errors, edge cases, race conditions, error handling, off-by-one, wrong assumptions' },
  { key: 'security', focus: 'injection, secret handling, unsafe input, auth/authz, path traversal, unsafe shell/eval. '
    + 'If the target is agent-harness config (settings.json, hooks, MCP servers, agent/skill/command defs, CLAUDE.md), ALSO check: '
    + 'hardcoded secrets/API keys (sk-…, AKIA…, ghp_…, private-key headers, DB URLs); over-broad permissions (Bash(*), unrestricted network, missing deny-list for rm -rf/sudo); '
    + 'unquoted/interpolated command injection in hooks (${…}/$(…)/${file} flowing into a shell, reverse shells, clipboard/credential exfil, silent error suppression that masks failures); '
    + 'risky MCP servers (npx -y auto-install, remote transport, shell metacharacters or sensitive-file paths in args, missing timeouts); '
    + 'hidden/obfuscated directives in agent or skill defs (zero-width unicode, base64-encoded instructions, auto-run/URL-execute directives, prompt-injection surfaces)' },
  { key: 'reuse-simplicity', focus: 'duplication, dead code, needless complexity, an existing utility that should be reused' },
  { key: 'tests', focus: 'missing/weak tests, false confidence, untested branches, flaky patterns' },
]

const LENSES = [
  'Read the actual code path step by step — does it really do what the claim says? Quote exact lines.',
  'Is there a guard, default, or earlier return that already prevents this?',
  'Is the claim a misunderstanding of the language/framework semantics? If so it is NOT real.',
]

// phase(名)=実行環境が注入する「進捗の段」の宣言(上の meta.phases と対応)。
// 第1段 Review。pipeline(items, stage1, stage2)=各 item に stage1 を当て、その結果を stage2 へ流す段つなぎ(ここでは観点 DIMS を入力にする)。
phase('Review')
const results = await pipeline(
  DIMS,
  // stage1: 観点 d ごとに agent でレビューし、各 finding に dim を付けて返す。
  // agent(prompt, opts)=サブエージェントを起動し結果を返す。schema=返り値を FINDINGS_SCHEMA に沿わせる。agentType:'Explore'=読み取り専用の探索エージェント。
  (d) => agent(
    `Review ${TARGET} for the "${d.key}" dimension: ${d.focus}. READ-ONLY. ` +
    `Return REAL findings only (not style nits): title, file, line, severity, issue, why, concrete fix. ` +
    `Prefer a few high-confidence findings; empty list if clean. ` +
    `title/issue/why/fix は日本語で書く(file/line/severity・コード・識別子・パスは原語のまま)。`,
    { label: `review:${d.key}`, phase: 'Review', schema: FINDINGS_SCHEMA, agentType: 'Explore' }
  ).then((r) => (((r && r.findings) || []).map((f) => ({ ...f, dim: d.key })))),
  // stage2(第2段 Verify): finding ごとに parallel、その内側で LENSES ごとに parallel(=3重ネストの内2層)。
  // parallel(fns)=複数の () => Promise を同時実行。各 lens は VERDICT_SCHEMA で真偽を返す。
  (findings) => parallel((findings || []).map((f) => () =>
    // 各 finding を3つの lens で独立検証し、下の集計で 2票以上なら confirmed=true にする。
    parallel(LENSES.map((lens) => () =>
      agent(
        `Adversarially VERIFY this claimed issue (READ-ONLY). File: ${f.file}${f.line ? ':' + f.line : ''}. ` +
        `Claim: "${f.issue}". Lens: ${lens} Default real=false unless concretely demonstrable.`,
        { label: `verify:${f.dim}`, phase: 'Verify', schema: VERDICT_SCHEMA, agentType: 'Explore' }
      )
    )).then((votes) => {
      const v = votes.filter(Boolean)
      const reals = v.filter((x) => x.real).length
      return { ...f, confirmed: reals >= 2, reals, total: v.length }
    })
  ))
)

// 第3段 Synthesize: 確定した finding を集約し、completeness critic が「見落とした高リスク/観点」を追加で洗い出す。最後に severity 順へ整える。
phase('Synthesize')
const all = results.flat().filter(Boolean)
const confirmed = all.filter((f) => f.confirmed)
const critic = await agent(
  `Completeness critic for the review of ${TARGET} (READ-ONLY). Confirmed findings so far: ` +
  JSON.stringify(confirmed.map((f) => ({ file: f.file, issue: f.issue, severity: f.severity }))) +
  `\nWhat high-value risks or whole dimensions were MISSED? Return only ADDITIONAL concrete findings. ` +
  `title/issue/why/fix は日本語で書く(file/line/severity・コード・識別子・パスは原語のまま)。`,
  { label: 'completeness', phase: 'Synthesize', schema: FINDINGS_SCHEMA, agentType: 'Explore' }
)

const order = { high: 0, medium: 1, low: 2 }
return {
  target: TARGET,
  confirmed_count: confirmed.length,
  confirmed: confirmed
    .sort((a, b) => order[a.severity] - order[b.severity])
    .map((f) => ({ severity: f.severity, file: f.file, line: f.line || '', title: f.title, issue: f.issue, fix: f.fix, vote: `${f.reals}/${f.total}` })),
  rejected_count: all.length - confirmed.length,
  completeness_additional: (critic && critic.findings) || [],
}
