"""Unit tests for claude-home/hooks/verify.py (run: python3 -m unittest)."""
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import verify  # noqa: E402

PASS_RUNNER = lambda *a, **k: (common.PASS, "")          # noqa: E731
FAIL_RUNNER = lambda *a, **k: (common.FAIL, "a:1 oops")  # noqa: E731


class TestBuildLint(unittest.TestCase):
    def test_substitutes_placeholder(self):
        self.assertEqual(verify.build_lint("eslint --fix {file}", "/a/b.ts"),
                         "eslint --fix '/a/b.ts'")

    def test_appends_when_no_placeholder(self):
        self.assertEqual(verify.build_lint("ruff check --fix", "/a/b.py"),
                         "ruff check --fix '/a/b.py'")


class TestProcess(_helpers.ConfigDirTestCase):
    def test_no_file_silent(self):
        code, msg = verify.process({"cwd": "/x"}, runner=FAIL_RUNNER)
        self.assertEqual((code, msg), (0, ""))

    def test_non_source_silent(self):
        payload = {"tool_input": {"file_path": "/x/readme.md"},
                   "cwd": "/x", "session_id": "s1"}
        code, msg = verify.process(payload, runner=FAIL_RUNNER)
        self.assertEqual((code, msg), (0, ""))  # never lints a .md, even if runner FAILs

    def test_source_no_command_silent(self):
        with tempfile.TemporaryDirectory() as proj:
            payload = {"tool_input": {"file_path": str(Path(proj) / "a.py")},
                       "cwd": proj, "session_id": "s1"}
            code, msg = verify.process(payload, runner=FAIL_RUNNER)
            self.assertEqual(code, 0)  # no resolvable linter -> silent (UNKNOWN)

    def test_source_fail_surfaces(self):
        with tempfile.TemporaryDirectory() as proj:
            _helpers.write_config_toml(proj, '[verify]\nlint_file = "echo {file}"\n')
            payload = {"tool_input": {"file_path": str(Path(proj) / "a.py")},
                       "cwd": proj, "session_id": "s1"}
            code, msg = verify.process(
                payload, runner=lambda *a, **k: (common.FAIL, "a.py:1 SyntaxError: bad"))
            self.assertEqual(code, 2)
            self.assertIn("SyntaxError", msg)
            self.assertLessEqual(len(msg.splitlines()), 21)  # header + <=20

    def test_source_pass_silent(self):
        with tempfile.TemporaryDirectory() as proj:
            _helpers.write_config_toml(proj, '[verify]\nlint_file = "echo {file}"\n')
            payload = {"tool_input": {"file_path": str(Path(proj) / "a.py")},
                       "cwd": proj, "session_id": "s1"}
            code, msg = verify.process(payload, runner=PASS_RUNNER)
            self.assertEqual((code, msg), (0, ""))

    def test_marks_dirty(self):
        with tempfile.TemporaryDirectory() as proj:
            f = str(Path(proj) / "a.py")
            verify.process({"tool_input": {"file_path": f}, "cwd": proj,
                            "session_id": "s1"}, runner=PASS_RUNNER)
            key = common.repo_key(proj)
            dirty = (Path(self.config_dir.name) / "state" / key
                     / "sessions" / "s1" / common.STATE_DIRTY)
            self.assertTrue(dirty.exists())


SECRET = "sk-ant-" + "api03-" + "A" * 32  # 合成(実鍵ではない)— test_ua_audit と同型


class TestIsConfigSurface(_helpers.ConfigDirTestCase):
    def _p(self, rel):
        return str(Path(common.config_dir()) / rel)

    def test_root_config_files(self):
        cdir = common.config_dir()
        for name in ("settings.json", "settings.local.json", "CLAUDE.md", ".mcp.json"):
            self.assertTrue(verify._is_config_surface(self._p(name), cdir))

    def test_scan_dirs(self):
        cdir = common.config_dir()
        for rel in ("hooks/x.py", "agents/y.md", "skills/z/SKILL.md", "workflows/w.js",
                    "commands/c.md", "rules/python.md"):
            self.assertTrue(verify._is_config_surface(self._p(rel), cdir))

    def test_state_dir_is_not_surface(self):
        cdir = common.config_dir()
        self.assertFalse(verify._is_config_surface(self._p("state/foo.json"), cdir))

    def test_root_random_file_is_not_surface(self):
        cdir = common.config_dir()
        self.assertFalse(verify._is_config_surface(self._p("notes.txt"), cdir))

    def test_outside_config_dir_is_not_surface(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertFalse(
                verify._is_config_surface(str(Path(other) / "app.py"), common.config_dir()))


class TestAutoAudit(_helpers.ConfigDirTestCase):
    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUDIT=None)

    def _cfg(self, rel, body):
        p = Path(common.config_dir()) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return str(p)

    def _edit(self, file, runner=PASS_RUNNER):
        return verify.process({"tool_input": {"file_path": file},
                               "cwd": str(common.config_dir()), "session_id": "s1"},
                              runner=runner)

    def test_clean_config_edit_silent(self):
        f = self._cfg("hooks/clean.py", "x = 1\n")
        self.assertEqual(self._edit(f), (0, ""))  # 監査 PASS → 無音

    def test_planted_secret_surfaces(self):
        f = self._cfg("hooks/leak.py", f'KEY = "{SECRET}"\n')
        code, msg = self._edit(f)
        self.assertEqual(code, 2)
        self.assertIn("ハードコードされた機密", msg)
        self.assertIn("leak.py", msg)  # provenance: 引き金ファイル

    def test_non_config_edit_is_not_audited(self):
        # config_dir 外の編集は、機密が仕込まれていても監査しない
        with tempfile.TemporaryDirectory() as proj:
            f = str(Path(proj) / "app.py")
            Path(f).write_text(f'KEY = "{SECRET}"\n')
            code, msg = verify.process({"tool_input": {"file_path": f},
                                        "cwd": proj, "session_id": "s1"}, runner=PASS_RUNNER)
            self.assertEqual((code, msg), (0, ""))

    def test_ua_audit_0_disables(self):
        _helpers.set_env(self, UA_AUDIT="0")
        f = self._cfg("hooks/leak.py", f'KEY = "{SECRET}"\n')
        self.assertEqual(self._edit(f), (0, ""))  # kill switch で無音

    def test_broken_settings_surfaces_unknown(self):
        self._cfg("settings.json", "{not json")
        code, msg = self._edit(str(Path(common.config_dir()) / "settings.json"))
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", msg)  # 検査不能を黙って PASS にしない


class TestHiddenUnicodeScan(_helpers.ConfigDirTestCase):
    """編集時の不可視/双方向制御文字スキャン(E): 検出=exit2 / 絵文字は誤検知しない / flag で無効。"""
    ZWSP = chr(0x200b)
    TAG = chr(0xe0001)
    EMOJI = chr(0x26a0) + chr(0xfe0f)  # ⚠️(VS16=FE0F は除外対象)

    def _edit(self, body, suffix=".py"):
        with tempfile.TemporaryDirectory() as proj:
            f = Path(proj) / ("a" + suffix)
            f.write_text(body, encoding="utf-8")
            return verify.process({"tool_input": {"file_path": str(f)},
                                   "cwd": proj, "session_id": "s1"}, runner=PASS_RUNNER)

    def test_zero_width_surfaces(self):
        code, msg = self._edit(f"x = 1  # ok{self.ZWSP} hidden\n")
        self.assertEqual(code, 2)
        self.assertIn("U+200B", msg)

    def test_tag_block_surfaces(self):
        code, msg = self._edit(f"y = 2  # {self.TAG}\n")
        self.assertEqual(code, 2)
        self.assertIn("U+E0001", msg)

    def test_clean_file_silent(self):
        self.assertEqual(self._edit("x = 1\n"), (0, ""))

    def test_emoji_fe0f_not_flagged(self):
        self.assertEqual(self._edit(f"MSG = '{self.EMOJI} warn'\n"), (0, ""))

    def test_non_source_md_also_scanned(self):
        code, _ = self._edit(f"see{self.ZWSP} this\n", suffix=".md")
        self.assertEqual(code, 2)

    def test_disabled_flag_silent(self):
        _helpers.set_env(self, UA_UNICODE_GUARD="0")
        self.assertEqual(self._edit(f"x = 1{self.ZWSP}\n"), (0, ""))


if __name__ == "__main__":
    unittest.main()
