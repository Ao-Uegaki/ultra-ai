"""Unit tests for claude-home/hooks/noreread.py (run: python3 -m unittest)."""
import tempfile
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import noreread as nr  # noqa: E402


def _read(cwd, fp="/x/a.py", offset=None, limit=None, resp="x"):
    ti = {"file_path": fp}
    if offset is not None:
        ti["offset"] = offset
    if limit is not None:
        ti["limit"] = limit
    return {"tool_name": "Read", "tool_input": ti, "tool_response": resp,
            "cwd": cwd, "session_id": "s1"}


def _edit(cwd, fp="/x/a.py"):
    return {"tool_name": "Edit", "tool_input": {"file_path": fp},
            "cwd": cwd, "session_id": "s1"}


class TestC1NoReread(_helpers.ConfigDirTestCase):
    """C1: 変更なしの純再 Read を updatedToolOutput でポインタ化(escape hatch・edit でクリア)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_NOREREAD=None, UA_BIGREAD_HINT=None)
        _helpers.stub_git_none(self)

    def test_first_read_passes(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(nr.process(_read(cwd)))

    def test_second_read_pointered(self):
        with tempfile.TemporaryDirectory() as cwd:
            nr.process(_read(cwd))
            out = nr.process(_read(cwd))
            self.assertIn("updatedToolOutput", out["hookSpecificOutput"])
            self.assertIn("読み込み済み", out["hookSpecificOutput"]["updatedToolOutput"])

    def test_escape_hatch_third_read_full(self):
        with tempfile.TemporaryDirectory() as cwd:
            nr.process(_read(cwd))                       # 1: pass
            nr.process(_read(cwd))                       # 2: pointer
            self.assertIsNone(nr.process(_read(cwd)))    # 3: escape → full

    def test_edit_clears_so_reread_full(self):
        with tempfile.TemporaryDirectory() as cwd:
            nr.process(_read(cwd))
            nr.process(_edit(cwd))                       # 変更 → クリア
            self.assertIsNone(nr.process(_read(cwd)))    # 変更後の Read は正当

    def test_different_range_not_redundant(self):
        with tempfile.TemporaryDirectory() as cwd:
            nr.process(_read(cwd, offset=1, limit=10))
            self.assertIsNone(nr.process(_read(cwd, offset=50, limit=10)))

    def test_kill_switch(self):
        _helpers.set_env(self, UA_NOREREAD="0")
        with tempfile.TemporaryDirectory() as cwd:
            nr.process(_read(cwd))
            self.assertIsNone(nr.process(_read(cwd)))    # 抑制しない

    def test_non_read_tool_ignored(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(nr.process({"tool_name": "Bash", "tool_input": {"command": "ls"},
                                          "cwd": cwd, "session_id": "s1"}))


class TestC3BigReadHint(_helpers.ConfigDirTestCase):
    """C3: 大きい Read に委譲 nudge(file ごと一度・閾値・kill-switch)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_NOREREAD=None, UA_BIGREAD_HINT=None)
        _helpers.stub_git_none(self)

    def test_fires_on_big_read(self):
        _helpers.set_env(self, UA_BIGREAD_CHARS="100")
        with tempfile.TemporaryDirectory() as cwd:
            out = nr.process(_read(cwd, fp="/x/big.py", resp="y" * 500))
            self.assertIn("additionalContext", out["hookSpecificOutput"])
            self.assertIn("Explore", out["hookSpecificOutput"]["additionalContext"])

    def test_silent_under_threshold(self):
        _helpers.set_env(self, UA_BIGREAD_CHARS="100000")
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(nr.process(_read(cwd, resp="small")))

    def test_dedup_per_file_when_noreread_off(self):
        # NOREREAD off で C1 が出ないので、同 file の2回目が C3 dedup されるかを純粋に見る
        _helpers.set_env(self, UA_NOREREAD="0", UA_BIGREAD_CHARS="100")
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNotNone(nr.process(_read(cwd, fp="/x/big.py", resp="y" * 500)))  # 1: nudge
            self.assertIsNone(nr.process(_read(cwd, fp="/x/big.py", resp="y" * 500)))     # 2: dedup

    def test_kill_switch(self):
        _helpers.set_env(self, UA_BIGREAD_HINT="0", UA_BIGREAD_CHARS="10")
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(nr.process(_read(cwd, resp="y" * 500)))


if __name__ == "__main__":
    unittest.main()
