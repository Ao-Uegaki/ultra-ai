#!/usr/bin/env python3
"""learn_capture.py — UserPromptSubmit hook: 訂正様プロンプトを学習候補として捕捉。

決定論・ゼロトークン。プロンプトが「直前の挙動の訂正」らしいときだけ、候補を state に1行 append する。
**注入は一切しない**(= capture。fire ではない)。`UA_AUTOAPPLY` OFF では完全に no-op(挙動不変)。
マーカー無し=何もしない(allow-on-uncertainty)。hook は決して session に例外を投げない。

終了コード: 常に 0(stdout に何も出さない=プロンプトに文脈を注入しない)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import learning  # noqa: E402


def process(payload: dict) -> dict | None:
    """純判定(テスト可能)。訂正らしければ候補 dict、そうでなければ None。

    フラグ OFF のときは常に None(=何も捕捉しない)。
    """
    if not common.autoapply_enabled():
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not learning.looks_like_correction(prompt):
        return None
    if learning.looks_like_noise(prompt):
        return None  # harness/システム断片を学習候補にしない(allow-on-uncertainty)
    return {
        "source": "correction",
        "text": prompt.strip()[:500],
        "branch": common.git_branch(common.hook_cwd(payload)),
    }


def main() -> int:
    try:
        payload = common.read_hook_input()
        cand = process(payload)
        if cand:
            cwd = common.hook_cwd(payload)
            common.append_jsonl_capped(
                common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES,
                cand, cap=500)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
