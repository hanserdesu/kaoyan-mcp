# -*- coding: utf-8 -*-
import os
import shutil
import unittest

from tests.util import demo_workspace, temp_workspace
from kaoyan_mcp.kb import quality
from kaoyan_mcp.kb.parse import load_chapters, parse_chapter


class ParseTest(unittest.TestCase):
    def test_parses_atoms_and_combos(self):
        ws = demo_workspace()
        chapters = load_chapters(ws.kb_dir)
        self.assertEqual(len(chapters), 3)
        by_file = dict((c.filename, c) for c in chapters)
        limit = by_file["第01讲_极限基础.md"]
        self.assertEqual(len(limit.atoms), 5)
        self.assertEqual(len(limit.combos), 2)
        a = limit.atoms[1]
        self.assertEqual(a.id, "SY-01-002")
        self.assertEqual(a.tier, "S")
        self.assertEqual(a.paradigm, "M-计算")
        self.assertEqual(a.status, "⚠️")
        self.assertEqual(a.pages, [9])
        self.assertIn("§1.3", a.source)
        self.assertTrue(a.evidence.startswith("【实测"))

    def test_chapter_title_from_filename(self):
        ws = demo_workspace()
        ch = parse_chapter(os.path.join(ws.kb_dir, "示例数学", "第01讲_极限基础.md"), "示例数学")
        self.assertEqual(ch.title, "极限基础")


class QualityTest(unittest.TestCase):
    def test_demo_workspace_passes(self):
        ws = demo_workspace()
        rep = quality.check_workspace(ws)
        self.assertTrue(rep.ok, rep.text())
        self.assertEqual(rep.stats["atoms"], 15)
        self.assertEqual(rep.stats["combos"], 6)

    def test_detects_planted_violations(self):
        ws, tmp = temp_workspace()
        try:
            path = os.path.join(ws.kb_dir, "示例数学", "第01讲_极限基础.md")
            text = open(path, encoding="utf-8").read()
            text = text.replace(
                "| SY-01-004 | 极限存在的直观意义（逼近而非到达） | B | M-概念 | §1.1@p3 | ⬜ |",
                "| SY-01-004 | 极限存在的直观意义（逼近而非到达） | B | M-概念 | §1.1 | ✅ |")
            text = text.replace(
                "| SY-01-005 | 夹逼准则的适用场景 | A | M-构造 | §1.4@p12 | ⬜ |",
                "| SY-01-005 | 夹逼准则的适用场景 | A | S-构造 | §1.4@p12 | ⚠️ |")
            text = text.replace("SY-01-001 → SY-01-002", "SY-01-001 → ZZ-99-999")
            open(path, "w", encoding="utf-8").write(text)
            rep = quality.check_workspace(ws)
            codes = rep.codes()
            self.assertIn("combo.ref", codes)
            self.assertIn("source.page", codes)
            self.assertIn("paradigm.subject", codes)
            self.assertIn("evidence.missing", codes)
            self.assertFalse(rep.ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_detects_duplicate_atom_id(self):
        ws, tmp = temp_workspace()
        try:
            path = os.path.join(ws.kb_dir, "示例数学", "第02讲_导数入门.md")
            text = open(path, encoding="utf-8").read()
            text = text.replace("| SY-02-003 |", "| SY-01-003 |")
            open(path, "w", encoding="utf-8").write(text)
            rep = quality.check_workspace(ws)
            self.assertIn("id.duplicate", rep.codes())
            self.assertFalse(rep.ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_index_mismatch_is_fail(self):
        ws, tmp = temp_workspace()
        try:
            path = os.path.join(ws.kb_dir, "_INDEX.md")
            text = open(path, encoding="utf-8").read()
            text = text.replace("| 示例数学·第01讲 | 2 | 1 | 0 | 2 |",
                                "| 示例数学·第01讲 | 5 | 0 | 0 | 0 |")
            open(path, "w", encoding="utf-8").write(text)
            rep = quality.check_workspace(ws)
            self.assertIn("index.mismatch", rep.codes())
            self.assertFalse(rep.ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unverified_note_is_not_a_status_leak(self):
        """「已讲未验收」带【实测】时间戳但状态仍该是 ⬜——不该报警；
        真考过却忘改状态，仍然要报。"""
        ws, tmp = temp_workspace()
        try:
            path = os.path.join(ws.kb_dir, "示例数学", "第01讲_极限基础.md")
            head = ("| SY-01-004 | 极限存在的直观意义（逼近而非到达） | B | M-概念 "
                    "| §1.1@p3 | ⬜ | ")
            old = head + "【建模推演】教材开篇的定性说明，未单独考察 |"
            taught = head + "【实测 2026-09-12】已讲未验收 |"
            passed = head + "【实测 2026-09-12】通过 |"
            text = open(path, encoding="utf-8").read()
            self.assertIn(old, text)

            open(path, "w", encoding="utf-8").write(text.replace(old, taught))
            self.assertNotIn("evidence.unjudged", quality.check_workspace(ws).codes())

            open(path, "w", encoding="utf-8").write(text.replace(old, passed))
            self.assertIn("evidence.unjudged", quality.check_workspace(ws).codes())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
