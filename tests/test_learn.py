"""Unit tests for claude-home/hooks/learn.py (run: python3 -m unittest).

賢く半自動の振り分け(correction→active / fail-pass→staging)、重複排除・上限・冪等。
"""
import tempfile
import unittest

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import learning  # noqa: E402
import learn  # noqa: E402


def _active(cwd):
    return learning.read_learned_texts(common.shared_state_dir(cwd) / common.STATE_LEARNED)


def _draft(cwd):
    return learning.read_learned_texts(common.shared_state_dir(cwd) / common.STATE_LEARN_DRAFT)


class TestMigrate(_helpers.ConfigDirTestCase):
    def test_renames_legacy_files_idempotent(self):
        with tempfile.TemporaryDirectory() as cwd:
            shared = common.shared_state_dir(cwd)
            # 旧名(移行元)を意図的に置く。migrate_state が新名へ rename することを検証。
            (shared / "INSTINCTS.md").write_text(
                "# h\n- use logging not print  <!-- src=correction -->\n", encoding="utf-8")
            (shared / "instinct-staging.md").write_text("- maybe a flaky test\n", encoding="utf-8")
            n = learn.migrate_state(cwd)
            self.assertGreaterEqual(n, 2)
            self.assertFalse((shared / "INSTINCTS.md").exists())
            self.assertTrue((shared / common.STATE_LEARNED).exists())
            self.assertEqual(_active(cwd), ["use logging not print"])
            self.assertEqual(_draft(cwd), ["maybe a flaky test"])
            self.assertEqual(learn.migrate_state(cwd), 0)  # 冪等: 2回目は何もしない


class TestRouting(unittest.TestCase):
    def test_route_for(self):
        # route_for は LLM/人手で般化済みの apply 入力の既定(決定論の自動経路は使わない)。
        self.assertEqual(learn.route_for("correction"), learn.ROUTE_ACTIVE)
        self.assertEqual(learn.route_for("fail-pass"), learn.ROUTE_DRAFT)
        self.assertEqual(learn.route_for("whatever"), learn.ROUTE_DRAFT)


class TestApply(_helpers.ConfigDirTestCase):
    def test_routes_and_persists(self):
        with tempfile.TemporaryDirectory() as cwd:
            res = learn.apply(cwd, [
                {"text": "use logging not print", "source": "correction", "route": "active"},
                {"text": "the auth test was flaky", "source": "fail-pass", "route": "draft"},
            ])
            self.assertEqual((res["active"], res["draft"]), (1, 1))
            self.assertEqual(_active(cwd), ["use logging not print"])
            self.assertEqual(_draft(cwd), ["the auth test was flaky"])

    def test_dedup_and_idempotent_bytes(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, [{"text": "rule about bbb", "source": "correction", "route": "active"}])
            learn.apply(cwd, [{"text": "rule about aaa", "source": "correction", "route": "active"},
                                {"text": "rule about bbb", "source": "correction", "route": "active"}])
            path = common.shared_state_dir(cwd) / common.STATE_LEARNED
            self.assertEqual(_active(cwd), ["rule about aaa", "rule about bbb"])  # deduped, sorted
            before = path.read_bytes()
            learn.apply(cwd, [])  # re-apply nothing -> identical file (idempotent/毎回同じ文章)
            self.assertEqual(path.read_bytes(), before)

    def test_active_cap(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, [{"text": f"reusable rule number {i:03d}", "source": "correction",
                                 "route": "active"} for i in range(40)])
            self.assertEqual(len(_active(cwd)), learn.MAX_ACTIVE)

    def test_empty_text_skipped(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, [{"text": "   ", "source": "correction", "route": "active"}])
            self.assertEqual(_active(cwd), [])

    def test_noise_route_active_downgraded_to_draft(self):
        # 防御: active 行きでも再利用不能(ノイズ)なら active に書かない
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, [{"text": "<task-notification> garbage text here",
                                 "source": "correction", "route": "active"}])
            self.assertEqual(_active(cwd), [])

    def test_existing_noise_purged_from_active_on_reapply(self):
        with tempfile.TemporaryDirectory() as cwd:
            ap = common.shared_state_dir(cwd) / common.STATE_LEARNED
            ap.write_text("# h\n- <task-notification> garbage line  <!-- src=correction -->\n"
                          "- use logging not print  <!-- src=correction -->\n", encoding="utf-8")
            learn.apply(cwd, [])  # 何も足さず再適用 → ノイズ行は物理削除・クリーン行は残る
            self.assertEqual(_active(cwd), ["use logging not print"])


class TestCandidates(_helpers.ConfigDirTestCase):
    def test_load_and_clear(self):
        with tempfile.TemporaryDirectory() as cwd:
            f = common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES
            common.append_jsonl_capped(f, {"source": "correction", "text": "a"})
            common.append_jsonl_capped(f, {"source": "fail-pass", "text": "b"})
            self.assertEqual(len(learn.load_candidates(cwd)), 2)
            learn.clear_candidates(cwd)
            self.assertEqual(learn.load_candidates(cwd), [])


class TestAutoInstincts(_helpers.ConfigDirTestCase):
    """b+ 決定論経路: 全件 staging 既定・反復(確かめた事実)でのみ active 昇格。"""

    def test_single_correction_goes_to_draft(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, learn.auto_lessons(
                cwd, [{"source": "correction", "text": "use logging not print"}]))
            self.assertEqual(_active(cwd), [])                       # 単発は active にしない
            self.assertEqual(_draft(cwd), ["use logging not print"])

    def test_repeated_correction_promotes_to_active(self):
        with tempfile.TemporaryDirectory() as cwd:
            cands = [{"source": "correction", "text": "use logging not print"}] * 2
            learn.apply(cwd, learn.auto_lessons(cwd, cands))   # 既定 N=2
            self.assertEqual(_active(cwd), ["use logging not print"])

    def test_repeat_count_persists_across_calls(self):
        with tempfile.TemporaryDirectory() as cwd:
            txt = {"source": "correction", "text": "always quote shell paths"}
            learn.apply(cwd, learn.auto_lessons(cwd, [txt]))   # 通算1回 → staging
            self.assertEqual(_active(cwd), [])
            learn.apply(cwd, learn.auto_lessons(cwd, [txt]))   # 通算2回 → active
            self.assertEqual(_active(cwd), ["always quote shell paths"])

    def test_repeat_threshold_configurable(self):
        _helpers.set_env(self, UA_PROMOTE_REPEAT="3")
        with tempfile.TemporaryDirectory() as cwd:
            cands = [{"source": "correction", "text": "always quote shell paths"}] * 2
            learn.apply(cwd, learn.auto_lessons(cwd, cands))   # 2回ではまだ(閾値3)
            self.assertEqual(_active(cwd), [])

    def test_noise_never_promotes(self):
        with tempfile.TemporaryDirectory() as cwd:
            cands = [{"source": "correction",
                      "text": "<task-notification> <task-id>x</task-id> 違う"}] * 5
            learn.apply(cwd, learn.auto_lessons(cwd, cands))
            self.assertEqual(_active(cwd), [])                       # ノイズは何回でも active にしない

    def test_short_correction_never_promotes(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, learn.auto_lessons(
                cwd, [{"source": "correction", "text": "もっと違う案"}] * 5))
            self.assertEqual(_active(cwd), [])                       # 短すぎ=再利用不能

    def test_failpass_stays_in_staging(self):
        with tempfile.TemporaryDirectory() as cwd:
            learn.apply(cwd, learn.auto_lessons(
                cwd, [{"source": "fail-pass", "text": "the auth test was flaky earlier"}] * 3))
            self.assertEqual(_active(cwd), [])
            self.assertIn("the auth test was flaky earlier", _draft(cwd))


class TestGlobalPromotion(_helpers.ConfigDirTestCase):
    """全プロジェクト共通の学習した約束ごと(2+ repo 合意)に一致する単発訂正は初回 active。現状の二値挙動も非退行で確認。"""

    INST = "ログは print ではなく logging を使う"

    def _make_global(self):
        learning.record_active_lessons("repoA", [self.INST])
        learning.record_active_lessons("repoB", [self.INST])

    def test_matching_global_single_correction_is_active(self):
        self._make_global()
        out = learn.auto_lessons("/x", [{"text": self.INST, "source": "correction"}])
        self.assertEqual(out[0]["route"], learn.ROUTE_ACTIVE)  # 単発でも cross-repo 裏付けで昇格

    def test_non_global_single_correction_is_staging(self):
        out = learn.auto_lessons("/x", [{"text": self.INST, "source": "correction"}])
        self.assertEqual(out[0]["route"], learn.ROUTE_DRAFT)  # 現状非退行(単発は下書き)

    def test_repeat_still_promotes_without_global(self):
        c = [{"text": self.INST, "source": "correction"}]
        self.assertEqual(learn.auto_lessons("/x", c)[0]["route"], learn.ROUTE_DRAFT)
        self.assertEqual(learn.auto_lessons("/x", c)[0]["route"], learn.ROUTE_ACTIVE)  # repeat>=2

    def test_global_disabled_reverts_to_binary(self):
        self._make_global()
        _helpers.set_env(self, UA_GLOBAL_LEARNING="0")
        out = learn.auto_lessons("/x", [{"text": self.INST, "source": "correction"}])
        self.assertEqual(out[0]["route"], learn.ROUTE_DRAFT)


if __name__ == "__main__":
    unittest.main()
