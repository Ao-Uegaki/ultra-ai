"""learning.py — 学習レイヤ(capture / fire)の共有ヘルパー(stdlib のみ, Python 3.11+)。

common.py から切り出した「学習」層:
  - 訂正らしさ・ノイズ・再利用可能性の純述語(b+ の品質ゲート)
  - LEARNED.md から注入可能テキストを読む(毎回同じ文章=prompt cache を冷やさない)
  - project→全プロジェクト共通の学習した約束ごと の registry(2+ repo 合意で global へ昇格)

すべて common.autoapply_enabled() の下でのみ作動し、フラグ OFF では新規挙動を一切持たない。
依存方向は learning → common の一方向(common は learning を import しない=循環なし)。
"""
from __future__ import annotations

import re
from pathlib import Path

import common


# 訂正様プロンプトの少数マーカー(高精度寄り)。マーカー無し=訂正でない(allow-on-uncertainty)。
# 通常作業("fix the bug" 等)を拾わないよう、行動の打ち消し/否定に絞る。
_CORRECTION_MARKERS = (
    "ではなく", "じゃなく", "ではなくて", "じゃなくて", "やめて", "やめろ",
    "間違", "訂正", "しないで", "するな", "違う", "ちがう", "そうじゃ", "ではない",
    "don't ", "do not ", "instead of", "rather than", "that's wrong",
    "that is wrong", "incorrect", "stop using", "no, ",
)


def text_has_marker(text: str, markers) -> bool:
    """マーカー走査の共通作法: ascii は小文字化して、非 ascii は原文で照合する。"""
    low = text.lower()
    return any((m in low if m.isascii() else m in text) for m in markers)


def looks_like_correction(text: str | None) -> bool:
    """プロンプトが「直前の挙動の訂正」らしいか(高精度ヒューリスティック)。"""
    if not text:
        return False
    return text_has_marker(text, _CORRECTION_MARKERS)


# harness/システム由来の断片を示すトークン(= ユーザーの再利用可能な訂正ではない)。
# 記録だけ/反映 の両境界で弾く。マーカーが含まれれば候補・学習した約束ごと として不適。
_NOISE_MARKERS = (
    "<task-notification>", "<system-reminder>", "<command-name>",
    "<command-message>", "<command-args>", "<local-command",
    "<output-file>", "tool-use-id", "toolu_", "<task-id>", "framing:", "approach:",
    "/tmp/claude-", "/private/tmp/claude-",
)
_MIN_LESSON_CHARS = 10  # これ未満は再利用できる規則になり得ない(例 "もっと違う案"=6)


def normalize_lesson_key(text: str | None) -> str:
    """反復カウント/重複判定用の正規化キー(空白畳み込み + 小文字化)。"""
    return " ".join((text or "").split()).lower()


def looks_like_noise(text: str | None) -> bool:
    """harness/システム由来の断片か(= ユーザーの再利用可能な訂正ではない)。"""
    if not text:
        return True
    return any(m in normalize_lesson_key(text) for m in _NOISE_MARKERS)


def is_reusable_lesson(text: str | None) -> bool:
    """注入/active fire に値する『再利用できる訂正』か。ノイズ断片・短すぎる空虚文を弾く。"""
    if not text:
        return False
    return len(" ".join(text.split())) >= _MIN_LESSON_CHARS and not looks_like_noise(text)


_LESSON_COMMENT = re.compile(r"\s*<!--.*?-->\s*$")


def read_learned_texts(path: Path, *, reusable_only: bool = False) -> list[str]:
    """LEARNED.md から注入可能なテキスト(provenance コメントを除く)を返す。

    毎回同じ文章: 重複排除 + ソートにより、同一集合なら必ず同一バイト列になる
    (score/timestamp/hit は注入テキストに含めない=prompt cache を冷やさない hard 前提)。
    `reusable_only=True` で harness 断片・空虚短文を除外する(active 注入の防御=既存ゴミの
    即時無効化)。下書き の件数読みは既定 False(隔離なので短文も保持)。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except Exception:
        return []
    texts = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("- "):
            continue
        body = _LESSON_COMMENT.sub("", ln[2:]).strip()
        if not body or (reusable_only and not is_reusable_lesson(body)):
            continue
        texts.append(body)
    return sorted(set(texts))


# --------------------------------------------------------- 全プロジェクト共通の学習した約束ごと ---
# project→global 昇格: 同一の学習した約束ごと が GLOBAL_REPO_THRESHOLD 以上の repo で active 化
# したら machine-wide な 全プロジェクト共通の学習した約束ごと とする。これは別リポでの単発訂正を昇格させる
# 唯一の決定論的「確かめた事実による裏付け」(numeric confidence は採らない)。
# state は repo-local と同じ git-undoable 空間(config_dir()/state/global)に置く。
GLOBAL_REPO_THRESHOLD = 2


def global_learning_enabled() -> bool:
    """project→全プロジェクト共通の学習した約束ごと の記録・注入・昇格。既定 ON。UA_GLOBAL_LEARNING=0 で無効。"""
    return common.flag_enabled("GLOBAL_LEARNING")


def _global_learned_path() -> Path:
    return common.global_state_dir() / common.STATE_LEARNED


def _global_repos_path() -> Path:
    return common.global_state_dir() / common.STATE_LEARN_REPOS


def record_active_lessons(repo_key_: str, texts: list[str]) -> None:
    """ある repo の現行 有効な学習した約束ごと 集合を global registry に反映する。

    learn-repos.json(正規化キー→{text, repos:[repo_key,...]})を更新し、
    GLOBAL_REPO_THRESHOLD 以上の repo で active な 学習した約束ごと から global LEARNED.md を
    毎回同じ文章に再生成する。注入バイトには件数/出どころを混ぜない(テキストのみ)。
    無効時・例外時は何もしない(hook を壊さない)。
    """
    if not global_learning_enabled():
        return
    try:
        repos = common.read_json(_global_repos_path())
        # まず全レコードからこの repo を外す(去った 学習した約束ごと を合意から落とす=現状反映)。
        for rec in repos.values():
            rec["repos"] = [r for r in (rec.get("repos") or []) if r != repo_key_]
        for t in texts:
            if not is_reusable_lesson(t):
                continue
            nk = normalize_lesson_key(t)
            rec = repos.get(nk) or {"text": t, "repos": []}
            rec["text"] = t  # 表示用に原文を保持(最新で上書き)
            if repo_key_ not in rec["repos"]:
                rec["repos"].append(repo_key_)
            rec["repos"] = sorted(set(rec["repos"]))
            repos[nk] = rec
        repos = {nk: rec for nk, rec in repos.items() if rec.get("repos")}  # 空は掃除
        common.write_json_atomic(_global_repos_path(), repos)
        gtexts = sorted({rec["text"] for rec in repos.values()
                         if len(rec.get("repos") or []) >= GLOBAL_REPO_THRESHOLD})
        body = "\n".join("- " + t for t in gtexts)
        common.write_text_atomic(
            _global_learned_path(),
            "# ultra-ai 全プロジェクト共通の学習した約束ごと (2+ repo で合意・毎回同じ文章で読み込ませる対象)\n"
            + body + ("\n" if body else ""))
    except Exception:
        pass


def read_global_learned_texts() -> list[str]:
    """global 有効な学習した約束ごと のテキストを sorted(set) で返す(無効/無ければ空)。"""
    if not global_learning_enabled():
        return []
    return read_learned_texts(_global_learned_path(), reusable_only=True)


def is_global_lesson(text: str | None) -> bool:
    """text が global active(2+ repo 合意)か。単発訂正の昇格ゲートが裏付けに参照する。"""
    if not text or not global_learning_enabled():
        return False
    nk = normalize_lesson_key(text)
    return nk in {normalize_lesson_key(t) for t in read_global_learned_texts()}
