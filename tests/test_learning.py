"""Unit tests for claude-home/hooks/learning.py (run: python3 -m unittest)."""
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import learning  # noqa: E402


class TestInstinctPredicates(unittest.TestCase):
    """学習層のノイズ/品質ゲート(b+): Claude Code 本体由来の断片・空虚な短文を弾く純述語。"""

    def test_noise_markers_detected(self):
        self.assertTrue(learning.looks_like_noise("<task-notification> <task-id>x</task-id>"))
        self.assertTrue(learning.looks_like_noise("see /private/tmp/claude-501/foo"))
        self.assertTrue(learning.looks_like_noise("toolu_01ABC ではなく Y"))
        self.assertTrue(learning.looks_like_noise("approach: 実装 → 仮定"))

    def test_clean_correction_is_not_noise(self):
        self.assertFalse(learning.looks_like_noise("print じゃなく logging を使って"))

    def test_reusable_requires_length_and_clean(self):
        self.assertTrue(learning.is_reusable_lesson("print じゃなく logging を使って"))
        self.assertFalse(learning.is_reusable_lesson("もっと違う案"))                 # 短すぎ
        self.assertFalse(learning.is_reusable_lesson("<task-notification> blah blah"))  # ノイズ
        self.assertFalse(learning.is_reusable_lesson(""))
        self.assertFalse(learning.is_reusable_lesson(None))

    def test_normalize_key_collapses_ws_and_lowercases(self):
        self.assertEqual(learning.normalize_lesson_key("  Use   LOGGING  "), "use logging")


class TestReadInstinctReusable(unittest.TestCase):
    """read_learned_texts(reusable_only=...) の絞り込み(active 注入のガード)。"""

    def _write(self, d, body):
        p = Path(d) / "LEARNED.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_default_keeps_all_nonempty_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "# h\n- short\n- a real reusable rule here  <!-- src=x -->\n")
            self.assertEqual(learning.read_learned_texts(p),
                             ["a real reusable rule here", "short"])

    def test_reusable_only_filters_noise_and_short(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(
                d, "# h\n- short\n- <task-notification> junk text here\n- a real reusable rule here\n")
            self.assertEqual(learning.read_learned_texts(p, reusable_only=True),
                             ["a real reusable rule here"])


class TestGlobalInstincts(_helpers.ConfigDirTestCase):
    """project→global 昇格: GLOBAL_REPO_THRESHOLD(2)以上の repo で active な 学習した約束ごと だけ global。"""

    INST = "ログは print ではなく logging を使う"

    def test_single_repo_not_global(self):
        learning.record_active_lessons("repoA", [self.INST])
        self.assertEqual(learning.read_global_learned_texts(), [])
        self.assertFalse(learning.is_global_lesson(self.INST))

    def test_two_repos_promote_to_global(self):
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoB", [self.INST])
        self.assertEqual(learning.read_global_learned_texts(), [self.INST])
        self.assertTrue(learning.is_global_lesson(self.INST))

    def test_same_repo_twice_not_global(self):
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoA", [self.INST])  # 同一 repo の再記録は 1 票
        self.assertFalse(learning.is_global_lesson(self.INST))

    def test_is_global_normalized_match(self):
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoB", [self.INST])
        # 空白畳み込み + ASCII 小文字化を吸収(normalize_lesson_key)
        self.assertTrue(learning.is_global_lesson("ログは  PRINT  ではなく LOGGING を使う"))

    def test_dropped_when_repo_no_longer_active(self):
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoB", [self.INST])
        self.assertTrue(learning.is_global_lesson(self.INST))
        learning.record_active_lessons("repoB", [])  # repoB がもう active にしていない
        self.assertFalse(learning.is_global_lesson(self.INST))  # 合意 1 repo に減り global 解除

    def test_byte_stable_sorted(self):
        a, b = "ログは print ではなく logging を使う", "境界で入力を必ず検証する"
        learning.record_active_lessons("r1", [a, b])
        learning.record_active_lessons("r2", [b, a])  # 記録順が違っても
        self.assertEqual(learning.read_global_learned_texts(), sorted([a, b]))

    def test_disabled_flag(self):
        _helpers.set_env(self, UA_GLOBAL_LEARNING="0")
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoB", [self.INST])
        self.assertEqual(learning.read_global_learned_texts(), [])
        self.assertFalse(learning.is_global_lesson(self.INST))


if __name__ == "__main__":
    unittest.main()
