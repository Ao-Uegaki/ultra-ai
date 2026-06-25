"""Unit tests for claude-home/hooks/statusline.py (run: python3 -m unittest)."""
import os
import re
import sys
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (副作用: hooks を sys.path へ + ConfigDirTestCase)
from _helpers import ConfigDirTestCase

HOOKS = Path(__file__).resolve().parent.parent / "claude-home" / "hooks"
sys.path.insert(0, str(HOOKS))
import common  # noqa: E402
import statusline  # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    """ANSI エスケープを除去。ツートーン着色で `ultra-` と `ai` の間に色コードが挟まるため、
    「ラベルが ultra-ai と読めるか」は素の文字列で検証する。"""
    return _ANSI.sub("", s)


class TestRender(unittest.TestCase):
    def test_full(self):
        out = statusline.render(
            {"model": {"display_name": "Opus 4.8"},
             "workspace": {"current_dir": "/Users/you/dev/my-project"}},
            branch="main")
        self.assertIn("ultra-ai", _plain(out))
        self.assertIn("Opus 4.8", out)
        self.assertIn("my-project", out)  # basename
        self.assertIn("main", out)

    def test_bar_label_reads_plain(self):
        # 先頭ラベルは素文字で `▌ ultra-ai`(色は環境依存なので素文字で検証)。
        out = statusline.render({"cwd": "/a/b/c"})
        self.assertTrue(_plain(out).startswith("▌ ultra-ai"))

    def test_rainbow_colors_chars_but_text_intact(self):
        # _rainbow は各文字を truecolor で着色し、素文字は元のまま(空白は素通り)。
        col = statusline._rainbow("ab")
        self.assertRegex(col, r"\x1b\[38;2;\d+;\d+;\d+m")
        self.assertEqual(_plain(col), "ab")

    def test_lift_brightens_dark_colors_only(self):
        # 暗い青(輝度<floor)は白寄せで floor 以上へ、明るい色(黄)は不変。
        r, g, b = statusline._lift(0.15, 0.37, 1.0, floor=0.5)  # 暗い青
        self.assertGreaterEqual(round(0.299 * r + 0.587 * g + 0.114 * b, 3), 0.5)
        self.assertEqual(statusline._lift(1.0, 1.0, 0.0, floor=0.5), (1.0, 1.0, 0.0))  # 黄は不変

    def test_model_id_fallback(self):
        out = statusline.render({"model": {"id": "claude-opus-4-8"}})
        self.assertIn("claude-opus-4-8", out)

    def test_cwd_fallback_when_no_workspace(self):
        out = statusline.render({"cwd": "/a/b/myrepo"})
        self.assertIn("myrepo", out)

    def test_no_branch_omitted(self):
        out = statusline.render(
            {"model": {"display_name": "Opus"}, "cwd": "/a/b/c"}, branch=None)
        self.assertIn("ultra-ai", _plain(out))
        self.assertIn("Opus", out)
        self.assertIn("c", out)

    def test_missing_model_still_shows_label(self):
        out = statusline.render({"cwd": "/a/b/c"})
        self.assertIn("ultra-ai", _plain(out))

    def test_empty_dict_no_crash(self):
        # workspace/cwd 欠如 → os.getcwd() フォールバック。例外なく "ultra-ai" を含む。
        out = statusline.render({})
        self.assertIn("ultra-ai", _plain(out))
        self.assertIn(os.path.basename(os.getcwd().rstrip("/")), out)


class TestMetricsRender(unittest.TestCase):
    DATA = {"model": {"display_name": "Opus 4.8"}, "cwd": "/a/b/repo"}

    def test_no_metrics_identical_to_base(self):
        # metrics=None / {} は従来出力と完全一致(後方互換)。
        base = statusline.render(self.DATA, branch="main")
        self.assertEqual(statusline.render(self.DATA, branch="main", metrics=None), base)
        self.assertEqual(statusline.render(self.DATA, branch="main", metrics={}), base)

    def test_full_metrics_segments(self):
        out = statusline.render(
            self.DATA, branch="main",
            metrics={"peak_main_context": 142000,
                     "total": {"weighted_cost": 0.834},
                     "wall_clock_s": 740.0})
        self.assertIn("142k ctx", out)
        self.assertIn("$0.83", out)
        self.assertIn("12m", out)

    def test_partial_metrics_omits_missing(self):
        # cost=0 と wall_clock_s=None は省略、ctx は残り、ベース行は維持。
        out = statusline.render(
            self.DATA, branch="main",
            metrics={"peak_main_context": 5000,
                     "total": {"weighted_cost": 0},
                     "wall_clock_s": None})
        self.assertIn("ultra-ai", _plain(out))
        self.assertIn("Opus 4.8", out)
        self.assertIn("5k ctx", out)
        self.assertNotIn("$", out)

    def test_empty_metrics_no_segments(self):
        out = statusline.render(self.DATA, metrics={"peak_main_context": 0,
                                                    "total": {}, "wall_clock_s": None})
        self.assertNotIn("ctx", out)
        self.assertNotIn("$", out)


class TestFormatters(unittest.TestCase):
    def test_fmt_tokens(self):
        self.assertEqual(statusline._fmt_tokens(980), "980")
        self.assertEqual(statusline._fmt_tokens(1000), "1k")
        self.assertEqual(statusline._fmt_tokens(142000), "142k")
        self.assertEqual(statusline._fmt_tokens(1_200_000), "1.2M")

    def test_fmt_cost(self):
        self.assertEqual(statusline._fmt_cost(0.834), "$0.83")
        self.assertEqual(statusline._fmt_cost(0), "$0.00")

    def test_fmt_duration(self):
        self.assertEqual(statusline._fmt_duration(None), "")
        self.assertEqual(statusline._fmt_duration(0), "")
        self.assertEqual(statusline._fmt_duration(45), "45s")
        self.assertEqual(statusline._fmt_duration(740), "12m")
        self.assertEqual(statusline._fmt_duration(3905), "1h05m")


class TestReadMetrics(ConfigDirTestCase):
    def test_no_session_id_returns_empty(self):
        self.assertEqual(statusline._read_metrics({}, "/a/b/repo"), {})

    def test_reads_written_metrics(self):
        cur, sid = "/a/b/repo", "sess-1"
        mfile = common.session_state_dir(cur, sid) / common.STATE_METRICS
        common.write_json_atomic(mfile, {"peak_main_context": 99000,
                                         "total": {"weighted_cost": 1.5},
                                         "wall_clock_s": 65})
        got = statusline._read_metrics({"session_id": sid}, cur)
        self.assertEqual(got["peak_main_context"], 99000)
        out = statusline.render({"cwd": cur}, branch="main", metrics=got)
        self.assertIn("99k ctx", out)
        self.assertIn("$1.50", out)
        self.assertIn("1m", out)


class TestPersistWindow(ConfigDirTestCase):
    """_persist_window: model.id/display_name から窓を検出し model.json に永続化(変化時のみ・表示を壊さない)。"""

    def _win(self, cur, sid):
        return common.read_json(common.session_state_dir(cur, sid) / common.STATE_MODEL).get("context_window")

    def test_persists_1m_from_id(self):
        cur, sid = "/a/b/repo", "sess-1m"
        statusline._persist_window({"session_id": sid, "model": {"id": "claude-opus-4-8[1m]"}}, cur)
        self.assertEqual(self._win(cur, sid), 1_000_000)

    def test_persists_1m_from_display_name(self):
        cur, sid = "/a/b/repo", "sess-disp"
        statusline._persist_window({"session_id": sid, "model": {"display_name": "Opus 4.8 (1M context)"}}, cur)
        self.assertEqual(self._win(cur, sid), 1_000_000)

    def test_persists_200k_for_base_model(self):
        cur, sid = "/a/b/repo", "sess-200"
        statusline._persist_window({"session_id": sid, "model": {"display_name": "Opus 4.8"}}, cur)
        self.assertEqual(self._win(cur, sid), 200_000)

    def test_no_session_id_writes_nothing(self):
        cur = "/a/b/repo"
        statusline._persist_window({"model": {"id": "claude-opus-4-8[1m]"}}, cur)  # 例外を出さず無書き込み
        self.assertIsNone(self._win(cur, "whatever"))

    def test_updates_when_model_changes(self):
        cur, sid = "/a/b/repo", "sess-chg"
        statusline._persist_window({"session_id": sid, "model": {"id": "claude-opus-4-8[1m]"}}, cur)
        statusline._persist_window({"session_id": sid, "model": {"display_name": "Opus 4.8"}}, cur)
        self.assertEqual(self._win(cur, sid), 200_000)


if __name__ == "__main__":
    unittest.main()
