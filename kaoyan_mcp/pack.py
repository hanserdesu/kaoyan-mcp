# -*- coding: utf-8 -*-
"""内容包：发现、安装、校验。

一个内容包 = 一个目录：pack.json（清单）+ kb/（知识库）。
本仓库自带的包放在 <仓库根>/packs/<名字>/；装进工作区后位于 <工作区>/packs/<名字>/，
由 kaoyan.config.json 的 pack 字段指名。引擎只认这份契约，不关心包是谁写的。
"""
from __future__ import annotations

import json
import os
import shutil

from .config import (CONFIG_FILENAME, DEFAULT_DIRS, PACK_FILENAME, PACK_SCHEMA_VERSION,
                     ConfigError)


def repo_root():
    """仓库根 = kaoyan_mcp 的上一级（clone 与 pip install -e 都成立）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def packs_root(explicit=None):
    return os.path.abspath(explicit) if explicit else os.path.join(repo_root(), "packs")


def discover(explicit=None):
    """列出 packs/ 下所有能读到的包（坏清单不抛错，标记 _error 返回）。"""
    root = packs_root(explicit)
    found = []
    if not os.path.isdir(root):
        return found
    for name in sorted(os.listdir(root)):
        mf = os.path.join(root, name, PACK_FILENAME)
        if not os.path.isfile(mf):
            continue
        data = {}
        try:
            with open(mf, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {"_error": "pack.json 必须是 JSON 对象"}
        except ValueError as exc:
            data = {"_error": str(exc)}
        data.setdefault("name", name)
        data["_dir"] = os.path.join(root, name)
        found.append(data)
    return found


def find(name, explicit=None):
    for data in discover(explicit):
        if data.get("name") == name or os.path.basename(data["_dir"]) == name:
            return data
    hint = ""
    if not os.path.isdir(packs_root(explicit)):
        hint = ("（没找到 %s 目录——内容包随仓库分发，"
                "请先 clone 仓库再用 pip install -e . 安装）" % packs_root(explicit))
    raise ConfigError("没有名为 %s 的内容包%s" % (name, hint))


def describe(data):
    sv = data.get("schema_version")
    exam = data.get("exam") or {}
    subjects = data.get("subjects") or {}
    books = data.get("books") or {}
    lines = ["- %s ｜ %s" % (data.get("name"), data.get("title") or "（未命名内容包）")]
    if exam:
        lines.append("  考试：%s %s" % (exam.get("code") or "", exam.get("label") or ""))
    lines.append("  科目 %d ｜ 教材 %d ｜ schema_version=%s（本引擎支持 %d）"
                 % (len(subjects), len(books), sv if sv is not None else "未声明",
                    PACK_SCHEMA_VERSION))
    if data.get("_error"):
        lines.append("  ⚠️ 清单读取失败：%s" % data["_error"])
    elif isinstance(sv, int) and sv > PACK_SCHEMA_VERSION:
        lines.append("  ⚠️ 该包声明的 schema_version 高于本引擎支持的版本，请升级 kaoyan-mcp。")
    return "\n".join(lines)


def list_text(explicit=None):
    found = discover(explicit)
    if not found:
        return ("%s 下没有内容包。内容包随仓库分发：clone 仓库后 packs/ 里就是全部自带的包。"
                % packs_root(explicit))
    return "自带内容包（%s）：\n\n%s" % (packs_root(explicit),
                                   "\n".join(describe(d) for d in found))


def install(name, into, explicit=None, force=False):
    """把自带包复制进工作区 packs/<名字>/，并把 kaoyan.config.json 指过去。

    装包会改变知识库的来源（pack.json 声明了 kb_dir 就由包提供知识库），
    所以工作区里已经有自己写的章节时默认拦住，要覆盖得显式加 force。
    """
    data = find(name, explicit)
    if data.get("_error"):
        raise ConfigError("内容包清单不可用：%s" % data["_error"])
    into = os.path.abspath(into)
    if not os.path.isdir(into):
        os.makedirs(into)
    cfg_path = os.path.join(into, CONFIG_FILENAME)
    cfg = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f) or {}
        except ValueError:
            cfg = {}
    own_kb = os.path.join(into, DEFAULT_DIRS["kb"])
    if not force and "kb" not in (cfg.get("dirs") or {}) and _has_own_chapters(own_kb):
        raise ConfigError(
            "工作区里已经有自己写的章节：%s\n"
            "装包会让内容包的知识库接管「知识库」目录。先确认这是你要的，"
            "或加 --force 覆盖，或在 kaoyan.config.json 里用 dirs.kb 指定自己的知识库目录。"
            % own_kb)
    target = os.path.join(into, "packs", name)
    if os.path.isdir(target) and not force:
        raise ConfigError("目标已存在：%s（加 --force 覆盖）" % target)
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(data["_dir"], target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cfg["pack"] = name
    cfg.setdefault("deliver", {"policy": "auto", "printer": "", "paper": "A4", "copies": 1})
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    files = 0
    for _root, _dirs, names in os.walk(target):
        files += len([n for n in names if not n.endswith(".pyc")])
    return ("内容包 %s 已装入 %s（%d 个文件）\n"
            "kaoyan.config.json 已指向 pack=%s；知识库改由包提供（%s）。\n"
            "接着跑 kaoyan-mcp validate --root %s"
            % (name, target, files, name,
               os.path.join(target, data.get("kb_dir") or ""), into))


def _has_own_chapters(kb_dir):
    """知识库目录里有没有「自己写的章节」（脚手架 _INDEX.md / _原子索引.md 不算）。"""
    if not os.path.isdir(kb_dir):
        return False
    for root, dirs, names in os.walk(kb_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            if n.endswith(".md") and n not in ("_INDEX.md", "_原子索引.md"):
                return True
    return False


def verify_text(name, explicit=None):
    """对一个自带包跑质量门禁（引擎只读，不写盘）。"""
    from .config import Workspace
    from . import tools as tools_mod
    data = find(name, explicit)
    ws = Workspace(data["_dir"])
    ok, payload = tools_mod.kb_validate(ws, {})
    return ok, payload
