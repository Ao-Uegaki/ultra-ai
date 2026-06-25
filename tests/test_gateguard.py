"""Unit tests for claude-home/hooks/gateguard.py (run: python3 -m unittest).

fact-forcing ゲートの判定: 初回タッチ→deny / 提示後 retry→allow / subagent・非コード・
flag-off→通過 / Write→作成メッセージ / denial dampening→1行版。
"""
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import gateguard  # noqa: E402


def _payload(tool, file, **extra):
    return {"tool_name": tool, "tool_input": {"file_path": file},
            "cwd": "/x", "session_id": "s1", **extra}


class TestFactGate(_helpers.ConfigDirTestCase):
    def test_first_edit_denies(self):
        code, msg = gateguard.process(_payload("Edit", "/x/a.py"))
        self.assertEqual(code, 2)
        self.assertIn("事実確認の関門", msg)

    def test_retry_after_first_allows(self):
        p = _payload("Edit", "/x/a.py")
        gateguard.process(p)               # 初回 → deny + checked へ
        code, msg = gateguard.process(p)   # retry → 通過
        self.assertEqual((code, msg), (0, ""))

    def test_different_file_still_denies(self):
        gateguard.process(_payload("Edit", "/x/a.py"))
        code, _ = gateguard.process(_payload("Edit", "/x/b.py"))
        self.assertEqual(code, 2)

    def test_non_code_file_allows(self):
        code, msg = gateguard.process(_payload("Edit", "/x/readme.md"))
        self.assertEqual((code, msg), (0, ""))

    def test_subagent_bypass(self):
        code, _ = gateguard.process(_payload("Edit", "/x/a.py", agent_id="sub-1"))
        self.assertEqual(code, 0)

    def test_disabled_flag_allows(self):
        _helpers.set_env(self, UA_FACTGATE="0")
        code, _ = gateguard.process(_payload("Edit", "/x/a.py"))
        self.assertEqual(code, 0)

    def test_write_uses_creation_message(self):
        code, msg = gateguard.process(_payload("Write", "/x/new.py"))
        self.assertEqual(code, 2)
        self.assertIn("新規作成", msg)

    def test_multiedit_is_gated(self):
        code, _ = gateguard.process(_payload("MultiEdit", "/x/a.py"))
        self.assertEqual(code, 2)

    def test_bash_is_ignored(self):
        code, msg = gateguard.process({"tool_name": "Bash",
                                       "tool_input": {"command": "ls"},
                                       "cwd": "/x", "session_id": "s1"})
        self.assertEqual((code, msg), (0, ""))

    def test_dampening_condenses_after_budget(self):
        _helpers.set_env(self, UA_FACTGATE_FULL="1")
        gateguard.process(_payload("Edit", "/x/a.py"))            # denial #1 (full)
        code, msg = gateguard.process(_payload("Edit", "/x/b.py"))  # denial #2 > 1 → 1行版
        self.assertEqual(code, 2)
        self.assertIn("回目", msg)


if __name__ == "__main__":
    unittest.main()
