"""Shared helpers for the ultra-ai hook test-suite (stdlib unittest only).

Importing this module puts `claude-home/hooks` on sys.path, so test modules can
`import common`, `import gate`, … without repeating the path bootstrap. It also
provides the fixtures duplicated across the suite:

  - init_git_repo(path):  turn a directory into a minimal, deterministic git repo
  - stdin_payload(payload): context manager feeding hook JSON on sys.stdin
  - ConfigDirTestCase:    a TestCase with an isolated CLAUDE_CONFIG_DIR state root
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make the hook modules importable by name (common, gate, verify, …).
HOOKS = Path(__file__).resolve().parent.parent / "claude-home" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

# Likewise the bench modules (ab_run, ab_report, compare, transcript_metrics), so the
# bench test files don't each repeat the path bootstrap (single source, like HOOKS above).
BENCH = Path(__file__).resolve().parent.parent / "bench"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import common  # noqa: E402  (CONFIG_FILE 等の単一ソース)


def init_git_repo(path) -> None:
    """`git init` `path` with a deterministic identity, quietly."""
    subprocess.run(["git", "init", "-q"], cwd=path)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path)


def make_git_repo(test):
    """Temp git repo registered for auto-cleanup. Returns the TemporaryDirectory
    (use `.name` for the path), so callers need no tearDown of their own."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    init_git_repo(tmp.name)
    return tmp


def write_config_toml(root, body) -> None:
    """Write `body` to the project's .ultra-ai.toml (single source: common.CONFIG_FILE)."""
    (Path(root) / common.CONFIG_FILE).write_text(body)


def _restore_env(key, old) -> None:
    if old is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old


def set_env(test, **kv) -> None:
    """Set env vars for `test` (value None unsets), each auto-restored to its pre-call
    value via addCleanup. Replaces the setUp-pop / tearDown-pop dance: whatever the test
    later does to the var, it is restored afterwards."""
    for k, v in kv.items():
        test.addCleanup(_restore_env, k, os.environ.get(k))
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def stub_git_none(test) -> None:
    """Stub common.git_toplevel / git_branch to return None for `test` (restored via
    addCleanup). For tests that exercise hooks against *non-git* temp dirs, the real
    functions already return None (git fails on a bare dir), so this is behavior-identical
    — it only removes the ~8 `git rev-parse --show-toplevel` subprocess spawns each
    resume_context.build() makes via repo_key/project_root (the suite's main speed hotspot).
    Tests that need a specific branch (e.g. branch-mismatch) override git_branch locally and
    still compose, since their own restore lands on this stub and addCleanup restores the real."""
    for name in ("git_toplevel", "git_branch"):
        test.addCleanup(setattr, common, name, getattr(common, name))
        setattr(common, name, lambda c: None)


@contextlib.contextmanager
def stdin_payload(payload):
    """Feed a hook payload (dict → JSON, or a raw str) on sys.stdin for the block."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    old = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = old


class ConfigDirTestCase(unittest.TestCase):
    """Base TestCase that points CLAUDE_CONFIG_DIR at a throwaway temp directory
    for the duration of each test (hook state lands under self.config_dir)."""

    def setUp(self):
        super().setUp()
        self.config_dir = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_CONFIG_DIR"] = self.config_dir.name

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.config_dir.cleanup()
        super().tearDown()
