# ultra-ai ロードマップ — 「より高精度・より少トークン」へ

> 出典: ultracode workflow(4レンズ=token-economy / precision / measurement / platform で39候補を生成 →
> 敵対的統合で却下・重複排除・レバレッジ順ランク)。本ファイルは結論のみを残す戦略文書。

## Top insight

**測れないものは最適化できない。価値は未証明。最初に作るのは計測。**
それまで他のレバーはすべて「仮説」にとどまる。→ 本セッションで `metrics.py` を実装済み(下記 ✅)。

## Thesis 再定義(正直版)

「少トークン」を**生トークン総量で測るのは誤り**。最適化対象は次の3つ:

1. **main-context トークン** — 高価な Opus 本命文脈の量
2. **コスト加重トークン** — Opus ≫ Sonnet ≫ Haiku ≫ **cache-read(≈1/10)**
3. **手戻り(rework)トークン** — 修正ループで捨てるトークン

生総量は cache-read が支配するため、ナイーブに測ると誤誘導される。**総トークンが増えても
cost-weighted / main-context が下がれば勝ち**。「少トークン」の主戦場は prompt cache を守ること。

## 検証済みの事実(本セッションで実コード/実データ直読により確認)

- **scope バグ(実在・修正済み)**: `gate.py` が `resolve_commands` の `scope` を捨てており、
  `scope="impacted"`(既定)が無効で毎ターン全スイートが走っていた。本セッションで配線を修正。
- **transcript schema(実データで確認)**: main `.jsonl` の assistant 行は `message.usage`
  (input/output/cache_read/cache_creation〔5m・1h 別〕)+ `message.model` を持ち、本環境では
  **全行 `isSidechain=false`**。subagent の usage は**別ファイル**(`<session>/subagents/**/agent-*.jsonl`、
  各 `isSidechain=true`)。→ **main/sub の分離は『ファイルの所在』で行う**(同一ファイル内 isSidechain ではない)。
  - 実セッションでの `metrics.summarize` スモーク: main 98 ターン / subagent 7 ファイル・258 ターン、
    main-context ピーク ≈395k、コスト加重 main vs total が別計上できることを確認。

## NOW(最高レバレッジ)

| # | レバー | serves | effort | 状態 | 要点 |
|---|---|---|---|---|---|
| 1 | `metrics.py` + Stop スナップショット | both | S | ✅ 実装 | transcript を解析、`isSidechain`/ファイル所在で main/sub 分離、cache-read 別計上。`gate.py` 冒頭で `metrics.snapshot`。main=HARD / total=best-effort |
| 2 | 単一コスト加重指標 | both | S | ✅ 実装 | `weight_cost`(モデル別単価、5m/1h cache 区別)。`metrics.json` に main/subagent/total を分離記録。`measurement.md` を cost-weighted 列へ刷新 |
| 4 | `scope=impacted` を gate に配線 | both | M | ✅ 実装 | verified バグ修正。pytest の*テストのみ変更*を安全に絞り、確信不能は full にフォールバック。積極的 source→test マップは将来作業 |
| 3 | SubagentStop 再・要点圧縮ゲート(block-until-compliant) | both | M | ⬜ NEXT へ | リポ自認の弱点#1(firewall は HARD だが 要点圧縮 ≤N 行は SOFT)。**出力は書換/切り詰め不可**(公式 hooks docs で確認)。`subagent_budget.py` + settings.json の SubagentStop で、agent_id から subagent transcript(`subagents/agent-<id>.jsonl`)を特定し最終出力サイズを測定、超過なら exit 2 で『≤N 行に再・要点圧縮』を強制(matcher で reviewer/deep-solver 別予算)。≤K 回、超えたら通過+log。**リテラル cap 不可=再・要点圧縮ゲート**。計測先行(ua-bench/metrics で出力肥大の実在を確認してから入れる) |

## NEXT

| # | レバー | serves | effort | 要点 |
|---|---|---|---|---|
| 3 | SubagentStop 再・要点圧縮ゲート | both | M | 上記(NOW から繰り越し)。soft prompting → **再・要点圧縮ゲート**(block-until-compliant)。出力書換は不可で、超過時に exit 2 で再・要点圧縮を強制する近似。計測先行 |
| 5 | UNKNOWN/timeout ギャップの硬化 | precision | M | flaky timeout が resume で黙って PASS になる(最悪の silent false-pass)を塞ぐ。timeout signature を terminal-cache しない/倍化で1回再実行/test コマンドありで UNKNOWN 連続なら FAIL 扱い。`require_pass` config |
| 6 | `ua-failpass` skill | both | S | iterate-until-pass は新挙動のテストが無ければ空虚。確定 FAIL→PASS で gate を**実行可能仕様**化(ua-spec の acceptance を実行可能に)。非自明な挙動変更に限定 |
| 7 | haiku-tier `explorer` agent | tokens | S | model-per-agent は実証済み(reviewer=sonnet, deep-solver=opus)。広い find-where-X は最安モデル向き(Haiku ≈ Opus の1/5〜1/25)。conclusion+file:line のみ返す。#3 の予算と併用 |
| 8 | `ua-bench` 一発 A/B ハーネス | both | M | A/B が文書のまま走らない原因=手動。`bench/` + 1コマンド→ cost-weighted テーブル。`metrics.py` を再利用し control(hook なし)/treatment(hook あり)を同一採点。タスク3個から。※`-p` で hook が headless 発火するか先に確認 |

## LATER

| # | レバー | serves | effort | 要点 |
|---|---|---|---|---|
| 9 | cache 規律ポリシー | tokens | S | 5分 TTL の cache 失効が最大の隠れコスト。CLAUDE.md に「タスク中に MCP/tool 可用性をトグルしない」、resume 注入から揮発 timestamp を外し同一 HEAD で 毎回同じ文章に。`metrics.json` の cache-read 比率で検証 |
| 10 | worktree/branch-aware resume | both | S | `resume_context.py` がブランチ不一致で進捗を黙って捨てる→ worktree で文脈喪失。per-branch keying(`latest-progress.<branchkey>.md`)で復元 |
| 11 | build + smoke を gate 信号に | both | M | lint+type+unit が PASS でも build 破綻/起動クラッシュは見えない(passed-but-does-not-run)。`Stack.build/smoke` + `aggregate()` tri-state。cache 温度と緊張するので opt-in & scope 連動 |

## CUT(却下・理由付き)

- **API 側計測(count_tokens / Batches / batch cache_control)**: カテゴリ違い。対話型の hook/firewall を
  API で A/B 不可、live session は自動キャッシュ、`metrics.py` が既に in-session の正確な usage を出す。
- **PreCompact での安価 compaction / fail-log を Haiku に**: 投機的(未確認 capability)or 限界利得
  (gate は既に ~16 行に要点圧縮済み)。
- **effort routing hook / Task gate / statusLine / verify+gate 統合**: 低価値 or リスク(モデルは effort
  自己切替不可=settings 固定、advisory hook は cached prefix にバイト追加、fast lane を弱める)。まず計測。
- **property/diff-coverage/spec checklist/context-manifest/scoped-retrieval MCP**: ua-failpass + haiku
  explorer に subsume、もしくは遅く stack 依存で cache を冷やす。

## 本セッションで変更したファイル

- `claude-home/hooks/metrics.py`(新規)— parse_transcript / discover_subagent_transcripts / weight_cost / summarize / snapshot
- `claude-home/hooks/gate.py` — scope 配線(バグ修正)+ metrics.snapshot
- `claude-home/hooks/common.py` — `impacted_test_cmd`(保守的・テストのみ変更を pytest で絞る)
- `docs/measurement.md` — cost-weighted 列 + HARD/best-effort 明記
- `tests/test_metrics.py`(新規)、`tests/test_common.py` / `tests/test_gate.py`(scope/impacted 追記)— 81 tests PASS
