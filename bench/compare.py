#!/usr/bin/env python3
"""bench/compare.py — 2つのセッション transcript を cost-weighted(コストで重み付け)で比較する A/B 採点器。

ua-bench は「学習レイヤ ON/OFF を同一タスクで回し、どちらが安く・手戻り少なく済んだか」を見る道具。
headless(`claude -p`)で Stop hook が確実に発火する保証が**無い**(公式 docs 未保証。`--bare` は hook を
スキップ、`--init`/`--init-only` だけが hook を明示起動)。よって **タスク実行は in-session 手動**
(control: `UA_AUTOAPPLY=0` / treatment: 既定)とし、本スクリプトは **transcript から機械的に(乱数を使わず)採点**する。
transcript は hook の有無に関わらず必ず書かれるので、`metrics.py` を再利用して安定に集計できる。

**計測はゲートにしない**(監視・洞察用)。小さいほど良い指標(コスト/ターン/peak context)と
大きいほど良い指標(cache_read=再利用が多いほど安い)を区別して「どちらが良いか」を出す。

使い方: `python3 bench/compare.py <control.jsonl> <treatment.jsonl>`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "claude-home" / "hooks"))
import metrics  # noqa: E402

# (label, summarize から値を取る関数, lower_is_better)
_METRICS = [
    ("cost_weighted_total", lambda s: s["total"]["weighted_cost"], True),
    ("cost_weighted_main", lambda s: s["main"]["weighted_cost"], True),
    ("turns_main", lambda s: s["turns_main"], True),
    ("peak_main_context", lambda s: s["peak_main_context"], True),
    ("cache_read_total", lambda s: s["total"]["cache_read"], False),
]


def _better(control, treatment, lower_is_better: bool) -> str:
    """control と treatment のどちらが良いかを返す。
    lower_is_better=True なら小さい方が勝ち(コスト/ターン/peak context)、
    False なら大きい方が勝ち(cache_read=再利用が多いほど安い)。同値は "tie"。"""
    if control == treatment:
        return "tie"
    treatment_wins = (treatment < control) if lower_is_better else (treatment > control)
    return "treatment" if treatment_wins else "control"


def compare(control_path: str, treatment_path: str) -> list[dict]:
    c, t = metrics.summarize(control_path), metrics.summarize(treatment_path)
    out = []
    for label, get, lib in _METRICS:
        cv, tv = get(c), get(t)
        out.append({"metric": label, "control": cv, "treatment": tv,
                    "delta": round(tv - cv, 6), "better": _better(cv, tv, lib)})
    return out


def format_table(rows: list[dict]) -> str:
    head = f"{'metric':<20} {'control':>14} {'treatment':>14} {'delta':>12}  better"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['metric']:<20} {r['control']:>14} {r['treatment']:>14} "
                     f"{r['delta']:>12}  {r['better']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: compare.py <control_transcript.jsonl> <treatment_transcript.jsonl>",
              file=sys.stderr)
        return 2
    print(format_table(compare(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
