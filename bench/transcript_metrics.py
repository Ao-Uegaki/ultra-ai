#!/usr/bin/env python3
"""bench/transcript_metrics.py — transcript から指標を決定論で抽出する共有モジュール。

`ab_run`(実行直後に結果へ inline 格納)と `ab_report`(集計時の fallback)の両方が使う。
cost/turns/peak/cache は `compare._METRICS`、コード書き直し(手戻り)は本モジュールで数える。
**API を呼ばない・transcript を読むだけ**(決定論)。

inline 格納のおかげで結果 jsonl が自己完結し、`ab_run` は run 後に transcript を消せる
(=副産物を溜めない)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "claude-home" / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare  # noqa: E402  (bench/compare.py — _METRICS を流用)
import metrics  # noqa: E402  (claude-home/hooks/metrics.py — summarize)

# transcript 上で「コードを書き直した」と数える tool。
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# コード書き直し(手戻り)指標: (label, lower_is_better)。
CHURN_METRICS = (("n_edits", True), ("reedit", True))


def churn_of(transcript_path: str) -> dict:
    """transcript から「コードの書き直し(手戻り)」を決定論で数える(API 不要)。

    - `n_edits` = Edit/Write/MultiEdit/NotebookEdit の tool_use 総数。
    - `reedit`  = sum(max(0, 同一 file_path の編集回数 - 1))(=初回を超える同一ファイルの書き直し)。

    transcript が読めない/壊れている行は安全に無視する(0 を返す)。
    """
    n_edits = 0
    per_file: dict[str, int] = {}
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"n_edits": 0, "reedit": 0}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _EDIT_TOOLS:
                continue
            n_edits += 1
            inp = block.get("input")
            fp = inp.get("file_path") if isinstance(inp, dict) else None
            if fp:
                per_file[fp] = per_file.get(fp, 0) + 1
    reedit = sum(max(0, c - 1) for c in per_file.values())
    return {"n_edits": n_edits, "reedit": reedit}


def metrics_of(summary: dict) -> dict:
    """summarize() の戻りから compare._METRICS の値を取り出す(欠損は None)。"""
    out = {}
    for label, get, _ in compare._METRICS:
        try:
            out[label] = get(summary)
        except Exception:
            out[label] = None
    return out


def extract(transcript_path: str, summarize=metrics.summarize) -> dict:
    """transcript から inline 用の指標 dict(cost/turns/peak/cache + churn)を作る。

    summarize は注入可能(テストは合成 transcript を渡す)。
    """
    out = metrics_of(summarize(transcript_path))
    out.update(churn_of(transcript_path))
    return out
