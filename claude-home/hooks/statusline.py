#!/usr/bin/env python3
"""statusline.py — settings.json の statusLine から呼ばれ、画面下に常駐表示する。

`ultra-ai` で起動していること(=素の claude ではない)を一目で分かるように、
`▌ ultra-ai · <model> · <dir>[ · <branch>][ · <metrics…>]` を1行で出す。stdin に
session JSON(`model.display_name`/`model.id`、`workspace.current_dir`、`session_id` 等)が
渡り、stdout が描画される。

メトリクスは計測スパイン(metrics.py)が Stop 毎に書く `metrics.json` を **読むだけ**
(常駐レンダで transcript を再パースするのは高コストなので避ける)。コンテキスト充填・
セッション費用・経過時間を追記する。metrics 不在時はベース行のみ(後方互換)。

設計ルール(他 hook と同じ): 例外を投げない。失敗しても最低限 `▌ ultra-ai` を出し、
statusLine が空/エラー表示にならないようにする。
"""
from __future__ import annotations

import colorsys
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

DIM, RESET = "\033[2m", "\033[0m"
CYAN = "\033[36m"  # 元の水色(虹色を無効化したときのフォールバック色)
LOGO = "▌ ultra-ai"  # 元グリフ: ▌ 左バー + ultra-ai


def _lift(r: float, g: float, b: float, floor: float = 0.5) -> tuple[float, float, float]:
    """知覚輝度(Rec.601)が floor 未満なら白へブレンドして持ち上げる。純関数(test 対象)。

    青/紫は value=1.0 でも知覚輝度が低く暗背景で沈むため、明度ではなく「白寄せ」で底上げする。
    入出力とも各成分 0..1。十分明るい色(輝度 >= floor)はそのまま返す。
    """
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum >= floor:
        return r, g, b
    t = (floor - lum) / (1.0 - lum)  # 白へのブレンド量 0..1
    return r + (1.0 - r) * t, g + (1.0 - g) * t, b + (1.0 - b) * t


def _rainbow(text: str) -> str:
    """各可視文字を truecolor(24bit)の虹色グラデーションで着色して返す。純関数(test 対象)。

    空白文字は素通り。左=赤(hue 0)→ 右=紫(hue 0.83)に均等割り(端で赤に巻き戻さない)。
    """
    visible = sum(1 for c in text if not c.isspace())
    n = max(1, visible)
    out: list[str] = []
    pos = 0
    for c in text:
        if c.isspace():
            out.append(c)
            continue
        frac = pos / (n - 1) if n > 1 else 0.0  # 0(左)→1(右)
        r, g, b = _lift(*colorsys.hsv_to_rgb(frac * 0.83, 0.85, 1.0))  # 彩度0.85=赤を緩和 + 暗色を白寄せ
        out.append(f"\033[38;2;{int(r * 255)};{int(g * 255)};{int(b * 255)}m{c}")
        pos += 1
    return "".join(out) + RESET


def _bar() -> str:
    """先頭のブランド表示。既定は虹色。UA_STATUSLINE_RAINBOW=0 / NO_COLOR で元の水色(cyan)に。"""
    if os.environ.get("UA_STATUSLINE_RAINBOW") == "0" or os.environ.get("NO_COLOR"):
        return f"{CYAN}{LOGO}{RESET}"
    return _rainbow(LOGO)


BAR = _bar()
SEP = f" {DIM}·{RESET} "


def _current_dir(data: dict) -> str:
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    return ws.get("current_dir") or data.get("cwd") or os.getcwd()


def _fmt_tokens(n: int) -> str:
    """142000 → '142k'、1_200_000 → '1.2M'、980 → '980'。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1000)}k"
    return str(n)


def _fmt_cost(usd: float) -> str:
    """0.83 → '$0.83'(相対コンパレータなのでセント精度で十分)。"""
    return f"${usd:.2f}"


def _fmt_duration(sec) -> str:
    """740 → '12m'、45 → '45s'、3905 → '1h05m'。None/0/負 → '' (呼び出し側が省略)。"""
    if not sec or sec <= 0:
        return ""
    s = int(sec)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, mm = divmod(m, 60)
    return f"{h}h{mm:02d}m"


def _metrics_segments(metrics: dict) -> list[str]:
    """metrics.json から表示セグメントを組む。欠損/0 のものは省略。純関数(test 対象)。"""
    if not isinstance(metrics, dict) or not metrics:
        return []
    segs: list[str] = []
    ctx = metrics.get("peak_main_context") or 0
    if ctx:
        segs.append(f"{_fmt_tokens(int(ctx))} ctx")
    total = metrics.get("total") if isinstance(metrics.get("total"), dict) else {}
    cost = total.get("weighted_cost") or 0
    if cost:
        segs.append(_fmt_cost(float(cost)))  # total = サブエージェント込み = 委譲の実費
    dur = _fmt_duration(metrics.get("wall_clock_s"))
    if dur:
        segs.append(dur)
    return segs


def render(data: dict, branch: str | None = None, metrics: dict | None = None) -> str:
    """`▌ ultra-ai · model · dir[ · branch][ · metrics…]`。欠損要素は出さない。純関数(test 対象)。

    metrics が falsy(None/{})なら metrics セグメントは付かず、従来と同一出力(後方互換)。
    """
    model_d = data.get("model") if isinstance(data.get("model"), dict) else {}
    model = model_d.get("display_name") or model_d.get("id") or ""
    cur = _current_dir(data)
    dirname = os.path.basename(cur.rstrip("/")) or cur
    line = BAR
    for seg in (model, dirname, branch, *_metrics_segments(metrics or {})):
        if seg:
            line += SEP + seg
    return line


def _read_metrics(data: dict, cur: str) -> dict:
    """当該セッションの metrics.json を読む。失敗/不在/session_id 欠如は {} に縮退。

    自前 try/except で握りつぶし、metrics の IO 失敗がベース行(model/dir/branch)を
    巻き添えにしないようにする(main() 外側 try との二重防御)。
    """
    try:
        sid = data.get("session_id")
        if not sid:
            return {}
        mfile = common.session_state_dir(cur, sid) / common.STATE_METRICS
        return common.read_json(mfile)  # 欠損/破損 → {}
    except Exception:
        return {}


def _persist_window(data: dict, cur: str) -> None:
    """アクティブモデルの context 窓を検出し、変化時のみ session state(model.json)へ永続化する。

    hook 入力(PostToolUse)にはモデル情報が無いため、唯一 model.id/display_name を受け取れる
    statusline が monitor へ橋渡しする(monitor が context% の分母に使う)。表示は絶対に壊さない
    (本体も握りつぶし・呼び出しは main() の try/except 内)。毎レンダー走るので値が変わった時だけ書く。
    """
    try:
        sid = data.get("session_id")
        if not sid:
            return
        md = data.get("model") if isinstance(data.get("model"), dict) else {}
        win = common.context_window_for_model(md.get("id"), md.get("display_name"))
        sp = common.session_state_dir(cur, sid) / common.STATE_MODEL
        if common.read_json(sp).get("context_window") != win:
            common.write_json_atomic(sp, {"context_window": win})
    except Exception:
        return


def main() -> int:
    try:
        data = common.read_hook_input()
        cur = _current_dir(data)  # 1回だけ計算して branch/metrics で再利用
        _persist_window(data, cur)
        branch = common.git_branch(cur)
        metrics = _read_metrics(data, cur)
        sys.stdout.write(render(data, branch, metrics))
    except Exception:
        sys.stdout.write(BAR)  # 何があっても ultra-ai ラベルは出す
    return 0


if __name__ == "__main__":
    sys.exit(main())
