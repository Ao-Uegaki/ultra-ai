---
name: ua-rules
description: このリポに関係する約束ごと(言語/ドメインの一般則)を確認・追記する。日常は SessionStart に自動で文脈へ読み込まれるので不要。自動検出を上書き/停止したい時や、規約を見たい/足したい時だけ使う。
---

`claude-home/rules/<topic>.md` の約束ごとは、**普段は SessionStart に自動で文脈へ読み込まれる**(言語は `detect_stack`、
ドメインは `detect_domains` がリポの依存/manifest から**自動検出**)。このスキルは**任意**:

- **今どれが効くか見る**: `claude-home/rules/` の該当 topic を読む(言語=stack、ドメイン=自動検出 ∪ 手動 `domains`)。
- **ドメインの上乗せ**: 自動検出で足りないドメインは `.ultra-ai.toml` に追記(加算・一度書けば以後自動):

      [ua-rules]
      domains = ["frontend", "backend"]

- **自動検出を止める**: 誤検出などで自動を切りたいときは `auto = false`(手動 `domains` は残る):

      [ua-rules]
      auto = false
      domains = ["backend"]

- **規約を足す/直す**: `claude-home/rules/<topic>.md` を編集(**terse・一般則のみ**。プロジェクト固有は書かない=
  それは学習レイヤが訂正から獲得する)。
- **一時停止**: `UA_RULES=0`(rules 全体)。

注意:ここは「言語/領域の一般則」だけ。常時ロードの巨大規約にしない(毎回まったく同じ文章・要点のみ=先頭が変わらなければ Claude が前置きの計算を使い回せる(プロンプトキャッシュ)ので速く・安い)。
