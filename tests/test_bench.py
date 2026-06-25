"""Unit tests for bench/compare.py (run: python3 -m unittest).

合成 transcript で A/B 採点を検証する: lower-is-better(コスト)と higher-is-better(cache_read)の判定。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (hooks + bench を sys.path に載せる -> compare/metrics importable)

import compare  # noqa: E402


def _transcript(path: Path, output_tokens: int, turns: int = 1, cache_read: int = 0) -> None:
    lines = []
    for i in range(turns):
        lines.append(json.dumps({
            "type": "assistant", "isSidechain": False,
            "message": {"model": "claude-opus-4-8",
                        "usage": {"input_tokens": 10, "output_tokens": output_tokens,
                                  "cache_read_input_tokens": cache_read}},
            "timestamp": f"2026-06-15T09:0{i}:00.000Z"}))
    path.write_text("\n".join(lines) + "\n")


class TestCompare(unittest.TestCase):
    def _rows(self, ctrl, treat):
        return {r["metric"]: r for r in compare.compare(str(ctrl), str(treat))}

    def test_treatment_cheaper_wins(self):
        with tempfile.TemporaryDirectory() as d:
            ctrl, treat = Path(d) / "c.jsonl", Path(d) / "t.jsonl"
            _transcript(ctrl, output_tokens=1000)   # 高コスト
            _transcript(treat, output_tokens=100)   # 低コスト
            rows = self._rows(ctrl, treat)
            self.assertEqual(rows["cost_weighted_total"]["better"], "treatment")
            self.assertLess(rows["cost_weighted_total"]["delta"], 0)  # treatment-control < 0

    def test_cache_read_higher_is_better(self):
        with tempfile.TemporaryDirectory() as d:
            ctrl, treat = Path(d) / "c.jsonl", Path(d) / "t.jsonl"
            _transcript(ctrl, output_tokens=100, cache_read=0)
            _transcript(treat, output_tokens=100, cache_read=5000)  # 再利用が多い=良い
            self.assertEqual(self._rows(ctrl, treat)["cache_read_total"]["better"], "treatment")

    def test_tie(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
            _transcript(a, 100)
            _transcript(b, 100)
            self.assertEqual(self._rows(a, b)["cost_weighted_total"]["better"], "tie")

    def test_format_table_renders(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
            _transcript(a, 100)
            _transcript(b, 50)
            out = compare.format_table(compare.compare(str(a), str(b)))
            self.assertIn("cost_weighted_total", out)
            self.assertIn("better", out)


if __name__ == "__main__":
    unittest.main()
