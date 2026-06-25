#!/usr/bin/env python3
"""monitor.py — PostToolUse の proactive 観測ナッジ(ambient・既定 ON)。

ECC の ecc-context-monitor / suggest-compact を ultra-ai 流に要点圧縮。毎 PostToolUse で
session-scoped な bridge(直近ツールのリング + 編集ファイル集合 + ツール呼び出し数)を
増分更新し、閾値超過の「気づき」だけを `additionalContext` で柔らかく注入する。

設計(憲法: 観測ナッジは measure-first の対象外=既定 ON。毎回同じ文章 規律は resume 注入にのみ適用):
  - これは毎回 fire する学習注入ではなく、**閾値超過時だけの transient ナッジ**。live 数値を含んでよい。
  - **内容ハッシュで dedup**(call counter でなく)。同じ警告は再注入しない・深刻度が上がった時だけ再掲。
  - 検知: ① tool ループ(同一 (tool,input) が直近 RING 回で LOOP 回以上=詰まり)
          ② scope creep(編集ファイル数 超過)③ compact 提案(tool 数の節目)
          ④ context 逼迫(直近 Stop の metrics.json の peak context vs window)。
  - 重い transcript 再パースはしない(ループ/スコープ/数は payload から増分・context は metrics.json 参照)。
  - 通過時は無出力・exit 0。例外は握りつぶす(hook を止めない)。
  - kill switch: UA_MONITOR=0 / ultra-ai-safe(disableAllHooks)。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import detect  # noqa: E402
import notify  # noqa: E402  (活動再開で待機 record を解除=集約通知の誤検知を止める)

STATE_BRIDGE = "monitor-bridge.json"   # session-scoped: recent/files/tools/last_warn/last_sev
RING = 5                               # 直近ツールのリングバッファ長
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

DEFAULTS = {
    "loop": 3,            # 同一 (tool,input) が直近 RING 回で何回以上ならループ警告
    "files": 20,          # 編集ファイル数がこれを超えたら scope 警告
    "compact_at": 50,     # ツール呼び出し数の最初の compact 提案
    "compact_every": 25,  # 以後この間隔で再提案
    "context_window": 200_000,  # context% の分母(モデルの window)
    "context_warn_pct": 75,
    "context_crit_pct": 90,
}


def cfg(root: str) -> dict:
    """`.ultra-ai.toml [ua-monitor]` で閾値を上書き(未設定は DEFAULTS)。"""
    out = dict(DEFAULTS)
    user = detect.load_project_config(root).get("ua-monitor")
    if isinstance(user, dict):
        for k, v in user.items():
            if k in out and isinstance(v, int):
                out[k] = v
    return out


def content_hash(tool_input: dict) -> str:
    """tool_input の安定ハッシュ(同一ツール同一入力の繰り返し=ループの判定キー)。"""
    try:
        blob = json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:
        blob = str(tool_input)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:12]


def _loop_msg(tool: str) -> str:
    # カウントは入れない(毎回同じ文字列 → dedup が効く=詰まりを一度だけ警告)。
    return (f"⟳ ループ警告: {tool} を同じ入力で繰り返し実行しています。"
            "詰まっている可能性。別アプローチか前提の再確認を。")


def _scope_msg(n: int) -> str:
    return f"▢ スコープ警告: 本セッションで {n} ファイルを編集。変更が散らばっていないか確認を。"


def _compact_msg(n: int) -> str:
    return f"◧ {n} ツール呼び出し。フェーズの区切りなら /compact を検討。"


def _context_msg(pct: int, tok: int, win: int, crit: bool) -> str:
    head = "context 逼迫(critical)" if crit else "context 警告"
    return (f"◴ {head}: ~{pct}% ({tok // 1000}k/{win // 1000}k)。"
            "区切りで /compact か /ua-checkpoint を検討。")


# --- 言語別 reviewer の review-hint(D): 編集拡張子 → 推奨 reviewer。1セッション1言語1回。
_REVIEWER_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "react", ".jsx": "react",
    ".go": "go", ".rs": "rust",
}


def _review_hint_msg(lang: str) -> str:
    # 1言語1回(reviewed_langs で dedup)なので固定文でよい=毎回同じ文章。
    return (f"🔍 {lang} を編集中。まとまったら `{lang}-reviewer` サブエージェントに diff を渡すと、"
            "言語特有の観点で要点に絞ったレビューが得られます(任意・UA_REVIEW_HINT=0 で無効)。")


def _review_hint(tool: str, fp, reviewed: set) -> str | None:
    """編集拡張子が言語別 reviewer に対応し、本セッション未提案なら一度だけ hint を返す。
    reviewed を破壊的に更新する(呼び出し側が bridge に保存)。"""
    if not common.flag_enabled("REVIEW_HINT"):
        return None
    if tool not in _EDIT_TOOLS or not isinstance(fp, str) or not fp:
        return None
    lang = _REVIEWER_BY_EXT.get(Path(fp).suffix)
    if not lang or lang in reviewed:
        return None
    reviewed.add(lang)
    return _review_hint_msg(lang)


def evaluate(bridge: dict, context_tokens: int, conf: dict) -> list[tuple]:
    """bridge と context から (severity, message) を算出(純粋・テスト可能)。severity 降順。"""
    out: list[tuple] = []
    recent = bridge.get("recent") or []
    if recent:
        last = recent[-1]
        cnt = recent.count(last)
        if cnt >= conf["loop"]:
            out.append((2, _loop_msg(last[0] if last else "tool")))
    nfiles = len(bridge.get("files") or [])
    if nfiles > conf["files"]:
        out.append((2, _scope_msg(nfiles)))
    tools = int(bridge.get("tools") or 0)
    at, every = conf["compact_at"], conf["compact_every"]
    if tools == at or (tools > at and every > 0 and (tools - at) % every == 0):
        out.append((1, _compact_msg(tools)))
    win = conf["context_window"]
    if context_tokens > 0 and win > 0:
        pct = round(context_tokens / win * 100)
        if pct >= conf["context_crit_pct"]:
            out.append((3, _context_msg(pct, context_tokens, win, crit=True)))
        elif pct >= conf["context_warn_pct"]:
            out.append((2, _context_msg(pct, context_tokens, win, crit=False)))
    return sorted(out, key=lambda w: -w[0])


def _context_metrics(cwd: str, sid: str | None) -> tuple[int, int]:
    """直近 Stop の metrics.json から (last, peak) context(tokens)を読む(無ければ (0, 0))。

    last = 現在の文脈圧(警告判定の分子)。peak ではなく last を使う: peak_main_context は履歴最大で
    /compact 後も下がらず過大な context% を誤警告するため。古い metrics.json には last が無いので peak へ縮退。
    peak = 履歴最大(窓 backstop の evidence: peak が分母を超えた=真の窓はより大きい)。
    """
    data = common.read_json(common.session_state_dir(cwd, sid) / common.STATE_METRICS)
    try:
        peak = int(data.get("peak_main_context") or 0)
        last = int(data.get("last_main_context") or peak or 0)
        return last, peak
    except Exception:
        return 0, 0


_WINDOW_TIERS = (200_000, 1_000_000)   # 既知の context 窓段(observed が下段を超えたら上段が真の窓)


def _resolve_window(cwd: str, sid: str | None, base: int, peak: int) -> int:
    """context% の分母(モデルの実 window)を解決する。優先順:
       ① 明示 override(UA_CONTEXT_WINDOW・または base が既定 200k と異なる=toml 指定)→ そのまま尊重(backstop しない)。
       ② statusline が検出した model.json の context_window(1M/200k)。
       ③ 既定 200k。
       ②③ には peak-evidence backstop: 実測 peak が解決窓をほぼ超えていたら既知 tier へ繰り上げ
          (statusline 無効でも「peak が窓超=窓はより大きい」という確かめた事実で自己補正)。
    """
    env = common.env_int("CONTEXT_WINDOW", 0)
    if env > 0:
        return env
    if base != DEFAULTS["context_window"]:
        return base
    win = base
    detected = common.read_json(common.session_state_dir(cwd, sid) / common.STATE_MODEL).get("context_window")
    if isinstance(detected, int) and detected > 0:
        win = detected
    for tier in _WINDOW_TIERS:
        if tier > win and peak > win * 0.95:
            win = tier
    return win


def process(payload: dict) -> str | None:
    """bridge を更新し、出すべき additionalContext(なければ None)を返す。"""
    # 活動再開=待機解除(承認に答えて tool が走った)。UA_MONITOR と独立に registry を衛生化。
    notify.note_active(payload.get("session_id"))
    if not common.flag_enabled("MONITOR"):
        return None
    tool = payload.get("tool_name") or ""
    if not tool:
        return None
    cwd = common.hook_cwd(payload)
    sid = payload.get("session_id")
    sp = common.session_state_dir(cwd, sid) / STATE_BRIDGE
    bridge = common.read_json(sp)

    ti = payload.get("tool_input") or {}
    recent = (bridge.get("recent") or [])[-(RING - 1):] + [[tool, content_hash(ti)]]
    files = set(bridge.get("files") or [])
    fp = ti.get("file_path") or ti.get("notebook_path")
    if tool in _EDIT_TOOLS and isinstance(fp, str) and fp:
        files.add(fp)
    reviewed = set(bridge.get("reviewed_langs") or [])
    hint = _review_hint(tool, fp, reviewed)   # reviewed を更新(副作用)
    new = {
        "recent": recent,
        "files": sorted(files),
        "tools": int(bridge.get("tools") or 0) + 1,
        "last_warn": bridge.get("last_warn"),
        "last_sev": int(bridge.get("last_sev") or 0),
        "reviewed_langs": sorted(reviewed),
    }

    last, peak = _context_metrics(cwd, sid)
    conf = cfg(common.project_root(cwd))   # cfg は毎回 fresh dict を返すのでそのまま注入してよい
    conf["context_window"] = _resolve_window(cwd, sid, conf["context_window"], peak)
    warnings = evaluate(new, last, conf)
    emit = None
    if warnings:
        msg = "\n".join(m for _, m in warnings[:2])
        sev = warnings[0][0]
        escalated = sev > new["last_sev"]
        if msg != new["last_warn"] or escalated:
            emit = msg
            new["last_warn"], new["last_sev"] = msg, sev
    else:
        new["last_warn"], new["last_sev"] = None, 0   # 解消したら dedup を解除(再発で再掲)

    common.write_json_atomic(sp, new)
    # review-hint(一度きり)と閾値警告(dedup 済)を合成。hint は last_warn を汚さない。
    parts = [x for x in (hint, emit) if x]
    return "\n".join(parts) if parts else None


def main() -> int:
    emit = process(common.read_hook_input())
    if emit:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": emit}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
