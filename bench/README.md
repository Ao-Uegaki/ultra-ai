# bench — 対・素 Claude の A/B 点検ツール(PoC)

「ultra-ai は素の Claude Code より、現実に効くのか」を**数字で**見るための、**独立・オフライン・必要なときだけ動く**ツール。
ultra-ai のランタイム挙動は一切変えない(毎セッション比較などしない)。テスト/CI を回すのと同じ別物。
憲法どおり **計測はゲートにしない**(監視・洞察用)。

## 3アーム

| arm | CLAUDE_CONFIG_DIR | 中身 |
|---|---|---|
| `control`(素 claude) | `bench/arm-control-config/` | `settings.json`(model=opus)のみ。hook/CLAUDE.md/学習/approach なし。`UA_*` も全 OFF。 |
| `control-xhigh`(素 + 思考量だけ) | `bench/arm-control-config/`(control と共有) | control と同一の素 config に `--effort xhigh` フラグ**だけ**付与。差は effort のみ=思考量の単独効果を切り分ける。 |
| `treatment`(ultra-ai) | `claude-home/` | 全部入り(hook 5種・CLAUDE.md・skills・effort=xhigh・学習・approach)。 |

比較の読み方: **treatment vs control = 総合効果**(ultra-ai の仕組み + 思考量)、**treatment vs control-xhigh = 仕組みのみ**
(思考量を揃えた上での、仕組みの効果)。集計列は `ab_report.py` が両方を出す。

採点は **transcript から機械的に(乱数を使わず)**(headless で Stop hook 発火は未保証だが transcript は必ず書かれる)。
`metrics.py`(cost-weighted=コストで重み付け)と `compare.py` の指標定義を流用する。

## 使い方

```bash
# 1) 3アームを走らせる(実際に `claude` を動かす)。-k = 各 (task,arm) の試行回数。
python3 bench/ab_run.py -k 3                 # 全タスク
python3 bench/ab_run.py -k 1 --tasks slugify # 一部タスク・少回数(スモーク)
#   → results path を stdout に出す(例: bench/results/20260618-120000.jsonl)

# 2) 集計して markdown レポート化(API 不要・transcript を読むだけ)
python3 bench/ab_report.py bench/results/<stamp>.jsonl
```

指標: **pass_rate / pass@k**(大きいほど良い)、**cost_weighted / turns_main / peak_main_context**
(小さいほど良い)、**cache_read_total**(大きいほど良い)。

## タスクの足し方

`bench/tasks/<id>/` に:
- `prompt.md` — agent への指示(**spec のみ**。oracle は見せない)。
- `repo/` — 開始時のファイル一式(使い捨て sandbox にコピーされる)。
- `oracle/` — **held-out** 採点テスト(agent 実行**後**に sandbox へ入れて実行 → gaming 防止)。
- `meta.json` — `{"id","lang","test_cmd":[...],"oracle_timeout"}`。`test_cmd` は sandbox 内で実行し
  return code 0 を成功とみなす。

タスクは「oracle が**開始 repo では fail・正しい修正で pass**」になるよう作る(=成功が本物の解決を意味する)。

## 正直な留保(過剰解釈しない)

- **effort の交絡**: treatment は effort=xhigh 固定。control は既定 effort なので、treatment vs control の差の一部は
  「ultra-ai の仕組み」でなく「思考量」。これを切り分けるための `control-xhigh` アーム(素 config + `--effort xhigh`)があり、
  **treatment vs control-xhigh = 仕組みのみ**を見られる。
- **小 N・高分散**: agentic は実行ごとにブレる。k=3・タスク 2-3 では結論は **directional**(参考値)であって decisive ではない。
- **モデル利用を消費**: 3アーム × タスク × k 回ぶん `claude -p` が走る(サブスクは別建て課金なし・利用枠を消費)。タスクは小さく・k は小さく保つ。
- **安全**: 実行は使い捨て `bench/.sandboxes/` のみ + **scoped `--allowedTools`**(`Read/Edit/Write/Glob/Grep` + 限定 `Bash`(`python3`/`pytest` 等)だけ。`--dangerously-skip-permissions` は使わない=任意コマンド不可)。本物の repo には走らせない。headless で非許可ツールは自動 deny(hang しない)。
- **treatment の副作用**: treatment は `claude-home` を config dir に使うため、bench の transcript/state が
  `claude-home/` 配下に **sandbox パスでキー付けされて**残る(実 repo の state は汚さない)。気になれば後で消す。
- **次段**: 差が見えたら ablation(approach だけ/学習だけ切るアーム)・定期実行へ拡張(本 PoC の外)。
