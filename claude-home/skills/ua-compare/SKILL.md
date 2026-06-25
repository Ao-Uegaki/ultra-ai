---
name: ua-compare
description: 学習レイヤ(や任意の設定)の効果を A/B で見る。同一タスクを control(学習を切る=UA_AUTOAPPLY=0、旧名 UA_AUTOFIRE も互換)と treatment(既定)で回し、transcript をコストで重み付けして比較する。計測は関門にしない(監視・洞察用)。
---

「学習 ON/OFF でどちらが安く・手戻り少なく済むか」を実データで見る。**計測は関門にしない**(出荷の前提に
しない・監視と洞察のため)。`claude -p` の headless では Stop hook 発火が未保証なので、**タスク実行は in-session 手動**、
採点はルールベースのスクリプトが transcript から行う(`metrics.py` 再利用)。

手順:
1. **タスクを2〜3個 用意**(再現可能な依頼。例:同じ小機能の実装、同じバグ修正)。
2. 各タスクを **2回** 同条件で回す:
   - **control**: `UA_AUTOAPPLY=0 ua-rules?` … 学習を切って起動(`UA_AUTOAPPLY=0`、旧名 `UA_AUTOFIRE` も互換)。
   - **treatment**: 既定(学習 ON)で起動。
   できるだけ初期状態を揃える(同じブランチ・同じ出発点)。
3. 各セッションの transcript パス(`~/.claude/projects/<munged>/<session>.jsonl` 相当)を控える。
4. 採点:`python3 bench/compare.py <control.jsonl> <treatment.jsonl>`。
   - 出力:`cost_weighted`(コスト)・`turns_main`(ターン≒手戻り)・`peak_main_context`・`cache_read`(再利用)を
     control/treatment/delta/better で比較。lower-is-better(コスト/ターン/peak)と higher-is-better(cache_read)を区別。
5. 複数タスクの傾向で判断(1回は誤差)。treatment が安く・少ターンなら学習が効いている。悪化(treatment が悪い)を
   見たら `UA_AUTOAPPLY=0`(旧名 `UA_AUTOFIRE` も互換)で止める。

注意:
- これは**監視**であって**関門ではない**。「計測で良し悪しが出るまで本番に出さない方針」(measure-first)は採らない。
- headless 自動化(`bench/run.py`)は、`claude -p` で Stop/PostToolUse が確実に発火することを実機確認できたら追加する
  (現状 docs 未保証)。それまでは本 in-session プロトコルを使う。
- 計測は cache を冷やさない(transcript を読むだけ)。
