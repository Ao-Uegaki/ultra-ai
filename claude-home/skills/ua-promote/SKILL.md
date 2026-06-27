---
name: ua-promote
description: project→全プロジェクト共通の学習した約束ごと の registry を閲覧し、複数 repo にまたがって合意した学習(2+ repo で有効化)を確認・手動 pin / 取り消しする。マシン全体に効く学習レイヤ(Tier B・global)を点検する。
---

repo ごとの学習は repo ごとに分けて保存されているが、**同一の学習した約束ごと が 2+ repo で
有効化(active)すると 全プロジェクト共通の学習した約束ごと に昇格**され、全プロジェクトの SessionStart で毎回まったく同じ文章で文脈へ読み込まれる
(`UA_GLOBAL_INSTINCTS=0` で無効)。これは別リポでの**単発訂正でも昇格を許す**唯一のルールベースの
「確かめた事実による裏付け」(numeric confidence は不採用)。本スキルはその registry を**点検**する。

state は `state/global/`(repo ごとと同じ git で取り消せる空間):
- `LEARNED.md` … global active(2+ repo 合意・読み込み対象・テキストのみ=毎回まったく同じ文章)。
- `learn-repos.json` … 正規化キー→`{text, repos:[repo_key,...]}`(読み込ませない・票数の出どころ)。

手順:
1. `python3 "$CLAUDE_CONFIG_DIR/hooks/learn.py" global` で registry を JSON で得る。
   - `global_active`: いま注入されている 全プロジェクト共通の学習した約束ごと のテキスト。
   - `registry`: 各 学習した約束ごと の repo 票数(降順)。**2 票以上が global**、1 票は「あと1リポで昇格」。
2. 内容を**日本語で要約**して報告(global 件数 / もうすぐ昇格されそうな 1 票の項目 / 出どころ repo 数)。

手動 pin / 取り消し(任意・どちらも git で取り消せるテキスト操作):
- **取り消し**: `state/global/LEARNED.md` から 1 行削除すれば即無効(次の `record_active_instincts` で
  repos.json から作り直されるため、ずっと残す取り消しは `learn-repos.json` の該当レコードを削除する)。
- **手動 pin**(2 repo 待たず global 化したい): `learn-repos.json` の該当レコードの `repos` に
  別の repo_key を 1 つ足して 2 票にする(次回の読み込みで global active 入り)。乱用しない=複数 repo にまたがる合意の趣旨を守る。

注意:
- これは反映(実際に AI へ読み込ませて有効にする)レイヤ。全プロジェクト共通の学習した約束ごと も**見える形**(git テキスト)で 1 行削除でき、**あなたの次の訂正が最優先**。
- `UA_GLOBAL_INSTINCTS=0` で global の記録・読み込み・昇格をすべて停止(repo ごとの二値挙動に戻る)。`ultra-ai-safe` で全 hook 無効。
- 昇格は自動(2+ repo で有効化)が基本。手動 pin は例外措置。
