#!/usr/bin/env python3
"""notify.py — macOS デスクトップ通知ヘルパ。

二段構えの通知に使う:
  - 第2段(uai 特化): Stop hook の gate.py から `send_event(kind, ...)` を import して呼ぶ
    (応答が本当に完了した=gate.py が exit 0 する瞬間、または検証が詰まった瞬間)。
  - 第1段(標準): Notification hook(matcher `permission_prompt`)の CLI として直接起動。
    stdin の hook payload(notification_type / message / cwd)を読み、承認待ちを通知する。

terminal-notifier があれば、状態で出し分けたアイコン(=通知元アプリ)・クリックで通知元
セッションへ復帰・古い通知の置換(group)が効く。無ければ osascript に縮退(文面のみ)。

設計(rules/python.md 準拠):
  - 純粋部分(コマンド組み立て・kind→文面・ターミナル検出)と IO(subprocess)を分離。
  - 非 macOS / ツール不在 / `UA_NOTIFY=0` では静かに no-op。絶対に hook を壊さない。
  - 通知文面はモデル文脈に注入されない副作用なので、branch・件数・要約など動的な中身を載せてよい。
  - 音は付けない(ユーザー設定)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import common  # UA_NOTIFY kill switch 用(hooks ディレクトリは sys.path 上)

APP_TITLE = "ultra-ai"

# 状態 → タイトルに付ける絵文字(状態で出し分け)。
_KIND_EMOJI = {
    "pass": "✅",       # 検証 PASS
    "unknown": "❓",    # 未検証(テスト/型チェック未検出)
    "done": "✓",       # 変更なしターンの完了
    "stuck": "⚠️",      # 検証が繰り返し失敗=要人手
    "approval": "🔔",   # 承認待ち
    "idle": "💬",       # 入力待ち
}

# VS Code アイコン(-contentImage の右サムネ用・best-effort)。存在しなければ送信時に無視。
VSCODE_ICON = "/Applications/Visual Studio Code.app/Contents/Resources/Code.icns"

# 通知の左(アプリ)アイコン(-appIcon)。未指定だと terminal-notifier 既定=灰色の
# プレースホルダになるため、ultra-ai ロゴを当てる。repo ルート(config_dir の親)に置く
# ultra-ai.png。UA_NOTIFY_ICON で差し替え可。存在しなければ送信時に無視(既定に縮退)。
UAI_ICON = os.environ.get("UA_NOTIFY_ICON") or str(common.config_dir().parent / "ultra-ai.png")

# 人手を待つ kind → 集約での優先度(小さいほど緊急=先頭・click 先)。registry に積む対象も
# これ。idle はコード上対応するが、現状 Notification matcher が permission_prompt のみ=経路なし
# (将来 matcher を広げれば自動で乗る)。
_WAITING_PRIORITY = {"stuck": 0, "approval": 1, "idle": 2}
# 集約通知は固定 group で常に1枚に置換(積み上がらない)。
_AGGREGATE_GROUP = "ultra-ai:aggregate"


# ----------------------------------------------------- pure: command build ----

def _esc(s: str) -> str:
    """AppleScript 文字列リテラル用に " と \\ をエスケープする。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _esc_dq(s: str) -> str:
    """シェルの二重引用符の中に安全に埋めるためのエスケープ(\\ " ` $)。"""
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"), ("$", "\\$")):
        s = s.replace(a, b)
    return s


def _osascript_cmd(title: str, message: str, subtitle: str | None = None) -> list[str]:
    """osascript の display notification コマンド(純関数)。音(sound)は付けない。"""
    script = f'display notification "{_esc(message)}" with title "{_esc(title)}"'
    if subtitle:
        script += f' subtitle "{_esc(subtitle)}"'
    return ["osascript", "-e", script]


def _terminal_notifier_cmd(title: str, message: str, subtitle: str | None = None, *,
                           execute: str | None = None, group: str | None = None,
                           app_icon: str | None = None,
                           content_image: str | None = None,
                           notifier_bin: str = "terminal-notifier") -> list[str]:
    """terminal-notifier コマンド(純関数)。

    execute=クリックで通知元へ戻る方法、group=古い通知を置換、app_icon=左(アプリ)アイコン、
    content_image=右側サムネ、notifier_bin=実行するバイナリ(自前バンドルのパス or PATH 上の名前)。
    `-sender` は使わない(付けるとクリックが送信元起動に固定され `-execute` が無視され、
    Sonoma では不発になる。terminal-notifier に通知を所有させてクリックを効かせる)。`-appIcon` は
    左アイコンの差し替えのみで、`-sender` と違いクリック挙動を壊さない。左アイコンの本命は notifier_bin
    が指す自前バンドル(ロゴを焼き込んだ .app)で、macOS の「左=投稿アプリのアイコン」固定に乗せる。
    """
    cmd = [notifier_bin, "-title", title, "-message", message]
    if subtitle:
        cmd += ["-subtitle", subtitle]
    if execute:
        cmd += ["-execute", execute]
    if group:
        cmd += ["-group", group]
    if app_icon:
        cmd += ["-appIcon", app_icon]
    if content_image:
        cmd += ["-contentImage", content_image]
    return cmd


def select_cmd(title: str, message: str, subtitle: str | None, *,
               has_tn: bool, has_osa: bool, execute: str | None = None,
               group: str | None = None, app_icon: str | None = None,
               content_image: str | None = None,
               notifier_bin: str = "terminal-notifier") -> list[str] | None:
    """どのツールで送るかの純粋な判断。両方無ければ None(=no-op)。

    osascript は execute/group/appIcon/contentImage 非対応=文面のみの無害フォールバック。
    """
    if has_tn:
        return _terminal_notifier_cmd(title, message, subtitle, execute=execute,
                                      group=group, app_icon=app_icon,
                                      content_image=content_image,
                                      notifier_bin=notifier_bin)
    if has_osa:
        return _osascript_cmd(title, message, subtitle)
    return None


def _terminal_target(term_program: str | None,
                     cwd: str | None) -> tuple[str | None, str | None]:
    """通知元ターミナルの (クリック実行コマンド, content_image) を返す(純関数)。

    TERM_PROGRAM で分岐し、クリックで通知元アプリ(セッション)へ復帰させる(`-execute`)。
    `-sender` は使わない(Sonoma でクリック不発になるため)。アイコンは best-effort の
    `-contentImage`(右サムネ)で、VS Code のときだけ付ける。
    vscode は該当プロジェクトのウィンドウを開く/フォーカス(`open` は `code` CLI 不在でも常用可)。
    """
    tp = (term_program or "").strip()
    if tp in ("iTerm.app", "iTerm"):
        return ('open -a "iTerm"', None)
    if tp == "Apple_Terminal":
        return ('open -a "Terminal"', None)
    # vscode・既定: VS Code(このユーザーの主環境)。cwd があればそのプロジェクトへ。
    execute = (f'open -a "Visual Studio Code" "{_esc_dq(cwd)}"' if cwd
               else 'open -a "Visual Studio Code"')
    return (execute, VSCODE_ICON)


def _project_label(cwd: str | None) -> str:
    """通知の subtitle に使うプロジェクト名(cwd の basename)。異常時は "uai"。"""
    try:
        return Path(cwd or Path.cwd()).name or "uai"
    except Exception:
        return "uai"


def _subtitle(cwd: str | None, branch: str | None) -> str:
    """subtitle = "プロジェクト · branch"(branch 無→プロジェクトのみ)。"""
    proj = _project_label(cwd)
    return f"{proj} · {branch}" if branch else proj


def _group_id(cwd: str | None) -> str:
    """通知グループ id。同一プロジェクトは最新1件に置換(積み上がらない)。"""
    try:
        return "ultra-ai:" + str(Path(cwd or Path.cwd()).resolve())
    except Exception:
        return "ultra-ai"


def notification_args(payload: dict) -> tuple[str, str, str | None]:
    """Notification hook の payload を (kind, label, cwd) へ変換(純関数)。"""
    ntype = payload.get("notification_type") or ""
    msg = (payload.get("message") or "").strip()
    cwd = payload.get("cwd")
    if ntype == "idle_prompt":
        return ("idle", msg or "入力待ちです", cwd)
    if ntype == "permission_prompt":
        return ("approval", msg or "承認待ちです", cwd)
    return ("approval", msg or "通知", cwd)


def _smart_cfg() -> dict:
    """賢いタイミング抑制の設定(env で可変・既定 ON)。"""
    return {
        "smart": common.flag_enabled("NOTIFY_SMART"),
        "idle": common.env_int("NOTIFY_IDLE_SEC", 120),
        "mindur": common.env_int("NOTIFY_MINDUR_SEC", 10),
    }


def _aggregate_cfg() -> dict:
    """マルチセッション集約の設定(env で可変・既定 ON)。"""
    return {
        "on": common.flag_enabled("NOTIFY_AGGREGATE"),
        "min": common.env_int("NOTIFY_AGGREGATE_MIN", 2),
        "stale": common.env_int("NOTIFY_STALE_SEC", 14400),  # 4h(承認待ちの離席を許容)
    }


def should_emit(kind: str, *, user_idle_s: float | None,
                wall_clock_s: float | None, cfg: dict) -> bool:
    """通知を実際に出すかの純粋な判断。

    人が要る系(stuck/approval/idle)は常に送る。完了系(pass/done/unknown)は
    「席を外していて(idle≥閾値) かつ そこそこ時間がかかった(所要≥閾値)」時だけ。
    None(取得不能)は安全側=送る(通知を取りこぼさない)。
    """
    if not cfg.get("smart", True):
        return True                       # UA_NOTIFY_SMART=0 → 従来どおり常に送る
    if kind in ("stuck", "approval", "idle"):
        return True
    away = user_idle_s is None or user_idle_s >= cfg.get("idle", 120)
    long_enough = wall_clock_s is None or wall_clock_s >= cfg.get("mindur", 10)
    return away and long_enough


def _aggregate_summary(records: list, *, now: float, stale_sec: int,
                       top_n: int = 4) -> tuple[str, str | None, str | None] | None:
    """待機中セッションを1枚に集約する純粋な判断。

    stale(now-ts>stale_sec)を除き waiting 系に絞り、優先度(stuck>approval>idle)→
    ts 昇順(古い=待たせている順)に並べる。message は `proj+絵文字` を ` / ` 連結
    (top_n 超は `+M`)。click 先は最緊急(最高優先度かつ最古)の record の (cwd, term)
    =送信中セッションではなく record 由来。waiting が無ければ None。
    """
    live = [r for r in records
            if r.get("kind") in _WAITING_PRIORITY
            and (now - float(r.get("ts", 0))) <= stale_sec]
    if not live:
        return None
    live.sort(key=lambda r: (_WAITING_PRIORITY[r["kind"]], float(r.get("ts", 0))))
    parts = [f'{r.get("proj") or "uai"}{_KIND_EMOJI.get(r["kind"], "")}'
             for r in live[:top_n]]
    extra = len(live) - top_n
    if extra > 0:
        parts.append(f"+{extra}")
    head = live[0]
    return (" / ".join(parts), head.get("cwd") or None, head.get("term") or None)


# --------------------------------------------------------------------- IO -----

_HID_IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def _user_idle_seconds() -> float | None:
    """ユーザーの無操作秒数(macOS の HIDIdleTime)。非 darwin / 失敗は None(=安全側)。"""
    if sys.platform != "darwin":
        return None
    try:
        cp = subprocess.run(["ioreg", "-c", "IOHIDSystem"], check=False,
                            capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    m = _HID_IDLE.search(cp.stdout or "")
    return int(m.group(1)) / 1e9 if m else None


def _notifier_bin() -> str:
    """通知に使う terminal-notifier バイナリを決める(IO=ファイル存在確認)。

    左アイコンを ultra-ai ロゴにするための自前バンドル
    (`state/notifier/ultra-ai-notifier.app`・launcher の `ua_notifier_ensure` が生成)が在れば
    その絶対パスを優先。無ければ PATH 上の `"terminal-notifier"` に縮退(=従来の灰色アイコン)。
    """
    app = (common.config_dir() / "state" / "notifier" / "ultra-ai-notifier.app"
           / "Contents" / "MacOS" / "terminal-notifier")
    return str(app) if app.exists() else "terminal-notifier"


def _has_notifier(notifier_bin: str) -> bool:
    """notifier_bin が使えるか(絶対パスなら実在、名前なら PATH 解決)。"""
    if os.path.isabs(notifier_bin):
        return os.path.exists(notifier_bin)
    return bool(shutil.which(notifier_bin))


def _send(title: str, message: str, subtitle: str | None = None, *,
          execute: str | None = None, group: str | None = None,
          app_icon: str | None = None, content_image: str | None = None) -> None:
    """通知を送る。`UA_NOTIFY=0` / 非 darwin / ツール不在 / 失敗時は静かに no-op。"""
    if not common.flag_enabled("NOTIFY"):
        return
    if sys.platform != "darwin":
        return
    # app_icon / content_image は存在するファイルのときだけ渡す(無ければ既定アイコンに縮退)。
    ai = app_icon if (app_icon and os.path.exists(app_icon)) else None
    ci = content_image if (content_image and os.path.exists(content_image)) else None
    tn = _notifier_bin()
    cmd = select_cmd(title, message, subtitle,
                     has_tn=_has_notifier(tn),
                     has_osa=bool(shutil.which("osascript")),
                     execute=execute, group=group, app_icon=ai, content_image=ci,
                     notifier_bin=tn)
    if not cmd:
        return
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=5)
    except Exception:
        return


def _unlink_quiet(path) -> None:
    """存在すれば削除・失敗は黙殺(hook を壊さない)。"""
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _remove_group(group: str) -> None:
    """terminal-notifier の指定 group の通知を消す(IO)。`UA_NOTIFY=0`/非 darwin/
    tool 不在/失敗で no-op。`_send` は -message 必須前提なので流用せず専用に。"""
    if not common.flag_enabled("NOTIFY") or sys.platform != "darwin":
        return
    tn = _notifier_bin()                         # 投稿元と同じバイナリで消す(bundle id 一致)。
    if not _has_notifier(tn):
        return
    try:
        subprocess.run([tn, "-remove", group],
                       check=False, capture_output=True, timeout=5)
    except Exception:
        return


def _record_pending(kind: str, *, session_id: str | None, cwd: str | None,
                    branch: str | None, label: str, term: str | None,
                    now: float) -> None:
    """自セッションの待機状態を registry に反映(IO)。

    waiting 系(stuck/approval/idle)は record を upsert、解決系(pass/done/unknown)は
    自分の record を削除。session_id 無し / `UA_NOTIFY` / `UA_NOTIFY_AGGREGATE` off で no-op。
    """
    if not (session_id and common.flag_enabled("NOTIFY")
            and common.flag_enabled("NOTIFY_AGGREGATE")):
        return
    path = common.pending_path(session_id)
    if kind in _WAITING_PRIORITY:
        common.write_json_atomic(path, {
            "ts": now, "session_id": session_id, "cwd": cwd or "",
            "proj": _project_label(cwd), "branch": branch, "kind": kind,
            "label": label, "term": term or "",
        })
    else:
        _unlink_quiet(path)


def clear_pending(session_id: str | None) -> bool:
    """セッションが活動を再開した(=待機ではない)とき、自分の pending record を消す(IO)。

    PostToolUse(承認に答えて tool が走った)/ SessionEnd から呼ぶ。`UA_NOTIFY(_AGGREGATE)` off /
    session_id 無し は no-op。record が在って消したら True(=集約の再評価が要る)、無ければ False。
    """
    if not (session_id and common.flag_enabled("NOTIFY")
            and common.flag_enabled("NOTIFY_AGGREGATE")):
        return False
    path = common.pending_path(session_id)
    existed = path.exists()
    _unlink_quiet(path)
    return existed


def note_active(session_id: str | None) -> None:
    """活動再開シグナルで待機を解除し集約を再評価する。

    自分の record が在ったときだけ集約を更新(無ければ安価に no-op=毎 tool 呼んでも軽い)。
    """
    if clear_pending(session_id):
        send_pending_summary(time.time())


def _aggregate_sig_path() -> Path:
    """集約 dedup の署名ファイル。pending_dir の外=record glob/stale prune に拾われない。
    親(state/)は pending_dir() が必ず作るので、呼び出し順に依存せず書き込める。"""
    return common.pending_dir().parent / "notify_aggregate.json"


def _aggregate_sig(waiting: list) -> str:
    """待機集合の署名: `session_id:kind` を並べ替えて連結(ts/proj の表示順に依存しない)。"""
    return ",".join(sorted(f'{r.get("session_id")}:{r.get("kind")}' for r in waiting))


def _read_aggregate_sig() -> str | None:
    return common.read_json(_aggregate_sig_path()).get("sig")


def _write_aggregate_sig(sig: str) -> None:
    common.write_json_atomic(_aggregate_sig_path(), {"sig": sig})


def _clear_aggregate_sig() -> None:
    _unlink_quiet(_aggregate_sig_path())


def send_pending_summary(now: float) -> None:
    """全セッションの registry を読み、stale を prune して集約通知を更新(IO)。

    待機中 >= `UA_NOTIFY_AGGREGATE_MIN` なら集約1枚を送る(固定 group で置換)、未満なら
    集約 group を消す(stale ダッシュボードを残さない)。`UA_NOTIFY(_AGGREGATE)=0` で no-op。
    """
    cfg = _aggregate_cfg()
    if not (cfg["on"] and common.flag_enabled("NOTIFY")):
        return
    try:
        paths = list(common.pending_dir().glob("*.json"))
    except Exception:
        paths = []
    waiting = []
    for p in paths:
        rec = common.read_json(p)
        if not rec:                                  # 壊れた/空 → スキップ(read_json 縮退)
            continue
        if (now - float(rec.get("ts", 0))) > cfg["stale"]:
            _unlink_quiet(p)                         # stale なファイルだけ掃除(生 record は消さない)
            continue
        if rec.get("kind") in _WAITING_PRIORITY:
            waiting.append(rec)
    summary = (_aggregate_summary(waiting, now=now, stale_sec=cfg["stale"])
               if len(waiting) >= cfg["min"] else None)
    if not summary:
        _remove_group(_AGGREGATE_GROUP)
        _clear_aggregate_sig()                       # 静かになった→次の待機集合で再アラート可
        return
    sig = _aggregate_sig(waiting)
    if sig == _read_aggregate_sig():
        return                                       # 同一集合→再アラートしない(置換 post を止める)
    message, click_cwd, click_term = summary
    execute, content_image = _terminal_target(click_term, click_cwd)
    _send(f"{APP_TITLE} ⏳", message, f"{len(waiting)}件が待機中",
          execute=execute, group=_AGGREGATE_GROUP, app_icon=UAI_ICON,
          content_image=content_image)
    _write_aggregate_sig(sig)


def send_event(kind: str, *, label: str, cwd: str | None = None,
               branch: str | None = None, detail: str | None = None,
               term_program: str | None = None,
               wall_clock_s: float | None = None,
               session_id: str | None = None) -> None:
    """状態(kind)に応じた通知を送る統一 API。

    title=`ultra-ai <絵文字>`、message=`label`(detail があれば付加)、
    subtitle=`プロジェクト · branch`。execute/content_image は通知元ターミナルから決める。
    賢いタイミング(should_emit)で完了系を抑制する(人が要る系は常に送る)。
    session_id がある(=実 hook 由来)なら、抑制とは独立にマルチセッション集約 registry を
    更新し集約通知を再評価する(解決系で個別が抑制されても registry は必ず更新)。
    """
    tp = term_program if term_program is not None else os.environ.get("TERM_PROGRAM")
    cfg = _smart_cfg()
    emit = (not cfg["smart"]) or should_emit(
        kind, user_idle_s=_user_idle_seconds(), wall_clock_s=wall_clock_s, cfg=cfg)
    if emit:
        emoji = _KIND_EMOJI.get(kind, "")
        title = f"{APP_TITLE} {emoji}".rstrip()
        if detail and len(detail) > 100:  # 本文は2〜3行=detail を ~100字で切り詰め
            detail = detail[:99] + "…"
        message = f"{label} · {detail}" if detail else label
        execute, content_image = _terminal_target(tp, cwd)
        _send(title, message, _subtitle(cwd, branch),
              execute=execute, group=_group_id(cwd), app_icon=UAI_ICON,
              content_image=content_image)
    # ③ マルチセッション集約: 個別通知の抑制とは独立に registry を更新し集約を再評価する。
    if session_id:
        now = time.time()
        _record_pending(kind, session_id=session_id, cwd=cwd, branch=branch,
                        label=label, term=tp, now=now)
        send_pending_summary(now)


def turn_complete(label: str = "完了", cwd: str | None = None) -> None:
    """後方互換: 応答完了を通知(kind=done)。新規呼び出しは send_event を使う。"""
    send_event("done", label=label, cwd=cwd)


def _notification_cli() -> None:
    """Notification hook 用 CLI。stdin の payload を読み、承認/入力待ちを通知。"""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    kind, label, cwd = notification_args(payload)
    sid = payload.get("session_id")
    branch = None
    try:
        branch = common.git_branch(cwd) if cwd else None
    except Exception:
        branch = None
    send_event(kind, label=label, cwd=cwd, branch=branch, session_id=sid)


def _session_end_cli() -> None:
    """SessionEnd hook 用 CLI。終了セッションの pending record を消し集約を更新する。

    承認待ちのまま閉じた残骸(=ゴースト)を 4h の stale を待たずに即時掃除する。
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    note_active(payload.get("session_id"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "session-end":
        _session_end_cli()
    else:
        _notification_cli()
