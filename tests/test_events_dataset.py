# -*- coding: utf-8 -*-
"""事件流格式与算法数据集：CSV 往返、旧制表符兼容、讲法码归因。"""
import io
import os
import shutil
import tempfile
import unittest

from kaoyan_mcp import events, learn


def _tmp():
    d = tempfile.mkdtemp(prefix="kaoyan-events-")
    return os.path.join(d, "分析", "画像事件流.csv"), d


class StreamFormatTest(unittest.TestCase):
    def test_round_trip_keeps_commas_and_bom(self):
        path, tmp = _tmp()
        try:
            events.append_event(path, verdict="✅", event="微验收", atom="GL-02-001",
                                subject="概率论", chapter="第02讲", mode="CM-13",
                                note="反解、绝对值，区间映射")
            with io.open(path, "rb") as f:
                raw = f.read()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            got = events.load_events(path)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].note, "反解、绝对值，区间映射")
            self.assertEqual(got[0].verdict, "✅")
            self.assertEqual(got[0].atom, "GL-02-001")
            self.assertEqual(got[0].mode, "CM-13")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reads_legacy_tab_separated_stream(self):
        path, tmp = _tmp()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write("\t".join(events.COLUMNS) + "\n")
                f.write("2026-09-13\t—\t回填\t概率论\t第02讲\tGL-02-001\tCM-13"
                        "\t首测\t❌\t\t方向反\n")
            got = events.load_events(path)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].mode, "CM-13")
            self.assertEqual(got[0].note, "方向反")
            self.assertTrue(got[0].judged)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TeachAttributionTest(unittest.TestCase):
    def test_teach_code_inherits_to_next_attempt(self):
        evs = [events.Event(**dict(event="微验收", atom="A", verdict="✅")),
               events.Event(**dict(event="直讲", atom="A", verdict="", teach="TH-03")),
               events.Event(**dict(event="微验收", atom="A", verdict="❌"))]
        rows = events.to_dataset(evs)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["teach"], "")
        self.assertEqual(rows[1]["teach"], "TH-03")
        self.assertEqual(rows[1]["teach_dt"], 0.0)

    def test_teach_beyond_window_is_not_credited(self):
        evs = [events.Event(**dict(event="直讲", atom="A", verdict="",
                                   teach="TH-03", date="2026-01-01")),
               events.Event(**dict(event="微验收", atom="A", verdict="✅",
                                   date="2026-03-01"))]
        rows = events.to_dataset(evs)
        self.assertEqual(rows[0]["teach"], "TH-03")
        self.assertGreater(rows[0]["teach_dt"], 10)
        self.assertEqual(learn.teach_rank(rows, window_days=10), [])

    def test_teach_rank_counts_attempts(self):
        rows = [dict(atom="A", y=1, teach="TH-03", teach_dt=0.0),
                dict(atom="A", y=0, teach="TH-03", teach_dt=1.0),
                dict(atom="B", y=1, teach="TH-06", teach_dt=0.0)]
        out = dict((r["teach"], r) for r in learn.teach_rank(rows))
        self.assertEqual(out["TH-03"]["n"], 2)
        self.assertEqual(out["TH-03"]["ok"], 1)
        self.assertAlmostEqual(out["TH-03"]["p"], 0.5)
        self.assertEqual(out["TH-06"]["p"], 1.0)
        self.assertTrue(out["TH-03"]["explore"])


if __name__ == "__main__":
    unittest.main()

