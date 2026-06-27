---
name: ua-ship
description: コミット済みの変更を feature ブランチへ安全に push し、必要なら PR を開く。ブランチ・公開可否・権利・露出を確認してから出す。/ua-checkpoint の後に使う。明示的に頼まれたときだけ使う。
---

コミット済みの変更を、**本職の運用**(feature ブランチ → push → PR・壊れたコード/未コミットは出さない)で公開する。

手順:
1. **事前点検**: Bash で `python3 "$CLAUDE_CONFIG_DIR/hooks/push.py" check` を実行し、JSON を読む。
   - `committed`(クリーンか)/ `local_branch`・`upstream`・`ahead`/ `target`(remote・branch・url)/ `visibility`/
     `is_main_target`/ `pass_gate`(PASS 検証済みか)/ `commits_to_push`/ public なら `public_checks`(LICENSE・露出 secret・author メール)。
2. **未コミット / 未検証なら止める**: `committed=false` なら「先に `/ua-checkpoint`」。`pass_gate=false` なら PASS 検証してから
   checkpoint するよう促す(勝手に `--allow-*` を付けない)。
3. **チェックリストを日本語で提示して確認**(状況適応):
   - **ブランチ**: 「`<local_branch>` → `<remote>/<target>`(ahead N)」。`is_main_target=true` なら **main 直 push は既定で拒否**される旨を伝え、
     feature ブランチ + PR を勧める(まだ feature でなければ `git switch -c feature/<topic>` を提案)。
   - **公開可否**: `visibility` が PUBLIC なら、`public_checks` を必ず人に見せる — **LICENSE はあるか / 露出する secret は無いか /
     出る author メールはこれで良いか**(会社/個人メールが公開に出ないか)。private なら簡潔でよい。
   - `commits_to_push` の要約。
4. **push**: ユーザーが branch/target/公開可否を確認したら
   `python3 "$CLAUDE_CONFIG_DIR/hooks/push.py" do --remote <remote> --branch <target>` を実行
   (直前に clean-tree・PASS・main 直 を再検証して push。main を意図的に出すときだけ環境変数 `UA_SHIP_ALLOW_MAIN=1`)。
5. **PR(任意)**: gh があり feature ブランチを出したなら、`gh pr create --base main --title "<title>" --body "<body>"` で PR を開く。
   body の末尾に必ず次の行を入れる:
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
   gh が無ければ、PR を Web で開く手順を日本語で案内する(安全にフォールバック)。
6. 結果(push 先・PR URL があれば)を日本語で簡潔に報告する。

注意: これは不可逆な公開境界を渡る操作。push 失敗は検証失敗とは別物として報告する。停止スイッチ: `UA_SHIP=0`(全体)・`UA_PUSH_GUARD=0`(未コミット push ガード)。
