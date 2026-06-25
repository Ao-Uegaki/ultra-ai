"""Unit tests for claude-home/hooks/monitor.py (run: python3 -m unittest).

proactive 観測ナッジ: evaluate(純関数)で loop/scope/compact/context を、process で
ループ検知の発火と内容ベース dedup を検証する。
"""
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import monitor  # noqa: E402
import notify  # noqa: E402


def _conf(**over):
    c = dict(monitor.DEFAULTS)
    c.update(over)
    return c


def _bash(cmd="ls"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd},
            "cwd": "/x", "session_id": "s1"}


class TestEvaluate(unittest.TestCase):
    def test_loop_detected(self):
        bridge = {"recent": [["Bash", "h"]] * 3, "files": [], "tools": 3}
        msgs = [m for _, m in monitor.evaluate(bridge, 0, _conf())]
        self.assertTrue(any("ループ" in m for m in msgs))

    def test_no_loop_below_threshold(self):
        bridge = {"recent": [["Bash", "h"]] * 2, "files": [], "tools": 2}
        self.assertEqual(monitor.evaluate(bridge, 0, _conf()), [])

    def test_scope_creep(self):
        bridge = {"recent": [], "files": [f"/f{i}.py" for i in range(21)], "tools": 1}
        msgs = [m for _, m in monitor.evaluate(bridge, 0, _conf())]
        self.assertTrue(any("スコープ" in m for m in msgs))

    def test_compact_milestone(self):
        bridge = {"recent": [], "files": [], "tools": 50}
        msgs = [m for _, m in monitor.evaluate(bridge, 0, _conf())]
        self.assertTrue(any("/compact" in m for m in msgs))

    def test_context_warn_and_crit(self):
        bridge = {"recent": [], "files": [], "tools": 1}
        warn = monitor.evaluate(bridge, 150_000, _conf())   # 75%
        crit = monitor.evaluate(bridge, 185_000, _conf())   # 92%
        self.assertEqual(warn[0][0], 2)
        self.assertEqual(crit[0][0], 3)  # critical はより高い severity が先頭


class TestProcess(_helpers.ConfigDirTestCase):
    def test_loop_fires_on_third_identical(self):
        self.assertIsNone(monitor.process(_bash()))   # 1
        self.assertIsNone(monitor.process(_bash()))   # 2
        emit = monitor.process(_bash())               # 3 → ループ発火
        self.assertIsNotNone(emit)
        self.assertIn("ループ", emit)

    def test_same_warning_deduped(self):
        for _ in range(3):
            monitor.process(_bash())
        self.assertIsNone(monitor.process(_bash()))   # 同一警告 → 再注入しない

    def test_disabled_flag(self):
        _helpers.set_env(self, UA_MONITOR="0")
        for _ in range(4):
            self.assertIsNone(monitor.process(_bash()))

    def test_varied_tools_no_warning(self):
        self.assertIsNone(monitor.process(_bash("ls")))
        self.assertIsNone(monitor.process(_bash("pwd")))
        self.assertIsNone(monitor.process({"tool_name": "Read",
                                           "tool_input": {"file_path": "/x/a.py"},
                                           "cwd": "/x", "session_id": "s1"}))


class TestReviewHint(_helpers.ConfigDirTestCase):
    """言語別 reviewer の review-hint(D): 言語ごと1回・未対応拡張子は無言・flag で無効。"""

    def _edit(self, path):
        return monitor.process({"tool_name": "Edit",
                                "tool_input": {"file_path": path},
                                "cwd": "/x", "session_id": "s1"})

    def test_python_edit_hints_once(self):
        emit = self._edit("/x/a.py")
        self.assertIsNotNone(emit)
        self.assertIn("python-reviewer", emit)

    def test_same_lang_not_repeated(self):
        self._edit("/x/a.py")
        self.assertIsNone(self._edit("/x/b.py"))   # 同一言語は再提案しない

    def test_different_lang_hints(self):
        self._edit("/x/a.py")
        emit = self._edit("/x/c.rs")
        self.assertIsNotNone(emit)
        self.assertIn("rust-reviewer", emit)

    def test_tsx_maps_to_react(self):
        emit = self._edit("/x/App.tsx")
        self.assertIn("react-reviewer", emit)

    def test_unmapped_ext_no_hint(self):
        self.assertIsNone(self._edit("/x/readme.md"))

    def test_disabled_flag(self):
        _helpers.set_env(self, UA_REVIEW_HINT="0")
        self.assertIsNone(self._edit("/x/a.py"))


class TestContextSource(_helpers.ConfigDirTestCase):
    """_context_metrics は (last, peak) を返す。last は /compact 後の誤警告回避のため peak でなく現値。"""

    def _write_metrics(self, **kv):
        sdir = common.session_state_dir("/x", "s1")
        sdir.mkdir(parents=True, exist_ok=True)
        common.write_json_atomic(sdir / common.STATE_METRICS, kv)

    def test_prefers_last_over_peak(self):
        self._write_metrics(peak_main_context=366_000, last_main_context=40_000)
        self.assertEqual(monitor._context_metrics("/x", "s1"), (40_000, 366_000))

    def test_falls_back_to_peak_when_no_last(self):
        self._write_metrics(peak_main_context=150_000)   # 古い metrics.json(last 無し)
        self.assertEqual(monitor._context_metrics("/x", "s1"), (150_000, 150_000))

    def test_missing_metrics_is_zero(self):
        self.assertEqual(monitor._context_metrics("/x", "s1"), (0, 0))


class TestResolveWindow(_helpers.ConfigDirTestCase):
    """context% の分母解決: env override > toml(base≠既定) > model.json 検出 > 既定 + peak backstop。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_CONTEXT_WINDOW=None)

    def _write_model(self, win):
        sdir = common.session_state_dir("/x", "s1")
        sdir.mkdir(parents=True, exist_ok=True)
        common.write_json_atomic(sdir / common.STATE_MODEL, {"context_window": win})

    def test_env_override_wins(self):
        _helpers.set_env(self, UA_CONTEXT_WINDOW="500000")
        self._write_model(1_000_000)
        self.assertEqual(monitor._resolve_window("/x", "s1", 200_000, peak=900_000), 500_000)

    def test_toml_base_respected_no_backstop(self):
        # base が既定 200k と異なる=ユーザー明示 → peak が大きくても backstop しない
        self.assertEqual(monitor._resolve_window("/x", "s1", 300_000, peak=900_000), 300_000)

    def test_detected_model_window(self):
        self._write_model(1_000_000)
        self.assertEqual(monitor._resolve_window("/x", "s1", 200_000, peak=10_000), 1_000_000)

    def test_default_when_nothing(self):
        self.assertEqual(monitor._resolve_window("/x", "s1", 200_000, peak=10_000), 200_000)

    def test_peak_backstop_bumps_to_1m(self):
        # statusline 未検出でも実測 peak が 200k 超 → 1M tier へ自己補正
        self.assertEqual(monitor._resolve_window("/x", "s1", 200_000, peak=420_000), 1_000_000)


class TestContextWindowRegression(unittest.TestCase):
    """本バグの回帰: 1M モデルで 338k は crit/warn を出さない(200k 既定では従来どおり crit)。"""

    def test_1m_window_no_false_crit(self):
        bridge = {"recent": [], "files": [], "tools": 1}
        self.assertEqual(monitor.evaluate(bridge, 338_000, _conf(context_window=1_000_000)), [])  # 34%

    def test_200k_window_still_crits(self):
        bridge = {"recent": [], "files": [], "tools": 1}
        self.assertEqual(monitor.evaluate(bridge, 338_000, _conf())[0][0], 3)   # 169% → crit(後方互換)


class TestNoteActiveWiring(_helpers.ConfigDirTestCase):
    """PostToolUse(process)で活動再開→自分の待機 record を解除(UA_MONITOR と独立)。"""

    def setUp(self):
        super().setUp()
        orig_send, orig_remove = notify._send, notify._remove_group
        notify._send = lambda *a, **k: None
        notify._remove_group = lambda g: None
        self.addCleanup(lambda: setattr(notify, "_send", orig_send))
        self.addCleanup(lambda: setattr(notify, "_remove_group", orig_remove))

    def _put(self, sid):
        common.write_json_atomic(
            common.pending_path(sid),
            {"ts": 1000.0, "session_id": sid, "cwd": "/x", "proj": "x",
             "branch": None, "kind": "approval", "label": "x", "term": "vscode"})

    def test_posttooluse_clears_own_pending(self):
        self._put("s1")
        monitor.process(_bash())                          # tool が走った=待機ではない
        self.assertFalse(common.pending_path("s1").exists())

    def test_clears_even_when_monitor_off(self):
        _helpers.set_env(self, UA_MONITOR="0")
        self._put("s1")
        self.assertIsNone(monitor.process(_bash()))       # MONITOR off→警告は出ない
        self.assertFalse(common.pending_path("s1").exists())  # でも待機解除は効く


if __name__ == "__main__":
    unittest.main()
