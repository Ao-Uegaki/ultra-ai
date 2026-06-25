"""Unit tests for claude-home/hooks/notify.py (run: python3 -m unittest)."""
import io
import sys
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import notify  # noqa: E402


class TestOsascriptCmd(unittest.TestCase):
    def test_basic_shape(self):
        cmd = notify._osascript_cmd("ultra-ai", "完了", "proj")
        self.assertEqual(cmd[0], "osascript")
        self.assertEqual(cmd[1], "-e")
        self.assertIn('display notification "完了"', cmd[2])
        self.assertIn('with title "ultra-ai"', cmd[2])
        self.assertIn('subtitle "proj"', cmd[2])

    def test_no_subtitle_omits_clause(self):
        cmd = notify._osascript_cmd("t", "m")
        self.assertNotIn("subtitle", cmd[2])

    def test_escapes_quotes_and_backslash(self):
        # " と \ を含む文面が AppleScript 文字列を壊さない
        cmd = notify._osascript_cmd("t", 'say "hi"\\done')
        self.assertIn('\\"hi\\"', cmd[2])
        self.assertIn("\\\\done", cmd[2])

    def test_no_sound(self):
        # 音は付けない: 生成コマンドに sound を含めない
        cmd = notify._osascript_cmd("t", "m", "s")
        self.assertNotIn("sound", cmd[2])


class TestTerminalNotifierCmd(unittest.TestCase):
    def test_includes_subtitle(self):
        cmd = notify._terminal_notifier_cmd("t", "m", "s")
        self.assertEqual(cmd[0], "terminal-notifier")
        self.assertIn("-subtitle", cmd)
        self.assertIn("s", cmd)
        self.assertNotIn("-sound", cmd)  # 音なし

    def test_omits_subtitle_flag_when_absent(self):
        cmd = notify._terminal_notifier_cmd("t", "m")
        self.assertNotIn("-subtitle", cmd)

    def test_execute_group_content_image_flags(self):
        cmd = notify._terminal_notifier_cmd(
            "t", "m", "s", execute='open -a "Visual Studio Code" "/p"',
            group="ultra-ai:/p", app_icon="/r/ultra-ai.png",
            content_image="/Applications/X.app/Code.icns")
        self.assertEqual(cmd[cmd.index("-execute") + 1], 'open -a "Visual Studio Code" "/p"')
        self.assertEqual(cmd[cmd.index("-group") + 1], "ultra-ai:/p")
        self.assertEqual(cmd[cmd.index("-appIcon") + 1], "/r/ultra-ai.png")
        self.assertEqual(cmd[cmd.index("-contentImage") + 1], "/Applications/X.app/Code.icns")
        self.assertNotIn("-sender", cmd)  # -sender は使わない(Sonoma でクリック不発)

    def test_app_icon_flag_when_present(self):
        # 左(アプリ)アイコンは -appIcon で渡す(右サムネ -contentImage とは独立)
        cmd = notify._terminal_notifier_cmd("t", "m", app_icon="/r/ultra-ai.png")
        self.assertEqual(cmd[cmd.index("-appIcon") + 1], "/r/ultra-ai.png")
        self.assertNotIn("-contentImage", cmd)  # app_icon だけでは右サムネは付かない

    def test_default_notifier_bin_is_plain_name(self):
        cmd = notify._terminal_notifier_cmd("t", "m")
        self.assertEqual(cmd[0], "terminal-notifier")

    def test_notifier_bin_override_is_argv0(self):
        # 自前バンドルの絶対パスを渡すと argv[0] に使う(左アイコン=ロゴの本命経路)
        cmd = notify._terminal_notifier_cmd("t", "m", notifier_bin="/x/ultra-ai-notifier.app/Contents/MacOS/terminal-notifier")
        self.assertEqual(cmd[0], "/x/ultra-ai-notifier.app/Contents/MacOS/terminal-notifier")

    def test_omits_optional_flags_when_absent(self):
        cmd = notify._terminal_notifier_cmd("t", "m")
        for flag in ("-sender", "-execute", "-group", "-appIcon", "-contentImage"):
            self.assertNotIn(flag, cmd)


class TestSelectCmd(unittest.TestCase):
    def test_prefers_terminal_notifier(self):
        cmd = notify.select_cmd("t", "m", None, has_tn=True, has_osa=True)
        self.assertEqual(cmd[0], "terminal-notifier")

    def test_terminal_notifier_carries_options(self):
        cmd = notify.select_cmd("t", "m", None, has_tn=True, has_osa=True,
                                execute="x", group="g", app_icon="/logo.png",
                                content_image="/i.icns", notifier_bin="/x/tn")
        self.assertEqual(cmd[0], "/x/tn")  # notifier_bin が argv[0] に素通し
        self.assertIn("-execute", cmd)
        self.assertIn("-group", cmd)
        self.assertIn("-appIcon", cmd)
        self.assertIn("-contentImage", cmd)

    def test_falls_back_to_osascript_ignoring_options(self):
        # osascript は execute/group/appIcon/contentImage 非対応=文面のみの無害フォールバック
        cmd = notify.select_cmd("t", "m", None, has_tn=False, has_osa=True,
                                execute="y", group="z", app_icon="/logo.png",
                                content_image="/i.icns")
        self.assertEqual(cmd[0], "osascript")
        for flag in ("-execute", "-group", "-appIcon", "-contentImage"):
            self.assertNotIn(flag, cmd)

    def test_none_when_neither_available(self):
        self.assertIsNone(notify.select_cmd("t", "m", None, has_tn=False, has_osa=False))


class TestNotifierBin(unittest.TestCase):
    """自前バンドル(左アイコン=ロゴ)の解決と縮退。"""

    def _patch_config_dir(self, root):
        import pathlib
        orig = notify.common.config_dir
        notify.common.config_dir = lambda: pathlib.Path(root)
        self.addCleanup(lambda: setattr(notify.common, "config_dir", orig))

    def test_falls_back_to_plain_when_app_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._patch_config_dir(d)
            self.assertEqual(notify._notifier_bin(), "terminal-notifier")

    def test_prefers_custom_app_binary_when_present(self):
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            macos = (pathlib.Path(d) / "state" / "notifier"
                     / "ultra-ai-notifier.app" / "Contents" / "MacOS")
            macos.mkdir(parents=True)
            binp = macos / "terminal-notifier"
            binp.write_text("")
            self._patch_config_dir(d)
            self.assertEqual(notify._notifier_bin(), str(binp))

    def test_has_notifier_abs_path_checks_existence(self):
        self.assertFalse(notify._has_notifier("/no/such/ultra-ai-notifier"))

    def test_has_notifier_plain_name_uses_path(self):
        # PATH 上の確実に在るコマンドで True(sh は必ず在る)
        self.assertTrue(notify._has_notifier("sh"))


class TestTerminalTarget(unittest.TestCase):
    def test_vscode_opens_project(self):
        execute, content_image = notify._terminal_target("vscode", "/a/b/proj")
        self.assertEqual(execute, 'open -a "Visual Studio Code" "/a/b/proj"')
        self.assertEqual(content_image, notify.VSCODE_ICON)

    def test_iterm_opens_iterm_no_icon(self):
        execute, content_image = notify._terminal_target("iTerm.app", "/p")
        self.assertEqual(execute, 'open -a "iTerm"')  # クリック→iTerm 前面化
        self.assertIsNone(content_image)

    def test_apple_terminal(self):
        execute, content_image = notify._terminal_target("Apple_Terminal", "/p")
        self.assertEqual(execute, 'open -a "Terminal"')
        self.assertIsNone(content_image)

    def test_unknown_defaults_to_vscode(self):
        execute, content_image = notify._terminal_target("", "/p")
        self.assertEqual(execute, 'open -a "Visual Studio Code" "/p"')
        self.assertEqual(content_image, notify.VSCODE_ICON)

    def test_no_cwd_opens_app_without_path(self):
        execute, content_image = notify._terminal_target("vscode", None)
        self.assertEqual(execute, 'open -a "Visual Studio Code"')
        self.assertEqual(content_image, notify.VSCODE_ICON)

    def test_cwd_with_quote_is_escaped(self):
        execute, _ = notify._terminal_target("vscode", '/a"b')
        self.assertIn('\\"', execute)  # シェルの二重引用符を壊さない


class TestNotificationArgs(unittest.TestCase):
    def test_permission_prompt_default(self):
        kind, label, cwd = notify.notification_args({"notification_type": "permission_prompt"})
        self.assertEqual(kind, "approval")
        self.assertEqual(label, "承認待ちです")
        self.assertIsNone(cwd)

    def test_message_takes_priority(self):
        kind, label, _ = notify.notification_args(
            {"notification_type": "permission_prompt", "message": "承認待ち（Bash）"})
        self.assertEqual(kind, "approval")
        self.assertEqual(label, "承認待ち（Bash）")

    def test_idle_prompt_default(self):
        kind, label, _ = notify.notification_args({"notification_type": "idle_prompt"})
        self.assertEqual(kind, "idle")
        self.assertEqual(label, "入力待ちです")

    def test_unknown_type_and_empty_payload(self):
        kind, label, _ = notify.notification_args({})
        self.assertEqual(kind, "approval")
        self.assertEqual(label, "通知")

    def test_passes_cwd_through(self):
        _, _, cwd = notify.notification_args({"cwd": "/a/b/myproj"})
        self.assertEqual(cwd, "/a/b/myproj")


class TestProjectLabel(unittest.TestCase):
    def test_basename(self):
        self.assertEqual(notify._project_label("/x/y/proj"), "proj")

    def test_root_has_no_name_falls_back_to_uai(self):
        # Path("/").name == "" なので "uai" に縮退する
        self.assertEqual(notify._project_label("/"), "uai")


class TestSubtitle(unittest.TestCase):
    def test_project_and_branch(self):
        self.assertEqual(notify._subtitle("/a/b/proj", "main"), "proj · main")

    def test_project_only_when_no_branch(self):
        self.assertEqual(notify._subtitle("/a/b/proj", None), "proj")


class TestSendEvent(unittest.TestCase):
    def _capture(self):
        # 抑制ゲートを無効化(UA_NOTIFY_SMART=0)=常に _send まで届く・ioreg も呼ばない
        _helpers.set_env(self, UA_NOTIFY_SMART="0")
        seen = {}
        orig = notify._send

        def fake(title, message, subtitle=None, *, execute=None, group=None,
                 app_icon=None, content_image=None):
            seen.update(title=title, message=message, subtitle=subtitle,
                        execute=execute, group=group, app_icon=app_icon,
                        content_image=content_image)
        notify._send = fake
        self.addCleanup(lambda: setattr(notify, "_send", orig))
        return seen

    def test_pass_with_detail_and_branch(self):
        seen = self._capture()
        notify.send_event("pass", label="完了(PASS)", cwd="/a/b/proj", branch="main",
                          detail="typecheck✓ test✓", term_program="vscode")
        self.assertEqual(seen["title"], f"{notify.APP_TITLE} {notify._KIND_EMOJI['pass']}")
        self.assertEqual(seen["message"], "完了(PASS) · typecheck✓ test✓")
        self.assertEqual(seen["subtitle"], "proj · main")
        self.assertEqual(seen["content_image"], notify.VSCODE_ICON)
        self.assertEqual(seen["app_icon"], notify.UAI_ICON)  # 左 = ultra-ai ロゴ
        self.assertEqual(seen["execute"], 'open -a "Visual Studio Code" "/a/b/proj"')
        self.assertTrue(seen["group"].startswith("ultra-ai:"))

    def test_no_detail_omits_separator(self):
        seen = self._capture()
        notify.send_event("unknown", label="完了(未検証)", cwd="/x/proj",
                          term_program="Apple_Terminal")
        self.assertEqual(seen["title"], f"{notify.APP_TITLE} {notify._KIND_EMOJI['unknown']}")
        self.assertEqual(seen["message"], "完了(未検証)")
        self.assertEqual(seen["execute"], 'open -a "Terminal"')
        self.assertIsNone(seen["content_image"])

    def test_stuck_uses_warning_emoji(self):
        seen = self._capture()
        notify.send_event("stuck", label="検証が詰まった(要確認)", cwd="/p",
                          detail="✗ test が失敗:", term_program="vscode")
        self.assertEqual(seen["title"], f"{notify.APP_TITLE} {notify._KIND_EMOJI['stuck']}")
        self.assertEqual(seen["message"], "検証が詰まった(要確認) · ✗ test が失敗:")

    def test_turn_complete_is_done_event(self):
        seen = self._capture()
        notify.turn_complete("完了(PASS)", cwd="/a/b/proj")
        self.assertEqual(seen["title"], f"{notify.APP_TITLE} {notify._KIND_EMOJI['done']}")
        self.assertEqual(seen["message"], "完了(PASS)")
        self.assertEqual(seen["subtitle"], "proj")


class TestShouldEmit(unittest.TestCase):
    CFG = {"smart": True, "idle": 120, "mindur": 10}

    def test_human_needed_kinds_always_emit(self):
        for k in ("stuck", "approval", "idle"):
            self.assertTrue(notify.should_emit(k, user_idle_s=0, wall_clock_s=0, cfg=self.CFG))

    def test_completion_suppressed_when_user_active(self):
        # 在席(idle 小)→ 完了系は抑制(見ているので不要)
        self.assertFalse(
            notify.should_emit("pass", user_idle_s=5, wall_clock_s=999, cfg=self.CFG))

    def test_completion_suppressed_when_task_short(self):
        # 離席でも数秒で終わったタスクは抑制
        self.assertFalse(
            notify.should_emit("done", user_idle_s=999, wall_clock_s=2, cfg=self.CFG))

    def test_completion_emitted_when_away_and_long(self):
        self.assertTrue(
            notify.should_emit("pass", user_idle_s=999, wall_clock_s=999, cfg=self.CFG))

    def test_none_signals_emit_safely(self):
        # idle/所要が取得不能 → 安全側で送る(取りこぼさない)
        self.assertTrue(
            notify.should_emit("pass", user_idle_s=None, wall_clock_s=None, cfg=self.CFG))

    def test_smart_off_always_emits(self):
        off = {"smart": False, "idle": 120, "mindur": 10}
        self.assertTrue(notify.should_emit("pass", user_idle_s=0, wall_clock_s=0, cfg=off))


class TestKillSwitch(unittest.TestCase):
    def test_ua_notify_off_sends_nothing(self):
        # UA_NOTIFY=0 なら subprocess を一切呼ばない(プラットフォーム非依存で確認)。
        _helpers.set_env(self, UA_NOTIFY="0")
        called = {"n": 0}
        orig = notify.subprocess.run
        notify.subprocess.run = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        try:
            notify._send("t", "m", "s", execute="y", group="g", content_image="/i.icns")
        finally:
            notify.subprocess.run = orig
        self.assertEqual(called["n"], 0)


class TestAggregateSummary(unittest.TestCase):
    """③ 集約の純粋判断(IO を呼ばない)。"""
    NOW = 1000.0
    STALE = 100

    def _rec(self, kind, proj, ts, cwd="/p", term="vscode"):
        return {"ts": ts, "kind": kind, "proj": proj, "cwd": cwd, "term": term}

    def test_empty_is_none(self):
        self.assertIsNone(notify._aggregate_summary([], now=self.NOW, stale_sec=self.STALE))

    def test_single_waiting(self):
        recs = [self._rec("approval", "app", self.NOW, cwd="/a", term="vscode")]
        msg, cwd, term = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
        self.assertEqual(msg, "app" + notify._KIND_EMOJI["approval"])
        self.assertEqual(cwd, "/a")
        self.assertEqual(term, "vscode")

    def test_priority_order_stuck_first(self):
        recs = [self._rec("idle", "d", self.NOW),
                self._rec("approval", "a", self.NOW),
                self._rec("stuck", "s", self.NOW)]
        msg, _, _ = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
        self.assertEqual(msg, "s{stuck} / a{approval} / d{idle}".format(**notify._KIND_EMOJI))

    def test_tie_break_oldest_first(self):
        # 同優先度は ts 昇順(古い=先頭=待たせている順)
        recs = [self._rec("approval", "new", self.NOW),
                self._rec("approval", "old", self.NOW - 50)]
        msg, _, _ = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
        ap = notify._KIND_EMOJI["approval"]
        self.assertEqual(msg, f"old{ap} / new{ap}")

    def test_click_target_is_most_urgent(self):
        # click 先 = 最高優先度(stuck)かつ最古の record の cwd/term
        recs = [self._rec("approval", "a", self.NOW - 10, cwd="/a", term="iTerm.app"),
                self._rec("stuck", "s1", self.NOW - 5, cwd="/s1", term="vscode"),
                self._rec("stuck", "s2", self.NOW - 9, cwd="/s2", term="Apple_Terminal")]
        _, cwd, term = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
        self.assertEqual(cwd, "/s2")
        self.assertEqual(term, "Apple_Terminal")

    def test_top_n_overflow_appends_plus(self):
        recs = [self._rec("approval", f"p{i}", self.NOW - i) for i in range(6)]
        msg, _, _ = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE,
                                              top_n=4)
        self.assertTrue(msg.endswith("+2"))
        self.assertEqual(len(msg.split(" / ")), 5)  # 4 件 + "+2"

    def test_resolved_only_is_none(self):
        recs = [self._rec("pass", "a", self.NOW), self._rec("done", "b", self.NOW)]
        self.assertIsNone(notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE))

    def test_stale_excluded(self):
        recs = [self._rec("approval", "fresh", self.NOW - 10),
                self._rec("stuck", "old", self.NOW - 999)]  # stale=除外
        msg, _, _ = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
        self.assertEqual(msg, "fresh" + notify._KIND_EMOJI["approval"])

    def test_emoji_matches_kind_map(self):
        for kind in ("stuck", "approval", "idle"):
            recs = [self._rec(kind, "x", self.NOW)]
            msg, _, _ = notify._aggregate_summary(recs, now=self.NOW, stale_sec=self.STALE)
            self.assertEqual(msg, "x" + notify._KIND_EMOJI[kind])


class TestRecordPending(_helpers.ConfigDirTestCase):
    """③ registry round-trip(実 IO・temp CLAUDE_CONFIG_DIR)。"""

    def test_upsert_then_resolve_deletes(self):
        notify._record_pending("approval", session_id="sid1", cwd="/x/p", branch="main",
                               label="承認待ち", term="vscode", now=1000.0)
        path = notify.common.pending_path("sid1")
        self.assertTrue(path.exists())
        rec = notify.common.read_json(path)
        self.assertEqual(rec["kind"], "approval")
        self.assertEqual(rec["proj"], "p")
        self.assertEqual(rec["session_id"], "sid1")
        # 解決系(pass)で自分の record を削除
        notify._record_pending("pass", session_id="sid1", cwd="/x/p", branch="main",
                               label="完了", term="vscode", now=1001.0)
        self.assertFalse(path.exists())

    def test_multiple_sids_enumerable(self):
        for sid in ("a", "b", "c"):
            notify._record_pending("approval", session_id=sid, cwd="/p", branch=None,
                                   label="x", term="vscode", now=1000.0)
        self.assertEqual(len(list(notify.common.pending_dir().glob("*.json"))), 3)

    def test_session_id_none_is_noop(self):
        notify._record_pending("approval", session_id=None, cwd="/p", branch=None,
                               label="x", term="vscode", now=1000.0)
        self.assertEqual(list(notify.common.pending_dir().glob("*.json")), [])

    def test_aggregate_off_is_noop(self):
        _helpers.set_env(self, UA_NOTIFY_AGGREGATE="0")
        notify._record_pending("approval", session_id="sid1", cwd="/p", branch=None,
                               label="x", term="vscode", now=1000.0)
        self.assertFalse(notify.common.pending_path("sid1").exists())


class TestSendPendingSummary(_helpers.ConfigDirTestCase):
    """③ 集約送信の IO(_send / _remove_group を mock)。"""

    def setUp(self):
        super().setUp()
        self.sent, self.removed = [], []
        orig_send, orig_remove = notify._send, notify._remove_group
        notify._send = lambda *a, **k: self.sent.append((a, k))
        notify._remove_group = lambda g: self.removed.append(g)
        self.addCleanup(lambda: setattr(notify, "_send", orig_send))
        self.addCleanup(lambda: setattr(notify, "_remove_group", orig_remove))

    def _put(self, sid, kind, ts, proj="p", cwd="/p", term="vscode"):
        notify.common.write_json_atomic(
            notify.common.pending_path(sid),
            {"ts": ts, "session_id": sid, "cwd": cwd, "proj": proj,
             "branch": None, "kind": kind, "label": "x", "term": term})

    def test_below_min_removes_group(self):
        self._put("a", "approval", 1000.0)
        notify.send_pending_summary(1000.0)  # 1件 < MIN(2)
        self.assertEqual(self.removed, [notify._AGGREGATE_GROUP])
        self.assertEqual(self.sent, [])

    def test_at_min_sends_aggregate(self):
        self._put("a", "approval", 1000.0, proj="app", cwd="/app")
        self._put("b", "stuck", 1000.0, proj="infra", cwd="/infra")
        notify.send_pending_summary(1000.0)
        self.assertEqual(len(self.sent), 1)
        args, kw = self.sent[0]
        self.assertEqual(kw["group"], notify._AGGREGATE_GROUP)
        self.assertIn("infra" + notify._KIND_EMOJI["stuck"], args[1])  # stuck が先頭
        self.assertIn("app" + notify._KIND_EMOJI["approval"], args[1])

    def test_corrupt_json_skipped(self):
        self._put("a", "approval", 1000.0)
        self._put("b", "stuck", 1000.0)
        (notify.common.pending_dir() / "bad.json").write_text("{ not json")
        notify.send_pending_summary(1000.0)  # 壊れ混在でも他2件で集約
        self.assertEqual(len(self.sent), 1)

    def test_stale_pruned_then_below_min(self):
        self._put("a", "approval", 1000.0)
        self._put("old", "stuck", 0.0)
        _helpers.set_env(self, UA_NOTIFY_STALE_SEC="100")
        notify.send_pending_summary(1000.0)  # old を prune→残1件<MIN→remove
        self.assertFalse(notify.common.pending_path("old").exists())
        self.assertEqual(self.removed, [notify._AGGREGATE_GROUP])

    def test_aggregate_off_is_noop(self):
        _helpers.set_env(self, UA_NOTIFY_AGGREGATE="0")
        self._put("a", "approval", 1000.0)
        self._put("b", "stuck", 1000.0)
        notify.send_pending_summary(1000.0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.removed, [])

    def test_dedup_same_set_sends_once(self):
        # 同一の待機集合で2回呼んでも再アラートしない(承認連発の「何度も来る」を断つ)
        self._put("a", "approval", 1000.0)
        self._put("b", "stuck", 1000.0)
        notify.send_pending_summary(1000.0)
        notify.send_pending_summary(1000.0)
        self.assertEqual(len(self.sent), 1)

    def test_dedup_changed_set_sends_again(self):
        # 新セッションが待機に加わる=集合が変化→再アラートする
        self._put("a", "approval", 1000.0)
        self._put("b", "stuck", 1000.0)
        notify.send_pending_summary(1000.0)
        self._put("c", "approval", 1000.0)
        notify.send_pending_summary(1000.0)
        self.assertEqual(len(self.sent), 2)

    def test_dedup_resets_after_below_min(self):
        # いったん静かになって(min 未満)再び待機→署名がクリアされ再アラートする
        self._put("a", "approval", 1000.0)
        self._put("b", "stuck", 1000.0)
        notify.send_pending_summary(1000.0)              # 送信(署名保存)
        notify._unlink_quiet(notify.common.pending_path("b"))  # 1件<MIN
        notify.send_pending_summary(1000.0)              # remove + 署名クリア
        self._put("b", "stuck", 1000.0)                  # 再び同じ集合
        notify.send_pending_summary(1000.0)              # 署名クリア済→再送
        self.assertEqual(len(self.sent), 2)


class TestSendEventAggregateWiring(_helpers.ConfigDirTestCase):
    """send_event の ③ 配線(session_id 経路・抑制と独立)。"""

    def setUp(self):
        super().setUp()
        orig_send, orig_remove, orig_idle = (
            notify._send, notify._remove_group, notify._user_idle_seconds)
        notify._send = lambda *a, **k: None
        notify._remove_group = lambda g: None
        notify._user_idle_seconds = lambda: 999  # 既定は離席(必要なテストで上書き)
        self.addCleanup(lambda: setattr(notify, "_send", orig_send))
        self.addCleanup(lambda: setattr(notify, "_remove_group", orig_remove))
        self.addCleanup(lambda: setattr(notify, "_user_idle_seconds", orig_idle))

    def test_session_id_none_does_not_touch_registry(self):
        # turn_complete 経路(session_id 無し)は registry に触れない
        _helpers.set_env(self, UA_NOTIFY_SMART="0")
        notify.send_event("approval", label="x", cwd="/p", session_id=None)
        self.assertEqual(list(notify.common.pending_dir().glob("*.json")), [])

    def test_resolve_deletes_record_even_when_suppressed(self):
        # 在席(idle=0)で完了系は個別抑制されるが、registry は更新(record 削除)される
        notify._user_idle_seconds = lambda: 0
        _helpers.set_env(self, UA_NOTIFY_SMART="1")
        notify.send_event("approval", label="承認", cwd="/p", session_id="sid1")
        self.assertTrue(notify.common.pending_path("sid1").exists())
        notify.send_event("pass", label="完了", cwd="/p", wall_clock_s=999,
                          session_id="sid1")
        self.assertFalse(notify.common.pending_path("sid1").exists())


class TestClearPendingNoteActive(_helpers.ConfigDirTestCase):
    """活動再開での待機解除(=動いているのに待機表示 の誤検知を断つ)。"""

    def setUp(self):
        super().setUp()
        self.sent, self.removed = [], []
        orig_send, orig_remove = notify._send, notify._remove_group
        notify._send = lambda *a, **k: self.sent.append((a, k))
        notify._remove_group = lambda g: self.removed.append(g)
        self.addCleanup(lambda: setattr(notify, "_send", orig_send))
        self.addCleanup(lambda: setattr(notify, "_remove_group", orig_remove))

    def _put(self, sid, kind, ts):
        notify.common.write_json_atomic(
            notify.common.pending_path(sid),
            {"ts": ts, "session_id": sid, "cwd": "/p", "proj": "p",
             "branch": None, "kind": kind, "label": "x", "term": "vscode"})

    def test_clear_pending_removes_own_record_and_reports(self):
        notify._record_pending("approval", session_id="me", cwd="/p", branch=None,
                               label="x", term="vscode", now=1000.0)
        self.assertTrue(notify.clear_pending("me"))            # 在った→True
        self.assertFalse(notify.common.pending_path("me").exists())
        self.assertFalse(notify.clear_pending("me"))           # 既に無い→False

    def test_clear_pending_guards_none_and_off(self):
        self.assertFalse(notify.clear_pending(None))
        _helpers.set_env(self, UA_NOTIFY_AGGREGATE="0")
        self._put("s", "approval", 1000.0)
        self.assertFalse(notify.clear_pending("s"))            # AGGREGATE off→触らない
        self.assertTrue(notify.common.pending_path("s").exists())

    def test_note_active_drops_aggregate_below_min(self):
        now = notify.time.time()
        self._put("me", "approval", now)
        self._put("ghost", "stuck", now)                       # ゴースト1件が居残り「2件」に
        notify.note_active("me")                               # 自分が消え 1件<MIN
        self.assertFalse(notify.common.pending_path("me").exists())
        self.assertIn(notify._AGGREGATE_GROUP, self.removed)   # 集約を除去(静かになる)
        self.assertEqual(self.sent, [])                        # 下回り→送信せず

    def test_note_active_noop_without_own_record(self):
        now = notify.time.time()
        self._put("ghost", "stuck", now)
        notify.note_active("me")                               # 自分の record 無し→何もしない
        self.assertTrue(notify.common.pending_path("ghost").exists())
        self.assertEqual(self.removed, [])


class TestSessionEndCli(_helpers.ConfigDirTestCase):
    """SessionEnd で自分のゴースト record を即時掃除(4h の stale を待たない)。"""

    def setUp(self):
        super().setUp()
        orig_send, orig_remove = notify._send, notify._remove_group
        notify._send = lambda *a, **k: None
        notify._remove_group = lambda g: None
        self.addCleanup(lambda: setattr(notify, "_send", orig_send))
        self.addCleanup(lambda: setattr(notify, "_remove_group", orig_remove))

    def _feed(self, payload):
        orig = sys.stdin
        sys.stdin = io.StringIO(payload)
        self.addCleanup(lambda: setattr(sys, "stdin", orig))

    def test_session_end_removes_own_record(self):
        notify._record_pending("approval", session_id="dead", cwd="/p", branch=None,
                               label="x", term="vscode", now=1000.0)
        self.assertTrue(notify.common.pending_path("dead").exists())
        self._feed('{"session_id": "dead"}')
        notify._session_end_cli()
        self.assertFalse(notify.common.pending_path("dead").exists())

    def test_session_end_bad_payload_is_noop(self):
        self._feed("{ not json")
        notify._session_end_cli()   # 壊れ payload でも例外を出さない(hook を壊さない)


if __name__ == "__main__":
    unittest.main()
