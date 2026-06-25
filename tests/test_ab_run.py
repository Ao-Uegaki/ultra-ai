"""Unit tests for bench/ab_run.py (run: python3 -m unittest).

純部分(task 読込・sandbox 準備・oracle 実行・transcript 特定・run_one)を検証する。
`claude -p`(invoke_claude)は差し替えて実 API を呼ばない。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (hooks + bench を sys.path に載せる)

import ab_run  # noqa: E402


def _make_task(base: Path, *, correct: bool) -> str:
    td = base / "task"
    (td / "repo").mkdir(parents=True)
    (td / "oracle").mkdir(parents=True)
    body = "def f():\n    return 42\n" if correct else "def f():\n    return 0\n"
    (td / "repo" / "mod.py").write_text(body)
    (td / "oracle" / "test_oracle.py").write_text(
        "import unittest\nfrom mod import f\n"
        "class T(unittest.TestCase):\n"
        "    def test(self):\n        self.assertEqual(f(), 42)\n")
    (td / "meta.json").write_text(json.dumps(
        {"id": "demo", "lang": "python",
         "test_cmd": ["python3", "-m", "unittest", "-q", "test_oracle"]}))
    (td / "prompt.md").write_text("make f return 42")
    return str(td)


class TestPurePieces(unittest.TestCase):
    def test_load_task(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            task = ab_run.load_task(td)
            self.assertEqual(task["id"], "demo")
            self.assertIn("return 42", task["prompt"])

    def test_prepare_sandbox_copies_repo_only(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            sb = ab_run.prepare_sandbox(td, str(Path(d) / "sbx"))
            self.assertTrue((Path(sb) / "mod.py").exists())
            self.assertFalse((Path(sb) / "test_oracle.py").exists())  # oracle はまだ入れない

    def test_run_oracle_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            sb = ab_run.prepare_sandbox(td, str(Path(d) / "sbx"))
            meta = ab_run.load_task(td)["meta"]
            self.assertTrue(ab_run.run_oracle(meta, sb, td))     # mod.py が正しい → pass
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=False)
            sb = ab_run.prepare_sandbox(td, str(Path(d) / "sbx"))
            meta = ab_run.load_task(td)["meta"]
            self.assertFalse(ab_run.run_oracle(meta, sb, td))    # mod.py が誤り → fail

    def test_find_transcript_glob(self):
        with tempfile.TemporaryDirectory() as cfg:
            tp = Path(cfg) / "projects" / "some-proj" / "sess-xyz.jsonl"
            tp.parent.mkdir(parents=True)
            tp.write_text("{}\n")
            self.assertEqual(ab_run.find_transcript(cfg, "sess-xyz"), str(tp))
            self.assertIsNone(ab_run.find_transcript(cfg, "nope"))
            self.assertIsNone(ab_run.find_transcript(cfg, None))


class TestBuildCmd(unittest.TestCase):
    def test_scoped_allowedtools_not_skip_permissions(self):
        cmd = ab_run._build_cmd("solve it", model="opus")
        self.assertNotIn("--dangerously-skip-permissions", cmd)  # ungated 起動はしない
        self.assertIn("--allowedTools", cmd)
        allowed = cmd[cmd.index("--allowedTools") + 1]
        self.assertIn("Edit", allowed)
        self.assertIn("Bash(python3:*)", allowed)
        self.assertNotIn("Bash(rm", allowed)  # 破壊的コマンドは許可しない
        self.assertEqual(cmd[:3], ["claude", "-p", "solve it"])  # prompt は -p の値
        self.assertIn("opus", cmd)
        self.assertNotIn("--effort", cmd)  # 既定では effort フラグを付けない

    def test_effort_flag_appended_when_given(self):
        cmd = ab_run._build_cmd("solve it", model="opus", effort="xhigh")
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "xhigh")


class TestRunOne(unittest.TestCase):
    def test_run_one_with_fake_invoke(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            cfg = Path(d) / "cfg"

            def fake_invoke(prompt, sandbox, config_dir, env_overrides=None, *,
                            model=None, effort=None, timeout=900):
                sid = "sess-fake-1"
                tp = Path(config_dir) / "projects" / "p" / f"{sid}.jsonl"
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_text(json.dumps({"type": "assistant", "isSidechain": False,
                                          "message": {"model": "claude-opus-4-8",
                                                      "usage": {"input_tokens": 1}}}) + "\n")
                return {"session_id": sid, "total_cost_usd": 0.01}

            arm_cfg = {"config_dir": str(cfg), "env": {}}
            row = ab_run.run_one(td, "treatment", arm_cfg, 0, str(Path(d) / "sbx"),
                                 invoke=fake_invoke)
            self.assertTrue(row["success"])           # 正しい repo → oracle pass
            self.assertEqual(row["arm"], "treatment")
            self.assertEqual(row["cost_usd"], 0.01)
            self.assertTrue(row["transcript"].endswith("sess-fake-1.jsonl"))


class TestCleanup(unittest.TestCase):
    """run_one の自動掃除(sandbox/transcript 削除)と sandbox 名ガードを検証。"""

    def _fake_invoke(self, proj_name: str):
        def fake_invoke(prompt, sandbox, config_dir, env_overrides=None, *,
                        model=None, effort=None, timeout=900):
            sid = "sess-clean-1"
            tp = Path(config_dir) / "projects" / proj_name / f"{sid}.jsonl"
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text("{}\n")
            return {"session_id": sid, "total_cost_usd": 0.02}
        return fake_invoke

    def test_cleanup_removes_sandbox_and_sandbox_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            cfg = Path(d) / "cfg"
            proj = "-Users-x-bench--sandboxes-demo-treatment-0"   # 名前に sandbox を含む
            row = ab_run.run_one(td, "treatment", {"config_dir": str(cfg), "env": {}}, 0,
                                 str(Path(d) / "sbx"),
                                 invoke=self._fake_invoke(proj),
                                 extract=lambda tp: {"turns_main": 1})
            self.assertIsInstance(row["metrics"], dict)            # 自己完結 metrics を格納
            self.assertEqual(row["metrics"]["turns_main"], 1)
            self.assertFalse(Path(row["sandbox"]).exists())        # sandbox は掃除済み
            self.assertFalse((cfg / "projects" / proj).exists())   # transcript dir も掃除済み

    def test_keep_artifacts_when_cleanup_false(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            cfg = Path(d) / "cfg"
            proj = "-Users-x-bench--sandboxes-demo-treatment-1"
            row = ab_run.run_one(td, "treatment", {"config_dir": str(cfg), "env": {}}, 1,
                                 str(Path(d) / "sbx2"),
                                 invoke=self._fake_invoke(proj), cleanup=False,
                                 extract=lambda tp: {"turns_main": 1})
            self.assertTrue(Path(row["sandbox"]).exists())         # cleanup=False で残る
            self.assertTrue((cfg / "projects" / proj).exists())

    def test_guard_keeps_non_sandbox_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            td = _make_task(Path(d), correct=True)
            cfg = Path(d) / "cfg"
            proj = "-Users-ao-dev-ultra-ai"                        # 実セッション風(sandbox を含まない)
            row = ab_run.run_one(td, "treatment", {"config_dir": str(cfg), "env": {}}, 0,
                                 str(Path(d) / "sbx3"),
                                 invoke=self._fake_invoke(proj),
                                 extract=lambda tp: {"turns_main": 1})
            self.assertFalse(Path(row["sandbox"]).exists())        # sandbox は消える
            self.assertTrue((cfg / "projects" / proj).exists())    # 非 sandbox dir は守られる


class TestArms(unittest.TestCase):
    def test_control_xhigh_arm_shape(self):
        arms = ab_run.ARMS
        self.assertIn("control-xhigh", arms)
        cx = arms["control-xhigh"]
        self.assertEqual(cx["effort"], "xhigh")              # effort フラグだけ付ける
        self.assertEqual(cx["model"], "opus")
        self.assertEqual(cx["env"].get("UA_AUTOAPPLY"), "0")  # 学習/approach/rules は OFF=素
        # control と同一の authed config を共有(差は --effort のみ=effort 単独効果の切り分け)
        self.assertEqual(cx["config_dir"], arms["control"]["config_dir"])
        self.assertNotIn("effort", arms["control"])          # control は既定 effort

    def test_treatment_uses_claude_home(self):
        self.assertTrue(ab_run.ARMS["treatment"]["config_dir"].endswith("claude-home"))


class TestMain(unittest.TestCase):
    """main()/argparse(--tasks/--arms/-k フィルタと空タスク→return 1)を、run_one を
    差し替えて検証する(実 claude/oracle subprocess を呼ばず・実 results/.sandboxes も汚さない)。"""

    def _run_main(self, argv, *, tasks_dir, results_dir):
        import contextlib
        import io
        calls = []

        def fake_run_one(td, arm, arm_cfg, r, sandbox_root, cleanup=True):
            calls.append({"task": Path(td).name, "arm": arm, "run": r})
            return {"task": Path(td).name, "arm": arm, "run": r, "success": True}

        saved = (ab_run.run_one, ab_run.TASKS_DIR, ab_run.RESULTS_DIR)
        ab_run.run_one = fake_run_one
        ab_run.TASKS_DIR = Path(tasks_dir)
        ab_run.RESULTS_DIR = Path(results_dir)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = ab_run.main(argv)
            return code, calls, err.getvalue()
        finally:
            ab_run.run_one, ab_run.TASKS_DIR, ab_run.RESULTS_DIR = saved

    def test_filters_tasks_arms_and_k(self):
        with tempfile.TemporaryDirectory() as d:
            code, calls, _ = self._run_main(
                ["--tasks", "t1", "--arms", "control", "-k", "2"],
                tasks_dir=d, results_dir=str(Path(d) / "results"))
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 2)                          # 1 task × 1 arm × 2 runs
            self.assertTrue(all(c["arm"] == "control" for c in calls))  # 他アームは呼ばれない
            self.assertEqual(sorted(c["run"] for c in calls), [0, 1])
            self.assertTrue(all(c["task"] == "t1" for c in calls))

    def test_empty_tasks_dir_returns_1(self):
        # 空 dir + --tasks 無し → glob 空 → return 1(--tasks bogus は非空リストで到達しない別経路)
        with tempfile.TemporaryDirectory() as d:
            code, calls, err = self._run_main(
                [], tasks_dir=d, results_dir=str(Path(d) / "results"))
            self.assertEqual(code, 1)
            self.assertEqual(calls, [])
            self.assertIn("no tasks", err)


if __name__ == "__main__":
    unittest.main()
