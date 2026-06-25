"""Unit tests for claude-home/hooks/checkpoint.py (run: python3 -m unittest)."""
import contextlib
import io
import os
import subprocess
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import checkpoint  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
