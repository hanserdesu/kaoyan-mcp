# -*- coding: utf-8 -*-
"""内容包：发现、装入、门禁，以及"以后换版"的 schema_version 兼容位。"""
import json
import os
import shutil
import tempfile
import unittest

from tests.util import read, write
from kaoyan_mcp import __main__ as cli
from kaoyan_mcp import pack, tools
from kaoyan_mcp.config import ConfigError, load_workspace


class DiscoverTest(unittest.TestCase):
    def test_bundled_pack_is_discoverable(self):
        found = pack.discover()
        row = [d for d in found if d.get("name") == "11408"]
        self.assertEqual(len(row), 1)
        row = row[0]
        self.assertNotIn("_error", row)
        self.assertEqual(row.get("schema_version"), 1)
        self.assertTrue(os.path.isdir(os.path.join(row["_dir"], row["kb_dir"])))
        self.assertIn("schema_version=1", pack.describe(row))

    def test_missing_packs_dir_is_empty_not_error(self):
        self.assertEqual(pack.discover(os.path.join(tempfile.gettempdir(), "no-such-packs")), [])

    def test_unknown_pack_is_config_error(self):
        with self.assertRaises(ConfigError):
            pack.find("不存在的包")


class InstallTest(unittest.TestCase):
    def test_install_into_fresh_workspace(self):
        tmp = tempfile.mkdtemp(prefix="kaoyan-pack-")
        try:
            root = os.path.join(tmp, "ws")
            tools.init_workspace(root, pack="demo")
            msg = pack.install("11408", root)
            self.assertIn("11408", msg)
            cfg = json.loads(read(os.path.join(root, "kaoyan.config.json")))
            self.assertEqual(cfg["pack"], "11408")

            ws = load_workspace(root=root, cwd=root)
            expect = os.path.join(root, "packs", "11408", "kb")
            self.assertEqual(os.path.normcase(ws.kb_dir), os.path.normcase(expect))
            self.assertTrue(os.path.isfile(ws.kb_index_file))
            self.assertIn("概率论", ws.subjects)

            with self.assertRaises(ConfigError):   # 同名包默认不覆盖
                pack.install("11408", root)
            pack.install("11408", root, force=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_install_refuses_workspace_with_own_chapters(self):
        tmp = tempfile.mkdtemp(prefix="kaoyan-pack-")
        try:
            root = os.path.join(tmp, "ws")
            tools.init_workspace(root, pack="demo")
            write(os.path.join(root, "知识库", "第01讲_自建.md"), "# 自建章节\n")
            with self.assertRaises(ConfigError) as cm:
                pack.install("11408", root)
            self.assertIn("已经有自己写的章节", str(cm.exception))

            cfg_path = os.path.join(root, "kaoyan.config.json")
            cfg = json.loads(read(cfg_path))
            cfg["dirs"] = {"kb": "我的知识库"}   # 自己指定知识库目录后放行
            write(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2))
            pack.install("11408", root)
            self.assertEqual(load_workspace(root=root, cwd=root).dirs["kb"], "我的知识库")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SchemaVersionTest(unittest.TestCase):
    def _future_workspace(self):
        tmp = tempfile.mkdtemp(prefix="kaoyan-pack-")
        root = os.path.join(tmp, "ws")
        pdir = os.path.join(root, "packs", "future")
        os.makedirs(os.path.join(pdir, "kb"))
        write(os.path.join(pdir, "pack.json"), json.dumps(
            {"schema_version": 99, "name": "future", "title": "未来版本包", "kb_dir": "kb"},
            ensure_ascii=False))
        write(os.path.join(root, "kaoyan.config.json"),
              json.dumps({"pack": "future"}, ensure_ascii=False))
        return root, tmp

    def test_too_new_pack_fails_loudly_instead_of_guessing(self):
        root, tmp = self._future_workspace()
        try:
            with self.assertRaises(ConfigError) as cm:
                load_workspace(root=root, cwd=root)
            self.assertIn("schema_version", str(cm.exception))
            data = pack.find("future", explicit=os.path.join(root, "packs"))
            self.assertIn("高于本引擎支持", pack.describe(data))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class VerifyAndCliTest(unittest.TestCase):
    def test_bundled_pack_passes_gate(self):
        ok, payload = pack.verify_text("11408")
        self.assertTrue(ok, payload)
        self.assertIn("PASS", payload["_text"])

    def test_cli_pack_subcommands(self):
        self.assertEqual(cli.main(["pack", "list"]), 0)
        self.assertEqual(cli.main(["pack", "verify", "11408"]), 0)
        self.assertEqual(cli.main(["pack"]), 2)
        self.assertEqual(cli.main(["pack", "verify", "不存在的包"]), 3)


if __name__ == "__main__":
    unittest.main()
