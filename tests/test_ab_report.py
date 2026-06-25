"""Unit tests for bench/ab_report.py (run: python3 -m unittest).

pass@k / pass-rate と、合成 transcript からの指標平均・レポート整形を検証する(実 claude は呼ばない)。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (hooks + bench を sys.path に載せる)

import ab_report  # noqa: E402


def _transcript(path: Path, n_turns: int, inp: int = 10, out: int = 100) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": "assistant", "isSidechain": False,
                       "message": {"model": "claude-opus-4-8",
                                   "usage": {"input_tokens": inp, "output_tokens": out}},
                       "timestamp": "2026-06-18T00:00:00.000Z"})
    path.write_text("\n".join(line for _ in range(n_turns)) + "\n", encoding="utf-8")
    return str(path)


class TestPassMetrics(unittest.TestCase):
    def test_pass_at_k_any_success(self):
        self.assertTrue(ab_report.pass_at_k([False, False, True]))
        self.assertFalse(ab_report.pass_at_k([False, False, False]))

    def test_pass_rate(self):
        self.assertEqual(ab_report.pass_rate([True, False, False, True]), 0.5)
        self.assertEqual(ab_report.pass_rate([]), 0.0)


class TestAggregate(unittest.TestCase):
    def test_pass_metrics_and_means(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            ctrl_tr = _transcript(base / "ctrl.jsonl", n_turns=6)      # 手戻り多め
            treat_tr = _transcript(base / "treat.jsonl", n_turns=3)    # 手戻り少なめ
            results = [
                {"task": "t1", "arm": "control", "run": 0, "success": True, "transcript": ctrl_tr},
                {"task": "t1", "arm": "control", "run": 1, "success": False, "transcript": ctrl_tr},
                {"task": "t1", "arm": "treatment", "run": 0, "success": True, "transcript": treat_tr},
                {"task": "t1", "arm": "treatment", "run": 1, "success": True, "transcript": treat_tr},
            ]
            agg = ab_report.aggregate(results)
            c = agg["tasks"]["t1"]["control"]
            t = agg["tasks"]["t1"]["treatment"]
            self.assertEqual(c["pass_rate"], 0.5)
            self.assertTrue(c["pass_at_k"])
            self.assertEqual(t["pass_rate"], 1.0)
            self.assertEqual(c["metrics"]["turns_main"], 6)   # transcript の行数
            self.assertEqual(t["metrics"]["turns_main"], 3)
            self.assertIn("control", agg["overall"])
            self.assertIn("treatment", agg["overall"])

    def test_missing_transcript_counts_success_only(self):
        results = [{"task": "t", "arm": "control", "run": 0, "success": True}]
        agg = ab_report.aggregate(results)
        self.assertEqual(agg["tasks"]["t"]["control"]["pass_rate"], 1.0)
        self.assertIsNone(agg["tasks"]["t"]["control"]["metrics"]["turns_main"])


class TestReport(unittest.TestCase):
    def test_format_report_contains_arms_and_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _transcript(Path(d) / "s.jsonl", n_turns=2)
            agg = ab_report.aggregate([
                {"task": "t1", "arm": "control", "run": 0, "success": False, "transcript": tr},
                {"task": "t1", "arm": "treatment", "run": 0, "success": True, "transcript": tr},
            ])
            report = ab_report.format_report(agg)
            self.assertIn("pass_rate", report)
            self.assertIn("treatment", report)
            self.assertIn("t1", report)


def _edit_transcript(path: Path, files: list[str]) -> str:
    """各 file_path への Edit tool_use を1行ずつ持つ合成 transcript。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for fp in files:
        lines.append(json.dumps({
            "type": "assistant", "isSidechain": False,
            "message": {"model": "claude-opus-4-8",
                        "content": [{"type": "tool_use", "name": "Edit",
                                     "input": {"file_path": fp}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}},
            "timestamp": "2026-06-18T00:00:00.000Z"}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


class TestChurn(unittest.TestCase):
    def test_counts_edits_and_reedits(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _edit_transcript(Path(d) / "c.jsonl", ["a.py", "a.py", "b.py"])
            churn = ab_report.churn_of(tr)
            self.assertEqual(churn["n_edits"], 3)
            self.assertEqual(churn["reedit"], 1)  # a.py を2回=書き直し1、b.py は0

    def test_no_edits_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _transcript(Path(d) / "n.jsonl", n_turns=3)  # tool_use 無し
            self.assertEqual(ab_report.churn_of(tr), {"n_edits": 0, "reedit": 0})

    def test_missing_file_is_zero(self):
        self.assertEqual(ab_report.churn_of("/no/such/transcript.jsonl"),
                         {"n_edits": 0, "reedit": 0})

    def test_churn_flows_into_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _edit_transcript(Path(d) / "c.jsonl", ["a.py", "a.py"])
            agg = ab_report.aggregate(
                [{"task": "t", "arm": "control", "run": 0, "success": True, "transcript": tr}])
            m = agg["tasks"]["t"]["control"]["metrics"]
            self.assertEqual(m["n_edits"], 2)
            self.assertEqual(m["reedit"], 1)


class TestInlineMetrics(unittest.TestCase):
    """ab_run が結果行に格納した自己完結 metrics を優先し、transcript を読まないことを検証。"""

    def test_inline_metrics_preferred_without_transcript(self):
        def boom(_):  # inline がある時は summarize を呼ばない(呼ばれたら失敗)
            raise AssertionError("summarize should not be called when inline metrics present")
        results = [{"task": "t1", "arm": "treatment", "run": 0, "success": True,
                    "metrics": {"turns_main": 5, "n_edits": 2, "reedit": 1}}]
        agg = ab_report.aggregate(results, summarize=boom)
        m = agg["tasks"]["t1"]["treatment"]["metrics"]
        self.assertEqual(m["turns_main"], 5)
        self.assertEqual(m["n_edits"], 2)
        self.assertEqual(m["reedit"], 1)

    def test_inline_overrides_transcript_when_both_present(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _transcript(Path(d) / "s.jsonl", n_turns=9)   # transcript は 9 turns
            results = [{"task": "t", "arm": "control", "run": 0, "success": True,
                        "transcript": tr, "metrics": {"turns_main": 3}}]
            agg = ab_report.aggregate(results)
            self.assertEqual(agg["tasks"]["t"]["control"]["metrics"]["turns_main"], 3)  # inline 優先


class TestThreeArms(unittest.TestCase):
    def test_three_arms_and_harness_only_column(self):
        with tempfile.TemporaryDirectory() as d:
            tr = _transcript(Path(d) / "s.jsonl", n_turns=2)
            results = [
                {"task": "t1", "arm": "control", "run": 0, "success": False, "transcript": tr},
                {"task": "t1", "arm": "control-xhigh", "run": 0, "success": True, "transcript": tr},
                {"task": "t1", "arm": "treatment", "run": 0, "success": True, "transcript": tr},
            ]
            agg = ab_report.aggregate(results)
            for arm in ("control", "control-xhigh", "treatment"):
                self.assertIn(arm, agg["overall"])
            report = ab_report.format_report(agg)
            self.assertIn("control-xhigh", report)
            self.assertIn("vs control-xhigh", report)  # 「仕組みのみ」比較列
            self.assertIn("vs control", report)


if __name__ == "__main__":
    unittest.main()
