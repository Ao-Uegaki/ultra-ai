"""Unit tests for claude-home/hooks/gate.py (run: python3 -m unittest)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
from _helpers import stdin_payload  # noqa: E402
import common  # noqa: E402
import gate  # noqa: E402

# 1つのチェック結果タプル (チェック名, 状態, 出力) を作る小さなヘルパー。
# gate.aggregate / evaluate などに渡す入力を、読みやすく組み立てるためのもの。
def pass_result(name):
    return (name, common.PASS, "")

def fail_result(name, output="boom"):  # output 既定の "boom" は中身を問わないダミーの失敗出力
    return (name, common.FAIL, output)

def unknown_result(name):
    return (name, common.UNKNOWN, "")


class TestAggregate(unittest.TestCase):
    """複数チェックの状態を1つに畳む規則: 1つでも FAIL なら FAIL、UNKNOWN 混在は UNKNOWN。"""

    def test_empty_is_unknown(self):
        self.assertEqual(gate.aggregate([]), common.UNKNOWN)

    def test_all_pass(self):
        self.assertEqual(gate.aggregate([pass_result("typecheck"), pass_result("test")]), common.PASS)

    def test_any_fail_is_fail(self):
        self.assertEqual(gate.aggregate([pass_result("typecheck"), fail_result("test")]), common.FAIL)

    def test_pass_plus_unknown_is_unknown(self):
        # couldn't fully verify -> must NOT claim PASS
        self.assertEqual(gate.aggregate([pass_result("typecheck"), unknown_result("test")]), common.UNKNOWN)


class TestEvaluate(unittest.TestCase):
    """1回の検証結果から exit コード・状態・連続失敗回数・通知要否を決める純ロジック。"""

    def test_pass(self):
        d = gate.evaluate({}, "sig1", [pass_result("test")])
        self.assertEqual(d["exit"], 0)
        self.assertEqual(d["state"]["result"], common.PASS)
        self.assertEqual(d["state"]["fail_streak"], 0)

    def test_fail_first_time(self):
        d = gate.evaluate({}, "sig1", [fail_result("test")])
        self.assertEqual(d["exit"], 2)
        self.assertEqual(d["state"]["fail_streak"], 1)
        self.assertFalse(d["escalate"])

    def test_fail_repeated_escalates(self):
        d = gate.evaluate({"fail_streak": 1}, "sig1", [fail_result("test")])
        self.assertEqual(d["state"]["fail_streak"], 2)
        self.assertTrue(d["escalate"])

    def test_unknown_notifies_once(self):
        first = gate.evaluate({}, "sigX", [])
        self.assertEqual(first["exit"], 0)
        self.assertTrue(first["notify"])
        again = gate.evaluate({"notified_sig": "sigX"}, "sigX", [])
        self.assertFalse(again["notify"])  # same unverifiable state -> silent

    def test_suggest_state_carried_forward(self):
        # 提案 dedup state は PASS/FAIL/UNKNOWN いずれでも引き継ぐ(FAIL→PASS で nag させない)
        prior = {"suggest": {"ckpt_sig": "x", "ckpt_ts": 1.0}}
        for results in ([pass_result("test")], [fail_result("test")], []):
            self.assertEqual(
                gate.evaluate(prior, "sig", results)["state"]["suggest"],
                {"ckpt_sig": "x", "ckpt_ts": 1.0})


class TestDecideSuggestions(unittest.TestCase):
    """Tier 2 提案の純ロジック: 関係条件 + 1状態1回 + 時間 throttle(nag 回避の回帰)。"""

    CFG = {"ckpt_on": True, "refactor_on": True, "ckpt_throttle_sec": 1800,
           "refactor_throttle_sec": 1800, "refactor_min_files": 4, "refactor_min_added": 80}

    def _decide(self, prior, sig, **kw):
        kw.setdefault("now", 1.0)
        return gate.decide_suggestions(prior, sig, cfg=self.CFG, **kw)

    def test_checkpoint_fires_on_tracked_change_new_sig(self):
        hints, st = self._decide({}, "sigA", tracked=5, files_changed=0, added=0)
        self.assertTrue(any("/ua-checkpoint" in h for h in hints))
        self.assertEqual(st["ckpt_sig"], "sigA")

    def test_no_checkpoint_when_clean(self):
        hints, _ = self._decide({}, "sigA", tracked=0, files_changed=0, added=0)
        self.assertFalse(any("/ua-checkpoint" in h for h in hints))

    def test_checkpoint_dedup_same_sig(self):
        prior = {"ckpt_sig": "sigA", "ckpt_ts": 1.0}
        hints, _ = self._decide(prior, "sigA", tracked=5, files_changed=0, added=0, now=9999)
        self.assertFalse(any("/ua-checkpoint" in h for h in hints))  # 同状態は二度出ない

    def test_checkpoint_throttled_then_fires(self):
        prior = {"ckpt_sig": "old", "ckpt_ts": 100.0}
        early, _ = self._decide(prior, "new", tracked=1, files_changed=1, added=1, now=200)
        self.assertFalse(any("/ua-checkpoint" in h for h in early))   # throttle 未経過
        late, _ = self._decide(prior, "new", tracked=1, files_changed=1, added=1, now=100 + 1801)
        self.assertTrue(any("/ua-checkpoint" in h for h in late))      # 経過後は出る

    def test_refactor_fires_on_large_diff_and_dedups(self):
        h1, st = self._decide({}, "sX", tracked=1, files_changed=6, added=200)
        self.assertTrue(any("/ua-refactor" in h for h in h1))
        h2, _ = self._decide(st, "sX", tracked=1, files_changed=6, added=200, now=2)
        self.assertFalse(any("/ua-refactor" in h for h in h2))         # 同 sig=再提案しない

    def test_refactor_not_on_small_diff(self):
        hints, _ = self._decide({}, "sX", tracked=1, files_changed=2, added=10)
        self.assertFalse(any("/ua-refactor" in h for h in hints))

    def test_kill_switch_off(self):
        cfg = {**self.CFG, "ckpt_on": False, "refactor_on": False}
        hints, _ = gate.decide_suggestions({}, "s", tracked=9, files_changed=9,
                                           added=999, now=1.0, cfg=cfg)
        self.assertEqual(hints, [])

    def test_ship_fires_when_ahead_and_clean(self):
        cfg = {**self.CFG, "ship_on": True, "ship_throttle_sec": 1800}
        hints, st = gate.decide_suggestions({}, "sZ", tracked=0, files_changed=0,
                                            added=0, now=1.0, cfg=cfg, ahead=2)
        self.assertTrue(any("/ua-ship" in h for h in hints))
        self.assertEqual(st["ship_sig"], "sZ")

    def test_ship_not_when_dirty(self):
        cfg = {**self.CFG, "ship_on": True, "ship_throttle_sec": 1800}
        hints, _ = gate.decide_suggestions({}, "sZ", tracked=3, files_changed=0,
                                           added=0, now=1.0, cfg=cfg, ahead=2)
        self.assertFalse(any("/ua-ship" in h for h in hints))  # 未コミットは先に checkpoint

    def test_ship_not_when_not_ahead(self):
        cfg = {**self.CFG, "ship_on": True, "ship_throttle_sec": 1800}
        hints, _ = gate.decide_suggestions({}, "sZ", tracked=0, files_changed=0,
                                           added=0, now=1.0, cfg=cfg, ahead=0)
        self.assertFalse(any("/ua-ship" in h for h in hints))


class TestResolveCommands(unittest.TestCase):
    """検証コマンドの解決: プロジェクト設定(.ultra-ai.toml)が自動検出より優先される。"""

    def test_project_config_overrides_detect(self):
        with tempfile.TemporaryDirectory() as d:
            _helpers.write_config_toml(
                d, '[verify]\ntypecheck = "tc"\ntest = "pytest -q"\n'
                   'scope = "full"\ntimeout_seconds = 42\n')
            tc, test, scope, timeout = gate.resolve_commands(d)
            self.assertEqual(tc, "tc")
            self.assertEqual(test, "pytest -q")
            self.assertEqual(scope, "full")
            self.assertEqual(timeout, 42)


class TestSignature(unittest.TestCase):
    """working-tree 署名: 同じ状態は同じ値、変化(HEAD/変更ファイル)で必ず変わる(dedup の土台)。"""

    def test_deterministic(self):
        self.assertEqual(gate.sig_of("head", ["M a.py"]), gate.sig_of("head", ["M a.py"]))

    def test_sensitive_to_changes(self):
        self.assertNotEqual(gate.sig_of("h1", ["M a.py"]), gate.sig_of("h2", ["M a.py"]))
        self.assertNotEqual(gate.sig_of("h", ["M a.py"]), gate.sig_of("h", ["M b.py"]))


class TestFailSummary(unittest.TestCase):
    """失敗要約は行数を上限内に抑え、完全ログへの参照を必ず付ける(context を溢れさせない)。"""

    def test_bounded_lines(self):
        big = "\n".join(f"error {i}" for i in range(200))
        msg = gate.build_fail_summary([fail_result("typecheck", big), fail_result("test", big)], "/tmp/x.log")
        self.assertLessEqual(len(msg.splitlines()), 22)  # 2 headers + ~16 + log line
        self.assertIn("完全ログ:", msg)


class TestPassDetail(unittest.TestCase):
    """PASS 時の1行サマリの組み立て(チェック印・ファイル数・追加行・所要時間の整形)。"""

    def test_summarizes_checks_and_files(self):
        d = gate._pass_detail([pass_result("typecheck"), pass_result("test")], 3)
        self.assertEqual(d, "typecheck✓ test✓ · 3 files")

    def test_unknown_check_uses_question_mark(self):
        self.assertIn("test?", gate._pass_detail([unknown_result("test")], 0))

    def test_no_files_omits_files_clause(self):
        self.assertEqual(gate._pass_detail([pass_result("test")], 0), "test✓")

    def test_empty_is_none(self):
        self.assertIsNone(gate._pass_detail([], 0))

    def test_added_lines_appended(self):
        d = gate._pass_detail([pass_result("test")], 4, added=87)
        self.assertEqual(d, "test✓ · 4 files(+87)")

    def test_elapsed_appended_as_mmss(self):
        d = gate._pass_detail([pass_result("test")], 0, wall_clock_s=135)
        self.assertEqual(d, "test✓ · 2:15")

    def test_sub_second_elapsed_omitted(self):
        # 1秒未満は所要を載せない(ノイズ回避)
        self.assertEqual(gate._pass_detail([pass_result("test")], 0, wall_clock_s=0.4), "test✓")


class TestFailDetail(unittest.TestCase):
    """失敗通知の中身: 「…が失敗:」の後の核心1行を拾い、連続失敗回数を添える。"""

    def test_picks_core_error_after_failure_marker(self):
        summary = "✗ test が失敗:\nAssertionError: expected 42 got 41\n(more)"
        self.assertEqual(gate._fail_detail(summary), "AssertionError: expected 42 got 41")

    def test_appends_streak_when_repeated(self):
        out = gate._fail_detail("✗ test が失敗:\nboom", fail_streak=3)
        self.assertEqual(out, "boom · 連続3回失敗")

    def test_falls_back_to_headline_when_no_marker(self):
        # "が失敗:" が無ければ _fail_headline と同等の先頭意味行
        self.assertEqual(gate._fail_detail("just a line"), "just a line")

    def test_blank_falls_back(self):
        self.assertEqual(gate._fail_detail(""), "検証に失敗")


class TestFailHeadline(unittest.TestCase):
    """失敗の見出し: 要約の先頭の意味ある1行を返す(ESCALATE 行は飛ばす)。"""

    def test_first_meaningful_line(self):
        self.assertEqual(gate._fail_headline("✗ test が失敗:\nboom"), "✗ test が失敗:")

    def test_skips_escalate_line(self):
        # ⚠ で始まる ESCALATE 行は飛ばして次の意味ある行を返す
        out = gate._fail_headline(gate.ESCALATE + "\n✗ test が失敗:\nboom")
        self.assertEqual(out, "✗ test が失敗:")

    def test_blank_falls_back(self):
        self.assertEqual(gate._fail_headline(""), "検証に失敗")


class TestStopNotification(unittest.TestCase):
    """Stop 時の通知意図: PASS/UNKNOWN/エスカレート FAIL は出し、通常 FAIL は無音。"""

    def test_pass(self):
        n = gate.stop_notification(common.PASS, escalate=False, cwd="/p", branch="main",
                                   pass_detail="test✓")
        self.assertEqual(n["kind"], "pass")
        self.assertEqual(n["label"], "完了(PASS)")
        self.assertEqual(n["detail"], "test✓")
        self.assertEqual(n["branch"], "main")

    def test_unknown_is_unknown(self):
        n = gate.stop_notification(common.UNKNOWN, escalate=False, cwd="/p", branch=None)
        self.assertEqual(n["kind"], "unknown")

    def test_fail_escalate_is_stuck(self):
        n = gate.stop_notification(common.FAIL, escalate=True, cwd="/p", branch="main",
                                   fail_headline="✗ test が失敗:")
        self.assertEqual(n["kind"], "stuck")
        self.assertEqual(n["detail"], "✗ test が失敗:")

    def test_fail_not_escalate_is_silent(self):
        # 通常の FAIL(修正ループ中)は通知しない
        self.assertIsNone(
            gate.stop_notification(common.FAIL, escalate=False, cwd="/p", branch="main"))


class TestRunChecksScope(unittest.TestCase):
    """scope が run_checks まで届き、impacted が安全に絞る/full に戻ることを検証。"""

    def _recording_runner(self, seen):
        def runner(cmd, root, timeout=180):
            seen.append(cmd)
            return common.PASS, ""
        return runner

    def test_full_scope_runs_test_verbatim(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            gate.run_checks(d, None, "pytest -q", 60, self._recording_runner(seen),
                            scope="full", changed=["M  tests/test_a.py"])
        self.assertEqual(seen, ["pytest -q"])

    def test_impacted_narrows_test_only_change(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            gate.run_checks(d, None, "pytest -q", 60, self._recording_runner(seen),
                            scope="impacted", changed=["M  tests/test_a.py"])
        self.assertEqual(len(seen), 1)
        self.assertIn("test_a.py", seen[0])

    def test_impacted_falls_back_to_full_on_source_change(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            gate.run_checks(d, None, "pytest -q", 60, self._recording_runner(seen),
                            scope="impacted", changed=["M  src/app.py"])
        self.assertEqual(seen, ["pytest -q"])


class TestMainIntegration(_helpers.ConfigDirTestCase):
    """gate.main() end-to-end with a real temp git repo + cheap commands."""

    def setUp(self):
        super().setUp()
        self.repo = _helpers.make_git_repo(self)

    def _toml(self, test):
        _helpers.write_config_toml(self.repo.name, f'[verify]\ntest = "{test}"\n')

    def _main(self, sid="s"):
        with stdin_payload({"cwd": self.repo.name, "session_id": sid}), \
                contextlib.redirect_stderr(io.StringIO()):
            return gate.main()

    def _verif(self, sid="s"):
        key = common.repo_key(self.repo.name)
        return (Path(self.config_dir.name) / "state" / key
                / "sessions" / sid / common.STATE_VERIFICATION)

    def test_no_changes_allows_stop(self):
        self.assertEqual(self._main(), 0)

    def test_pass_allows_stop_and_records(self):
        (Path(self.repo.name) / "f.txt").write_text("x")
        self._toml("true")
        self.assertEqual(self._main(), 0)
        self.assertTrue(self._verif().exists())
        self.assertEqual(json.loads(self._verif().read_text()).get("result"), common.PASS)

    def test_fail_blocks_stop(self):
        (Path(self.repo.name) / "f.txt").write_text("x")
        self._toml("false")
        self.assertEqual(self._main(), 2)

    def test_dedup_unchanged_pass(self):
        (Path(self.repo.name) / "f.txt").write_text("x")
        self._toml("true")
        self.assertEqual(self._main(), 0)
        self.assertEqual(self._main(), 0)  # same tree state -> dedup, still allowed

    def _main_capture(self, sid="s"):
        err = io.StringIO()
        with stdin_payload({"cwd": self.repo.name, "session_id": sid}), \
                contextlib.redirect_stderr(err):
            code = gate.main()
        return code, err.getvalue()

    def _commit_initial(self):
        repo = self.repo.name
        (Path(repo) / "f.txt").write_text("v1")
        common.run_git(["git", "add", "-A"], repo)
        common.run_git(["git", "commit", "-q", "-m", "init"], repo)

    def test_pass_suggests_checkpoint_on_tracked_change(self):
        self._commit_initial()
        (Path(self.repo.name) / "f.txt").write_text("v2")  # 追跡済み変更(未コミット)
        self._toml("true")
        code, err = self._main_capture()
        self.assertEqual(code, 0)                       # 提案は PASS を妨げない(exit 0)
        self.assertIn("/ua-checkpoint", err)

    def test_checkpoint_suggested_once_per_state(self):
        self._commit_initial()
        (Path(self.repo.name) / "f.txt").write_text("v2")
        self._toml("true")
        _, first = self._main_capture()
        _, second = self._main_capture()                # 同じ working-tree → dedup
        self.assertIn("/ua-checkpoint", first)
        self.assertNotIn("/ua-checkpoint", second)

    def test_unknown_allows_stop_notifies_once_and_no_progress(self):
        # 検出不能(manifest も tests/test_*.py も無い素 repo)→ detect_stack 全None →
        # run_checks=[] → UNKNOWN。憲法「UNKNOWN は PASS でない」を main 統合で固定する。
        (Path(self.repo.name) / "f.txt").write_text("x")  # 変更あり・検出可能なコマンドは無し
        code, err = self._main_capture()
        self.assertEqual(code, 0)                          # UNKNOWN は Stop を妨げない
        self.assertIn(gate.UNKNOWN_NOTICE, err)            # 一度だけ未検証を通知
        self.assertEqual(
            json.loads(self._verif().read_text()).get("result"), common.UNKNOWN)
        # UNKNOWN は PASS でない=自動 progress を更新しない
        prog = common.shared_state_dir(self.repo.name) / common.STATE_PROGRESS
        self.assertFalse(prog.exists())
        # 同一 tree の2回目は dedup で NOTICE を再出力しない(main レベルの notify-once)
        _, err2 = self._main_capture()
        self.assertNotIn(gate.UNKNOWN_NOTICE, err2)

    def test_fail_reblock_escalates_on_second_run_without_rerun(self):
        # 変更なし + 依然 FAIL → 再実行せず再ブロック・streak エスカレート(gate.py:389-398)。
        # 初回 FAIL は silent、2回目で初めて ESCALATE、fail_streak は 1→2。
        (Path(self.repo.name) / "f.txt").write_text("x")
        self._toml("false")
        code1, err1 = self._main_capture()
        self.assertEqual(code1, 2)                          # FAIL は Stop をブロック
        self.assertNotIn("連続して失敗", err1)               # streak=1 は silent
        self.assertEqual(json.loads(self._verif().read_text()).get("fail_streak"), 1)
        code2, err2 = self._main_capture()                  # 同一 tree → 再実行なしで再ブロック
        self.assertEqual(code2, 2)
        self.assertIn("連続して失敗", err2)                  # streak>=2 でエスカレート
        self.assertEqual(json.loads(self._verif().read_text()).get("fail_streak"), 2)

    def test_runner_unknown_does_not_become_pass(self):
        # コマンドは在るが runner が UNKNOWN(timeout 等)を返す → PASS に昇格させない。
        # run_checks 単体でなく main 経由で、UNKNOWN が evaluate→exit/通知まで流れることを固定。
        (Path(self.repo.name) / "f.txt").write_text("x")
        self._toml("sometest")
        orig = common.run_cmd
        common.run_cmd = lambda cmd, root, timeout=180: (common.UNKNOWN, "timed out")
        try:
            code, err = self._main_capture()
        finally:
            common.run_cmd = orig
        self.assertEqual(code, 0)                            # UNKNOWN は Stop を妨げない
        self.assertEqual(
            json.loads(self._verif().read_text()).get("result"), common.UNKNOWN)
        self.assertIn(gate.UNKNOWN_NOTICE, err)              # 未検証を通知
        prog = common.shared_state_dir(self.repo.name) / common.STATE_PROGRESS
        self.assertFalse(prog.exists())                      # UNKNOWN は progress を書かない


class TestStatePersistence(unittest.TestCase):
    """_load_state / _save_state delegate to common's atomic JSON I/O."""

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            gate._save_state(Path(d), {"result": common.PASS, "fail_streak": 0})
            self.assertEqual(gate._load_state(Path(d)),
                             {"result": common.PASS, "fail_streak": 0})

    def test_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gate._load_state(Path(d)), {})

    def test_corrupt_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / common.STATE_VERIFICATION).write_text("{broken")
            self.assertEqual(gate._load_state(Path(d)), {})

    def test_save_is_atomic_no_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            gate._save_state(Path(d), {"v": 1})
            self.assertEqual([f.name for f in Path(d).iterdir()],
                             [common.STATE_VERIFICATION])


class TestCaptureFailpass(_helpers.ConfigDirTestCase):
    """FAIL→PASS を学習候補へ捕捉(UA_AUTOAPPLY 下・直前 FAIL のときだけ・注入しない)。"""

    def setUp(self):
        super().setUp()
        _helpers.set_env(self, UA_AUTOAPPLY=None)

    def _cands(self, cwd):
        f = common.shared_state_dir(cwd) / common.STATE_LEARN_CANDIDATES
        return f.read_text() if f.exists() else ""

    def test_captures_on_fail_to_pass_when_flag_on(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            gate._capture_failpass(cwd, {"result": common.FAIL,
                                         "summary": "✗ test_x failed: assert 1 == 2"})
            self.assertIn("fail-pass", self._cands(cwd))

    def test_noop_when_disabled(self):
        _helpers.set_env(self, UA_AUTOAPPLY="0")  # 明示的に無効化
        with tempfile.TemporaryDirectory() as cwd:
            gate._capture_failpass(cwd, {"result": common.FAIL, "summary": "x"})
            self.assertEqual(self._cands(cwd), "")

    def test_captures_by_default_unset(self):
        os.environ.pop("UA_AUTOAPPLY", None)  # 未設定=既定 ON
        with tempfile.TemporaryDirectory() as cwd:
            gate._capture_failpass(cwd, {"result": common.FAIL, "summary": "✗ boom"})
            self.assertIn("fail-pass", self._cands(cwd))

    def test_noop_when_prior_not_fail(self):
        _helpers.set_env(self, UA_AUTOAPPLY="1")
        with tempfile.TemporaryDirectory() as cwd:
            gate._capture_failpass(cwd, {"result": common.PASS})
            self.assertEqual(self._cands(cwd), "")


class TestEmptyTestRun(unittest.TestCase):
    """0件しか走らないテスト(pytest 関数スタイル × unittest discover 等)を UNKNOWN に降格。"""

    def test_detects_empty(self):
        for o in ("Ran 0 tests in 0.000s\n\nOK", "no tests ran in 0.01s", "collected 0 items",
                  # node: jest/vitest の 0件表現(--passWithNoTests で exit0 の false-pass 用)
                  "No tests found, exiting with code 0",
                  "No test files found, exiting with code 0"):
            self.assertTrue(gate._empty_test_run(o), o)

    def test_normal_output_not_empty(self):
        for o in ("Ran 193 tests in 4.9s\n\nOK", "collected 12 items", "",
                  "Tests:  12 passed, 12 total"):
            self.assertFalse(gate._empty_test_run(o), o)

    def test_run_checks_downgrades_node_empty_pass_to_unknown(self):
        # node test コマンドが 0件 exit0 で返っても UNKNOWN に降格(false-pass を作らない)
        for out in ("No tests found, exiting with code 0",
                    "No test files found, exiting with code 0"):
            def runner(cmd, root, timeout=None, _o=out):
                return (common.PASS, _o)
            res = gate.run_checks("/x", None, "npx jest --watchAll=false --ci",
                                  60, runner, scope="full")
            states = {n: s for n, s, _ in res}
            self.assertEqual(states["test"], common.UNKNOWN, out)

    def test_run_checks_downgrades_empty_pass_to_unknown(self):
        def runner(cmd, root, timeout=None):
            return (common.PASS, "Ran 0 tests in 0.000s\n\nOK")
        res = gate.run_checks("/x", None, "python3 -m unittest discover -s tests",
                              60, runner, scope="full")
        states = {n: s for n, s, _ in res}
        self.assertEqual(states["test"], common.UNKNOWN)  # 0件=未検証=PASS にしない

    def test_run_checks_keeps_normal_pass(self):
        def runner(cmd, root, timeout=None):
            return (common.PASS, "Ran 5 tests in 0.1s\n\nOK")
        res = gate.run_checks("/x", None, "pytest -q", 60, runner, scope="full")
        states = {n: s for n, s, _ in res}
        self.assertEqual(states["test"], common.PASS)


if __name__ == "__main__":
    unittest.main()
