---
name: ua-check
description: ultra-ai 自身の設定面(settings/hooks/agents/skills/MCP/CLAUDE.md)を自己監査する。ハードコード機密・過剰権限・hook/MCP が勝手に指示を読み込ませるリスク・隠し指示をルールベース(機械的処理)で検出。明示的に頼まれたとき、または設定を変えた後に使う。
---

ultra-ai が自分で走らせている土台(config / hooks / agents / skills / MCP)を監査する。
自分が実行する hook やコマンドにリスクが無いかを、自分自身でチェックするための安全網。

手順:
1. Bash で `python3 "$CLAUDE_CONFIG_DIR/hooks/ua_audit.py"` を実行する。
   - 選び抜いた設定面だけをルールベースでスキャンする(state/ plugins/ cache/ projects/ 等の実行時データは対象外)。
   - 検出系統(AgentShield の5系統を要点だけまとめたもの): 機密のベタ書き / 過剰な権限付与 / hook が勝手に指示を読み込ませる・実行する経路 /
     MCP の供給網・リモートリスク / 隠し指示(不可視文字)。
   - 三状態を返す: **PASS**(問題なし)/ **FAIL**(critical・high の指摘あり、終了コード 1)/
     **UNKNOWN**(settings.json が壊れている等で検査不能=未検証であって PASS ではない)。
2. 出力の各 `[SEV] path: 指摘` を確認し、該当ファイルを直読して妥当性を判断する(誤検知なら理由を述べる)。
   実在リスクなら、機密を環境変数/外部へ移す・権限を絞る・hook コマンドを修正する等で解消する。
3. 結果(overall と指摘の要約)を簡潔に報告する。UNKNOWN は「未検証」として扱い、原因(壊れた JSON 等)を伝える。

注意:
- これは読むだけ(read-only)の監査であり、何も自動修正・自動コミットしない(最後は人が判断する)。
- ルールは ua_audit.py に直書きしてある(意図的に十数個の critical/high のみ)。`rules/` ディレクトリは作らない。
