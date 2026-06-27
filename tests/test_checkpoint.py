"""Unit tests for claude-home/hooks/checkpoint.py (run: python3 -m unittest)."""
import contextlib
import io
import os
import subprocess
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import checkpoint  # noqa: E402
import common  # noqa: E402


class TestCommitMessage(unittest.TestCase):
    def test_uses_basenames(self):
        self.assertEqual(
            checkpoint.commit_message(["src/a/b.py", "c.txt"]),
            "checkpoint: b.py, c.txt")

    def test_truncates_after_five(self):
        files = [f"f{i}.py" for i in range(7)]
        msg = checkpoint.commit_message(files)
        self.assertTrue(msg.startswith("checkpoint: "))
        self.assertIn("(+2 more)", msg)
        self.assertEqual(msg.count(","), 4)  # first 5 names -> 4 commas before "(+..."

    def test_single_file(self):
        self.assertEqual(checkpoint.commit_message(["x.py"]), "checkpoint: x.py")


class TestLooksSecret(unittest.TestCase):
    def test_detects_secrets(self):
        for p in (".env", ".env.local", "a/.env.production", "x/server.key",
                  "id_rsa", "deploy.pem", ".credentials.json"):
            self.assertTrue(checkpoint._looks_secret(p), p)

    def test_allows_non_secrets(self):
        for p in (".env.example", "src/main.py", "config.ts", "README.md",
                  "key_handler.ts", "secrets_manager.ts"):
            self.assertFalse(checkpoint._looks_secret(p), p)


class TestMainIntegration(unittest.TestCase):
    def setUp(self):
        self.repo = _helpers.make_git_repo(self)
        self.addCleanup(os.chdir, os.getcwd())  # restore cwd before the repo is removed
        _helpers.set_env(self, UA_CHECKPOINT_GATE="0")  # commit 機構のみ検証(PASS ゲートは別クラス)

    def _add_commit(self, name, content):
        r = self.repo.name
        (Path(r) / name).write_text(content)
        subprocess.run(["git", "add", "-f", name], cwd=r)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=r)

    def _main(self):
        os.chdir(self.repo.name)
        with contextlib.redirect_stdout(io.StringIO()):
            return checkpoint.main()

    def _log(self):
        return subprocess.run(["git", "log", "--oneline"], cwd=self.repo.name,
                              capture_output=True, text=True).stdout

    def test_commits_tracked_change(self):
        self._add_commit("a.txt", "v1")
        (Path(self.repo.name) / "a.txt").write_text("v2")
        self.assertEqual(self._main(), 0)
        self.assertIn("checkpoint", self._log())

    def test_no_tracked_changes(self):
        self._add_commit("a.txt", "v1")
        self.assertEqual(self._main(), 0)
        self.assertNotIn("checkpoint", self._log())

    def test_refuses_secret(self):
        self._add_commit(".env", "SECRET=1")
        (Path(self.repo.name) / ".env").write_text("SECRET=2")
        self.assertEqual(self._main(), 1)
        self.assertNotIn("checkpoint", self._log())

    def test_handles_filename_with_space(self):
        self._add_commit("my file.txt", "v1")
        (Path(self.repo.name) / "my file.txt").write_text("v2")
        self.assertEqual(self._main(), 0)
        log = self._log()
        self.assertIn("checkpoint", log)
        self.assertIn("my file.txt", log)  # 1件として扱われ basename が壊れない


class TestPassGate(_helpers.ConfigDirTestCase):
    """hard PASS ゲート(UA_CHECKPOINT_GATE 既定 ON): FAIL は拒否・未検証は拒否・PASS で commit。"""

    def setUp(self):
        super().setUp()  # isolated CLAUDE_CONFIG_DIR
        self.repo = _helpers.make_git_repo(self)
        self.addCleanup(os.chdir, os.getcwd())
        r = self.repo.name
        (Path(r) / "a.txt").write_text("v1")
        subprocess.run(["git", "add", "-f", "a.txt"], cwd=r)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=r)
        (Path(r) / "a.txt").write_text("v2")  # 追跡済みの変更
        os.chdir(r)

    def _seed(self, result):
        # 現在の working-tree(add -u 前)の署名で verification を仕込む(gate と同じ鍵)。
        cwd = os.getcwd()
        sig = common.verification_sig(common.git_head(cwd) or "",
                                      common.git_status_porcelain(cwd))
        sdir = common.session_state_dir(cwd, "s1")
        common.write_json_atomic(sdir / common.STATE_VERIFICATION,
                                 {"signature": sig, "result": result})

    def _main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return checkpoint.main(list(args))

    def _log(self):
        return subprocess.run(["git", "log", "--oneline"], cwd=os.getcwd(),
                              capture_output=True, text=True).stdout

    def test_pass_commits(self):
        self._seed(common.PASS)
        self.assertEqual(self._main(), 0)
        self.assertIn("checkpoint", self._log())

    def test_pass_records_verified_head(self):
        self._seed(common.PASS)
        self._main()
        vh = common.read_json(common.shared_state_dir(os.getcwd()) / common.STATE_VERIFIED_HEAD)
        self.assertEqual(vh.get("head"), common.git_head(os.getcwd()))

    def test_fail_refused(self):
        self._seed(common.FAIL)
        self.assertEqual(self._main(), 1)
        self.assertNotIn("checkpoint", self._log())

    def test_fail_allow_override(self):
        self._seed(common.FAIL)
        self.assertEqual(self._main("--allow-fail"), 0)
        self.assertIn("checkpoint", self._log())

    def test_unverified_refused(self):
        # seed しない=この状態の記録なし=未検証。
        self.assertEqual(self._main(), 1)
        self.assertNotIn("checkpoint", self._log())

    def test_unverified_allow_override(self):
        self.assertEqual(self._main("--allow-unverified"), 0)
        self.assertIn("checkpoint", self._log())

    def test_gate_disabled_commits_unverified(self):
        _helpers.set_env(self, UA_CHECKPOINT_GATE="0")
        self.assertEqual(self._main(), 0)
        self.assertIn("checkpoint", self._log())

    def test_message_override_sets_subject(self):
        self._seed(common.PASS)
        self._main("--message", "feat: add a")
        subj = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=os.getcwd(),
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(subj, "feat: add a")

    def test_coauthor_trailer_in_body(self):
        self._seed(common.PASS)
        self._main()
        body = subprocess.run(["git", "log", "-1", "--format=%b"], cwd=os.getcwd(),
                              capture_output=True, text=True).stdout
        self.assertIn("Co-Authored-By: Claude", body)


if __name__ == "__main__":
    unittest.main()
