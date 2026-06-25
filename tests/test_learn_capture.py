"""Unit tests for claude-home/hooks/learn_capture.py (run: python3 -m unittest).

UserPromptSubmit の訂正捕捉: フラグ下でのみ・訂正様プロンプトのみ候補化・注入はしない。
"""
import os
import tempfile
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import learn_capture  # noqa: E402


class TestProcess(unittest.TestCase):
    def setUp(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")

    def test_correction_prompt_captured(self):
        c = learn_capture.process({"prompt": "print じゃなく logging を使って", "cwd": "/x"})
        self.assertIsNotNone(c)
        self.assertEqual(c["source"], "correction")
        self.assertIn("logging", c["text"])

    def test_non_correction_returns_none(self):
        self.assertIsNone(learn_capture.process({"prompt": "テストを追加して", "cwd": "/x"}))

    def test_harness_fragment_rejected_even_with_marker(self):
        # 訂正マーカー("違う")を含んでも harness 由来の断片は候補にしない
        noisy = ("<task-notification> <task-id>w1</task-id> "
                 "<output-file>/private/tmp/claude-501/x</output-file> もっと違う案")
        self.assertIsNone(learn_capture.process({"prompt": noisy, "cwd": "/x"}))

    def test_english_correction(self):
        self.assertIsNotNone(
            learn_capture.process({"prompt": "use a generator instead of a list here", "cwd": "/x"}))

    def test_disabled_returns_none(self):
        _helpers.set_env(self, UA_AUTOAPPLY="0")  # 明示的に無効化
        self.assertIsNone(learn_capture.process({"prompt": "X ではなく Y にして", "cwd": "/x"}))

    def test_default_unset_is_on(self):
        os.environ.pop("UA_AUTOAPPLY", None)  # 未設定=既定 ON
        self.assertIsNotNone(learn_capture.process({"prompt": "X ではなく Y にして", "cwd": "/x"}))

    def test_missing_prompt_none(self):
        self.assertIsNone(learn_capture.process({}))


class TestMainWrites(_helpers.ConfigDirTestCase):
    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUTOAPPLY="1")

    def test_main_appends_candidate(self):
        with tempfile.TemporaryDirectory() as cwd:
            with _helpers.stdin_payload(
                    {"prompt": "そうじゃなくて A にして", "cwd": cwd, "session_id": "s"}):
                code = learn_capture.main()
            self.assertEqual(code, 0)
            f = common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES
            self.assertTrue(f.exists())
            self.assertIn("correction", f.read_text())

    def test_main_silent_on_non_correction(self):
        with tempfile.TemporaryDirectory() as cwd:
            with _helpers.stdin_payload({"prompt": "build the feature", "cwd": cwd}):
                self.assertEqual(learn_capture.main(), 0)
            self.assertFalse(
                (common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES).exists())


if __name__ == "__main__":
    unittest.main()
