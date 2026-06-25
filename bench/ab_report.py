#!/usr/bin/env python3
"""bench/ab_report.py — N アーム A/B の結果(results jsonl)を集計し markdown レポート化する。

`ab_run.py` が出す result 行(1 run = 1行: task / arm / success / transcript / ...)を読み、
アームごとに **pass@k(k回中 ≥1 成功)・pass-rate(成功割合)** と、各 run の transcript を
`metrics.summarize` で採点した **cost/turns/peak context** + **コード書き直し(手戻り)** の平均を出す。
指標定義と「どちらが良いか」は `bench/compare.py` の `_METRICS` を流用する。

アームは control(素) / control-xhigh(素+effortLevel:xhigh) / treatment(ultra-ai)。
比較列は **vs control=総合効果(仕組み+effort)** と **vs control-xhigh=仕組みのみ(effort を揃えた差)**。

決定論・API を呼ばない(transcript を読むだけ)。**計測はゲートにしない**(監視・洞察用)。
使い方: `python3 bench/ab_report.py <results.jsonl>`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "claude-home" / "hooks"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare  # noqa: E402  (bench/compare.py — _METRICS, _better を流用)
import metrics  # noqa: E402  (claude-home/hooks/metrics.py — summarize)
import transcript_metrics  # noqa: E402  (churn_of/metrics_of/extract を ab_run と共有)

# 後方互換: 旧来 ab_report.churn_of / metrics_of を参照する呼び出し・テスト向けに再エクスポート。
churn_of = transcript_metrics.churn_of
metrics_of = transcript_metrics.metrics_of

# 表示順(存在するアームだけ出す)。未知のアームは末尾に付く。
_ARM_ORDER = ("control", "control-xhigh", "treatment")
# コード書き直し(手戻り)指標: (label, lower_is_better)。共有モジュールの定義を流用。
_CHURN_METRICS = transcript_metrics.CHURN_METRICS


def pass_at_k(successes: list[bool]) -> bool:
    """k 回中に少なくとも1回成功したか(ECC eval-harness の pass@k)。"""
    return any(successes)


def pass_rate(successes: list[bool]) -> float:
    """成功割合(0.0〜1.0)。"""
    return round(sum(1 for s in successes if s) / len(successes), 3) if successes else 0.0


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 6) if xs else None


def load_results(path: str) -> list[dict]:
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    return rows


def _summarize_bucket(bucket: dict) -> dict:
    succ = bucket["success"]
    metric_means = {}
    for label, _, _ in compare._METRICS:
        metric_means[label] = _mean([m.get(label) for m in bucket["metrics"]])
    for label, _ in _CHURN_METRICS:
        metric_means[label] = _mean([m.get(label) for m in bucket["metrics"]])
    return {"runs": len(succ), "pass_rate": pass_rate(succ),
            "pass_at_k": pass_at_k(succ), "metrics": metric_means}


def aggregate(results: list[dict], summarize=metrics.summarize) -> dict:
    """results を (task, arm) で束ね、pass@k/pass-rate と指標平均(churn 含む)を出す。

    summarize は注入可能(テストは合成 transcript を渡す)。transcript 欠損 run は成否だけ数える。
    """
    by_task: dict = {}
    for r in results:
        arm, task = r.get("arm"), r.get("task")
        a = by_task.setdefault(task, {}).setdefault(arm, {"success": [], "metrics": []})
        a["success"].append(bool(r.get("success")))
        inline = r.get("metrics")
        if isinstance(inline, dict):
            a["metrics"].append(inline)          # ab_run 格納の自己完結 metrics を優先
            continue
        tp = r.get("transcript")
        if tp:
            try:
                a["metrics"].append(transcript_metrics.extract(tp, summarize))
            except Exception:
                pass

    tasks = {task: {arm: _summarize_bucket(b) for arm, b in arms.items()}
             for task, arms in by_task.items()}

    overall: dict = {}
    arms_seen = {r.get("arm") for r in results}
    for arm in arms_seen:
        merged = {"success": [], "metrics": []}
        for arms in by_task.values():
            if arm in arms:
                merged["success"] += arms[arm]["success"]
                merged["metrics"] += arms[arm]["metrics"]
        overall[arm] = _summarize_bucket(merged)
    return {"tasks": tasks, "overall": overall}


def _metric_specs() -> list[tuple]:
    """表に出す指標の順序付きリスト: (label, lower_is_better, kind)。kind='top'|'mean'。"""
    specs: list[tuple] = [("pass_rate", False, "top"), ("pass_at_k", False, "top")]
    specs += [(label, lower, "mean") for label, _, lower in compare._METRICS]
    specs += [(label, lower, "mean") for label, lower in _CHURN_METRICS]
    return specs


def _metric_value(bucket: dict, label: str, kind: str):
    return bucket.get(label) if kind == "top" else bucket["metrics"].get(label)


def _verdict(base, treat, lower_is_better: bool, base_label: str) -> str:
    """treatment が base(control / control-xhigh)に対し良いか。"""
    if base is None or treat is None:
        return "n/a"
    b, t = (int(base), int(treat)) if isinstance(base, bool) else (base, treat)
    if b == t:
        return "tie"
    treatment_wins = (t < b) if lower_is_better else (t > b)
    return "treatment" if treatment_wins else base_label


def _render(rows: list[list]) -> str:
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    def fmt(r):
        return "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([fmt(rows[0]), sep] + [fmt(r) for r in rows[1:]])


def _format_table(arms_summ: dict) -> str:
    if not arms_summ:
        return "(結果なし)"
    present = [a for a in _ARM_ORDER if a in arms_summ]
    present += [a for a in arms_summ if a not in present]  # 未知アームは末尾
    comparisons = []  # (header, base_arm)
    if "treatment" in present and "control" in present:
        comparisons.append(("vs control", "control"))
    if "treatment" in present and "control-xhigh" in present:
        comparisons.append(("vs control-xhigh", "control-xhigh"))
    header = ["metric"] + present + [h for h, _ in comparisons]
    rows: list[list] = [header]
    for label, lower, kind in _metric_specs():
        vals = {a: _metric_value(arms_summ[a], label, kind) for a in present}
        cells = [label] + ["-" if vals[a] is None else vals[a] for a in present]
        for _, base in comparisons:
            cells.append(_verdict(vals.get(base), vals.get("treatment"), lower, base))
        rows.append(cells)
    return _render(rows)


def format_report(agg: dict) -> str:
    out = ["# A/B bench report — control(素) / control-xhigh(素+xhigh) / treatment(ultra-ai)",
           "",
           "> directional(小N・高分散)。pass_rate/pass_at_k=higher / "
           "cost·turns·peak·n_edits·reedit=lower / cache_read=higher。",
           "> vs control=総合効果(仕組み+effort) / vs control-xhigh=仕組みのみ(effort を揃えた差)。",
           "", "## Aggregate(全タスク合算)", "",
           _format_table(agg.get("overall", {})), "", "## Per task", ""]
    for task in sorted(agg["tasks"]):
        out.append(f"### {task}")
        out.append(_format_table(agg["tasks"][task]))
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: ab_report.py <results.jsonl>", file=sys.stderr)
        return 2
    print(format_report(aggregate(load_results(argv[0]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
