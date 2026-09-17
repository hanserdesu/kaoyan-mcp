# -*- coding: utf-8 -*-
"""测试辅助：临时工作区（复制示例包，绝不碰真实数据）。"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import warnings

warnings.simplefilter("ignore", ResourceWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
DEMO = os.path.join(PROJECT, "examples", "demo-workspace")

if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from kaoyan_mcp.config import load_workspace  # noqa: E402


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def demo_workspace():
    return load_workspace(root=DEMO, cwd=PROJECT)


def temp_workspace():
    """复制一份示例工作区到临时目录——写测试只动副本。"""
    tmp = tempfile.mkdtemp(prefix="kaoyan-test-")
    dst = os.path.join(tmp, "demo")
    shutil.copytree(DEMO, dst)
    return load_workspace(root=dst, cwd=PROJECT), tmp

