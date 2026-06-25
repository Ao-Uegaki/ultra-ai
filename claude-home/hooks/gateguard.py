#!/usr/bin/env python3
"""gateguard.py — PreToolUse(Edit|Write|MultiEdit) の事実確認の関門(ゼロトークン)。

ECC の gateguard-fact-force を ultra-ai 流に要点化。harness は「対象ファイル自体の read」は
強制するが、その**周辺(=依存)の調査**は強制しない。このゲートはコードファイルの
**セッション初回の編集**を一度だけ deny し、このファイルを読み込んでいる箇所/影響する API/
データの構造・形式/ユーザー指示 の提示を要求する → 提示してそのまま再実行すれば通す(checked 済みになる)。

設計(憲法準拠・検証ループの第一級の層):
  - 「確かめた事実だけを本命モデルへ」「編集前に Explore で地図化」を**構造的に強制**する。
  - exit 2 + stderr でブロック(shell_guard と同じ PreToolUse 規約)。通過時は exit 0・無出力
    (fast lane と cached prefix にバイトを足さない)。
  - 一度きり: file は per-session で1回だけ gate(checked に入れば以後素通り=反復で苛立たせない)。
  - denial dampening: 同一セッションで規定回数(既定3)を超えたら1行版に圧縮し、近似テキストの
    連投による反復ループ・文脈肥大を避ける(ECC #2142 の要点圧縮)。
  - subagent はバイパス(親セッションが既に gate を通過済み)。
  - 対象はコードファイルのみ(.md/.json/設定/state/plan は通す=友好的・低摩擦)。
  - kill switch: UA_FACTGATE=0 / ultra-ai-safe(disableAllHooks)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STATE_FACTGATE = "factgate.json"   # session-scoped: {"checked": [...paths...], "denials": N}

# fact-gate を当てる「コードファイル」拡張子(lint より広い: 依存/API/schema が問題になる言語)。
CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".rb", ".php", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
}

_GATE_TOOLS = {"Edit", "Write", "MultiEdit"}


def _in_subagent(payload: dict) -> bool:
    """subagent 由来の呼び出しか(親セッションが既に first-touch gate を通過済み)。"""
    for k in ("agent_id", "agentId", "parent_tool_use_id", "parentToolUseId"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return True
    return False


def _is_code_file(path: str | None) -> bool:
    return bool(path) and Path(path).suffix in CODE_EXTS


def _state_path(cwd: str, sid: str | None) -> Path:
    return common.session_state_dir(cwd, sid) / STATE_FACTGATE


def _edit_msg(file: str) -> str:
    return (f"⚑ ultra-ai 事実確認の関門: {file} を編集する前に、次を提示してください(本セッション初回):\n"
            "  1. このファイルを読み込んでいる箇所(import/require している箇所を Grep で確認)\n"
            "  2. この変更が影響する公開関数/クラス/インターフェース\n"
            "  3. データを読み書きするなら、そのフィールド名・データの構造・形式(要点のみ)\n"
            "  4. いま対応中のユーザー指示を一言で\n"
            "提示したら、同じ編集をそのまま再実行してください。"
            "(無効化: UA_FACTGATE=0 / 全停止: ultra-ai-safe)")


def _write_msg(file: str) -> str:
    return (f"⚑ ultra-ai 事実確認の関門: {file} を新規作成する前に、次を提示してください(本セッション初回):\n"
            "  1. この新ファイルを呼び出す既存の file:line\n"
            "  2. 同じ役割の既存ファイルが無いこと(Glob で確認)\n"
            "  3. データを読み書きするなら、そのフィールド名・データの構造・形式(要点のみ)\n"
            "  4. いま対応中のユーザー指示を一言で\n"
            "提示したら、同じ作成をそのまま再実行してください。"
            "(無効化: UA_FACTGATE=0 / 全停止: ultra-ai-safe)")


def _condensed_msg(tool: str, file: str, n: int) -> str:
    act = "作成" if tool == "Write" else "編集"
    return (f"⚑ ultra-ai 事実確認の関門 (本セッション {n} 回目): {file} の初回{act}。"
            "このファイルを読み込んでいる箇所/影響する API/データの構造・形式/ユーザー指示 を"
            "一言で述べてから再実行。(UA_FACTGATE=0 で無効)")


def process(payload: dict) -> tuple[int, str]:
    """判定本体(ConfigDir を差し替えればテスト可能)。Returns (exit_code, stderr_message)。

    exit 2 = ブロック(stderr をモデルへ surface)/ exit 0 = 通過(無出力)。
    """
    if not common.flag_enabled("FACTGATE"):
        return 0, ""
    tool = payload.get("tool_name") or ""
    if tool not in _GATE_TOOLS or _in_subagent(payload):
        return 0, ""
    file = (payload.get("tool_input") or {}).get("file_path")
    if not _is_code_file(file):
        return 0, ""
    cwd = common.hook_cwd(payload)
    sid = payload.get("session_id")
    sp = _state_path(cwd, sid)
    state = common.read_json(sp)
    checked = set(state.get("checked") or [])
    if file in checked:
        return 0, ""   # 既に fact を述べた file → 素通り(retry はここを通る)
    # 初回タッチ: checked に入れ denial を数えて deny する(同じ操作の再実行は上で素通りになる)
    checked.add(file)
    denials = int(state.get("denials") or 0) + 1
    common.write_json_atomic(sp, {"checked": sorted(checked), "denials": denials})
    if denials > common.env_int("FACTGATE_FULL", 3):
        return 2, _condensed_msg(tool, file, denials)
    return 2, (_write_msg(file) if tool == "Write" else _edit_msg(file))


def main() -> int:
    code, msg = process(common.read_hook_input())
    if code == 2 and msg:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
