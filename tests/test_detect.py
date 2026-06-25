"""Unit tests for claude-home/hooks/detect.py (run: python3 -m unittest)."""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402  (CONFIG_FILE の単一ソース)
import detect  # noqa: E402


class TestDetect(unittest.TestCase):
    def test_node(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(
                json.dumps({"scripts": {"test": "mocha", "typecheck": "tsc"}}))
            st = detect.detect_stack(d)
            self.assertEqual(st.kind, "node")
            self.assertEqual(st.test, "npm test")
            self.assertEqual(st.typecheck, "npm run typecheck")

    def test_node_watch_runners_get_run_once(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
            self.assertEqual(detect.detect_stack(d).test, "npx vitest run")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(
                json.dumps({"scripts": {"test": "jest --coverage"}}))
            self.assertEqual(detect.detect_stack(d).test, "npx jest --watchAll=false --ci")

    def test_python(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[tool.x]\n")
            self.assertEqual(detect.detect_stack(d).kind, "python")

    def test_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.detect_stack(d).kind, "unknown")

    def test_unittest_layout_no_manifest(self):
        # manifest 無し + tests/test_*.py → python 扱いで unittest discover(設定ゼロ)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tests").mkdir()
            (Path(d) / "tests" / "test_foo.py").write_text("import unittest\n")
            st = detect.detect_stack(d)
            self.assertEqual(st.kind, "python")
            self.assertEqual(st.test, "python3 -m unittest discover -s tests")

    def test_manifest_wins_over_unittest_layout(self):
        # manifest があれば従来検出(unittest discover で上書きしない)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[tool.x]\n")
            (Path(d) / "tests").mkdir()
            (Path(d) / "tests" / "test_foo.py").write_text("x\n")
            self.assertNotEqual(detect.detect_stack(d).test,
                                "python3 -m unittest discover -s tests")

    def test_tests_dir_without_test_files_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tests").mkdir()
            (Path(d) / "tests" / "helper.py").write_text("x\n")  # test_* ではない
            self.assertEqual(detect.detect_stack(d).kind, "unknown")


class TestDetectStackDegradation(unittest.TestCase):
    """検出の縮退を固定する characterization test(バグ修正でなく現挙動のスナップショット)。
    どちらも gate 側の『型チェック不在のまま test PASS だけで緑』『monorepo の片側を取りこぼす』
    という縮退を、回帰として落とせるようにするのが目的。"""

    def test_typecheck_absent_when_only_pytest_on_path(self):
        # pyright/mypy が PATH に無い環境では typecheck=None になり、gate は test PASS だけで
        # 緑にできる(=型チェック不在経路)。detect._which を単一 seam として patch する。
        orig = detect._which
        detect._which = lambda name: name == "pytest"  # pytest だけ在る
        try:
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "pyproject.toml").write_text("[tool.x]\n")
                st = detect.detect_stack(d)
                self.assertEqual(st.kind, "python")
                self.assertIsNone(st.typecheck)        # pyright/mypy 不在 → 型チェック無し
                self.assertEqual(st.test, "pytest -q")
                self.assertIsNone(st.lint_file)        # ruff/flake8 不在
        finally:
            detect._which = orig

    def test_monorepo_packagejson_shadows_pyproject(self):
        # 既知の(意図された)単一ルート縮退: package.json があれば node 検出が優先され、
        # 同居する pyproject.toml(Python)は取りこぼす。将来 monorepo 対応するなら別 issue。
        # ここはその現挙動を固定し、黙って変わらないようにするだけ(取りこぼし自体は直さない)。
        import json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
            (Path(d) / "pyproject.toml").write_text('[project]\ndependencies = ["pytest"]\n')
            self.assertEqual(detect.detect_stack(d).kind, "node")


class TestDetectDomains(unittest.TestCase):
    def _repo(self, **files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, body in files.items():
            p = Path(d.name) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        return d.name

    def test_frontend_react(self):
        d = self._repo(**{"package.json": json.dumps({"dependencies": {"react": "^18"}})})
        self.assertIn("frontend", detect.detect_domains(d))

    def test_frontend_angular_scoped_prefix(self):
        d = self._repo(**{"package.json": json.dumps({"dependencies": {"@angular/core": "^17"}})})
        self.assertIn("frontend", detect.detect_domains(d))

    def test_vite_alone_is_not_frontend(self):
        # vite はツールチェーンに広く出る弱信号 → 単独では frontend にしない
        d = self._repo(**{"package.json": json.dumps({"devDependencies": {"vite": "^5"}})})
        self.assertNotIn("frontend", detect.detect_domains(d))

    def test_backend_node_express(self):
        d = self._repo(**{"package.json": json.dumps({"dependencies": {"express": "^4"}})})
        self.assertIn("backend", detect.detect_domains(d))

    def test_backend_python_pyproject(self):
        d = self._repo(**{"pyproject.toml": '[project]\ndependencies = ["fastapi>=0.100"]\n'})
        self.assertIn("backend", detect.detect_domains(d))

    def test_backend_requirements_normalized(self):
        d = self._repo(**{"requirements.txt": "Django==4.2\n"})  # 正規化 django で一致
        self.assertIn("backend", detect.detect_domains(d))

    def test_ml_torch(self):
        d = self._repo(**{"pyproject.toml": '[project]\ndependencies = ["torch"]\n'})
        self.assertIn("ml", detect.detect_domains(d))

    def test_numpy_alone_is_not_ml(self):
        # numpy/pandas は汎用 → ml 誤検出の最大要因なので採らない(保守性の核心)
        d = self._repo(**{"requirements.txt": "numpy\npandas\n"})
        self.assertNotIn("ml", detect.detect_domains(d))

    def test_ml_shallow_ipynb(self):
        d = self._repo(**{"model.ipynb": "{}"})
        self.assertIn("ml", detect.detect_domains(d))

    def test_deep_ipynb_not_detected(self):
        # 深いネストは拾わない(rglob しない=SessionStart を冷やさない保証)
        d = self._repo(**{"a/b/c.ipynb": "{}"})
        self.assertNotIn("ml", detect.detect_domains(d))

    def test_infra_dockerfile(self):
        d = self._repo(**{"Dockerfile": "FROM scratch\n"})
        self.assertIn("infra", detect.detect_domains(d))

    def test_infra_terraform_toplevel(self):
        d = self._repo(**{"main.tf": "resource {}\n"})
        self.assertIn("infra", detect.detect_domains(d))

    def test_infra_github_workflows(self):
        d = self._repo(**{".github/workflows/ci.yml": "on: push\n"})
        self.assertIn("infra", detect.detect_domains(d))

    def test_empty_repo_no_domains(self):
        d = self._repo()
        self.assertEqual(detect.detect_domains(d), set())

    def test_broken_manifests_never_raise(self):
        d = self._repo(**{"package.json": "{not json",
                          "pyproject.toml": "[broken"})
        self.assertEqual(detect.detect_domains(d), set())  # 例外でなく空集合へ縮退

    def test_multiple_domains_union(self):
        d = self._repo(**{"package.json": json.dumps({"dependencies": {"react": "1", "express": "1"}}),
                          "Dockerfile": "FROM scratch\n"})
        self.assertEqual(detect.detect_domains(d), {"frontend", "backend", "infra"})


class TestProjectConfig(unittest.TestCase):
    def test_reads_toml(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".ultra-ai.toml").write_text('[verify]\ntest = "pytest"\n')
            cfg = detect.load_project_config(d)
            self.assertEqual(cfg.get("verify", {}).get("test"), "pytest")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.load_project_config(d), {})


class TestImpactedTestCmd(unittest.TestCase):
    PY = detect.Stack(kind="python", test="pytest -q")

    def test_test_only_change_narrows(self):
        cmd = detect.impacted_test_cmd(
            "pytest -q", ["M  tests/test_a.py", "M  tests/test_b.py"], self.PY, "/r")
        self.assertIsNotNone(cmd)
        self.assertIn("test_a.py", cmd)
        self.assertIn("test_b.py", cmd)
        self.assertTrue(cmd.startswith("pytest -q "))

    def test_source_change_falls_back_to_full(self):
        # ソース変更を含むと関連テスト取りこぼし回避のため None(=full)
        self.assertIsNone(
            detect.impacted_test_cmd("pytest -q", ["M  src/app.py"], self.PY, "/r"))

    def test_mixed_source_and_test_falls_back(self):
        self.assertIsNone(detect.impacted_test_cmd(
            "pytest -q", ["M  tests/test_a.py", "M  app.py"], self.PY, "/r"))

    def test_non_python_change_falls_back(self):
        self.assertIsNone(detect.impacted_test_cmd(
            "pytest -q", ["M  tests/data.json"], self.PY, "/r"))

    def test_empty_or_missing(self):
        self.assertIsNone(detect.impacted_test_cmd("pytest -q", [], self.PY, "/r"))
        self.assertIsNone(detect.impacted_test_cmd(None, ["M  tests/test_a.py"], self.PY, "/r"))

    def test_rename_takes_new_path(self):
        cmd = detect.impacted_test_cmd(
            "pytest -q", ["R  tests/old_test.py -> tests/new_test.py"], self.PY, "/r")
        self.assertIsNotNone(cmd)
        self.assertIn("new_test.py", cmd)
        self.assertNotIn("old_test.py", cmd)

    UT = "python3 -m unittest discover -s tests"
    UTST = detect.Stack(kind="python", test="python3 -m unittest discover -s tests")

    def test_unittest_single_test_file_narrows_with_discover_pattern(self):
        # 単一テストファイル変更 → discover -s tests -p <basename>(ドットつき形は使わない)。
        cmd = detect.impacted_test_cmd(self.UT, ["M  tests/test_a.py"], self.UTST, "/r")
        self.assertIsNotNone(cmd)
        self.assertIn("discover -s tests", cmd)
        self.assertIn("-p test_a.py", cmd)
        self.assertNotIn("tests.test_a", cmd)  # ドットつきモジュール形にしない(ModuleNotFoundError 回避)
        self.assertNotIn("pytest", cmd)        # pytest コマンドは出さない(command-not-found 回帰防止)

    def test_unittest_multiple_test_files_falls_back_to_full(self):
        self.assertIsNone(detect.impacted_test_cmd(
            self.UT, ["M  tests/test_a.py", "M  tests/test_b.py"], self.UTST, "/r"))

    def test_unittest_source_change_falls_back_to_full(self):
        self.assertIsNone(detect.impacted_test_cmd(
            self.UT, ["M  claude-home/hooks/gate.py"], self.UTST, "/r"))

    def test_unittest_file_outside_start_dir_falls_back(self):
        # start-dir(tests)配下でないテストファイル → full(0件 discover で UNKNOWN に落とさない)
        self.assertIsNone(detect.impacted_test_cmd(
            self.UT, ["M  test_root.py"], self.UTST, "/r"))

    def test_discover_start_dir_parsing(self):
        self.assertEqual(detect._discover_start_dir("python3 -m unittest discover -s pkg/tests"),
                         "pkg/tests")
        self.assertEqual(detect._discover_start_dir(
            "python3 -m unittest discover --start-directory=t"), "t")
        self.assertEqual(detect._discover_start_dir("python3 -m unittest discover"), "tests")


class TestVerifyConfig(unittest.TestCase):
    def test_present(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / common.CONFIG_FILE).write_text(
                '[verify]\ntest = "pytest -q"\nscope = "full"\n')
            cfg = detect.verify_config(d)
            self.assertEqual(cfg.get("test"), "pytest -q")
            self.assertEqual(cfg.get("scope"), "full")

    def test_missing_section_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / common.CONFIG_FILE).write_text("[other]\nx = 1\n")
            self.assertEqual(detect.verify_config(d), {})

    def test_no_config_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.verify_config(d), {})


if __name__ == "__main__":
    unittest.main()
