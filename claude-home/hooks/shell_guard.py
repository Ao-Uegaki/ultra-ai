#!/usr/bin/env python3
"""shell_guard.py — PreToolUse(Bash) の決定論ガード(ゼロトークン)。

不可逆・流出系の **ごく少数の高確度パターン**だけを、実行される前にブロックする。
設計原則(憲法準拠・最後の安全網):
  - deny-narrow: パターンは意図的に狭い。確信できないものは**通す**(allow-on-uncertainty)。
    `rm -rf node_modules` や `git push --force-with-lease feature` のような正当操作は止めない。
  - silent-on-pass: 通過時は exit 0・無出力(fast lane と cached prefix にバイトを足さない)。
  - 一致時のみ exit 2 + stderr に理由。Claude Code は PreToolUse の exit 2 で
    **ツール呼び出しをブロックし、stderr をモデルへ渡す**(docs: code.claude.com/docs/en/hooks.md で確認)。
  - 明示オーバーライド: コマンドに `ua-allow` を含めれば、その1回だけ通す(審査可能な意図表明)。

gate.py(検証)とは別物: gate は Stop で*結果*を検証し、shell-guard は実行*前*に
壊滅的操作を止める。第二の不安定ゲートにはしない(偽陽性ゼロを最優先)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

OVERRIDE = "ua-allow"  # コマンドにこの語があれば 1 回だけ通す(意図的な上書き)

# ターゲット境界: 空白・コマンド区切り・**引用符**・括弧・行末(引用符内の実コマンド
# `bash -c "rm -rf /"` を取りこぼさないため。quote 剥がしは false-negative を生むのでしない)。
_T = r"(?=[\s;&|\"')]|$)"
# rm -rf が指す「壊滅的ターゲット」だけを列挙(ローカル dir 削除は対象外=偽陽性回避)。
_RM_TARGET = (
    r"(?:"
    r"/(?:\*)?" + _T +                                           # ルート: / または /*
    r"|/(?:etc|usr|var|bin|sbin|lib|lib64|boot|sys|dev|opt|root"  # 重要システムdir
    r"|System|Library|Applications)(?:/\S*)?"
    r"|~(?:/\S*)?" + _T +                                        # ホーム: ~ / ~/...
    r"|\$HOME\b"                                                  # $HOME
    r"|\*" + _T +                                                # 裸のワイルドカード(cwd 全消し)
    r"|\.\.?" + _T +                                             # . / .. 単独
    r")"
)
# rm が再帰(-r/-R)かつ強制(-f)で、上記ターゲットを伴う場合(git rm は除外=回復可能)。
_RM_RF = re.compile(
    r"(?<!git )\brm\b"
    r"(?=[^\n|;&]*?\s-\w*[rR])"     # どこかに -r / -R 系フラグ
    r"(?=[^\n|;&]*?\s-\w*f)"        # どこかに -f 系フラグ
    r"[^\n|;&]*?\s" + _RM_TARGET
)

# (compiled pattern, reason)。各パターンは「不可逆 or リモートコード/流出」に限定。
_RULES: list[tuple[re.Pattern, str]] = [
    (_RM_RF,
     "再帰・強制の rm がルート/ホーム/システムdir/ワイルドカード全体を指しています(不可逆な全削除)"),
    (re.compile(r"\bgit\s+push\b(?![^\n]*--force-with-lease)"
                r"(?=[^\n]*(?:\s--force\b|\s-f\b))[^\n]*\b(?:main|master)\b"),
     "main/master への非 lease な force-push(他者の履歴を上書きする恐れ — --force-with-lease を使ってください)"),
    (re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b"),
     "ネットワーク取得をシェルに直パイプ(未検証のリモートコード実行)"),
    (re.compile(r"\bsudo\s+rm\b(?=[^\n|;&]*?\s-\w*[rR])(?=[^\n|;&]*?\s-\w*f)"),
     "sudo + 再帰強制 rm(権限昇格つきの不可逆削除)"),
    (re.compile(r"\bdd\b[^\n|;&]*\bof=/dev/"),
     "dd がブロックデバイスに書き込み(ディスク破壊の恐れ)"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b"),
     "mkfs によるファイルシステム作成(対象デバイスを消去します)"),
    (re.compile(r">\s*/dev/(?:sd|nvme|disk|hd|vd)\w*"),
     "ブロックデバイスへのリダイレクト(ディスク破壊の恐れ)"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
     "fork bomb(システムを枯渇させます)"),
    (re.compile(r"\bchmod\s+(?:-[\w-]+\s+)*0?777\b"),
     "chmod 777(全ユーザに read/write/exec を付与する過剰な権限)"),
]


def command_of(payload: dict) -> str | None:
    """PreToolUse payload から Bash コマンド文字列を取り出す(Bash 以外・欠損は None)。"""
    if (payload.get("tool_name") or "") != "Bash":
        return None
    cmd = (payload.get("tool_input") or {}).get("command")
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def check(command: str) -> str | None:
    """deny-narrow ルールに一致すれば理由を返す。一致しなければ None(=通す)。"""
    if OVERRIDE in command:
        return None
    for pat, reason in _RULES:
        if pat.search(command):
            return reason
    return None


def process(payload: dict) -> tuple[int, str]:
    """純粋な判定(ユニットテスト可能)。Returns (exit_code, stderr_message)。"""
    cmd = command_of(payload)
    if not cmd:
        return 0, ""
    reason = check(cmd)
    if not reason:
        return 0, ""
    shown = cmd if len(cmd) <= 200 else cmd[:200] + "…"
    msg = ("⛔ ultra-ai shell-guard: " + reason + "\n"
           "   command: " + shown + "\n"
           "   意図的なら、対象を限定して書き直すか、コマンドに `ua-allow` を付けて再実行してください "
           "(全 hook を切るなら ultra-ai-safe)。")
    return 2, msg


def main() -> int:
    code, msg = process(common.read_hook_input())
    if code == 2 and msg:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
