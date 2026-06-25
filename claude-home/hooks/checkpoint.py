#!/usr/bin/env python3
"""checkpoint.py — 手動の安全なチェックポイント・コミット(/ua-checkpoint から起動)。

追跡済みの変更だけを `git add -u` でコミットする — 未追跡ファイルは決して含めない —
ので機密やスクラッチが紛れ込まない。現ブランチに、決定論的なメッセージで。プロジェクトの
cwd で動く(これは hook ではなく通常コマンドなので stdin は読まない)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def commit_message(files: list[str]) -> str:
    names = [Path(f).name for f in files[:5]]
    msg = "checkpoint: " + ", ".join(names)
    if len(files) > 5:
        msg += f" (+{len(files) - 5} more)"
    return msg


def _looks_secret(path: str) -> bool:
    """Conservative secret-file detector. Delegates to the shared helper so
    checkpoint(コミット拒否)と ua-audit(自己監査)が同じ定義を使う。"""
    return common.looks_secret_file(path)


def main() -> int:
    cwd = os.getcwd()
    if not common.is_git_repo(cwd):
        print("ua-checkpoint: git リポジトリではありません — 何もコミットしていません。")
        return 1
    tracked_changed = [ln for ln in common.git_status_porcelain(cwd) if ln[:2] != "??"]
    if not tracked_changed:
        print("ua-checkpoint: コミットすべき追跡済みの変更はありません"
              "(未追跡ファイルは意図的に自動追加しません)。")
        return 0
    branch = common.git_branch(cwd) or "(unknown)"
    common.run_git(["git", "add", "-u"], cwd)
    diff = common.run_git(["git", "diff", "--cached", "--name-only", "-z"], cwd)
    if diff is None or diff.returncode != 0:
        print("ua-checkpoint: `git diff` に失敗しました(リポジトリの問題?):\n"
              + ((diff.stderr or diff.stdout)[:500] if diff else "(git timed out)"))
        return 1
    # -z で NUL 区切り出力にし、空白・改行入りパスや quote 問題を回避する。
    files = [f for f in diff.stdout.split("\0") if f]
    if not files:
        print("ua-checkpoint: `git add -u` 後にステージされたものがありません。")
        return 0
    secrets = [f for f in files if _looks_secret(f)]
    if secrets:
        common.run_git(["git", "reset", "-q", "HEAD", "--", *secrets], cwd)  # best-effort unstage
        print("ua-checkpoint: 拒否 — 次のステージ済みファイルは機密の可能性があります: "
              + ", ".join(secrets)
              + "。追跡から外す(git rm --cached <f>)かリネームしてから再試行してください。")
        return 1
    msg = commit_message(files)
    cp = common.run_git(["git", "commit", "-q", "-m", msg], cwd)
    if cp is None or cp.returncode != 0:
        print("ua-checkpoint: コミットに失敗しました(これは検証の失敗ではありません):\n"
              + ((cp.stderr or cp.stdout)[:500] if cp else "(git timed out)"))
        return 1
    head = (common.git_head(cwd) or "")[:8]
    print(f"ua-checkpoint: {len(files)} 件のファイルを {branch} にコミットしました ({head}): {msg}")
    if branch in ("main", "master"):
        print("  ⚠ 主要ブランチへ直接コミットしました — フィーチャーブランチの利用を検討してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
