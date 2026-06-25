#!/usr/bin/env python3
"""verify.py — PostToolUse(Edit|Write|NotebookEdit)の即時フィードバック hook。

検証ループの「軽量レーン」:
  - 編集の発生をセッションの .dirty に記録する(あくまでヒント。Stop ゲートは
    `git status` を真実とし、PostToolUse が取りこぼす Bash 由来の編集も拾う)
  - 編集がソースファイルなら、プロジェクトの file-scoped な lint を **auto-fix 付き**で実行
    する(例: `eslint --fix`, `ruff check --fix`)。整形や自動修正可能な問題は黙って直す
    → exit 0・無出力・トークンはほぼ 0
  - **直せない**エラー(構文 / 未定義 / 型)だけを surface する: exit 2 + stderr に ≤20 行の
    要点圧縮要約。これを Claude Code がモデルへフィードバックする
  - UNKNOWN(lint コマンド無し / ツール未導入 / timeout)は記録のみで沈黙する
    — ここでは決してブロックしない。本物の PASS ゲートは Stop の gate.py

終了コード: 0 = surface するものなし / 2 = stderr をモデルへ surface。
この hook は exit 2 しない限りモデルのトークンを消費しない。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import detect  # noqa: E402


def lint_command(root: str) -> str | None:
    """Resolve the file-scoped lint command: project config first, else detect."""
    cmd = detect.verify_config(root).get("lint_file")
    if cmd:
        return cmd
    return detect.detect_stack(root).lint_file


def mark_dirty(cwd: str, session_id: str | None) -> None:
    """Mark this session as having unverified edits (a hint for the Stop gate;
    `git status` is the real source of truth there)."""
    common.write_text_atomic(common.session_state_dir(cwd, session_id) / common.STATE_DIRTY, "1")


def build_lint(cmd: str, file: str) -> str:
    """Substitute {file} (shell-quoted). If no placeholder, append the path."""
    q = "'" + file.replace("'", "'\\''") + "'"
    return cmd.replace("{file}", q) if "{file}" in cmd else f"{cmd} {q}"


# --------------------------------------------- hidden-unicode edit scan --------
# 編集ファイルに不可視/双方向の制御文字(隠し指示の注入面)が混入していないかを編集時に走査する。
# on-demand の ua-audit と違い、全拡張子の編集を即時に拾う第一線。検出は exit 2 で surface。
# `UA_UNICODE_GUARD=0` で無効。テキストとして読めない/巨大ファイルは安全側へ縮退(対象外)。

_UNICODE_MAX_BYTES = 2_000_000


def _scan_hidden_unicode(file: str) -> tuple[int, str]:
    if not common.flag_enabled("UNICODE_GUARD"):
        return 0, ""
    try:
        p = Path(file)
        if not p.is_file() or p.stat().st_size > _UNICODE_MAX_BYTES:
            return 0, ""
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, ""  # 読めない/バイナリは対象外(hook を壊さない)
    hits: list[tuple[int, int]] = []
    for ln, line in enumerate(text.splitlines(), 1):
        for m in common.HIDDEN_UNICODE.finditer(line):
            hits.append((ln, ord(m.group())))
            if len(hits) >= 8:
                break
        if len(hits) >= 8:
            break
    if not hits:
        return 0, ""
    detail = ", ".join(f"L{ln}:U+{cp:04X}" for ln, cp in hits)
    return 2, (f"⚠ 不可視/双方向の制御文字を検出: {os.path.basename(file)} — {detail}\n"
               "隠し指示の注入面の可能性。意図的でなければ除去を(誤検知なら UA_UNICODE_GUARD=0)。")


# ----------------------------------------------- ua-audit auto-fire ------------
# ultra-ai 自身の設定面を編集したら、決定論ゼロトークンの自己監査(ua_audit)を自動で走らせ、
# FAIL/UNKNOWN だけ surface する(PASS 無音)。on-demand の `/ua-check` は別途残る。
# 「設定を変えたら監査を忘れて死ぬ」を防ぐ auto-fire 層。`UA_AUDIT=0` で無効化。

_AUDIT_TRIGGER_NAMES = {"settings.json", "settings.local.json", "CLAUDE.md", ".mcp.json"}
_AUDIT_TRIGGER_DIRS = ("hooks", "agents", "skills", "commands", "workflows", "rules")


def _is_config_surface(file: str, config_dir: Path) -> bool:
    """編集ファイルが ultra-ai 自身の設定面(config_dir 配下の監査対象)か。外/不能なら False。"""
    try:
        rel = Path(file).resolve().relative_to(config_dir.resolve())
    except Exception:
        return False
    parts = rel.parts
    if len(parts) == 1 and rel.name in _AUDIT_TRIGGER_NAMES:
        return True
    return bool(parts) and parts[0] in _AUDIT_TRIGGER_DIRS


def _maybe_audit(file: str) -> tuple[int, str]:
    """設定面編集なら ua-audit を走らせ FAIL/UNKNOWN だけ surface(PASS 無音・ゼロトークン)。"""
    if not common.flag_enabled("AUDIT"):
        return 0, ""
    cdir = common.config_dir()
    if not _is_config_surface(file, cdir):
        return 0, ""
    try:
        import ua_audit
        res = ua_audit.audit(cdir)
    except Exception:
        return 0, ""  # hook は安全側へ縮退(監査の失敗で編集 lane を壊さない)
    if res["overall"] in (common.FAIL, common.UNKNOWN):
        return 2, (f"⚠ ua-audit({os.path.basename(file)} を編集 → 自己監査):\n"
                   + ua_audit.format_report(res))
    return 0, ""  # PASS -> 無音


def process(payload: dict, runner=common.run_cmd) -> tuple[int, str]:
    """Core logic (pure enough to unit-test). Returns (exit_code, stderr_message)."""
    cwd = common.hook_cwd(payload)
    sid = payload.get("session_id")
    file = common.edited_file(payload)
    if not file:
        return 0, ""
    # mark the session dirty (a hint for the Stop gate; git status is the truth)
    try:
        mark_dirty(cwd, sid)
    except Exception:
        pass
    # security: 隠し制御文字スキャン(全拡張子・最優先)。検出で即 surface。
    code, msg = _scan_hidden_unicode(file)
    if code == 2:
        return code, msg
    # source files: 先に lint レーン(壊れたコードの監査は二次)。FAIL なら即 surface。
    if common.is_source_file(file):
        root = common.project_root(cwd)
        cmd = lint_command(root)
        if cmd:
            timeout = int(detect.verify_config(root).get("lint_timeout_seconds", 60))
            state, output = runner(build_lint(cmd, file), root, timeout=timeout)
            if state == common.FAIL:
                header = f"✗ lint で自動修正できない問題: {os.path.basename(file)}:"
                return 2, header + "\n" + common.condense(output, limit=20)
    # 設定面の編集なら自己監査(source/非source 問わず・lint PASS のあと)
    return _maybe_audit(file)


def main() -> int:
    code, msg = process(common.read_hook_input())
    if code == 2 and msg:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
