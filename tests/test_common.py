"""Unit tests for claude-home/hooks/common.py (run: python3 -m unittest)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
from _helpers import stdin_payload  # noqa: E402
import common  # noqa: E402


class TestDistill(unittest.TestCase):
    def test_short_passthrough(self):
        self.assertEqual(common.condense("a\nb\nc"), "a\nb\nc")

    def test_caps_lines(self):
        out = common.condense("\n".join(str(i) for i in range(100)), limit=20)
        self.assertLessEqual(len(out.splitlines()), 20)

    def test_prefers_error_lines(self):
        text = "\n".join(["noise"] * 50 + ["src/a.ts:1 error TS2345: bad"])
        out = common.condense(text, limit=20)
        self.assertIn("error TS2345", out)

    def test_caps_even_with_many_errors(self):
        text = "\n".join(f"error {i}" for i in range(100))
        out = common.condense(text, limit=20)
        self.assertLessEqual(len(out.splitlines()), 20)
        self.assertIn("more error lines", out)


class TestSourceFile(unittest.TestCase):
    def test_true(self):
        self.assertTrue(common.is_source_file("/x/y.ts"))
        self.assertTrue(common.is_source_file("/x/y.py"))

    def test_false(self):
        self.assertFalse(common.is_source_file("/x/y.md"))
        self.assertFalse(common.is_source_file(None))


class TestRepoKey(unittest.TestCase):
    def test_stable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(common.repo_key(d), common.repo_key(d))

    def test_differs_by_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertNotEqual(common.repo_key(a), common.repo_key(b))


class TestEnvInt(unittest.TestCase):
    def setUp(self):
        _helpers.set_env(self, UA_FOO=None)

    def test_unset_returns_default(self):
        self.assertEqual(common.env_int("FOO", 7), 7)

    def test_valid_value(self):
        _helpers.set_env(self, UA_FOO="42")
        self.assertEqual(common.env_int("FOO", 7), 42)

    def test_invalid_falls_back(self):
        _helpers.set_env(self, UA_FOO="abc")
        self.assertEqual(common.env_int("FOO", 7), 7)

    def test_negative_falls_back(self):
        _helpers.set_env(self, UA_FOO="-5")
        self.assertEqual(common.env_int("FOO", 7), 7)


class TestContextWindowForModel(unittest.TestCase):
    """同一 base モデルの 200k / 1M 2モードを id / display_name から判別する純関数。"""

    def test_id_with_1m_suffix(self):
        self.assertEqual(common.context_window_for_model("claude-opus-4-8[1m]", None), 1_000_000)

    def test_display_name_1m_context(self):
        self.assertEqual(common.context_window_for_model(None, "Opus 4.8 (1M context)"), 1_000_000)

    def test_base_id_defaults_200k(self):
        self.assertEqual(common.context_window_for_model("claude-opus-4-8", "Opus 4.8"), 200_000)

    def test_other_model_defaults_200k(self):
        self.assertEqual(common.context_window_for_model("claude-sonnet-4-6", "Sonnet 4.6"), 200_000)

    def test_none_is_safe_default(self):
        self.assertEqual(common.context_window_for_model(None, None), 200_000)


class TestProjectRoot(unittest.TestCase):
    def test_walks_up_to_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "pyproject.toml").write_text("\n")
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            # not a git repo -> should walk up to the manifest dir
            self.assertEqual(Path(common.project_root(str(nested))), root)


class TestHookInput(unittest.TestCase):
    def _with_stdin(self, text):
        with stdin_payload(text):
            return common.read_hook_input()

    def test_parses_and_extracts_file(self):
        payload = {"tool_name": "Edit",
                   "tool_input": {"file_path": "/a/b.py"}, "cwd": "/a"}
        got = self._with_stdin(json.dumps(payload))
        self.assertEqual(common.edited_file(got), "/a/b.py")
        self.assertEqual(common.hook_cwd(got), "/a")

    def test_notebook_path(self):
        got = {"tool_input": {"notebook_path": "/a/n.ipynb"}}
        self.assertEqual(common.edited_file(got), "/a/n.ipynb")

    def test_empty(self):
        self.assertEqual(self._with_stdin(""), {})

    def test_garbage(self):
        self.assertEqual(self._with_stdin("not json"), {})


class TestRunCmd(unittest.TestCase):
    def test_pass(self):
        state, _ = common.run_cmd("true", os.getcwd())
        self.assertEqual(state, common.PASS)

    def test_fail(self):
        state, _ = common.run_cmd("false", os.getcwd())
        self.assertEqual(state, common.FAIL)

    def test_unknown_empty(self):
        state, _ = common.run_cmd("   ", os.getcwd())
        self.assertEqual(state, common.UNKNOWN)

    def test_timeout_is_unknown(self):
        state, out = common.run_cmd("sleep 5", os.getcwd(), timeout=1)
        self.assertEqual(state, common.UNKNOWN)
        self.assertIn("timed out", out)


class TestReadWriteJson(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            common.write_json_atomic(p, {"a": 1, "b": [2, 3]})
            self.assertEqual(common.read_json(p), {"a": 1, "b": [2, 3]})

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(common.read_json(Path(d) / "nope.json"), {})

    def test_corrupt_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json")
            self.assertEqual(common.read_json(p), {})

    def test_write_is_atomic_leaving_no_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            common.write_json_atomic(Path(d) / "s.json", {"x": 1})
            # os.replace cleans up the temp file -> only the final file remains
            self.assertEqual([f.name for f in Path(d).iterdir()], ["s.json"])

    def test_write_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            common.write_json_atomic(p, {"v": 1})
            common.write_json_atomic(p, {"v": 2})
            self.assertEqual(common.read_json(p), {"v": 2})


class TestRunGit(unittest.TestCase):
    def test_success(self):
        cp = common.run_git(["git", "--version"], os.getcwd())
        self.assertIsNotNone(cp)
        self.assertEqual(cp.returncode, 0)
        self.assertIn("git version", cp.stdout)

    def test_nonzero_in_non_repo(self):
        with tempfile.TemporaryDirectory() as d:
            cp = common.run_git(["git", "rev-parse", "HEAD"], d)
            self.assertIsNotNone(cp)
            self.assertNotEqual(cp.returncode, 0)

    def test_missing_binary_is_none(self):
        self.assertIsNone(common.run_git(["definitely-not-a-real-binary-xyz"], os.getcwd()))


class TestSecretPatterns(unittest.TestCase):
    """増補した高精度 secret パターン(E)。合成鍵で検出・pass 無し DSN は誤検知しない。"""

    def _labels(self, s):
        return [lab for pat, lab in common.SECRET_CONTENT_PATTERNS if pat.search(s)]

    def test_stripe_live_detected(self):
        self.assertIn("Stripe 本番 secret/restricted key",
                      self._labels("k = 'sk_live_" + "A" * 24 + "'"))

    def test_mongodb_with_auth_detected(self):
        self.assertIn("MongoDB 接続文字列(認証埋め込み)",
                      self._labels("mongodb+srv://u:p@host/db"))

    def test_postgres_without_password_not_flagged(self):
        self.assertNotIn("PostgreSQL 接続文字列(認証埋め込み)",
                         self._labels("postgres://localhost:5432/db"))


class TestHiddenUnicodeChars(unittest.TestCase):
    """HIDDEN_UNICODE の増補(E): タグブロック等は拾い、絵文字 FE0F は誤検知しない。"""

    def test_tag_block_matched(self):
        self.assertTrue(common.HIDDEN_UNICODE.search("a" + chr(0xe0001) + "b"))

    def test_zero_width_matched(self):
        self.assertTrue(common.HIDDEN_UNICODE.search("a" + chr(0x200b) + "b"))

    def test_emoji_vs16_not_matched(self):
        self.assertIsNone(common.HIDDEN_UNICODE.search("warn " + chr(0x26a0) + chr(0xfe0f)))


if __name__ == "__main__":
    unittest.main()
