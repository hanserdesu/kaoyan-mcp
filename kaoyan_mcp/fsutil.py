# -*- coding: utf-8 -*-
"""文件读写小工具：统一 UTF-8、容错 BOM、原子写。"""
from __future__ import annotations

import io
import os
import tempfile


def read_text(path, default=None):
    if not os.path.isfile(path):
        return default
    with io.open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def read_lines(path):
    t = read_text(path)
    if t is None:
        return []
    return t.splitlines()


def write_text(path, text, bom=False):
    """原子写：先写临时文件再替换，避免留下半截文件。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    ensure_dir(d)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with io.open(fd, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def append_text(path, text, bom_if_new=False):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    fresh = (not os.path.exists(path)) or os.path.getsize(path) == 0
    enc = "utf-8-sig" if (bom_if_new and fresh) else "utf-8"
    with io.open(path, "a", encoding=enc, newline="") as f:
        f.write(text)


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def rel(root, path):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path

