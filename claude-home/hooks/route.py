#!/usr/bin/env python3
"""route.py — UserPromptSubmit hook: 薄いプロンプトに「フレーミングの足場」を注入する。

決定論・ゼロトークン・毎回同じ文章。プロンプトが非自明なとき、固定の足場(戦略メニュー + 「着手前に
`approach:` 行を必ず出力せよ」という手続き)を additionalContext として stdout に出す。

役割分担(中核):
  - **決定論(この hook)**: いつ(毎 UserPromptSubmit)・選択肢(戦略メニュー)・手続き(approach 行先出し)を固定。
    粗い SILENCE(明白な trivial/会話/ユーザーが戦略を明示済み)だけを高精度に除外する。
  - **モデル**: intent の意味的な分類と戦略選択は奪わない(regex 分類は脆く、第一原理「思考 effort は削らない」に反する)。

不変条件:
  - **分類はしない**(per-class keyword テーブルを持たない)。SILENCE は「明白に不要なケースの除外」だけ。
  - 迷ったら無音にしない(=足場を出す側へ倒す)。薄いプロンプトを取りこぼさない方を優先=「品質によらない」。
  - 毎回同じ文章: 足場は固定文字列。プロンプト本文 / score / timestamp を注入バイトに混ぜない。
  - ユーザーが戦略を明示していれば素通り(二重フレーミングしない=訂正優先と同型)。
  - `UA_ROUTE=0/off/false/no` で無効化(kill switch)。`ultra-ai-safe`(disableAllHooks)でも丸ごと止まる。
  - hook は決して session に例外を投げない。終了コードは常に 0(注入は stdout)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import learning  # noqa: E402

# 着手前に必ず出力させる足場(毎回同じ文章・固定)。CLAUDE.md §approach が正本、これはその毎プロンプト再宣言。
_SCAFFOLD = """ultra-ai — approach: 着手前に、まず次の1行だけ出力してから進め(言い回しに依存せず意図を分類):
  approach: <設計|実装|デバッグ|レビュー|把握|横断|軽微> → <とる戦略(発散型は fan-out 法を明記)> / 仮定: <あれば>
発散型(設計/全体把握/広域バグ狩り/横断・移行)は fan-out 法を最初の探索より前に決め approach 行に明記する。
「余地あり」等で後回しにしたり solo 探索に流れたりしない=fan-out が初手の地図化そのもの。
  ・ultracode は既定 ON → 発散型は初手の探索を Workflow/understand の fan-out として実行する(solo で始めない)。
  ・UA_ULTRACODE=0 で無効化したときのみ → solo 探索に入る前に有効化を促す。
型ごとの既定戦略(design-panel/understand/deep-solver/review-audit/spec 等)は常駐 CLAUDE.md §approach を参照(ここでは再掲しない)。
オープンエンド/中身が未指定(「何か作って」「どうすべき」等)→ 実装に見えても発散型。ありきたりな既製リストを menu で出さない。ultracode は既定 ON なので fan-out で毛色の違う・意外な案を複数生成してから見せる。選んでもらう(お任せなら自分で選ぶ)のは最後。確定後の作り込みは集中フェーズ=xhigh への一時切替を促してよい。
原則: effort は自分で下げられない=下げる(xhigh)/上げる(max)を提案する(ultracode は既定 ON)。質問は結果が大きく分岐する曖昧さだけ。
軽微 と判断したら approach 行に `軽微` と書いて即実行(素通り可)。"""

# ユーザーが既に戦略/effort/skill を明示 → 二重フレーミングしない(訂正優先の同型)。
# ここに intent の語(「設計」「バグ」等)は入れない=分類はモデルに委ねる。
_STRATEGY_MARKERS = (
    "ultracode", "/effort", "effort:", "deep-solver", "deep solver",
    "design-panel", "design panel", "review-audit", "review audit",
    "/ua-", "ua-spec", "ua-failpass", "ua-refactor", "ua-learn", "ua-check",
    "failpass", "fail→pass", "fail-pass", "fail pass",
    "複数案", "案を比較", "compare approaches", "approaches and compare",
    "失敗するテストを先に", "テストを先に書", "test first", "tdd",
    "approach:",
)

# 明白に trivial/機械的 → フレーミング不要(高精度・取りこぼしは許容)。
_TRIVIAL_MARKERS = (
    "typo", "誤字", "タイポ", "スペルミス", "綴り",
    "インデント", "indent", "空白を", "whitespace",
    "フォーマットして", "format this", "改行を",
)

# 短い挨拶/相槌(完全一致寄り=実タスクの部分文字列誤爆を避ける)。
_GREETINGS = {
    "ありがとう", "ありがとうございます", "thanks", "thank you", "ok", "okay",
    "はい", "yes", "yep", "yeah", "了解", "了解です", "りょうかい", "続けて",
    "go ahead", "proceed", "sounds good", "lgtm", "nice", "いいね", "good", "👍",
}


def route_enabled() -> bool:
    """フレーミング注入の有効/無効。**既定 ON**。`UA_ROUTE=0/off/false/no` で無効化(kill switch)。

    `_rules_enabled`(resume_context)と同型の単一消費者向け kill switch。"""
    return common.flag_enabled("ROUTE")


def _is_greeting(text: str) -> bool:
    return text.strip().strip("!.。!?？、,").lower() in _GREETINGS


def should_silence(prompt) -> bool:
    """足場を**出さない**明白なケースだけ True(高精度)。曖昧は False=足場を出す。

    純関数(テスト可能)。分類はしない——「明白に不要なケースの除外」だけを担う。
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return True
    text = prompt.strip()
    if learning.text_has_marker(text, _STRATEGY_MARKERS):
        return True  # 既に戦略/effort を明示
    if learning.text_has_marker(text, _TRIVIAL_MARKERS):
        return True  # 明白に機械的
    if _is_greeting(text):
        return True  # 短い挨拶/相槌
    return False     # 迷ったら無音にしない


def scaffold() -> str:
    """注入する足場(毎回同じ文章 な固定文字列)。"""
    return _SCAFFOLD


def build(payload: dict) -> str:
    """注入する文字列(空 = 何も注入しない)。純関数(テスト可能)。"""
    if not route_enabled():
        return ""
    if should_silence(payload.get("prompt")):
        return ""
    return scaffold()


def main() -> int:
    try:
        msg = build(common.read_hook_input())
        if msg:
            print(msg)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
