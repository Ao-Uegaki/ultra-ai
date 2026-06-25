#!/usr/bin/env python3
"""learn.py — 学習候補 → 学習した約束ごと(賢く半自動の振り分け + 毎回同じ文章での永続化)。

Tier B。記録だけした候補(learn-candidates.jsonl)を 1行の約束ごとにまとめて振り分ける:
- source=correction(明示訂正=高確信) → 有効な約束ごと(反映で文脈へ読み込ませる)
- source=fail-pass(推測=誤帰属しうる) → 下書き(人が採否・文脈へ読み込ませない)

テキストの言い換え・要約は安価 LLM(agents/learner.md, haiku)が `/ua-learn` 経由で担い、
本モジュールはルールベースの「読込・振り分け・重複排除・上限・毎回同じ文章への整形・永続化」を担う。
LLM 無しでも動くルールベースの代替経路(`auto`)を持つ。文脈へ読み込ませるのは resume_context.py 側(本モジュールは書くだけ)。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import learning  # noqa: E402

MAX_ACTIVE = 16     # 有効な約束ごとの上限(有界=ためこみ回避・文脈へ読み込ませる量を小さく保つ)
MAX_DRAFT = 50
MAX_COUNTS = 200    # 反復カウント表の上限(文脈へ読み込ませない state・超過時は低カウントから刈る)
REPEAT_DEFAULT = 2  # 有効化(active)に必要な同一訂正の再発回数(UA_PROMOTE_REPEAT で可変)
ROUTE_ACTIVE, ROUTE_DRAFT = "active", "draft"

# 旧名(旧語彙のファイル名)→ 新名。状態ファイルを一回限り rename する(冪等)。
# route 値はファイルに永続しない(ファイルで active/draft を分離)ので中身の書換えは不要。
_LEGACY_RENAMES = {
    "INSTINCTS.md": common.STATE_LEARNED,
    "instinct-staging.md": common.STATE_LEARN_DRAFT,
    "instinct-candidates.jsonl": common.STATE_LEARN_CANDIDATES,
    "instinct-counts.json": common.STATE_LEARN_COUNTS,
    "instinct-repos.json": common.STATE_LEARN_REPOS,
}


def migrate_state(cwd: str) -> int:
    """旧名の学習状態ファイルを新名へ rename する(冪等)。renamed 件数を返す。

    per-session の shared dir と global state dir の両方が対象。新名が既にあれば触らない。
    失敗しても落とさず素通り(縮退=データ移行が SessionStart を壊さない)。
    """
    n = 0
    for d in (common.shared_state_dir(cwd), common.global_state_dir()):
        for old, new in _LEGACY_RENAMES.items():
            try:
                src, dst = d / old, d / new
                if src.exists() and not dst.exists():
                    src.rename(dst)
                    n += 1
            except Exception:
                pass
    return n


def load_candidates(cwd: str) -> list[dict]:
    path = common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES
    out = []
    try:
        if path.exists():
            for ln in path.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def clear_candidates(cwd: str) -> None:
    try:
        (common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES).unlink()
    except Exception:
        pass


def route_for(source: str) -> str:
    """LLM/人手で**一般ルール化済み**の訂正が向かう先(明示 route を持たない apply 入力の既定)。
    明示訂正→有効(active)、推測→下書き(draft)。ルールベースの自動経路はこれを使わない(一般ルール化
    していない生テキストを単発で有効にしないため・下記 auto_lessons 参照)。"""
    return ROUTE_ACTIVE if source == "correction" else ROUTE_DRAFT


def load_counts(cwd: str) -> dict:
    return common.read_json(common.shared_state_dir(cwd) / common.STATE_LEARN_COUNTS)


def save_counts(cwd: str, counts: dict) -> None:
    """反復カウントを上限付きで永続化(文脈へ読み込ませない state・超過時は低カウントから刈る)。"""
    if len(counts) > MAX_COUNTS:
        counts = dict(sorted(counts.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:MAX_COUNTS])
    common.write_json_atomic(
        common.shared_state_dir(cwd) / common.STATE_LEARN_COUNTS, counts)


def auto_lessons(cwd: str, candidates: list[dict], *, repeat_n: int | None = None) -> list[dict]:
    """ルールベースの自動振り分け(LLM なし)。生テキストは一般ルール化していないので原則 下書き。
    correction が **再利用可能 かつ 同一訂正が N回再発**(=確かめた事実=明示の繰り返し)の
    ときだけ有効(active)へ昇格させる。反復カウントを更新・永続化する。"""
    if repeat_n is None:
        repeat_n = common.env_int("PROMOTE_REPEAT", REPEAT_DEFAULT)
    repeat_n = max(1, repeat_n)
    counts = load_counts(cwd)
    # 全プロジェクト共通の学習した約束ごと(2+ repo で合意)の正規化キー集合を一度だけ読む。これに一致する
    # 単発訂正は「確かめた事実(他リポでの反復合意)」が裏にあるので初回でも有効(active)化する。
    global_keys = {learning.normalize_lesson_key(t)
                   for t in learning.read_global_learned_texts()}
    out = []
    for cand in candidates:
        text = " ".join((cand.get("text") or "").split())[:160]
        if not text:
            continue
        source = cand.get("source") or "unknown"
        route = ROUTE_DRAFT
        if source == "correction" and learning.is_reusable_lesson(text):
            key = learning.normalize_lesson_key(text)
            counts[key] = int(counts.get(key, 0)) + 1
            if counts[key] >= repeat_n or key in global_keys:
                route = ROUTE_ACTIVE
        out.append({"text": text, "source": source, "route": route})
    save_counts(cwd, counts)
    return out


def _lesson_line(text: str, source: str) -> str:
    # 文脈へ読み込ませるのは "- text" だけ。provenance はコメント(read_learned_texts が除去)。
    return f"- {text}  <!-- src={source} -->"


def _read_pairs(path: Path) -> list[tuple]:
    """既存ファイルから (text, source) を復元する(provenance を保つ → 再適用で毎回同じ文章・冪等)。"""
    pairs = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except Exception:
        lines = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("- "):
            continue
        m = re.search(r"<!--\s*src=([^\s>]+)", ln)
        source = m.group(1) if m else "kept"
        text = re.sub(r"\s*<!--.*?-->\s*$", "", ln[2:]).strip()
        if text:
            pairs.append((text, source))
    return pairs


def _write_md(path: Path, items: list[tuple], cap: int, header: str,
              *, reusable_only: bool = False) -> None:
    """items=[(text, source)]。text で重複排除し毎回同じ文章(text 昇順)で書く。
    reusable_only=True で harness 断片・空虚短文を除外(有効リストの物理一掃)。"""
    seen: dict[str, str] = {}
    for text, source in items:
        if not text or (reusable_only and not learning.is_reusable_lesson(text)):
            continue
        if text not in seen:
            seen[text] = source
    rows = sorted(seen.items())[:cap]  # text 昇順 = 同一集合なら同一バイト列
    body = "\n".join(_lesson_line(t, s) for t, s in rows)
    common.write_text_atomic(path, header + body + ("\n" if body else ""))


def apply(cwd: str, lessons: list[dict]) -> dict:
    """lessons=[{text, source, route}] を 有効リスト / 下書き へマージ。

    既存を読み、重複排除・上限・毎回同じ文章で書き直す。{active, draft} 件数を返す。
    provenance(src)は保持に best-effort。**文脈へ読み込ませるバイトに影響する text の集合だけが「毎回同じ文章」の保証対象**。
    """
    shared = common.shared_state_dir(cwd)
    active_path = shared / common.STATE_LEARNED
    draft_path = shared / common.STATE_LEARN_DRAFT
    active = _read_pairs(active_path)
    draft = _read_pairs(draft_path)
    for it in lessons or []:
        text = " ".join((it.get("text") or "").split())[:160]
        if not text:
            continue
        source = it.get("source") or "unknown"
        route = it.get("route") or route_for(source)
        # 防御: 有効(active)行きでも再利用不能(ノイズ/短文)なら下書きへ降格(LLM 出力の保険)。
        if route == ROUTE_ACTIVE and not learning.is_reusable_lesson(text):
            route = ROUTE_DRAFT
        (active if route == ROUTE_ACTIVE else draft).append((text, source))
    _write_md(active_path, active, MAX_ACTIVE,
              "# ultra-ai 学習した約束ごと (自動・毎回まったく同じ文章で文脈へ読み込ませる)\n",
              reusable_only=True)
    _write_md(draft_path, draft, MAX_DRAFT,
              "# ultra-ai 学習の下書き (人手承認待ち・文脈へ読み込ませない)\n")
    active_texts = learning.read_learned_texts(active_path, reusable_only=True)
    # この repo の現行 有効リストを global registry に反映(2+ repo 合意で全プロジェクト共通の約束ごとへ)。
    learning.record_active_lessons(common.repo_key(cwd), active_texts)
    return {"active": len(active_texts),
            "draft": len(learning.read_learned_texts(draft_path))}


def main(argv: list[str]) -> int:
    cwd = os.getcwd()
    cmd = argv[0] if argv else "candidates"
    if cmd == "candidates":
        print(json.dumps(load_candidates(cwd), ensure_ascii=False))
    elif cmd == "apply":  # stdin に lessons JSON([{text,source,route}])
        try:
            data = json.loads(sys.stdin.read() or "[]")
        except Exception:
            data = []
        print(json.dumps(apply(cwd, data if isinstance(data, list) else [])))
        clear_candidates(cwd)
    elif cmd == "auto":  # ルールベースの代替経路(LLM なし): 反復ゲートで振り分け
        res = apply(cwd, auto_lessons(cwd, load_candidates(cwd)))
        print(json.dumps(res))
        clear_candidates(cwd)
    elif cmd == "global":  # 全プロジェクト共通の約束ごと registry の閲覧(/ua-promote 用・読み取り専用)
        repos = common.read_json(common.global_state_dir() / common.STATE_LEARN_REPOS)
        out = {"global_active": learning.read_global_learned_texts(),
               "registry": sorted(
                   ({"text": r.get("text"), "repos": len(r.get("repos") or [])}
                    for r in repos.values()),
                   key=lambda r: (-r["repos"], r["text"] or ""))}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif cmd == "migrate":  # 旧語彙の状態ファイル名を新名へ rename(冪等)
        print(json.dumps({"renamed": migrate_state(cwd)}))
    else:
        print(f"learn.py: unknown command '{cmd}'", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
