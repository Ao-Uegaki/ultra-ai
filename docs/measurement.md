# ultra-ai 効果測定プロトコル

## 価値仮説(正直に)

ultra-ai が削るのは主に **メイン Opus が背負う文脈量** と **手戻り(修正ループ)** であって、
必ずしも全タスクの **総トークン** ではない。subagent / built-in Explore は別コンテキストを持つため、
総トークンはむしろ増える場合がある。

したがって主指標は **「長いタスクで Opus の文脈品質を保ち、完了までの手戻りを減らせるか」**。
総トークン削減は副次的に観測する(小タスクでは増えても妥当)。

## A/B プロトコル

代表タスクを N 個(例: バグ修正 ×3・小機能 ×2・リファクタ ×2)用意し、各タスクを2環境で実施:

- **対照**: 素の `claude`
- **処理**: `ultra-ai`(同一リポ・同一初期コミットから)

順序効果を避けるため、タスクごとに環境の実施順を入れ替える。

## 指標と取得方法

`metrics.py`(Stop hook = `gate.py` が毎ターン呼ぶ)が当該セッションの transcript を解析し、
`state/<key>/sessions/<sid>/metrics.json` と `state/<key>/shared/metrics-ledger.jsonl` に
**ゼロトークンで自動記録**する。main は単一ファイルの構造保証(**HARD**)、total は兄弟 subagent
ファイル(`<session>/subagents/**/agent-*.jsonl`)の集計(**best-effort**・ディレクトリ規約依存)。

| 指標 | 取得方法 | 保証 |
|---|---|---|
| 完了したか (Yes/No) | 人手判定(受け入れ条件) | — |
| 最終テスト成功 | タスク終了時に全テスト PASS か | HARD |
| 修正ループ回数 | `verification.json` の `fail_streak`、`logs/*.log` 本数 | HARD |
| main-context ピーク | `metrics.json` の `peak_main_context` | HARD |
| main コスト加重 | `metrics.json` の `main.weighted_cost` | HARD |
| subagent / 総 コスト加重 | `metrics.json` の `subagent.weighted_cost` / `total.weighted_cost` | best-effort |
| main / total 生トークン | `metrics.json` の `main` / `total`(input/output/cache_read/cache_creation) | main=HARD / total=best-effort |
| 所要時間 (wall-clock) | `metrics.json` の `wall_clock_s` | HARD |
| 誤検出 (verify/gate の誤った FAIL・UNKNOWN) | `state/.../logs/*.log` を確認 | — |

**コスト加重**: モデル別単価(Opus $5/$25・Sonnet $3/$15・Haiku $1/$5 per 1M、cache-read は input の
0.1×、cache-write は 5m=1.25×・1h=2.0×)で USD 換算した**相対コンパレータ**。サブスクは実課金しない
ため絶対額でなく A/B 間の相対比較に使う。手動の `/cost`・`/context` はクロスチェック用の補助。

## 判定基準

- ultra-ai が **完了率・最終 PASS 率を下げず**、かつ **修正ループ / メイン context** を有意に下げれば採用。
  総トークンが多少増えても、長尺タスクで品質・手戻りが改善すれば価値仮説は成立。
- 逆に小タスクでオーバーヘッド(誤検出・無駄な検証・遅延)が目立つなら、対象リポに `.ultra-ai.toml` を
  置いて `scope` や verify コマンドを絞る。

## 注意(自己採点で必ず分ける)

- **「少トークン」を生トークン総量で測るのは誤り**。真の最適化対象は (1) main-context (2) コスト加重
  (Opus ≫ Sonnet ≫ Haiku ≫ cache-read) (3) 手戻り(rework)トークン。生総量は **cache-read(≈1/10)が支配**する。
- **メイン context 削減 ≠ 総トークン削減**。総コスト加重が増えても main-context が下がれば、長尺タスクでは価値仮説は成立しうる。
- **main 計測=HARD(単一ファイルの構造保証)/ total=best-effort(ディレクトリ規約依存)**。両者を必ず分けて報告する。
- 「≤20 行 ≈ 0 トークン」は **PASS 経路でのみ真**(失敗時は要約分のトークンが入る)。
- 「生ファイルがメインに流れ込まない(context firewall)」は **構造保証(固い)**、「要点圧縮量 ≤N 行」は **prompting(柔らかい)**。後者の **hard 化は不可**(SubagentStop は出力を書換できない)— roadmap の SubagentStop は **再・要点圧縮ゲート**で近似する(NEXT・計測先行)。
