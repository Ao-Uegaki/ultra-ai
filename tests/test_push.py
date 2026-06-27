"""Unit tests for claude-home/hooks/push.py (run: python3 -m unittest)."""
import os
import subprocess
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import push  # noqa: E402


class TestDoBlockReason(unittest.TestCase):
    """do の hard ゲート(純関数): 未コミット / main 直 / 未検証 を止め、満たせば通す。"""

    def test_dirty_blocked(self):
        r = push.do_block_reason("feature/x", clean=False, pass_ok=True,
                                 allow_main=False, allow_unverified=False)
        self.assertIn("未コミット", r)

    def test_main_target_blocked(self):
        r = push.do_block_reason("main", True, True, allow_main=False, allow_unverified=False)
        self.assertIn("main", r)

    def test_main_allowed_with_flag(self):
        self.assertIsNone(push.do_block_reason("main", True, True, True, False))

    def test_unverified_blocked(self):
        r = push.do_block_reason("feature/x", True, pass_ok=False,
                                 allow_main=False, allow_unverified=False)
        self.assertIn("検証", r)

    def test_unverified_allowed_with_flag(self):
        self.assertIsNone(push.do_block_reason("feature/x", True, False, False, True))

    def test_clean_feature_pass_passes(self):
        self.assertIsNone(push.do_block_reason("feature/x", True, True, False, False))


class TestRepoHelpers(_helpers.ConfigDirTestCase):
    def setUp(self):
        super().setUp()
        self.repo = _helpers.make_git_repo(self)
        self.addCleanup(os.chdir, os.getcwd())
        r = self.repo.name
        (Path(r) / "a.txt").write_text("v1")
        subprocess.run(["git", "add", "-f", "a.txt"], cwd=r)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=r)
        os.chdir(r)

    def test_is_clean_tracks_worktree(self):
        self.assertTrue(push.is_clean(os.getcwd()))
        (Path(self.repo.name) / "a.txt").write_text("v2")
        self.assertFalse(push.is_clean(os.getcwd()))

    def test_license_present(self):
        self.assertFalse(push.license_present(os.getcwd()))
        (Path(self.repo.name) / "LICENSE").write_text("MIT")
        self.assertTrue(push.license_present(os.getcwd()))

    def test_exposed_secrets_flags_env(self):
        self.assertEqual(push.exposed_secrets(os.getcwd()), [])
        (Path(self.repo.name) / ".env").write_text("X=1")
        subprocess.run(["git", "add", "-f", ".env"], cwd=self.repo.name)
        subprocess.run(["git", "commit", "-q", "-m", "env"], cwd=self.repo.name)
        self.assertIn(".env", push.exposed_secrets(os.getcwd()))

    def test_pass_gate_matches_verified_head(self):
        cwd = os.getcwd()
        self.assertFalse(push.pass_gate_ok(cwd))  # verified-head 未記録
        common.write_json_atomic(
            common.shared_state_dir(cwd) / common.STATE_VERIFIED_HEAD,
            {"head": common.git_head(cwd)})
        self.assertTrue(push.pass_gate_ok(cwd))

    def test_pass_gate_stale_head_fails(self):
        cwd = os.getcwd()
        common.write_json_atomic(
            common.shared_state_dir(cwd) / common.STATE_VERIFIED_HEAD,
            {"head": "deadbeef" * 5})  # 別 HEAD
        self.assertFalse(push.pass_gate_ok(cwd))

    def test_author_emails_lists_committers(self):
        self.assertIn("t@t", push.author_emails(os.getcwd(), None))


if __name__ == "__main__":
    unittest.main()
