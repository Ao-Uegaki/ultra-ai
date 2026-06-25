#!/usr/bin/env python3
"""metrics.py — トークン会計(計測スパイン)。

このプロジェクトの中核仮説「より高精度・より少トークン」を測れるようにするための、
ゼロトークンの観測ユーティリティ。Stop hook(gate.py)から毎ターン呼ばれ、
当該セッションの transcript を読んで usage を集計し state に書き出す。

重要な構造(実データで確認済み):
  - メインの transcript(`<base>/<session>.jsonl`)の assistant 行は
    `message.usage`(input / output / cache_read / cache_creation〔5m・1h 別〕)と
    `message.model` を持つ。このプロジェクト環境では全行 `isSidechain=false`。
  - subagent の usage は **別ファイル**にある:
      Task 系   : `<base>/<session>/subagents/agent-*.jsonl`
      workflow 系: `<base>/<session>/subagents/workflows/<wf>/agent-*.jsonl`
    各行 `isSidechain=true`・自分の `message.model` を持つ。
  - したがって main / subagent の分離は「同一ファイル内の isSidechain」ではなく
    **ファイルの所在**で行う。main 計測 = 単一ファイルで HARD、
    total = 兄弟 subagent ファイルの集計(ディレクトリ規約依存 = best-effort)。

価格表は claude-api skill 由来(2026-06 時点)。サブスク利用では実課金しないため、
**相対コンパレータ**として使う(Opus ≫ Sonnet ≫ Haiku ≫ cache-read)。

設計ルール: hook を絶対に止めない。例外は握りつぶし、欠損フィールドは 0 とみなす。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

# 1M トークンあたり USD(input/output)。cache write = input×{5m:1.25, 1h:2.0}、
# cache read = input×0.1(claude-api skill のキャッシュ経済より)。
PRICES = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}
_CACHE_WRITE_5M = 1.25
_CACHE_WRITE_1H = 2.0
_CACHE_READ = 0.1
_SAFE_DEFAULT = "claude-opus-4-8"  # 未知モデルは安全側(最も高い)で評価

JOURNAL_CAP = 500  # session-journal を直近 N 行のリングに保つ


def _price(model: str | None) -> dict:
    if model:
        for key, p in PRICES.items():
            if model.startswith(key):
                return p
    return PRICES[_SAFE_DEFAULT]


_COUNT_KEYS = ("input", "output", "cache_read", "cache_creation", "cache_5m", "cache_1h")


def _zero_counts() -> dict:
    return {k: 0 for k in _COUNT_KEYS}


def _new_agg() -> dict:
    return {"by_model": {}, "turns": 0, "peak_context": 0, "last_context": 0,
            "first_ts": None, "last_ts": None}


def _add_usage(agg: dict, model: str | None, usage: dict, ts: str | None) -> None:
    m = agg["by_model"].setdefault(model or "unknown", _zero_counts())
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    cc = int(usage.get("cache_creation_input_tokens") or 0)
    cc_obj = usage.get("cache_creation") or {}
    c5 = int(cc_obj.get("ephemeral_5m_input_tokens") or 0)
    c1 = int(cc_obj.get("ephemeral_1h_input_tokens") or 0)
    m["input"] += inp
    m["output"] += out
    m["cache_read"] += cr
    m["cache_creation"] += cc
    m["cache_5m"] += c5
    m["cache_1h"] += c1
    agg["turns"] += 1
    ctx = inp + cr + cc  # その行の入力文脈量 ≈ ターン時のコンテキスト
    if ctx > agg["peak_context"]:
        agg["peak_context"] = ctx
    if ts:  # ISO8601(Z 付き・ゼロ詰め)なので辞書順比較で時刻順になる
        if agg["first_ts"] is None or ts < agg["first_ts"]:
            agg["first_ts"] = ts
        if agg["last_ts"] is None or ts > agg["last_ts"]:
            agg["last_ts"] = ts
            agg["last_context"] = ctx  # 最新ターンの文脈量(= 現在の文脈圧。peak と違い /compact 後に下がる)


def _merge_into(dst: dict, src: dict) -> None:
    for model, counts in src["by_model"].items():
        m = dst["by_model"].setdefault(model, _zero_counts())
        for k in _COUNT_KEYS:
            m[k] += counts.get(k, 0)
    dst["turns"] += src["turns"]
    dst["peak_context"] = max(dst["peak_context"], src["peak_context"])
    for fld, better in (("first_ts", min), ("last_ts", max)):
        a, b = dst[fld], src[fld]
        if b is not None:
            dst[fld] = b if a is None else better(a, b)


def parse_transcript(path: str) -> dict:
    """1ファイルを集計。`isSidechain` で main / side バケットに分けて返す。

    main / side の判定はここではファイルをまたがない(純集計)。呼び出し側が
    『どのファイルの main / side をどちらへ寄せるか』を決める。
    """
    main, side = _new_agg(), _new_agg()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                bucket = side if o.get("isSidechain") else main
                _add_usage(bucket, msg.get("model"), usage, o.get("timestamp"))
    except Exception:
        pass
    return {"main": main, "side": side}


def discover_subagent_transcripts(main_path: str) -> list[str]:
    """`<base>/<session>.jsonl` に対する `<base>/<session>/subagents/**/agent-*.jsonl`。"""
    try:
        p = Path(main_path)
        sub = p.parent / p.stem / "subagents"
        if not sub.is_dir():
            return []
        return sorted(str(f) for f in sub.rglob("agent-*.jsonl"))
    except Exception:
        return []


def weight_cost(counts: dict, model: str | None) -> float:
    """モデル別単価で 1ファイル分の counts を USD 換算(相対コンパレータ)。"""
    p = _price(model)
    pin, pout = p["input"], p["output"]
    c5 = counts.get("cache_5m", 0)
    c1 = counts.get("cache_1h", 0)
    # cache_creation 合計のうち 5m/1h に内訳が無い分は 5m 扱い(安全側に倒さない=過大評価回避)
    cc_rem = max(0, counts.get("cache_creation", 0) - c5 - c1)
    dollars = (
        counts.get("input", 0) * pin
        + counts.get("output", 0) * pout
        + counts.get("cache_read", 0) * pin * _CACHE_READ
        + (c5 + cc_rem) * pin * _CACHE_WRITE_5M
        + c1 * pin * _CACHE_WRITE_1H
    ) / 1_000_000
    return dollars


def _flatten(agg: dict) -> dict:
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    cost = 0.0
    for model, counts in agg["by_model"].items():
        for k in totals:
            totals[k] += counts.get(k, 0)
        cost += weight_cost(counts, model)
    totals["weighted_cost"] = round(cost, 6)
    totals["turns"] = agg["turns"]
    return totals


def _combine_totals(a: dict, b: dict) -> dict:
    keys = ("input", "output", "cache_read", "cache_creation", "weighted_cost", "turns")
    out = {k: round(a.get(k, 0) + b.get(k, 0), 6) for k in keys}
    return out


def _span_seconds(agg: dict) -> float | None:
    a, b = agg["first_ts"], agg["last_ts"]
    if not a or not b:
        return None
    try:
        from datetime import datetime
        fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((fb - fa).total_seconds(), 1)
    except Exception:
        return None


def summarize(main_path: str) -> dict:
    """main(HARD・単一ファイル)と total(best-effort・兄弟 subagent 集計)を分けて返す。"""
    mt = parse_transcript(main_path)
    main_agg = mt["main"]

    sub_agg = _new_agg()
    _merge_into(sub_agg, mt["side"])  # 同一ファイル内 inline sidechain(本環境では 0)
    for f in discover_subagent_transcripts(main_path):
        ft = parse_transcript(f)
        _merge_into(sub_agg, ft["main"])
        _merge_into(sub_agg, ft["side"])

    main_t = _flatten(main_agg)
    sub_t = _flatten(sub_agg)
    return {
        "main": main_t,
        "subagent": sub_t,
        "total": _combine_totals(main_t, sub_t),
        "peak_main_context": main_agg["peak_context"],
        "last_main_context": main_agg["last_context"],  # 現在の文脈圧(monitor が使う)
        "turns_main": main_agg["turns"],
        "wall_clock_s": _span_seconds(main_agg),
        "last_ts": main_agg["last_ts"],  # transcript 由来(決定論)— journal の ts に使う
        # main は単一ファイルの構造保証(HARD)、subagent は規約依存の集計(best-effort)
        "guarantee": {"main": "hard", "subagent": "best_effort"},
    }


def _guess_transcript(cwd: str, sid: str | None) -> str | None:
    """transcript_path が payload に無いときの再構成(~/.claude/projects/<munged>/<sid>.jsonl)。"""
    if not sid:
        return None
    try:
        munged = cwd.replace("/", "-")
        return str(Path.home() / ".claude" / "projects" / munged / f"{sid}.jsonl")
    except Exception:
        return None


def journal_row(cwd: str, sid: str | None, data: dict) -> dict:
    """1 Stop = 1行の捕捉スパイン。挙動を一切 nudge しない純粋なファクト記録。

    `verified_state` は直近の gate 結果(verification.json)。snapshot は gate.py 冒頭で
    走るため、これは「この Stop に入る時点で最後に分かっていた検証状態」を表す。
    `ts` は transcript 由来(決定論)。git でない場合 branch/head は None。
    """
    vstate = common.read_json(
        common.session_state_dir(cwd, sid) / common.STATE_VERIFICATION).get("result")
    return {
        "session_id": sid,
        "ts": data.get("last_ts"),
        "branch": common.git_branch(cwd),
        "head": common.git_head(cwd),
        "verified_state": vstate,
        "peak_main_context": data.get("peak_main_context", 0),
        "n_changed": len(common.git_status_porcelain(cwd)),
    }


def snapshot(payload: dict) -> None:
    """毎 Stop で当該セッションの metrics を state に書き出す。全体 try/except・無音。"""
    try:
        cwd = common.hook_cwd(payload)
        sid = payload.get("session_id")
        tpath = payload.get("transcript_path") or _guess_transcript(cwd, sid)
        if not tpath or not os.path.exists(tpath):
            return
        data = summarize(tpath)
        data["session_id"] = sid
        sdir = common.session_state_dir(cwd, sid)
        common.write_json_atomic(sdir / common.STATE_METRICS, data)
        shared = common.shared_state_dir(cwd)
        row = {"session_id": sid, "main": data["main"], "subagent": data["subagent"],
               "total": data["total"], "peak_main_context": data["peak_main_context"]}
        with (shared / common.STATE_METRICS_LEDGER).open("a") as f:
            f.write(json.dumps(row) + "\n")
        # 捕捉スパイン(capture-only・決して注入しない)。失敗しても snapshot を壊さない。
        common.append_jsonl_capped(shared / common.STATE_JOURNAL,
                                   journal_row(cwd, sid, data), cap=JOURNAL_CAP)
    except Exception:
        pass


if __name__ == "__main__":  # 手動スモーク: python3 metrics.py <transcript.jsonl>
    if len(sys.argv) > 1:
        print(json.dumps(summarize(sys.argv[1]), indent=2))
