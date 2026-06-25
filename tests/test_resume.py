"""Unit tests for claude-home/hooks/resume_context.py (run: python3 -m unittest)."""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import learning  # noqa: E402
import resume_context as rc  # noqa: E402


class TestProgressBranch(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(rc.progress_branch("- branch: main   HEAD: abc123"), "main")

    def test_none_when_absent(self):
        self.assertIsNone(rc.progress_branch("no branch line here"))


class TestBuild(_helpers.ConfigDirTestCase):
    def setUp(self):
        super().setUp()
        _helpers.stub_git_none(self)  # 非 git tempdir では挙動不変・git spawn を消す

    def _write(self, cwd, text):
        (common.shared_state_dir(cwd) / common.STATE_PROGRESS).write_text(text)

    def test_injects_on_startup(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._write(cwd, "# p\n- branch: None   HEAD: x\nhello-body")
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("hello-body", out)

    def test_injects_on_clear(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._write(cwd, "- branch: None   HEAD: x\nbody")
            self.assertIn("body", rc.build({"source": "clear", "cwd": cwd}))

    def test_skips_on_resume(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._write(cwd, "body")
            self.assertEqual(rc.build({"source": "resume", "cwd": cwd}), "")

    def test_no_progress_file(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(rc.build({"source": "startup", "cwd": cwd}), "")

    def test_skips_on_branch_mismatch(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._write(cwd, "- branch: feature-x   HEAD: y\nbody")
            orig = common.git_branch
            common.git_branch = lambda c: "main"  # pretend current branch is main
            try:
                self.assertEqual(rc.build({"source": "startup", "cwd": cwd}), "")
            finally:
                common.git_branch = orig


class TestInstinctInjection(_helpers.ConfigDirTestCase):
    """fire(注入)は UA_AUTOAPPLY 下のみ・毎回同じ文章・進捗ブロックは不変。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUTOAPPLY=None)
        _helpers.stub_git_none(self)

    def _write_learned(self, cwd, text):
        (common.shared_state_dir(cwd) / common.STATE_LEARNED).write_text(text)

    def test_no_injection_when_disabled(self):
        _helpers.set_env(self, UA_AUTOAPPLY="0")  # 明示的に無効化
        with tempfile.TemporaryDirectory() as cwd:
            self._write_learned(cwd, "# h\n- use logging not print  <!-- src=correction -->\n")
            self.assertEqual(rc.build({"source": "startup", "cwd": cwd}), "")

    def test_default_unset_injects(self):
        _helpers.set_env(self, UA_AUTOAPPLY=None)  # 未設定=既定 ON
        with tempfile.TemporaryDirectory() as cwd:
            self._write_learned(cwd, "- quote shell paths\n")
            self.assertIn("quote shell paths", rc.build({"source": "startup", "cwd": cwd}))

    def test_autodistill_single_correction_goes_to_staging(self):
        _helpers.set_env(self, UA_AUTOAPPLY=None)  # 既定 ON
        with tempfile.TemporaryDirectory() as cwd:
            common.append_jsonl_capped(
                common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES,
                {"source": "correction", "text": "use logging not print"})
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertNotIn("use logging not print", out)  # 単発はまだ active でない=注入しない
            self.assertEqual(  # 下書き(隔離・非注入)に入る
                learning.read_learned_texts(
                    common.shared_state_dir(cwd) / common.STATE_LEARN_DRAFT),
                ["use logging not print"])
            self.assertFalse(  # 候補は消費される
                (common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES).exists())

    def test_autodistill_repeated_correction_promotes_and_injects(self):
        _helpers.set_env(self, UA_AUTOAPPLY=None)  # 既定 ON
        with tempfile.TemporaryDirectory() as cwd:
            cpath = common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES
            common.append_jsonl_capped(cpath, {"source": "correction", "text": "use logging not print"})
            common.append_jsonl_capped(cpath, {"source": "correction", "text": "use logging not print"})
            out = rc.build({"source": "startup", "cwd": cwd})  # 同一訂正2回=確かめた事実 → active
            self.assertIn("use logging not print", out)

    def test_injects_when_flag_on_without_provenance(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            self._write_learned(cwd, "# h\n- use logging not print  <!-- src=correction -->\n")
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("use logging not print", out)
            self.assertIn("学習した約束ごと", out)
            self.assertNotIn("src=correction", out)  # provenance is NOT injected

    def test_injection_is_byte_stable_regardless_of_file_order(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            self._write_learned(cwd, "# h\n- rule about bbb  <!-- src=x -->\n- rule about aaa  <!-- src=y -->\n")
            out1 = rc.build({"source": "startup", "cwd": cwd})
            self._write_learned(cwd, "# h\n- rule about aaa  <!-- src=z -->\n- rule about bbb  <!-- src=w -->\n")
            out2 = rc.build({"source": "startup", "cwd": cwd})
            self.assertEqual(out1, out2)  # sorted + provenance excluded => identical bytes

    def test_instincts_inject_without_progress_file(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            self._write_learned(cwd, "- always quote shell paths\n")
            self.assertIn("always quote shell paths", rc.build({"source": "startup", "cwd": cwd}))

    def test_flag_on_but_no_instincts_is_empty(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(rc.build({"source": "startup", "cwd": cwd}), "")


class TestRulesInjection(_helpers.ConfigDirTestCase):
    """ua-rules: 関係トピックだけを 毎回同じ文章 自動注入(言語=stack / ドメイン=.ultra-ai.toml)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_RULES=None)
        _helpers.stub_git_none(self)
        rdir = common.config_dir() / "rules"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "python.md").write_text("## python\n- use logging\n", encoding="utf-8")
        (rdir / "typescript.md").write_text("## ts\n- strict mode\n", encoding="utf-8")
        (rdir / "frontend.md").write_text("## frontend\n- a11y first\n", encoding="utf-8")
        (rdir / "backend.md").write_text("## backend\n- validate input\n", encoding="utf-8")

    def _repo(self, d, manifest="pyproject.toml", toml=None):
        (Path(d) / manifest).write_text("")
        if toml:
            _helpers.write_config_toml(d, toml)

    def _pkg(self, d, deps, toml=None):
        """package.json(react 等の依存)を書く + 任意の .ultra-ai.toml。"""
        import json
        (Path(d) / "package.json").write_text(json.dumps({"dependencies": deps}))
        if toml:
            _helpers.write_config_toml(d, toml)

    def test_language_scoped_by_stack(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._repo(cwd, "pyproject.toml")
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("use logging", out)        # python.md(stack=python)
            self.assertNotIn("strict mode", out)     # typescript は無関係 → 注入しない
            self.assertIn("約束ごと", out)

    def test_domain_via_config(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._repo(cwd, "pyproject.toml", '[ua-rules]\ndomains = ["frontend"]\n')
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("use logging", out)        # python(stack)
            self.assertIn("a11y first", out)         # frontend(domain set-once)

    def test_byte_stable(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._repo(cwd, "pyproject.toml", '[ua-rules]\ndomains = ["frontend"]\n')
            a = rc.build({"source": "startup", "cwd": cwd})
            b = rc.build({"source": "startup", "cwd": cwd})
            self.assertEqual(a, b)                    # 同一リポ → 同一バイト列

    def test_disabled_with_ua_rules_0(self):
        _helpers.set_env(self, UA_RULES="0")
        with tempfile.TemporaryDirectory() as cwd:
            self._repo(cwd, "pyproject.toml")
            self.assertNotIn("約束ごと", rc.build({"source": "startup", "cwd": cwd}))

    def test_unknown_stack_no_rules(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertNotIn("約束ごと", rc.build({"source": "startup", "cwd": cwd}))

    def test_domain_auto_detected_without_config(self):
        # 本命回帰: .ultra-ai.toml 無しでも、react 依存からドメイン規約が自動注入される
        with tempfile.TemporaryDirectory() as cwd:
            self._pkg(cwd, {"react": "^18"})
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("a11y first", out)          # frontend(自動検出)
            self.assertIn("strict mode", out)         # typescript(node stack)

    def test_auto_and_manual_union(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._pkg(cwd, {"react": "^18"}, '[ua-rules]\ndomains = ["backend"]\n')
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("a11y first", out)          # frontend(自動)
            self.assertIn("validate input", out)      # backend(手動)= 和集合

    def test_auto_false_opts_out_but_keeps_manual(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._pkg(cwd, {"react": "^18"},
                      '[ua-rules]\nauto = false\ndomains = ["backend"]\n')
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertNotIn("a11y first", out)       # 自動は止まる
            self.assertIn("validate input", out)      # 手動 domains は残る(後方互換)

    def test_backward_compat_manual_only_unchanged(self):
        # 検出されない言語(空 pyproject=python・ドメイン信号なし)+ 手動 domains は従来どおり
        with tempfile.TemporaryDirectory() as cwd:
            self._repo(cwd, "pyproject.toml", '[ua-rules]\ndomains = ["frontend"]\n')
            out = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("a11y first", out)
            self.assertNotIn("validate input", out)

    def test_byte_stable_with_auto_domains(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._pkg(cwd, {"react": "^18", "express": "^4"})
            a = rc.build({"source": "startup", "cwd": cwd})
            b = rc.build({"source": "startup", "cwd": cwd})
            self.assertEqual(a, b)                     # 自動ドメインありでも同一バイト列


class TestDistillSuggestion(_helpers.ConfigDirTestCase):
    """SessionStart で 下書き が溜まったら /ua-learn を提案(固定文言・毎回同じ文章)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUTOAPPLY=None, UA_SUGGEST_LEARN=None,
                         UA_LEARN_MIN_DRAFT="2")
        _helpers.stub_git_none(self)

    def _draft(self, cwd, n):
        p = common.shared_state_dir(cwd) / common.STATE_LEARN_DRAFT
        p.write_text("\n".join(f"- cand {i}  <!-- src=fail-pass -->" for i in range(n)),
                     encoding="utf-8")

    def test_hint_when_over_threshold(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._draft(cwd, 3)
            self.assertIn("/ua-learn", rc.build({"source": "startup", "cwd": cwd}))

    def test_no_hint_under_threshold(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._draft(cwd, 1)
            self.assertNotIn("/ua-learn", rc.build({"source": "startup", "cwd": cwd}))

    def test_hint_omits_count_byte_stable(self):
        # 件数を注入しない → 件数が違っても hint は同一バイト列(別 cwd で帯を跨いで確認)
        with tempfile.TemporaryDirectory() as c1, tempfile.TemporaryDirectory() as c2:
            self._draft(c1, 5)
            self._draft(c2, 9)
            a = rc.build({"source": "startup", "cwd": c1})
            b = rc.build({"source": "startup", "cwd": c2})
            self.assertIn("/ua-learn", a)
            self.assertEqual(a, b)

    def test_dedup_same_band(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._draft(cwd, 5)
            first = rc.build({"source": "startup", "cwd": cwd})
            second = rc.build({"source": "startup", "cwd": cwd})
            self.assertIn("/ua-learn", first)
            self.assertNotIn("/ua-learn", second)  # 同じ帯では再提案しない(nag 回避)

    def test_kill_switch(self):
        _helpers.set_env(self, UA_SUGGEST_LEARN="0")
        with tempfile.TemporaryDirectory() as cwd:
            self._draft(cwd, 9)
            self.assertNotIn("/ua-learn", rc.build({"source": "startup", "cwd": cwd}))

    def test_off_when_autofire_disabled(self):
        _helpers.set_env(self, UA_AUTOAPPLY="0")
        with tempfile.TemporaryDirectory() as cwd:
            self._draft(cwd, 9)
            self.assertNotIn("/ua-learn", rc.build({"source": "startup", "cwd": cwd}))


class TestBenchSuggestion(_helpers.ConfigDirTestCase):
    """有効な学習した約束ごと がマイルストン帯に達したら一度だけ /ua-compare を提案(dedup=毎回出さない)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUTOAPPLY=None, UA_SUGGEST_BENCH=None,
                         UA_BENCH_MILESTONE="3")
        _helpers.stub_git_none(self)

    def _learned(self, cwd, n):
        p = common.shared_state_dir(cwd) / common.STATE_LEARNED
        p.write_text("\n".join(f"- rule {i}  <!-- src=correction -->" for i in range(n)),
                     encoding="utf-8")

    def test_hint_at_milestone(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._learned(cwd, 3)
            self.assertIn("/ua-compare", rc.build({"source": "startup", "cwd": cwd}))

    def test_no_hint_under_milestone(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._learned(cwd, 2)
            self.assertNotIn("/ua-compare", rc.build({"source": "startup", "cwd": cwd}))

    def test_dedup_same_milestone(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._learned(cwd, 4)
            first = rc.build({"source": "startup", "cwd": cwd})
            second = rc.build({"source": "startup", "cwd": cwd})  # 同帯 → 再提案しない
            self.assertIn("/ua-compare", first)
            self.assertNotIn("/ua-compare", second)

    def test_kill_switch(self):
        _helpers.set_env(self, UA_SUGGEST_BENCH="0")
        with tempfile.TemporaryDirectory() as cwd:
            self._learned(cwd, 9)
            self.assertNotIn("/ua-compare", rc.build({"source": "startup", "cwd": cwd}))


class TestGlobalInstinctInjection(_helpers.ConfigDirTestCase):
    """全プロジェクト共通の学習した約束ごと ブロック: project 学習した約束ごと より前に注入・重複は二重注入しない・flag で無効。"""

    CWD = "/x"
    GLOBAL = "ログは print ではなく logging を使う"
    LOCAL = "境界で入力を必ず検証する"

    def setUp(self):
        super().setUp()
        _helpers.stub_git_none(self)

    def _setup(self):
        learning.record_active_lessons("repoX", [self.GLOBAL])
        learning.record_active_lessons("repoY", [self.GLOBAL])   # 2 repo 合意 → global
        active = common.shared_state_dir(self.CWD) / common.STATE_LEARNED
        common.write_text_atomic(active, "# h\n- " + self.GLOBAL + "\n- " + self.LOCAL + "\n")

    def _build(self):
        return rc.build({"source": "startup", "cwd": self.CWD})

    def test_global_before_local_and_deduped(self):
        self._setup()
        out = self._build()
        self.assertIn("全プロジェクト共通の学習した約束ごと", out)
        self.assertIn(self.LOCAL, out)
        self.assertEqual(out.count(self.GLOBAL), 1)   # global と重複する分は二重注入しない
        self.assertLess(out.index("全プロジェクト共通の学習した約束ごと"),
                        out.index("ultra-ai — 学習した約束ごと:"))

    def test_disabled_flag_no_global_block(self):
        self._setup()
        _helpers.set_env(self, UA_GLOBAL_LEARNING="0")
        out = self._build()
        self.assertNotIn("全プロジェクト共通の学習した約束ごと", out)
        self.assertIn(self.GLOBAL, out)   # flag OFF なら local ブロックに GLOBAL も残る(除去しない)


class TestCompactReinject(_helpers.ConfigDirTestCase):
    """source=compact: 飲まれた byte-stable 層を再注入 + 継続アンカー(transient)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_COMPACT_RESUME=None, UA_AUTOAPPLY="1")
        _helpers.stub_git_none(self)

    def _payload(self, cwd, source="compact"):
        return {"source": source, "cwd": cwd, "session_id": "s1"}

    def _progress(self, cwd, text="- branch: None   HEAD: x\nprogress-body"):
        (common.shared_state_dir(cwd) / common.STATE_PROGRESS).write_text(text)

    def test_reinjects_progress_and_learned_on_compact(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._progress(cwd)
            (common.shared_state_dir(cwd) / common.STATE_LEARNED).write_text("- quote shell paths\n")
            out = rc.build(self._payload(cwd))
            self.assertIn("progress-body", out)      # progress 再注入
            self.assertIn("quote shell paths", out)  # 学習 再注入

    def test_anchor_includes_verification(self):
        with tempfile.TemporaryDirectory() as cwd:
            sdir = common.session_state_dir(cwd, "s1")
            (sdir / common.STATE_VERIFICATION).write_text(json.dumps({"result": "PASS"}))
            out = rc.build(self._payload(cwd))
            self.assertIn("compaction 引き継ぎ", out)
            self.assertIn("PASS", out)

    def test_anchor_includes_factgate_checked(self):
        with tempfile.TemporaryDirectory() as cwd:
            sdir = common.session_state_dir(cwd, "s1")
            (sdir / "factgate.json").write_text(json.dumps({"checked": ["/x/foo.py"], "denials": 1}))
            out = rc.build(self._payload(cwd))
            self.assertIn("foo.py", out)

    def test_anchor_only_on_compact_not_startup(self):
        with tempfile.TemporaryDirectory() as cwd:
            sdir = common.session_state_dir(cwd, "s1")
            (sdir / common.STATE_VERIFICATION).write_text(json.dumps({"result": "PASS"}))
            self._progress(cwd)
            out = rc.build({"source": "startup", "cwd": cwd, "session_id": "s1"})
            self.assertNotIn("compaction 引き継ぎ", out)  # アンカーは compact のときだけ

    def test_resume_still_empty(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._progress(cwd)
            self.assertEqual(rc.build(self._payload(cwd, source="resume")), "")

    def test_kill_switch(self):
        _helpers.set_env(self, UA_COMPACT_RESUME="0")
        with tempfile.TemporaryDirectory() as cwd:
            self._progress(cwd)
            self.assertEqual(rc.build(self._payload(cwd)), "")

    def test_empty_when_no_state(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(rc.build(self._payload(cwd)), "")


if __name__ == "__main__":
    unittest.main()
