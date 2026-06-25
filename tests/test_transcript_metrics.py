"""Unit tests for bench/transcript_metrics.py (run: python3 -m unittest).

これまで ab_report/ab_run 経由の間接被覆しかなかったモジュールを直接テストする。
churn(コード書き直し=手戻り)の数え方・metrics 抽出・extract の合成 を固定する。API は呼ばない。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (hooks + bench を sys.path に載せる)

import transcript_metrics as tm  # noqa: E402
import compare  # noqa: E402


def _write_transcript(path: Path, blocks) -> str:
    """blocks(=各 assistant turn の content list)を1行1イベントの jsonl にする。"""
    lines = [json.dumps({"message": {"content": content}}) for content in blocks]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _edit(file_path: str):
    return {"type": "tool_use", "name": "Edit", "input": {"file_path": file_path}}


class TestChurnOf(unittest.TestCase):
    def test_counts_edits_and_reedits(self):
        with tempfile.TemporaryDirectory() as d:
            tp = _write_transcript(Path(d) / "t.jsonl", [
                [_edit("a.py")],                                   # a 1回目
                [{"type": "text", "text": "thinking"}],            # 編集でない block は無視
                [_edit("a.py"), _edit("b.py")],                    # a 2回目 + b 1回目
            ])
            self.assertEqual(tm.churn_of(tp), {"n_edits": 3, "reedit": 1})  # a の再編集が1

    def test_write_and_multiedit_counted(self):
        with tempfile.TemporaryDirectory() as d:
            tp = _write_transcript(Path(d) / "t.jsonl", [
                [{"type": "tool_use", "name": "Write", "input": {"file_path": "x.py"}},
                 {"type": "tool_use", "name": "MultiEdit", "input": {"file_path": "x.py"}}],
            ])
            self.assertEqual(tm.churn_of(tp), {"n_edits": 2, "reedit": 1})

    def test_missing_file_is_zero(self):
        self.assertEqual(tm.churn_of("/no/such/transcript.jsonl"),
                         {"n_edits": 0, "reedit": 0})

    def test_broken_and_blank_lines_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            tp.write_text("\n{broken json\n"
                          + json.dumps({"message": {"content": [_edit("a.py")]}}) + "\n",
                          encoding="utf-8")
            self.assertEqual(tm.churn_of(str(tp)), {"n_edits": 1, "reedit": 0})


class TestMetricsOf(unittest.TestCase):
    def test_pulls_all_metric_labels(self):
        summary = {"total": {"weighted_cost": 1.5, "cache_read": 9},
                   "main": {"weighted_cost": 1.0},
                   "turns_main": 4, "peak_main_context": 1234}
        out = tm.metrics_of(summary)
        self.assertEqual(set(out), {label for label, _, _ in compare._METRICS})
        self.assertEqual(out["cost_weighted_total"], 1.5)
        self.assertEqual(out["turns_main"], 4)
        self.assertEqual(out["cache_read_total"], 9)

    def test_missing_fields_degrade_to_none(self):
        out = tm.metrics_of({})  # getter が KeyError → None へ縮退(例外を投げない)
        self.assertTrue(all(v is None for v in out.values()))
        self.assertEqual(set(out), {label for label, _, _ in compare._METRICS})


class TestExtract(unittest.TestCase):
    def test_merges_metrics_and_churn(self):
        with tempfile.TemporaryDirectory() as d:
            tp = _write_transcript(Path(d) / "t.jsonl", [[_edit("a.py"), _edit("a.py")]])
            fake_summary = {"total": {"weighted_cost": 2.0, "cache_read": 0},
                            "main": {"weighted_cost": 2.0},
                            "turns_main": 1, "peak_main_context": 10}
            out = tm.extract(tp, summarize=lambda _p: fake_summary)
            self.assertEqual(out["cost_weighted_total"], 2.0)   # metrics 由来
            self.assertEqual(out["n_edits"], 2)                 # churn 由来
            self.assertEqual(out["reedit"], 1)


if __name__ == "__main__":
    unittest.main()
