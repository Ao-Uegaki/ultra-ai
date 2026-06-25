#!/usr/bin/env python3
"""gate.py — Stop hook: iterate-until-pass ゲート + チェックポイントの起点。

毎ターン終了時に:
  - 新しく検証すべきものがあるか判断する。真実 = `git status --porcelain`
    (PostToolUse が取りこぼす Bash 由来の編集も捕まえる)。`.dirty` は非 git ディレクトリ
    向けの二次ヒント。前回検証した状態から何も変わっていなければ exit 0
    (毎ターン全スイートを回し直さない)。
  - プロジェクトの型チェック + テストを実行(プロジェクト設定優先、無ければ自動検出)。
  - 3状態:
      PASS    -> exit 0。progress を更新。(既定では自動コミットしない)
      FAIL    -> exit 2 + stderr に ≤20 行の構造化要約(full log は保存)。
                 Claude Code は停止を阻止される -> モデルは修正を続ける。
                 反復は CLAUDE_CODE_STOP_HOOK_BLOCK_CAP(settings.json)で上限を設ける。
                 連続失敗時はエスカレート: テスト自体が誤りかもしれないと人に伝える。
      UNKNOWN -> exit 0。ただし一度だけ可視の告知(ゲート不活性 / 未検証)を出す。
                 「実行できなかった」を黙って PASS にしない。チェックポイントもしない。

終了コード: 0 = 停止を許可 / 2 = 停止をブロックし stderr をモデルへ渡す。
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import detect  # noqa: E402

ESCALATE = ("⚠ ultra-ai: 同じ検証が連続して失敗しています。テストや型定義そのものが誤っている "
            "可能性があります — コードで直せないなら無理に反復せず、人に確認してください。")
UNKNOWN_NOTICE = ("ⓘ ultra-ai: テスト/型チェックのコマンドが検出できず、PASS ゲートは働いていません "
                  "(未検証・自動チェックポイントなし)。.ultra-ai.toml で verify を設定できます。")

# 通知意図(notify.send_event の引数 dict、または None=無音)。main() が結果に応じて設定し、
# __main__ が送る。通常の FAIL(修正ループ中)は None=無音、詰まり(escalate)では ⚠️ を送る。
_NOTIFY: dict | None = None


# ----------------------------------------------------- pure decision logic ----

def resolve_commands(root: str) -> tuple[str | None, str | None, str, int]:
    """typecheck, test, scope, timeout — project config wins over auto-detect."""
    cfg = detect.verify_config(root)
    st = detect.detect_stack(root)
    return (cfg.get("typecheck", st.typecheck),
            cfg.get("test", st.test),
            cfg.get("scope", "impacted"),
            int(cfg.get("timeout_seconds", 180)))


def aggregate(results: list[tuple[str, str, str]]) -> str:
    """Overall tri-state from the individual check results."""
    if not results:
        return common.UNKNOWN
    states = [s for _, s, _ in results]
    if common.FAIL in states:
        return common.FAIL
    if common.UNKNOWN in states:
        return common.UNKNOWN
    return common.PASS


def evaluate(prior: dict, signature: str, results: list[tuple[str, str, str]]) -> dict:
    """Decide what to do given prior state + this run's results. Pure/testable.

    提案層の dedup state(`suggest`)は結果に関わらず引き継ぐ。これを落とすと FAIL→PASS の
    たびに dedup がリセットされ、PASS のたびに提案が再発火(nag)してしまう。
    """
    suggest = prior.get("suggest", {})
    overall = aggregate(results)
    if overall == common.PASS:
        return {"overall": common.PASS, "exit": 0, "escalate": False, "notify": False,
                "state": {"signature": signature, "result": common.PASS, "fail_streak": 0,
                          "suggest": suggest}}
    if overall == common.FAIL:
        streak = prior.get("fail_streak", 0) + 1
        return {"overall": common.FAIL, "exit": 2, "escalate": streak >= 2, "notify": False,
                "state": {"signature": signature, "result": common.FAIL, "fail_streak": streak,
                          "suggest": suggest}}
    already = prior.get("notified_sig") == signature
    return {"overall": common.UNKNOWN, "exit": 0, "escalate": False, "notify": not already,
            "state": {"signature": signature, "result": common.UNKNOWN, "fail_streak": 0,
                      "notified_sig": signature, "suggest": suggest}}


def sig_of(head: str, status_lines: list[str]) -> str:
    return hashlib.sha256((head + "\0" + "\n".join(status_lines)).encode()).hexdigest()[:32]


def build_fail_summary(results: list[tuple[str, str, str]], log_path: str | None = None) -> str:
    failed = [(n, o) for n, s, o in results if s == common.FAIL]
    per = max(4, 16 // max(1, len(failed)))
    blocks = [f"✗ {n} が失敗:\n{common.condense(o, limit=per)}" for n, o in failed]
    msg = "\n".join(blocks) or "✗ 検証に失敗しました。"
    if log_path:
        msg += f"\n(完全ログ: {log_path})"
    return msg


# ------------------------------------------------- pure: notification intent --
# 通知文面はモデル文脈に注入されない副作用なので、件数・要約など動的な中身を載せてよい。

def _pass_detail(results: list[tuple[str, str, str]], file_count: int, *,
                 added: int = 0, wall_clock_s: float | None = None) -> str | None:
    """PASS 通知の concise な中身(純関数)。例: "typecheck✓ test✓ · 4 files(+87) · 2:15"。"""
    mark = {common.PASS: "✓", common.UNKNOWN: "?", common.FAIL: "✗"}
    checks = " ".join(f"{n}{mark.get(s, '?')}" for n, s, _ in results)
    files_str = (f"{file_count} files" + (f"(+{added})" if added else "")) if file_count else ""
    elapsed = ""
    if wall_clock_s and wall_clock_s >= 1:
        m, s = divmod(int(wall_clock_s), 60)
        elapsed = f"{m}:{s:02d}"
    parts = [p for p in (checks, files_str, elapsed) if p]
    return " · ".join(parts) or None


def _fail_headline(summary: str) -> str:
    """詰まり通知の中身=要約の先頭の意味ある1行(純関数・ESCALATE 行は飛ばす)。"""
    for ln in (summary or "").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("⚠"):
            return ln[:80]
    return "検証に失敗"


def _fail_detail(summary: str, fail_streak: int = 0) -> str:
    """詰まり通知の中身=失敗の核心1行(+連続回数)。純関数。

    "… が失敗:" の直後の最初の意味ある行(エラー/Assertion 等)を拾う。
    抽出不能は _fail_headline にフォールバック(best-effort・framework 形式依存)。
    """
    lines = (summary or "").splitlines()
    core = None
    for i, ln in enumerate(lines):
        if "が失敗:" in ln:
            for cand in lines[i + 1:i + 4]:
                c = cand.strip()
                if c and not c.startswith("(") and not c.startswith("⚠"):
                    core = c[:70]
                    break
            if core:
                break
    if not core:
        core = _fail_headline(summary)
    if fail_streak and fail_streak >= 2:
        core += f" · 連続{fail_streak}回失敗"
    return core


def stop_notification(overall: str, *, escalate: bool, cwd: str, branch: str | None,
                      pass_detail: str | None = None, fail_headline: str | None = None,
                      wall_clock_s: float | None = None,
                      session_id: str | None = None) -> dict | None:
    """Stop hook の通知意図を返す(純関数)。通常の FAIL(修正ループ中)は None=無音。

    session_id はマルチセッション集約 registry のキー(notify.send_event が使う)。
    """
    if overall == common.PASS:
        return {"kind": "pass", "label": "完了(PASS)", "detail": pass_detail,
                "cwd": cwd, "branch": branch, "wall_clock_s": wall_clock_s,
                "session_id": session_id}
    if overall == common.UNKNOWN:
        return {"kind": "unknown", "label": "完了(未検証)", "detail": None,
                "cwd": cwd, "branch": branch, "wall_clock_s": wall_clock_s,
                "session_id": session_id}
    if overall == common.FAIL and escalate:
        return {"kind": "stuck", "label": "検証が詰まった(要確認)", "detail": fail_headline,
                "cwd": cwd, "branch": branch, "wall_clock_s": wall_clock_s,
                "session_id": session_id}
    return None


# テストが「0件しか収集していない」のに exit 0 を返す false-pass を検出する。
# 例: pytest 関数スタイルのテストに `unittest discover` を当てると `Ran 0 tests ... OK`。
# 「0件=未検証」なので PASS でなく UNKNOWN に降格する(憲法「UNKNOWN は PASS ではない」)。
# unittest/pytest に加え、node(jest/vitest)の 0件表現も拾う。jest/vitest は既定では 0件で
# 非0 exit=既に FAIL だが、`--passWithNoTests` を設定した構成では exit0 で素通りしうる
# (=false-pass)。その defense-in-depth。go(`no test files`)は detect に go 分岐が無く
# dead code なので入れない(go 検出を足すとき同時に)。
_EMPTY_TEST = re.compile(
    r"\bRan 0 tests\b|\bno tests ran\b|collected 0 items"
    r"|\bno tests found\b|\bno test files found\b", re.IGNORECASE)


def _empty_test_run(output: str) -> bool:
    return bool(_EMPTY_TEST.search(output or ""))


# ------------------------------------------------------------------- IO -------

def run_checks(root, typecheck, test, timeout, runner,
               scope="full", changed=None) -> list[tuple[str, str, str]]:
    # scope=="impacted" のとき、変更集合から対象テストへ保守的に絞る。
    # マップ不能なら full にフォールバック(精度を落とさない側へ)。
    test_cmd = test
    if scope == "impacted" and test:
        narrowed = detect.impacted_test_cmd(test, changed or [],
                                            detect.detect_stack(root), root)
        if narrowed:
            test_cmd = narrowed
    out = []
    for name, cmd in (("typecheck", typecheck), ("test", test_cmd)):
        if cmd:
            state, output = runner(cmd, root, timeout=timeout)
            if name == "test" and state == common.PASS and _empty_test_run(output):
                state = common.UNKNOWN  # 0件しか走っていない=未検証であって PASS ではない
            out.append((name, state, output))
    return out


def _load_state(sdir: Path) -> dict:
    return common.read_json(sdir / common.STATE_VERIFICATION)


def _save_state(sdir: Path, state: dict) -> None:
    common.write_json_atomic(sdir / common.STATE_VERIFICATION, state)


def _write_log(sdir: Path, results) -> str | None:
    try:
        logs = sdir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        p = logs / (time.strftime("%Y%m%d-%H%M%S")
                    + f"-{int(time.time() * 1e6) % 1_000_000:06d}.log")
        common.write_text_atomic(p, "\n\n".join(f"=== {n} [{s}] ===\n{o}" for n, s, o in results))
        return str(p)
    except Exception:
        return None


def _update_progress(cwd: str, root: str, is_git: bool) -> None:
    try:
        shared = common.shared_state_dir(cwd)
        branch = common.git_branch(cwd) if is_git else None
        head = common.git_head(cwd) if is_git else None
        changed = common.git_status_porcelain(cwd) if is_git else []
        lines = ["# ultra-ai progress", "",
                 f"- repo: {root}",
                 f"- branch: {branch}   HEAD: {head}",
                 f"- last verified PASS: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"- uncommitted changes: {len(changed)} file(s)",
                 "- next: continue, or run /ua-checkpoint to commit this PASS state"]
        common.write_text_atomic(shared / common.STATE_PROGRESS, "\n".join(lines) + "\n")
    except Exception:
        pass


def _capture_failpass(cwd: str, prior: dict) -> None:
    """直前 FAIL → 今 PASS = FAIL→PASS 遷移を学習候補として捕捉する(capture・注入しない)。

    `UA_AUTOAPPLY` OFF、または直前が FAIL でないなら no-op。決して Stop を壊さない。
    候補は「あいまい」(PASS は相関であって因果ではない)なので learn 側で下書き行き。
    """
    try:
        if not common.autoapply_enabled() or prior.get("result") != common.FAIL:
            return
        cand = {"source": "fail-pass",
                "text": common.condense(prior.get("summary", ""), limit=6),
                "branch": common.git_branch(cwd), "head": common.git_head(cwd)}
        common.append_jsonl_capped(
            common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES, cand, cap=500)
    except Exception:
        pass


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except Exception:
        pass


# ----------------------------------------- proactive suggestions (Tier 2) -----
# PASS になった瞬間に、関係する on-demand skill を「適切な場面で一度だけ」提案する
# (手動 invoke だけに頼る機能が忘却で死ぬのを防ぐ)。fire ではなく提案=起動可否は人/モデル。
# nag 回避: ①関係条件 ②1状態1回(working-tree 署名) ③時間 throttle(各種類 最大30分に1回)。
# チャネルは stderr(失敗フィードバック経路=prompt cache を冷やさない)。各々 UA_SUGGEST_* で可逆。

_CKPT_HINT = ("ⓘ ultra-ai: PASS です。追跡済みの未コミット変更があります — "
              "/ua-checkpoint でこの PASS 状態を保存できます(自動コミットはしません)。")
_REFACTOR_HINT = ("ⓘ ultra-ai: PASS です。直近の差分が大きめです — "
                  "/ua-refactor で挙動不変の整理(型分類→小さく適用→型別コミット)を検討できます。")


def _suggest_cfg() -> dict:
    """提案層の on/off と閾値(env で上書き可・既定 ON / 30分 / 4ファイル / 80行)。"""
    return {"ckpt_on": common.flag_enabled("SUGGEST_CHECKPOINT"),
            "refactor_on": common.flag_enabled("SUGGEST_REFACTOR"),
            "ckpt_throttle_sec": common.env_int("CKPT_THROTTLE_SEC", 1800),
            "refactor_throttle_sec": common.env_int("REFACTOR_THROTTLE_SEC", 1800),
            "refactor_min_files": common.env_int("REFACTOR_MIN_FILES", 4),
            "refactor_min_added": common.env_int("REFACTOR_MIN_ADDED", 80)}


def _throttle_ok(last_ts, now: float, window: int) -> bool:
    """前回提案(last_ts)から window 秒以上経過か。未提案(None)なら常に可。"""
    return last_ts is None or (now - last_ts) >= window


def decide_suggestions(prior_suggest: dict, sig: str, *, tracked: int,
                       files_changed: int, added: int, now: float,
                       cfg: dict) -> tuple[list, dict]:
    """出す提案文言と、更新後の suggest state を返す(純関数・テスト容易)。

    1状態1回(`*_sig != sig`)+ 時間 throttle(`*_ts`)の二重 dedup。skill を使うと
    HEAD/working-tree 署名が変わり `sig` が更新される=自然リセット(別途検知は不要)。
    """
    out, nxt = [], dict(prior_suggest or {})
    if (cfg["ckpt_on"] and tracked >= 1 and nxt.get("ckpt_sig") != sig
            and _throttle_ok(nxt.get("ckpt_ts"), now, cfg["ckpt_throttle_sec"])):
        out.append(_CKPT_HINT)
        nxt["ckpt_sig"], nxt["ckpt_ts"] = sig, now
    if (cfg["refactor_on"]
            and (files_changed >= cfg["refactor_min_files"] or added >= cfg["refactor_min_added"])
            and nxt.get("refactor_sig") != sig
            and _throttle_ok(nxt.get("refactor_ts"), now, cfg["refactor_throttle_sec"])):
        out.append(_REFACTOR_HINT)
        nxt["refactor_sig"], nxt["refactor_ts"] = sig, now
    return out, nxt


def _diff_size(cwd: str) -> tuple[int, int]:
    """HEAD 比の (変更ファイル数, 追加行数)。read-only・git 失敗時は (0, 0) へ縮退。

    初コミット前(HEAD 無し)は returncode!=0 → (0,0)=refactor 提案は出ない(意図どおり・安全側)。
    バイナリ行は `-\\t-\\tpath` なので files は数えるが added はスキップ(isdigit ガード)。
    """
    cp = common._run(["git", "diff", "--numstat", "HEAD"], cwd)
    if not cp or cp.returncode != 0:
        return (0, 0)
    files = added = 0
    for ln in cp.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3:
            files += 1
            if parts[0].isdigit():
                added += int(parts[0])
    return (files, added)


def _read_wall_clock(sdir) -> float | None:
    """metrics.snapshot が書いた metrics.json から所要秒を読む(無ければ None=安全側)。"""
    try:
        return common.read_json(sdir / common.STATE_METRICS).get("wall_clock_s")
    except Exception:
        return None


def main() -> int:
    global _NOTIFY
    payload = common.read_hook_input()
    # 計測スナップショット(ゼロトークン)。transcript は累積なので毎 Stop 冒頭で
    # 1回呼べば全ターンを捕捉できる。絶対に Stop をブロックしない(無音 try/except)。
    try:
        import metrics
        metrics.snapshot(payload)
    except Exception:
        pass
    cwd = common.hook_cwd(payload)
    sid = payload.get("session_id")
    sdir = common.session_state_dir(cwd, sid)
    dirty = sdir / common.STATE_DIRTY
    is_git = common.is_git_repo(cwd)
    branch = common.git_branch(cwd) if is_git else None
    wclock = _read_wall_clock(sdir)

    # Anything to verify?
    if is_git:
        status = common.git_status_porcelain(cwd)
        changed = bool(status)
    else:
        status = []
        changed = dirty.exists()
    if not changed:
        _unlink(dirty)
        _NOTIFY = {"kind": "done", "label": "完了", "detail": None,
                   "cwd": cwd, "branch": branch, "wall_clock_s": wclock,
                   "session_id": sid}
        return 0

    prior = _load_state(sdir)
    sig = sig_of(common.git_head(cwd) or "", status) if is_git else ""

    # Dedup: this exact working-tree state was already evaluated -> don't re-run.
    if is_git and sig and sig == prior.get("signature"):
        res = prior.get("result")
        if res in (common.PASS, common.UNKNOWN):
            _NOTIFY = stop_notification(res, escalate=False, cwd=cwd, branch=branch,
                                        wall_clock_s=wclock, session_id=sid)
            return 0
        if res == common.FAIL:  # unchanged + still failing -> re-block (no re-run)
            streak = prior.get("fail_streak", 0) + 1
            prior["fail_streak"] = streak
            _save_state(sdir, prior)
            msg = prior.get("summary", "✗ verification still failing.")
            print((ESCALATE + "\n" + msg) if streak >= 2 else msg, file=sys.stderr)
            _NOTIFY = stop_notification(common.FAIL, escalate=streak >= 2, cwd=cwd,
                                        branch=branch, fail_headline=_fail_detail(msg, streak),
                                        session_id=sid)
            return 2

    root = common.project_root(cwd)
    typecheck, test, scope, timeout = resolve_commands(root)
    results = run_checks(root, typecheck, test, timeout, common.run_cmd,
                         scope=scope, changed=status)
    decision = evaluate(prior, sig, results)
    state = decision["state"]

    if decision["overall"] == common.FAIL:
        summary = build_fail_summary(results, _write_log(sdir, results))
        if decision["escalate"]:
            summary = ESCALATE + "\n" + summary
        state["summary"] = summary[:4000]
        _save_state(sdir, state)
        print(summary, file=sys.stderr)
        _NOTIFY = stop_notification(common.FAIL, escalate=decision["escalate"], cwd=cwd,
                                    branch=branch, wall_clock_s=wclock,
                                    fail_headline=_fail_detail(summary, state.get("fail_streak", 0)),
                                    session_id=sid)
        return 2

    if decision["overall"] == common.PASS:
        # PASS の瞬間の proactive 提案(stderr・1状態1回・throttle・各々 UA_SUGGEST_* で可逆)。
        tracked = sum(1 for ln in status if ln[:2] != "??") if is_git else 0
        files, added = _diff_size(cwd) if is_git else (0, 0)
        _NOTIFY = stop_notification(common.PASS, escalate=False, cwd=cwd, branch=branch,
                                    wall_clock_s=wclock, session_id=sid,
                                    pass_detail=_pass_detail(results, files, added=added,
                                                             wall_clock_s=wclock))
        hints, new_suggest = decide_suggestions(
            prior.get("suggest", {}), sig, tracked=tracked, files_changed=files,
            added=added, now=time.time(), cfg=_suggest_cfg())
        state["suggest"] = new_suggest  # 注入されない state=timestamp 可(cache を冷やさない)
        _save_state(sdir, state)
        _update_progress(cwd, root, is_git)
        _capture_failpass(cwd, prior)  # FAIL→PASS を学習候補へ(UA_AUTOAPPLY 下のみ・注入しない)
        _unlink(dirty)
        if hints:
            print("\n".join(hints), file=sys.stderr)
        return 0

    # UNKNOWN
    _NOTIFY = stop_notification(common.UNKNOWN, escalate=False, cwd=cwd, branch=branch,
                                wall_clock_s=wclock, session_id=sid)
    _save_state(sdir, state)
    if decision["notify"]:
        print(UNKNOWN_NOTICE, file=sys.stderr)
    return 0


if __name__ == "__main__":
    rc = main()
    if _NOTIFY:                       # 完了(PASS/未検証/done)、または詰まり(escalate)の瞬間だけ通知。
        try:                          # 通常の FAIL(修正ループ中)は _NOTIFY=None=無音。
            import notify             # hooks ディレクトリは上部で sys.path に追加済み
            notify.send_event(**_NOTIFY)
        except Exception:
            pass                      # 通知失敗で Stop を絶対に壊さない
    sys.exit(rc)
