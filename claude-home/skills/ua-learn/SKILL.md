---
name: ua-learn
description: セッションの学習候補(あなたの訂正 / テストの FAIL→PASS)から、再利用できる 1行ルールを作る。明示訂正は自動で有効・推測は下書き(まだ読み込ませない保留)に振り分ける。UA_AUTOAPPLY(旧 UA_AUTOFIRE)下の学習レイヤ(Tier B)を手動で動かす。
---

capture(記録だけ=状態ファイルに書くが AI には読み込ませない。gate.py の FAIL→PASS / learn_capture.py の訂正)が貯めた学習候補から、再利用できる 1行ルールを作る。
**賢く半自動**:明示訂正(高確信)は有効へ自動で振り分け、FAIL→PASS の推測(別の原因のせいだったかもしれない)は下書きへ。
次セッション開始時に resume_context が毎回まったく同じ文章で文脈へ読み込ませる(本スキルは書くだけ)。

手順:
1. `python3 "$CLAUDE_CONFIG_DIR/hooks/learn.py" candidates` で候補 JSON を得る。**空なら「学習候補なし」と報告して終了**。
2. 候補を `learner` サブエージェント(haiku)に渡し、1行ルールの JSON を作る
   (active=本当に一般ルールになる明示訂正のみ / 下書き=FAIL→PASS の推測。ノイズ・誤検知・矛盾は捨てる)。
   - 急ぎ・LLM 不要ならルールベースの代替 `python3 "$CLAUDE_CONFIG_DIR/hooks/learn.py" auto`(質は落ちる)。
3. 生成 JSON を適用: `echo '<JSON>' | python3 "$CLAUDE_CONFIG_DIR/hooks/learn.py" apply`。
4. 結果(active / 下書き 件数)を報告。active は次回 `UA_AUTOAPPLY=1`(旧名 `UA_AUTOFIRE` も互換)起動時に文脈へ読み込まれる。

注意:
- これは学習レイヤ(反映=実際に AI へ読み込ませて有効にする)。active の 学習した約束ごと は自動で次セッションに効くが、**見える形**(`LEARNED.md`)で
  1行削除でき、**あなたの次の訂正が最優先で上書き**する。`UA_AUTOAPPLY`(旧 `UA_AUTOFIRE`)未設定なら読み込みは起きない。
- 下書き(`learn-draft.md`)は人が読んで採否を決める下書き=まだ読み込ませない保留(自動では効かない)。
- `/ua-check` で学習 state を監査でき、`ultra-ai-safe` で全 hook を無効化できる。
