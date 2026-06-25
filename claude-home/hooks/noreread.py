#!/usr/bin/env python3
"""noreread.py — PostToolUse(Read|Edit|…) の read 規律 hook(ゼロトークン・既定 ON)。

セッション中に積もる"証明できる無駄"を、信号を一切削らずに抑える(compaction を遅らせる):
  C1(lossless・機械): 同一 file を **間に編集なし** で再 Read したら、出力を
      `updatedToolOutput` で1行ポインタに差し替える(内容は既に context 内なので無損失)。
      escape hatch: 直前に抑制した同一 Read は次に素通り(全文を取り戻せる=常に回復可能)。
  C3(nudge): 大きい Read 出力には「非編集なら Explore に委譲 / Read の limit を使う」を
      `additionalContext` で一度だけ提案(その場限り・file ごと dedup)。委譲は強制しない。
編集(Edit/Write/…)が起きたら当該 file の read 記録をクリア(変更後の Read は無駄でない)。

設計(憲法準拠・monitor と同層の transient ナッジ):
  - session-scoped state(noreread.json)。例外は握りつぶす(hook を絶対に止めない)。
  - C1 と C3 は排他(全文を返す Read のときだけ C3 を出す)→ 返す JSON は1つ。
  - kill switch: C1=UA_NOREREAD=0 / C3=UA_BIGREAD_HINT=0 / ultra-ai-safe(disableAllHooks)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STATE = "noreread.json"   # session-scoped: {"seen":[keys], "just":<key|null>, "hinted":[paths]}
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEFAULT_BIG_CHARS = 8000
_SEEN_CAP = 500           # marathon session でも state を小さく保つ(リング)


def _read_key(ti: dict) -> str:
    """Read の同一性キー(file_path + 範囲)。range 違いは別読み=無駄ではない。"""
    return "%s|%s|%s" % (ti.get("file_path"), ti.get("offset"), ti.get("limit"))


def _response_len(resp) -> int:
    """tool_response の文字長(str / list[{text}] / dict を吸収・閾値判定用の近似)。"""
    if isinstance(resp, str):
        return len(resp)
    if isinstance(resp, list):
        return sum(len(x.get("text", "")) for x in resp if isinstance(x, dict))
    if isinstance(resp, dict):
        t = resp.get("text") or resp.get("content")
        return len(t) if isinstance(t, str) else len(json.dumps(resp, default=str))
    return 0


def _pointer() -> str:
    return ("[ultra-ai] 本セッションで読み込み済み(変更なし)。先の Read 結果を参照してください。"
            "内容が必要ならもう一度 Read すると全文を返します。(UA_NOREREAD=0 で無効)")


def _bigread_hint() -> str:
    return ("📖 大きな Read です。これが編集対象でないなら、Explore サブエージェントに委譲する"
            "(全文は subagent 側で読み、main には結論+file:line だけ返す)か、Read の offset/limit で"
            "必要な範囲に絞ると context を節約できます。(任意・UA_BIGREAD_HINT=0 で無効)")


def _big_chars(root: str) -> int:
    """大 Read 判定の閾値(char)。`.ultra-ai.toml [ua-noreread]` か UA_BIGREAD_CHARS で可変。"""
    try:
        import detect
        user = detect.load_project_config(root).get("ua-noreread")
        if isinstance(user, dict) and isinstance(user.get("big_read_chars"), int):
            return user["big_read_chars"]
    except Exception:
        pass
    return common.env_int("BIGREAD_CHARS", DEFAULT_BIG_CHARS)


def _hso(field: str, value: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", field: value}}


def process(payload: dict) -> dict | None:
    """state を更新し、出すべき hookSpecificOutput(updatedToolOutput / additionalContext)を返す。"""
    tool = payload.get("tool_name") or ""
    cwd = common.hook_cwd(payload)
    sid = payload.get("session_id")
    sp = common.session_state_dir(cwd, sid) / STATE
    state = common.read_json(sp)
    seen = list(state.get("seen") or [])
    just = state.get("just")
    hinted = set(state.get("hinted") or [])
    ti = payload.get("tool_input") or {}

    # 編集 → 当該 file の read 記録をクリア(変更後の Read は無駄でないので素通りさせる)
    if tool in _EDIT_TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path")
        if fp:
            pref = str(fp) + "|"
            seen = [k for k in seen if not k.startswith(pref)]
            if isinstance(just, str) and just.startswith(pref):
                just = None
            hinted.discard(fp)
            common.write_json_atomic(sp, {"seen": seen, "just": just, "hinted": sorted(hinted)})
        return None

    if tool != "Read":
        return None
    fp = ti.get("file_path")
    if not fp:
        return None
    key = _read_key(ti)

    out = None
    if common.flag_enabled("NOREREAD"):
        if key not in seen:
            seen.append(key)
            just = None
        elif key == just:
            just = None                       # escape hatch: 直前に抑制した同一 Read → 全文を返す
        else:
            just = key                        # 変更なしの再 Read → ポインタ化(lossless)
            out = _hso("updatedToolOutput", _pointer())
    elif key not in seen:                      # C1 無効でも seen は温めておく(再有効化に備える)
        seen.append(key)

    # C3: 全文を返す Read のときだけ、大きければ委譲 nudge(file ごと一度・その場限り)
    if out is None and common.flag_enabled("BIGREAD_HINT") and fp not in hinted:
        if _response_len(payload.get("tool_response")) > _big_chars(common.project_root(cwd)):
            hinted.add(fp)
            out = _hso("additionalContext", _bigread_hint())

    common.write_json_atomic(sp, {"seen": seen[-_SEEN_CAP:], "just": just, "hinted": sorted(hinted)})
    return out


def main() -> int:
    try:
        out = process(common.read_hook_input())
    except Exception:
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
