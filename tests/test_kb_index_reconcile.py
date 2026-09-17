# -*- coding: utf-8 -*-
"""_INDEX 对账：缺行自动补、幂等、新工作区脚手架能过门禁。"""
import os
import shutil
import tempfile
import unittest

from tests.util import read, temp_workspace, write
from kaoyan_mcp import tools
from kaoyan_mcp.config import load_workspace
from kaoyan_mcp.kb import quality, stats


class ReconcileTest(unittest.TestCase):
    def test_missing_row_is_restored_then_idempotent(self):
        ws, tmp = temp_workspace()
        try:
            path = ws.kb_index_file
            row = "| 示例数学·第02讲 | 1 | 0 | 0 | 4 |"
            text = read(path)
            self.assertIn(row, text)
            write(path, text.replace(row + "\n", ""))
            self.assertIn("index.row_missing", quality.check_workspace(ws).codes())

            ok, _msg, changed = stats.rewrite_index(ws)
            self.assertTrue(ok)
            self.assertEqual(changed, 1)
            self.assertIn(row, read(path))
            self.assertNotIn("index.row_missing", quality.check_workspace(ws).codes())
            self.assertEqual(stats.rewrite_index(ws)[2], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_table_is_present_not_missing(self):
        ws, tmp = temp_workspace()
        try:
            head = read(ws.kb_index_file).split("| 科目·章节 | ✅ | ⚠️ | ❌ | ⬜ |")[0]
            write(ws.kb_index_file,
                  head + "| 科目·章节 | ✅ | ⚠️ | ❌ | ⬜ |\n|---|---|---|---|---|\n")
            codes = quality.check_workspace(ws).codes()
            self.assertNotIn("index.missing", codes)
            self.assertIn("index.row_missing", codes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class InitTest(unittest.TestCase):
    def test_fresh_workspace_validates_clean(self):
        tmp = tempfile.mkdtemp(prefix="kaoyan-init-")
        try:
            root = os.path.join(tmp, "ws")
            tools.init_workspace(root, pack="demo")
            ws = load_workspace(root=root, cwd=root)
            rep = quality.check_workspace(ws)
            self.assertTrue(rep.ok, rep.text())
            self.assertEqual([i.code for i in rep.warns], [])
            self.assertEqual(stats.rewrite_index(ws)[2], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

