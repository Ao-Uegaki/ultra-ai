"""Unit tests for claude-home/hooks/metrics.py (run: python3 -m unittest).

合成 fixture(メイン transcript + subagents/ 配下の別ファイル)で、main/subagent が
**ファイルの所在**で正しく分離・集計され、モデル別単価で加重されることを検証する。
"""
import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (puts claude-home/hooks on sys.path)
import common  # noqa: E402
import metrics  # noqa: E402


def _line(model, usage, *, side=False, ts=None, typ="assistant"):
    o = {"type": typ, "isSidechain": side, "message": {"model": model, "usage": usage}}
    if ts:
        o["timestamp"] = ts
    return json.dumps(o)


def _write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _fixture(base: Path) -> Path:
    """`<base>/sess.jsonl` + subagents/ ツリーを作り、main_path を返す。"""
    main = base / "sess.jsonl"
    _write(main, [
        _line("claude-opus-4-8",
              {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000},
              ts="2026-06-15T09:00:00.000Z"),
        "not valid json {{{",                                   # 壊れ行 → 無視
        _line("claude-opus-4-8", {"input_tokens": 999}, typ="user"),  # 非 assistant → 無視
        _line("claude-opus-4-8",
              {"input_tokens": 20, "output_tokens": 200, "cache_read_input_tokens": 2000,
               "cache_creation_input_tokens": 500,
               "cache_creation": {"ephemeral_5m_input_tokens": 500,
                                  "ephemeral_1h_input_tokens": 0}},
              ts="2026-06-15T09:00:30.000Z"),
    ])
    # Task 系 subagent(sonnet)
    _write(base / "sess" / "subagents" / "agent-a.jsonl", [
        _line("claude-sonnet-4-6",
              {"input_tokens": 5, "output_tokens": 50, "cache_read_input_tokens": 500},
              side=True),
    ])
    # workflow 系 subagent(haiku)+ usage を持たない journal(無視されるべき)
    _write(base / "sess" / "subagents" / "workflows" / "wf1" / "agent-b.jsonl", [
        _line("claude-haiku-4-5",
              {"input_tokens": 2, "output_tokens": 20, "cache_read_input_tokens": 200},
              side=True),
    ])
    _write(base / "sess" / "subagents" / "workflows" / "wf1" / "journal.jsonl",
           [json.dumps({"type": "log", "msg": "no usage here"})])
    return main


class TestParseTranscript(unittest.TestCase):
    def test_main_only_in_main_file(self):
        with tempfile.TemporaryDirectory() as d:
            main = _fixture(Path(d))
            t = metrics.parse_transcript(str(main))
            self.assertEqual(t["main"]["turns"], 2)        # 壊れ行と user 行は除外
            self.assertEqual(t["side"]["turns"], 0)        # main ファイルに sidechain は無い
            self.assertIn("claude-opus-4-8", t["main"]["by_model"])
            self.assertEqual(t["main"]["peak_context"], 2520)  # 20+2000+500

    def test_subagent_file_is_sidechain(self):
        with tempfile.TemporaryDirectory() as d:
            main = _fixture(Path(d))
            sub = metrics.discover_subagent_transcripts(str(main))
            t = metrics.parse_transcript(sub[0])
            # subagent ファイルの行は isSidechain=true 側に入る
            self.assertEqual(t["main"]["turns"] + t["side"]["turns"], 1)
            self.assertEqual(t["side"]["turns"], 1)


class TestDiscover(unittest.TestCase):
    def test_finds_agent_files_excludes_journal(self):
        with tempfile.TemporaryDirectory() as d:
            main = _fixture(Path(d))
            found = metrics.discover_subagent_transcripts(str(main))
            self.assertEqual(len(found), 2)
            self.assertTrue(all("agent-" in f for f in found))
            self.assertFalse(any("journal" in f for f in found))

    def test_no_subagents_dir(self):
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "lonely.jsonl"
            main.write_text(_line("claude-opus-4-8", {"input_tokens": 1}) + "\n")
            self.assertEqual(metrics.discover_subagent_transcripts(str(main)), [])


class TestWeightCost(unittest.TestCase):
    def test_per_model_input_price(self):
        self.assertAlmostEqual(
            metrics.weight_cost({"input": 1_000_000}, "claude-opus-4-8"), 5.0)
        self.assertAlmostEqual(
            metrics.weight_cost({"input": 1_000_000}, "claude-sonnet-4-6"), 3.0)
        self.assertAlmostEqual(
            metrics.weight_cost({"input": 1_000_000}, "claude-haiku-4-5"), 1.0)

    def test_dated_model_id_matches_prefix(self):
        self.assertAlmostEqual(
            metrics.weight_cost({"input": 1_000_000}, "claude-haiku-4-5-20251001"), 1.0)

    def test_unknown_model_defaults_to_opus(self):
        self.assertAlmostEqual(
            metrics.weight_cost({"input": 1_000_000}, "who-knows"), 5.0)

    def test_cache_read_is_cheap(self):
        # 1M cache-read at opus = 5.0 * 0.1 = 0.5
        self.assertAlmostEqual(
            metrics.weight_cost({"cache_read": 1_000_000}, "claude-opus-4-8"), 0.5)


class TestSummarize(unittest.TestCase):
    def test_main_vs_total_split(self):
        with tempfile.TemporaryDirectory() as d:
            s = metrics.summarize(str(_fixture(Path(d))))
            self.assertEqual(s["turns_main"], 2)
            self.assertEqual(s["main"]["turns"], 2)
            self.assertEqual(s["subagent"]["turns"], 2)        # sonnet + haiku
            self.assertEqual(s["peak_main_context"], 2520)
            self.assertEqual(s["wall_clock_s"], 30.0)
            # total = main + subagent(別計上)
            self.assertEqual(s["total"]["input"], 37)          # 30 + 5 + 2
            self.assertGreater(s["total"]["weighted_cost"], s["main"]["weighted_cost"])
            self.assertEqual(s["guarantee"], {"main": "hard", "subagent": "best_effort"})

    def test_main_weighted_cost_value(self):
        with tempfile.TemporaryDirectory() as d:
            s = metrics.summarize(str(_fixture(Path(d))))
            # (30*5 + 300*25 + 3000*5*0.1 + 500*5*1.25)/1e6 = 12275/1e6
            self.assertAlmostEqual(s["main"]["weighted_cost"], 0.012275, places=6)


class TestPrice(unittest.TestCase):
    def test_exact_and_prefix(self):
        self.assertEqual(metrics._price("claude-opus-4-8")["input"], 5.0)
        self.assertEqual(metrics._price("claude-sonnet-4-6")["output"], 15.0)
        # a dated model id still resolves by prefix
        self.assertEqual(metrics._price("claude-haiku-4-5-20251001")["input"], 1.0)

    def test_unknown_defaults_to_opus(self):
        self.assertEqual(metrics._price("who-knows"), metrics.PRICES["claude-opus-4-8"])
        self.assertEqual(metrics._price(None), metrics.PRICES["claude-opus-4-8"])


class TestSnapshot(_helpers.ConfigDirTestCase):
    """snapshot() writes metrics.json + appends one ledger row, and stays silent
    (no write, no raise) when there is no transcript."""

    def test_writes_metrics_and_ledger(self):
        with tempfile.TemporaryDirectory() as cwd:
            main = _fixture(Path(cwd))
            sid = "sess-x"
            metrics.snapshot({"cwd": cwd, "session_id": sid,
                              "transcript_path": str(main)})
            mfile = common.session_state_dir(cwd, sid) / common.STATE_METRICS
            self.assertTrue(mfile.exists())
            data = json.loads(mfile.read_text())
            self.assertEqual(data["session_id"], sid)
            self.assertIn("main", data)
            ledger = common.shared_state_dir(cwd) / common.STATE_METRICS_LEDGER
            rows = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["session_id"], sid)

    def test_no_transcript_is_silent(self):
        with tempfile.TemporaryDirectory() as cwd:
            sid = "sess-y"
            metrics.snapshot({"cwd": cwd, "session_id": sid,
                              "transcript_path": str(Path(cwd) / "nope.jsonl")})
            self.assertFalse(
                (common.session_state_dir(cwd, sid) / common.STATE_METRICS).exists())
            # no transcript -> no journal row either
            self.assertFalse(
                (common.shared_state_dir(cwd) / common.STATE_JOURNAL).exists())


class TestJournal(_helpers.ConfigDirTestCase):
    """session-journal: capture-only Stop spine riding snapshot()."""

    def test_writes_one_row_with_schema(self):
        with tempfile.TemporaryDirectory() as cwd:
            main = _fixture(Path(cwd))
            sid = "sess-j"
            metrics.snapshot({"cwd": cwd, "session_id": sid,
                              "transcript_path": str(main)})
            jf = common.shared_state_dir(cwd) / common.STATE_JOURNAL
            self.assertTrue(jf.exists())
            rows = [json.loads(ln) for ln in jf.read_text().splitlines() if ln.strip()]
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["session_id"], sid)
            self.assertEqual(r["ts"], "2026-06-15T09:00:30.000Z")  # transcript 由来
            self.assertEqual(r["peak_main_context"], 2520)
            # non-git tempdir -> branch/head None, verified_state None, n_changed 0
            self.assertIsNone(r["branch"])
            self.assertIsNone(r["verified_state"])
            self.assertEqual(r["n_changed"], 0)

    def test_journal_failure_never_breaks_snapshot(self):
        with tempfile.TemporaryDirectory() as cwd:
            main = _fixture(Path(cwd))
            sid = "sess-k"
            orig = common.append_jsonl_capped
            common.append_jsonl_capped = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                metrics.snapshot({"cwd": cwd, "session_id": sid,
                                  "transcript_path": str(main)})  # must not raise
            finally:
                common.append_jsonl_capped = orig
            # metrics.json still written despite the journal blowing up
            self.assertTrue(
                (common.session_state_dir(cwd, sid) / common.STATE_METRICS).exists())


class TestAppendCapped(unittest.TestCase):
    def test_ring_caps_to_last_n(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.jsonl"
            for i in range(10):
                common.append_jsonl_capped(p, {"i": i}, cap=3)
            rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
            self.assertEqual([r["i"] for r in rows], [7, 8, 9])  # last 3 only

    def test_first_write_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.jsonl"
            common.append_jsonl_capped(p, {"i": 0}, cap=5)
            self.assertEqual(p.read_text().strip(), '{"i": 0}')


class TestLastContext(unittest.TestCase):
    """last_context は履歴最大(peak)でなく最新ターンの文脈圧(/compact 後の誤警告対策)。"""

    def test_last_tracks_latest_turn_not_peak(self):
        agg = metrics._new_agg()
        # 古い大ターン → 新しい小ターン(/compact 後を模す)。ts の辞書順で新旧を決める。
        metrics._add_usage(agg, "m", {"input_tokens": 300_000}, "2026-06-18T01:00:00Z")
        metrics._add_usage(agg, "m", {"input_tokens": 40_000}, "2026-06-18T02:00:00Z")
        self.assertEqual(agg["peak_context"], 300_000)
        self.assertEqual(agg["last_context"], 40_000)

    def test_out_of_order_does_not_overwrite_last(self):
        agg = metrics._new_agg()
        metrics._add_usage(agg, "m", {"input_tokens": 40_000}, "2026-06-18T02:00:00Z")
        metrics._add_usage(agg, "m", {"input_tokens": 300_000}, "2026-06-18T01:00:00Z")  # 古い ts
        self.assertEqual(agg["last_context"], 40_000)  # 最新 ts のターンを維持


if __name__ == "__main__":
    unittest.main()
