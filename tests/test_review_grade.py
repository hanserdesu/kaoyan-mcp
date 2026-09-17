# -*- coding: utf-8 -*-
import os
import shutil
import unittest

from tests.util import temp_workspace
from kaoyan_mcp import review


class LadderTest(unittest.TestCase):
    def test_ladder_rules(self):
        self.assertEqual(review.next_stage("1d"), "2d")
        self.assertEqual(review.next_stage("2d"), "4d")
        self.assertEqual(review.next_stage("60d"), "60d")
        self.assertEqual(review.grade_stage("4d", "✅"), "7d")
        self.assertEqual(review.grade_stage("4d", "⚠️"), "4d")
        self.assertEqual(review.grade_stage("15d", "❌"), "1d")

    def test_verdict_aliases(self):
        self.assertEqual(review.normalize_verdict("流畅"), "✅")
        self.assertEqual(review.normalize_verdict("半错"), "⚠️")
        self.assertEqual(review.normalize_verdict("失败"), "❌")


class PlanTest(unittest.TestCase):
    def test_due_order_is_layer_then_tier(self):
        ws, tmp = temp_workspace()
        try:
            plan = review.plan(ws, today="2026-09-17", quota=3)
            self.assertEqual(plan["total_due"], 3)
            self.assertEqual([it.atom for it in plan["due"]],
                             ["SY-01-002", "LT-01-002", "SY-01-003"])
            self.assertEqual(plan["due"][0].layer, "A")
            self.assertEqual(plan["due"][1].tier, "S")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_quota_defers_rest(self):
        ws, tmp = temp_workspace()
        try:
            plan = review.plan(ws, today="2026-09-17", quota=2)
            self.assertEqual(len(plan["due"]), 2)
            self.assertEqual(len(plan["deferred"]), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GradeTest(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        ws, tmp = temp_workspace()
        try:
            before = open(ws.queue_file, encoding="utf-8").read()
            res = review.grade(ws, "SY-01-002", "✅", note="演练", write=False)
            self.assertTrue(res["ok"])
            self.assertEqual(res["queue"][0]["stage"], "4d")
            self.assertEqual(before, open(ws.queue_file, encoding="utf-8").read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_grade_writes_queue_chapter_index_and_stream(self):
        ws, tmp = temp_workspace()
        try:
            res = review.grade(ws, "SY-01-002", "❌", note="加减里替换错",
                               mode="CM-02", teach="TH-02", today="2026-09-17")
            self.assertTrue(res["ok"])
            q = open(ws.queue_file, encoding="utf-8").read()
            self.assertIn("2026-09-18", q)
            self.assertIn("| 1d |", q)
            self.assertIn("复习未过", q)
            chapter = open(os.path.join(ws.kb_dir, "示例数学", "第01讲_极限基础.md"),
                           encoding="utf-8").read()
            self.assertIn("【实测 2026-09-17 未过】", chapter)
            stream = open(ws.stream_file, encoding="utf-8").read()
            self.assertIn("SY-01-002", stream.splitlines()[-1])
            self.assertIn("❌", stream.splitlines()[-1])
            # 原状态就是 ⚠️，失败后仍是 ⚠️——账目不变，但对账必须仍然通过
            from kaoyan_mcp.kb import quality
            self.assertTrue(quality.check_workspace(ws).ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_change_syncs_index(self):
        ws, tmp = temp_workspace()
        try:
            res = review.grade(ws, "SY-01-003", "❌", note="左右极限没算",
                               today="2026-09-17")
            self.assertTrue(res["ok"])
            chapter = open(os.path.join(ws.kb_dir, "示例数学", "第01讲_极限基础.md"),
                           encoding="utf-8").read()
            self.assertIn("| SY-01-003 | 连续性：左右极限存在且等于函数值 | A | M-概念 | §1.5@p15 | ⚠️ |",
                          chapter)
            index = open(ws.kb_index_file, encoding="utf-8").read()
            self.assertIn("| 示例数学·第01讲 | 1 | 2 | 0 | 2 |", index)
            self.assertIn("| 示例数学 | 2 | 10 | 2 | 2 | 0 | 6 |", index)
            from kaoyan_mcp.kb import quality
            self.assertTrue(quality.check_workspace(ws).ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_flowing_pass_advances_stage(self):
        ws, tmp = temp_workspace()
        try:
            review.grade(ws, "LT-01-002", "流畅", note="公式记对了", today="2026-09-17")
            q = open(ws.queue_file, encoding="utf-8").read()
            self.assertIn("| 4d |", q)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
