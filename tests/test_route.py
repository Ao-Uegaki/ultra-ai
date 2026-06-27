"""Unit tests for claude-home/hooks/route.py (run: python3 -m unittest).

UserPromptSubmit のフレーミング足場: 機械的(乱数なし)・毎回同じ文章・薄いプロンプトにだけ注入。
分類はしない(モデルの仕事)。粗い SILENCE(trivial/会話/戦略明示)だけ機械的に判定。
"""
import contextlib
import io
import os
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import route  # noqa: E402


class TestSilence(unittest.TestCase):
    """should_silence: trivial / 会話 / 戦略明示は無音にし、非自明は無音にしない(足場を出す)、を守る。"""

    def setUp(self):
        _helpers.set_env(self, UA_ROUTE=None)  # 既定 ON

    # --- SILENCE(最重要・厚く): trivial/会話は無音 ---
    def test_typo_silenced(self):
        self.assertTrue(route.should_silence("typo直して foo.py"))

    def test_format_silenced(self):
        self.assertTrue(route.should_silence("このファイルをフォーマットして"))

    def test_greeting_silenced(self):
        for p in ("ありがとう", "ok", "了解です!", "go ahead", "👍"):
            self.assertTrue(route.should_silence(p), p)

    def test_empty_silenced(self):
        self.assertTrue(route.should_silence(""))
        self.assertTrue(route.should_silence("   "))
        self.assertTrue(route.should_silence(None))

    # --- ユーザー戦略明示 → 抑制(二重フレーミングしない=訂正優先の同型) ---
    def test_user_strategy_suppresses(self):
        for p in ("ultracode で設計を比較して", "/effort max にして",
                  "design-panel で案出して", "失敗するテストを先に書いて",
                  "review-audit を回して", "/ua-spec して"):
            self.assertTrue(route.should_silence(p), p)

    # --- 非自明 → 足場あり(分類はしない・足場が出ることだけ確認) ---
    def test_nontrivial_not_silenced(self):
        for p in ("認証まわりを作り直したい", "ログインが時々失敗する原因を調べて",
                  "支払いフローを設計して", "build a rate limiter for the API",
                  "このリポジトリ全体の構造を把握したい",
                  "全モジュールで logger を新APIへ移行して"):
            self.assertFalse(route.should_silence(p), p)

    # 実タスクが短い挨拶語を部分文字列に含んでも誤爆しない(完全一致の確認)。
    def test_greeting_substring_not_false_positive(self):
        self.assertFalse(route.should_silence("hide the modal on close"))  # 'hi' を含む


class TestBuild(unittest.TestCase):
    """route.build と scaffold の不変条件(approach 強制・毎回同じ文章・本文を漏らさない・尋問しない/effort を変えない wording)を守る。"""

    def setUp(self):
        _helpers.set_env(self, UA_ROUTE=None)

    def test_nontrivial_injects_scaffold(self):
        msg = route.build({"prompt": "認証まわりを作り直したい"})
        self.assertTrue(msg)
        self.assertEqual(msg, route.scaffold())

    def test_trivial_empty(self):
        self.assertEqual(route.build({"prompt": "typo直して"}), "")

    def test_missing_prompt_empty(self):
        self.assertEqual(route.build({}), "")

    # 3b. コミット強要(approach 行の必須出力)が消えていない
    def test_scaffold_has_commitment(self):
        s = route.scaffold()
        self.assertIn("approach:", s)
        self.assertIn("着手前", s)
        self.assertIn("出力してから", s)

    # 4. 毎回同じ文章 + プロンプト本文の非漏洩
    def test_byte_stable_and_no_prompt_leak(self):
        self.assertEqual(route.scaffold(), route.scaffold())
        uniq = "ZZUNIQUE_TOKEN_42"
        out = route.build({"prompt": f"{uniq} を実装して新機能を作る"})
        self.assertTrue(out)
        self.assertNotIn(uniq, out)  # 注入バイトにプロンプト本文を混ぜない

    # 5. 尋問しない wording ガード(仮定して進む を保持)
    def test_no_interrogation_wording(self):
        s = route.scaffold()
        for bad in ("確認してから", "ask the user", "確認を取って", "尋ねてから"):
            self.assertNotIn(bad, s)
        self.assertIn("仮定", s)

    # 6. effort 不変 wording ガード(提案のみ・自分で変えない)
    def test_no_effort_mutation_wording(self):
        s = route.scaffold()
        for bad in ("切り替え", "use ultracode", "set effort", "effort を上げ"):
            self.assertNotIn(bad, s)
        self.assertIn("提案", s)

    # 8. 後回し禁止 / fan-out 先導 / ON·OFF 分岐 ガード(後回し提案の回帰防止)
    def test_no_afterthought_fanout_ordering(self):
        s = route.scaffold()
        self.assertIn("fan-out", s)
        self.assertIn("発散型", s)
        self.assertIn("最初の探索より前", s)
        self.assertIn("「余地あり」", s)      # 後回しのぼかし(hedge)を明示禁止
        self.assertIn("solo", s)
        self.assertIn("既定 ON", s)           # 既定(ON)/無効化(OFF)双方の枝が存在
        self.assertIn("UA_ULTRACODE=0", s)
        # 「fan-out が初手・solo はフォールバック」の指針が消えていないことを存在ガードで担保する
        # (脆い index 語順固定はやめる=文言の並べ替えで誤って壊さない)。
        self.assertIn("solo で始めない", s)

    # 9. オープンエンド/中身未指定は発散型として扱う指針が消えていない
    def test_open_ended_guidance_present(self):
        s = route.scaffold()
        self.assertIn("オープンエンド", s)
        self.assertIn("発散型", s)
        self.assertIn("ありきたり", s)  # 既製 menu に逃げない指針が消えていない

    # 7. kill switch / default-on
    def test_kill_switch(self):
        _helpers.set_env(self, UA_ROUTE="0")
        self.assertEqual(route.build({"prompt": "認証まわりを作り直したい"}), "")

    def test_default_unset_is_on(self):
        os.environ.pop("UA_ROUTE", None)
        self.assertTrue(route.build({"prompt": "認証まわりを作り直したい"}))


class TestMain(unittest.TestCase):
    """route.main が非自明で出力し・trivial で無音・壊れた入力でも例外を出さない、を守る。"""

    def setUp(self):
        _helpers.set_env(self, UA_ROUTE=None)

    def _run(self, payload) -> tuple[int, str]:
        buf = io.StringIO()
        with _helpers.stdin_payload(payload):
            with contextlib.redirect_stdout(buf):
                code = route.main()
        return code, buf.getvalue()

    def test_main_prints_on_nontrivial(self):
        code, out = self._run({"prompt": "支払いフローを設計して"})
        self.assertEqual(code, 0)
        self.assertIn("approach:", out)

    def test_main_silent_on_trivial(self):
        code, out = self._run({"prompt": "typo直して"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_main_never_raises_on_garbage(self):
        code, out = self._run("not json {")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
