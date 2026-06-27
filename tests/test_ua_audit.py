"""Unit tests for claude-home/hooks/ua_audit.py (run: python3 -m unittest).

合成 config dir に問題を仕込み、3状態(問題あり→FAIL / 健全→PASS / 壊れ→UNKNOWN)を検証する。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import ua_audit  # noqa: E402


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _clean_base(root: Path) -> None:
    """critical/high の無い、最小の健全な設定面。"""
    _write(root, "settings.json", json.dumps({
        "permissions": {"deny": ["Read(**/.env)"]},
        "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": 'python3 "$CLAUDE_CONFIG_DIR/hooks/gate.py"'}]}]},
    }))
    _write(root, "CLAUDE.md", "# rules\nbe good\n")
    _write(root, "hooks/safe.py", "import subprocess\nsubprocess.run(['ls'])\n")


class TestClean(unittest.TestCase):
    """健全な設定面は overall=PASS かつ findings が空、を守る。"""

    def test_clean_surface_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _clean_base(root)
            res = ua_audit.audit(root)
            self.assertEqual(res["overall"], common.PASS, res["findings"])
            self.assertEqual(res["findings"], [])


class TestFails(unittest.TestCase):
    """危険な設定面(過剰権限・ハードコード機密・注入ベクトル・隠し unicode 等)は overall=FAIL になる、を守る。"""

    def _audit_with(self, mutate) -> dict:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _clean_base(root)
            mutate(root)
            return ua_audit.audit(root)

    def _msgs(self, res):
        return " | ".join(m for _, _, m in res["findings"])

    def test_over_broad_permission(self):
        def m(root):
            _write(root, "settings.json", json.dumps({"permissions": {"allow": ["Bash(*)"]}}))
        res = self._audit_with(m)
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("過剰に広い権限", self._msgs(res))

    def test_disable_all_hooks(self):
        def m(root):
            _write(root, "settings.json", json.dumps({"disableAllHooks": True}))
        res = self._audit_with(m)
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("disableAllHooks", self._msgs(res))

    def test_hardcoded_secret(self):
        token = "sk-ant-" + "api03-" + "A" * 32  # 合成(実鍵ではない)
        res = self._audit_with(lambda root: _write(root, "hooks/leak.py", f"KEY = '{token}'\n"))
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("ハードコードされた機密", self._msgs(res))

    def test_hook_injection_command(self):
        def m(root):
            _write(root, "settings.json", json.dumps({"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": 'echo $(cat /etc/passwd) | bash'}]}]}}))
        res = self._audit_with(m)
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("注入/実行ベクトル", self._msgs(res))

    def test_code_exec_in_hook(self):
        res = self._audit_with(
            lambda root: _write(root, "hooks/bad.py", "import os\nos.system('rm -rf /tmp/x')\n"))
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("os.system", self._msgs(res))

    def test_secret_file_present(self):
        res = self._audit_with(lambda root: _write(root, "hooks/.env", "SECRET=1\n"))
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("機密ファイル", self._msgs(res))

    def test_hidden_unicode(self):
        res = self._audit_with(
            lambda root: _write(root, "skills/x/SKILL.md", "do this\u200b secretly\n"))
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("不可視", self._msgs(res))

    def test_mcp_auto_install(self):
        def m(root):
            _write(root, ".mcp.json", json.dumps({"mcpServers": {
                "x": {"command": "npx", "args": ["-y", "@foo/bar"]}}}))
        res = self._audit_with(m)
        self.assertEqual(res["overall"], common.FAIL)
        self.assertIn("npx -y", self._msgs(res))


class TestUnknown(unittest.TestCase):
    """解析できない settings は PASS でなく UNKNOWN にする(未検証を緑にしない)、を守る。"""

    def test_unparseable_settings_is_unknown_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "settings.json", "{ this is not json")
            res = ua_audit.audit(root)
            self.assertEqual(res["overall"], common.UNKNOWN)
            self.assertEqual(res["findings"], [])
            self.assertTrue(any("JSON 解析に失敗" in u for u in res["unknown"]))


class TestAgentBaseline(unittest.TestCase):
    """全 subagent 定義に防御 baseline が組み込まれているかの drift(ずれ)検出。"""

    def test_agent_without_baseline_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "agents/foo.md", "---\nname: foo\nmodel: haiku\n---\n\nあなたは foo です。\n")
            res = ua_audit.audit(root)
            self.assertEqual(res["overall"], common.FAIL)
            self.assertIn("防御 baseline", " | ".join(m for _, _, m in res["findings"]))

    def test_agent_with_baseline_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "agents/foo.md",
                   "---\nname: foo\nmodel: haiku\n---\n\n"
                   + common.AGENT_DEFENSE_BASELINE + "\n\nあなたは foo です。\n")
            res = ua_audit.audit(root)
            self.assertEqual(res["overall"], common.PASS, res["findings"])

    def test_non_agent_md_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "agents/README.md", "# agents\nガイド本文\n")  # frontmatter name: なし
            res = ua_audit.audit(root)
            self.assertEqual(res["overall"], common.PASS, res["findings"])


class TestRealAgentsHaveBaseline(unittest.TestCase):
    """同梱の reviewer/deep-solver/learner に baseline が実際に入っているか(drift 防止)。"""

    def test_shipped_agents_have_baseline(self):
        root = Path(__file__).resolve().parent.parent / "claude-home"
        self.assertEqual(ua_audit.check_agent_baseline(root), [])


if __name__ == "__main__":
    unittest.main()
