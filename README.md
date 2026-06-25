# ultra-ai

素の `claude` の代わりに `ultra-ai` を起動すると、品質優先の足場(ゼロトークン検証ループ・
要点をまとめて渡す探索・モデルの使い分け)が乗った Claude Code が立ち上がる、自己完結型の環境です。

基本原則: **品質が一次。トークン削減はその結果。** トークンは「文脈の削減・雑用の委譲・
ゼロトークンのルールベース(機械的処理)検証」から得るのであって、本命モデルの推論 effort を削って得るのではない。

## 仕組み

`bin/ultra-ai` は `CLAUDE_CONFIG_DIR=claude-home` を指定して Claude Code を起動し、
`claude-home/` を設定ルート(CLAUDE.md・settings.json・agents/・skills/・hooks)にする。
サブスク認証は macOS Keychain から読まれるため、**API キーは不要**。

## 機能全体像

3つのテーマで束ねられる:**①PASS を保証する ②土台を信頼する ③同じ失敗を繰り返さない**。
登録 hook は `PreToolUse / PostToolUse / Stop / SessionStart / UserPromptSubmit`。

- **検証ループ(PASS になるまで直す・ゼロトークン)** — `verify.py`[PostToolUse]が編集ファイルを lint+autofix し、
  直せないエラーだけ表に出す。`gate.py`[Stop]が型+テストを三状態(PASS/FAIL/UNKNOWN)で判定する(UNKNOWN は PASS にしない)。
- **計測** — `metrics.py` が transcript のトークンをコスト換算で会計(main=HARD / total=best-effort)、
  `session-journal.jsonl` が Stop 境界を捕捉、`statusline.py` が model·dir·branch·コスト·時間 を表示、
  `bench/compare.py` で設定の A/B をコスト換算で比較(計測はゲートにしない=監視用)。
- **セキュリティ(自己防衛)** — `shell_guard.py`[PreToolUse:Bash]が取り返しのつかない/情報が漏れるコマンドだけを狙ってブロック、
  `/ua-check` が自分の config/hooks/MCP/skills を三状態で自己点検(設定面ファイルの編集時に**自動で走る**・ゼロトークン・`UA_AUDIT=0` で無効)、
  `permissions.deny` が `.env`/`*.key` 等の読取を拒否。
- **学習レイヤ(既定 ON・`UA_AUTOAPPLY=0` で無効化)** — あなたの訂正(`learn_capture.py`[UserPromptSubmit])と
  テストの FAIL→PASS(`gate.py`)を**記録だけ**し、SessionStart で**自動で要点をまとめ**(ルールベース・ゼロトークン)、
  `resume_context.py` が学習を **毎回まったく同じ文章で(数字・時刻を混ぜない)文脈へ読み込ませ**る。賢く半自動=明示訂正は自動適用・推測は `learn-draft.md` で人手承認。
  学習は `LEARNED.md`(可視・1行削除可・次の訂正が最優先で上書き)。`/ua-learn` は任意の LLM 品質パス。
- **文脈引継ぎ** — `resume_context.py`[SessionStart]が同一ブランチで**進捗+学習+関係する規約(rules)**を、毎回同じ文章で文脈へ読み込ませる。
  規約は言語(`detect_stack`)もドメイン(`detect_domains`=react/fastapi/torch/Dockerfile 等から保守的に自動検出。`[ua-rules] auto=false` で停止)も**自動スコープ**。
  `MEMORY.md`+`memory/` がファイルベース記憶。`/ua-rules` で規約の閲覧・上書き。
- **自動提案(Tier 2・手動機能を忘却で死なせない)** — 手動で呼び出す skill を「適切な場面で一度だけ」差し出す
  (毎回ではない=1状態1回 dedup + 時間 throttle)。PASS+未コミットで `/ua-checkpoint`、PASS+大きめ diff で `/ua-refactor`(`gate.py`[Stop])、
  下書きの蓄積で `/ua-learn`、学習した約束ごとのマイルストンで `/ua-compare`(`resume_context.py`[SessionStart])。各々 `UA_SUGGEST_*` で可逆。
- **委譲(メイン文脈を生コードで汚さない防火壁)** — agents(`reviewer`=sonnet / `deep-solver`=opus+max / `learner`=haiku)、
  skills(`ua-spec`/`ua-checkpoint`/`ua-check`/`ua-learn`/`ua-refactor`/`ua-rules`/`ua-failpass`/`ua-compare`)、
  workflows(`understand`/`design-panel`/`review-audit` = 並列で分担 + 敵対検証)。
- **ガバナンス** — `CLAUDE.md`(運用憲法)、`docs/roadmap.md`・`docs/measurement.md`、state は全て `claude-home/state/<repo-key>/`。

## 使い方

```sh
ultra-ai            # カレントディレクトリで起動
uai                 # 同上の短縮(bin/uai → ultra-ai の symlink)
ultra-ai-safe       # 同じ設定で、ただし全 hook 無効(壊れた hook からの退避用)
```

## 前提

- **macOS** — 認証は macOS Keychain、通知・画像バナーは macOS 前提(非対応環境では該当機能のみ no-op で素通り)。
- **`claude` CLI**(Claude Code 本体)が PATH 上にあること。未導入なら
  `curl -fsSL https://claude.ai/install.sh | bash` または `npm install -g @anthropic-ai/claude-code`。
- **Python 3.11+** — hook が標準ライブラリ `tomllib` を使うため(外部依存はゼロ)。

## セットアップ

```sh
git clone <this-repo> ultra-ai
cd ultra-ai
./install.sh
```

`install.sh` は依存(`claude` / Python 3.11+)を確認し、`bin/` を PATH に**冪等追記**し、起動方法を表示する。
**何も上書きせず、インストールも強制しない**(不足は手順を案内するだけ・追記ブロックはマーカーで囲み後で消せる)。

手動で入れるなら `bin/` を PATH に追加するだけでよい(`~/.zshrc` 等。`/path/to/ultra-ai` は clone 先に置き換える):

```sh
export PATH="/path/to/ultra-ai/bin:$PATH"
```

ランチャは自身の位置(symlink も解決)から設定ルートを導出するので、clone 先がどこでも、
また `bin/ultra-ai` を PATH 上に symlink しても動く(絶対パスのハードコードなし)。

## 構成

- `bin/` — `ultra-ai` ランチャと `ultra-ai-safe` 退避ランチャ
- `claude-home/` — 設定ルート(CLAUDE_CONFIG_DIR)。ここに溜まる runtime state は git 管理外
  - `hooks/` — ルールベースで動く hook(`common.py` が共通ライブラリ)。検証(`verify`/`gate`)・計測(`metrics`/`statusline`)・
    セキュリティ(`shell_guard`/`ua_audit`)・学習(`learn_capture`/`learn`)・引継ぎ(`resume_context`)・`checkpoint`
  - `agents/` — サブエージェント(`reviewer`=sonnet / `deep-solver`=opus+max / `learner`=haiku)。広範な探索は組み込み Explore
  - `skills/` — `ua-*` スキル(spec/checkpoint/check/learn/refactor/rules/failpass/compare)
  - `rules/` — 必要なときだけ使う約束ごと(言語/ドメイン)。SessionStart に**関係分だけ、毎回同じ文章で文脈へ読み込ませる**(`UA_RULES=0` で無効)
  - `workflows/` — 並列で分担 + 敵対検証(`understand` / `design-panel` / `review-audit`)
- `tests/` — hook のルールベース部分の unit test(`python3 -m unittest discover -s tests`)。
  push/PR では GitHub Actions(`.github/workflows/test.yml`)が full スイートを回す(ローカルの `gate.py`[Stop] とは役割分担の安全網)
- `bench/` — A/B 採点器(`compare.py`。2 transcript をコスト換算で比較。計測はゲートにしない)
- `docs/` — `roadmap.md`(戦略)・`measurement.md`(効果測定プロトコル)

## 安全性(信頼モデル)

ultra-ai は検証のために**プロジェクトが設定した test/lint/typecheck コマンドを実行**します
(`.ultra-ai.toml` または package.json 等から自動検出)。これは CI と同様に**リポジトリのコードを
実行する**ことを意味するので、**信頼するリポジトリでのみ使ってください**(Claude Code の初回 trust
確認がゲート)。`/ua-checkpoint` は追跡済み変更のみをコミットし、`.env`/`*.key`/`*.pem`/`.credentials*`
等がステージされていればコミットを拒否します。機密ファイルは `settings.json` の `permissions.deny`
でも読み取りを禁止しています。

運用方針は `claude-home/CLAUDE.md`(運用憲法)、設計判断の記録は `claude-home/plans/` を参照。

## ライセンス

MIT License — `LICENSE` を参照。

fork して使う場合は、最初のコミット前に git author を自分のものへ設定してください
(前オーナーの名前/メールを引き継がないため):

```sh
git config user.name "Your Name"
git config user.email "you@example.com"
```
