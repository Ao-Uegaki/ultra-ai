#!/usr/bin/env python3
"""resume_context.py — SessionStart hook: progress / rules / 学習 を文脈へ注入する。

source 別に振る舞う:
  - `startup` / `clear`: 常駐の初回ロード(progress + rules + 学習 + 提案 hint)。
  - `compact`: compaction は hook 注入の文脈(rules/学習/progress)を要約に飲む(disk 由来の
    project CLAUDE.md/MEMORY.md しか生き残らない)。そこで飲まれた byte-stable 層を disk から
    **再注入**し、さらに「いま分かっている検証/未コミット/既読 file」を一回限りの継続アンカー
    (transient)として渡す。kill switch `UA_COMPACT_RESUME=0`。
  - `resume`: 何もしない(会話履歴が自動再ロードされ重複するため)。
保存された progress が**現在の git ブランチ**のものであるときだけ注入する(別ブランチの古い
progress はスキップ)。exit 0 時の stdout がモデルへの additionalContext になる。

hook 自体はゼロトークン。注入されるノートは小さく意図的。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import detect  # noqa: E402
import learning  # noqa: E402

INJECT_SOURCES = {"startup", "clear"}


def progress_branch(text: str) -> str | None:
    m = re.search(r"branch:\s*(\S+)", text)
    return m.group(1) if m else None


def _progress_block(cwd: str) -> str:
    """同一ブランチの latest-progress を注入(従来挙動・無改訂)。"""
    pfile = common.shared_state_dir(cwd) / common.STATE_PROGRESS
    if not pfile.exists():
        return ""
    text = pfile.read_text()
    cur = common.git_branch(cwd)
    pb = progress_branch(text)
    if cur and pb and pb != "None" and cur != pb:
        return ""  # stale: progress is for a different branch
    return "ultra-ai — 前回の進捗(resume):\n" + text.strip()


def _global_learned_block(cwd: str) -> str:
    """2+ repo で合意した 全プロジェクト共通の学習した約束ごと を 毎回同じ文章に注入(project 学習した約束ごと より前=優先)。"""
    if not common.autoapply_enabled():
        return ""
    texts = learning.read_global_learned_texts()
    if not texts:
        return ""
    return ("ultra-ai — 全プロジェクト共通の学習した約束ごと:\n"
            + "\n".join("- " + t for t in texts))


def _learned_block(cwd: str) -> str:
    """学習済み 学習した約束ごと を **毎回同じ文章** に注入(fire・`UA_AUTOAPPLY` 下のみ)。

    注入されるのはテキスト集合だけ(score/timestamp を含めない)→ 同一集合なら同一バイト列
    = prompt cache を冷やさない。フラグ OFF・学習した約束ごと 無しなら何も注入しない。
    global と重複する分は _global_learned_block が出すので除く(二重注入回避・毎回同じ文章)。
    """
    if not common.autoapply_enabled():
        return ""
    texts = learning.read_learned_texts(
        common.shared_state_dir(cwd) / common.STATE_LEARNED, reusable_only=True)
    if not texts:
        return ""
    gkeys = {learning.normalize_lesson_key(t) for t in learning.read_global_learned_texts()}
    texts = [t for t in texts if learning.normalize_lesson_key(t) not in gkeys]
    if not texts:
        return ""
    return "ultra-ai — 学習した約束ごと:\n" + "\n".join("- " + t for t in texts)


def _rules_enabled() -> bool:
    """rules 自動注入の有効/無効。既定 ON。`UA_RULES=0/off/false/no` で無効化(kill switch)。"""
    return common.flag_enabled("RULES")


def _rules_topics(cwd: str) -> set:
    """このリポに関係する rules トピック。言語は detect_stack、ドメインは detect_domains(自動) ∪ 手動。

    言語規約(python/typescript)が自動なのと同型に、ドメイン規約(frontend/backend/ml/infra)も
    リポの依存/manifest から自動検出する(手動 set-once でしか起きない=眠る、を解消)。
    手動 `[ua-rules] domains` は常に加算(後方互換)、`[ua-rules] auto = false` で自動のみ止める。
    """
    topics = set()
    root = common.project_root(cwd)
    kind = detect.detect_stack(root).kind
    if kind == "python":
        topics.add("python")
    elif kind == "node":
        topics.add("typescript")
    cfg = detect.load_project_config(root).get("ua-rules") or {}
    if cfg.get("auto", True):                  # 既定 ON。自動検出ドメインを加える
        topics |= detect.detect_domains(root)
    for d in (cfg.get("domains") or []):       # 手動は常に加算(set-once は生きる)
        if isinstance(d, str) and d.strip():
            topics.add(d.strip().lower())
    return topics


def _rules_block(cwd: str) -> str:
    """このリポに関係する rules/<topic>.md を **毎回同じ文章** に連結注入(curated 規約・既定 ON)。

    言語/ドメインでスコープし、無関係トピックは入れない。topic 昇順=同一リポなら同一バイト列(cache を冷やさない)。
    小さく 毎回同じ文章 な常駐は妥協でなく正解(CLAUDE.md 自体が常駐の好例)。
    """
    if not _rules_enabled():
        return ""
    topics = _rules_topics(cwd)
    if not topics:
        return ""
    rules_dir = common.config_dir() / "rules"
    parts = []
    for topic in sorted(topics):  # 毎回同じ文章
        p = rules_dir / (topic + ".md")
        try:
            if p.is_file():
                txt = p.read_text(encoding="utf-8").strip()
                if txt:
                    parts.append(txt)
        except Exception:
            continue
    if not parts:
        return ""
    return "ultra-ai — このリポの約束ごと(rules):\n\n" + "\n\n".join(parts)


# ------------------------------------- proactive suggestions (Tier 2) ---------
# SessionStart で、忘れられがちな on-demand skill を「適切な場面で」提案する固定文言。
# 毎回同じ文章: 件数などの動的値は注入しない(閾値の真偽で出す/出さないだけ)。各々 UA_SUGGEST_* で可逆。

_LEARN_HINT = ("ⓘ ultra-ai — 下書きに未承認の学習候補が溜まっています。"
                 "/ua-learn で LLM 品質パス(言い換え・採否)を検討してください。")
_BENCH_HINT = ("ⓘ ultra-ai — 学習した約束ごとが蓄積しています。"
               "/ua-compare で学習レイヤの効果(A/B・control vs treatment)を測れます。")


def _learn_hint_block(cwd: str) -> str:
    """下書き が**新しい蓄積帯**に達したら一度だけ固定文言を出す(件数は注入しない=毎回同じ文章)。
    b+ では 下書き が常時溜まるため、毎セッション出さない milestone dedup にする。UA_AUTOAPPLY 下のみ。"""
    if not common.autoapply_enabled() or not common.flag_enabled("SUGGEST_LEARN"):
        return ""
    try:
        size = common.env_int("LEARN_MIN_DRAFT", 5)
        if size <= 0:
            return ""
        n = len(learning.read_learned_texts(
            common.shared_state_dir(cwd) / common.STATE_LEARN_DRAFT))
        band = n // size
        sfile = common.shared_state_dir(cwd) / common.STATE_SUGGEST
        st = common.read_json(sfile)
        prev = int(st.get("learn_band", 0))
        if band != prev:  # 帯が動いたら state を追従(減少時も=再蓄積で再提案可能に)
            st["learn_band"] = band
            common.write_json_atomic(sfile, st)
        return _LEARN_HINT if band > prev else ""  # 増えた時だけ提案(nag 回避)
    except Exception:
        return ""


def _bench_hint_block(cwd: str) -> str:
    """有効な学習した約束ごと が**新しいマイルストン帯**に達したら一度だけ提案(milestone dedup=毎回出さない)。"""
    if not common.autoapply_enabled() or not common.flag_enabled("SUGGEST_BENCH"):
        return ""
    try:
        size = common.env_int("BENCH_MILESTONE", 10)
        if size <= 0:
            return ""
        n = len(learning.read_learned_texts(
            common.shared_state_dir(cwd) / common.STATE_LEARNED))
        band = n // size
        if band < 1:
            return ""
        sfile = common.shared_state_dir(cwd) / common.STATE_SUGGEST
        st = common.read_json(sfile)
        if band <= int(st.get("bench_band", 0)):
            return ""  # 同じ帯では再提案しない(nag 回避)
        st["bench_band"] = band
        common.write_json_atomic(sfile, st)
        return _BENCH_HINT
    except Exception:
        return ""


def _autolearn(cwd: str) -> None:
    """SessionStart で保留中の学習候補を**決定論で**学習した約束ごと 化する(ゼロトークン・LLM を呼ばない)。

    生テキストは般化していないので原則 下書き(=コンテキスト非注入の隔離)。active 昇格は
    同一訂正の N回再発(=確かめた事実)のときだけ(learn.auto_lessons)。あいまいな般化や
    言い換えは任意の LLM 品質パス `/ua-learn` に委ねる。決して SessionStart を壊さない。
    """
    try:
        import learn
        cands = learn.load_candidates(cwd)
        if not cands:
            return
        learn.apply(cwd, learn.auto_lessons(cwd, cands))
        learn.clear_candidates(cwd)
    except Exception:
        pass


def _compact_anchor_block(cwd: str, payload: dict) -> str:
    """compaction 直後だけの transient 引き継ぎ: いま分かっている検証状態・未コミット・既読 file。

    動的値(検証結果・HEAD・件数)を含むが、これは常駐の毎回ロードではなく compaction 直後の
    **一回限りの引き継ぎ**(monitor の transient nudge と同層)なので byte-stable 規律の対象外。
    要約後のモデルに「もう確かめた事実/もう調べた所」を渡し、再検証・再 Read を防ぐ。
    """
    sdir = common.session_state_dir(cwd, payload.get("session_id"))
    lines = []
    vstate = common.read_json(sdir / common.STATE_VERIFICATION).get("result")
    if vstate:
        head = common.git_head(cwd)
        lines.append(f"- 直近の検証: {vstate}" + (f" @ {head[:8]}" if head else ""))
    nchanged = len(common.git_status_porcelain(cwd))
    if nchanged:
        lines.append(f"- 未コミットの変更: {nchanged} file")
    checked = common.read_json(sdir / "factgate.json").get("checked") or []  # gateguard.STATE_FACTGATE
    if checked:
        names = ", ".join(Path(p).name for p in checked[:8])
        lines.append(f"- 本セッションで事実確認済み(再 Read 不要): {names}"
                     + (f" 他{len(checked) - 8}" if len(checked) > 8 else ""))
    if not lines:
        return ""
    return "ultra-ai — compaction 引き継ぎ(要約後の継続メモ):\n" + "\n".join(lines)


def build(payload: dict) -> str:
    source = payload.get("source")
    cwd = common.hook_cwd(payload)
    if source in INJECT_SOURCES:
        try:  # 旧名の学習状態ファイルを新名へ移行(autofire と独立・壊さない)
            import learn
            learn.migrate_state(cwd)
        except Exception:
            pass
        if common.autoapply_enabled():
            _autolearn(cwd)  # 注入前に保留候補を学習化(ゼロトークン)
        blocks = [b for b in (_progress_block(cwd), _rules_block(cwd),
                              _global_learned_block(cwd), _learned_block(cwd),
                              _learn_hint_block(cwd), _bench_hint_block(cwd)) if b]
        return "\n\n".join(blocks)
    if source == "compact" and common.flag_enabled("COMPACT_RESUME"):
        # compaction が要約に飲んだ byte-stable 層を disk から再注入 + 継続アンカー(transient)。
        blocks = [b for b in (_progress_block(cwd), _rules_block(cwd),
                              _global_learned_block(cwd), _learned_block(cwd),
                              _compact_anchor_block(cwd, payload)) if b]
        return "\n\n".join(blocks)
    return ""


def main() -> int:
    msg = build(common.read_hook_input())
    if msg:
        print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
