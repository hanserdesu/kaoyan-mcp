# -*- coding: utf-8 -*-
"""章节文件解析：把 markdown 章节文件读成 原子（A 表）/ 组合（C 表）。

格式契约（与既有工作区一致，v2 规格）::

    | ID | 原子知识点 | 档 | 得分范式 | 出处 | 状态 | 证据 / 备注 |
    | GL-02-001 | 分布函数法 | S | M-构造 | §2.4@p57 | ⚠️ | 【实测 2026-09-13】... |

    | ID | 组合模式 | 原子链 | 题目形态 | 断点 | 真题锚点 |
    | C-GL-02-01 | C2 多考点串联 | GL-02-001 → GL-02-002 | ... | ... | 2015 真题 |

设计口径：**内容驱动取字段**，不按列号硬取——单元格里混进裸竖线时索引仍可用，
由质量门禁去报这一条。
"""
from __future__ import annotations

import os
import re

from ..config import guess_kind, guess_paradigm
from ..fsutil import read_lines

UNIT = r"(?:\d{2}|AP)"
ATOM_ROW_RE = re.compile(r"^\|\s*([A-Z]{2}-" + UNIT + r"-\d{3})\s*\|")
COMBO_ROW_RE = re.compile(r"^\|\s*(C-[A-Z]{2}-" + UNIT + r"-\d{2})\s*\|")
ATOM_ID_ANY_RE = re.compile(r"\b([A-Z]{2}-" + UNIT + r"-\d{3})\b")
COMBO_ID_RE = re.compile(r"^C-([A-Z]{2})-(" + UNIT + r")-(\d{2})$")
ATOM_ID_RE = re.compile(r"^([A-Z]{2})-(" + UNIT + r")-(\d{3})$")
COMBO_MODE_RE = re.compile(r"^\s*(C[1-7])\b")

TIER_CELL = re.compile(r"^(S|A|B)$")
PARADIGM_CELL = re.compile(r"^[MS]-\S+$")
STATUS_CELL = re.compile(r"^(✅|⚠️|❌|⬜)$")
SRC_CELL = re.compile(r"@p\d+")
PAGE_RE = re.compile(r"@p(\d+)")
SEC_RE = re.compile(r"§([^@|\s]+)")

STATUSES = ("✅", "⚠️", "❌", "⬜")


def cells(line):
    return [p.strip() for p in line.strip().strip("|").split("|")]


class Atom(object):
    __slots__ = ("id", "name", "tier", "paradigm", "source", "status",
                 "evidence", "subject", "chapter", "file", "line", "ncell")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def pages(self):
        return [int(x) for x in PAGE_RE.findall(self.source or "")]

    @property
    def section(self):
        m = SEC_RE.search(self.source or "")
        return m.group(1) if m else ""

    def as_dict(self):
        d = {k: getattr(self, k) for k in self.__slots__}
        d["pages"] = self.pages
        return d

    def __repr__(self):
        return "<Atom %s %s %s>" % (self.id, self.tier, self.status)


class Combo(object):
    __slots__ = ("id", "mode", "chain", "shape", "breakpoint", "anchor",
                 "subject", "chapter", "file", "line", "ncell")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "<Combo %s %s>" % (self.id, self.mode)


class Chapter(object):
    def __init__(self, path, subject, filename):
        self.path = path
        self.subject = subject
        self.filename = filename
        self.title = chapter_title(filename)
        self.atoms = []
        self.combos = []
        self.header = []
        self.has_void_note = False
        self.book_hint = ""

    @property
    def rel(self):
        return "%s/%s" % (self.subject, self.filename)

    def as_dict(self, with_rows=True):
        d = {
            "subject": self.subject,
            "chapter": self.title,
            "file": self.filename,
            "atoms": len(self.atoms),
            "combos": len(self.combos),
            "tiers": tier_counts(self.atoms),
            "status": status_counts(self.atoms),
        }
        if with_rows:
            d["atom_rows"] = [a.as_dict() for a in self.atoms]
            d["combo_rows"] = [c.as_dict() for c in self.combos]
        return d


def chapter_title(filename):
    base = filename[:-3] if filename.endswith(".md") else filename
    parts = base.split("_", 1)
    return parts[1] if len(parts) > 1 else base


def tier_counts(atoms):
    out = {}
    for a in atoms:
        out[a.tier or "?"] = out.get(a.tier or "?", 0) + 1
    return out


def status_counts(atoms):
    out = dict((s, 0) for s in STATUSES)
    for a in atoms:
        if a.status in out:
            out[a.status] += 1
    return out


def parse_chapter(path, subject):
    filename = os.path.basename(path)
    ch = Chapter(path, subject, filename)
    lines = read_lines(path)
    for i, ln in enumerate(lines, 1):
        if ln.startswith(">"):
            ch.header.append(ln)
            if "作废" in ln:
                ch.has_void_note = True
            continue
        c = cells(ln)
        if ATOM_ROW_RE.match(ln) and len(c) >= 6:
            tier = next((x for x in c if TIER_CELL.match(x)), "?")
            par = next((x for x in c if PARADIGM_CELL.match(x)), "?")
            src = next((x for x in c if SRC_CELL.search(x)), "?")
            status = next((x for x in c if STATUS_CELL.match(x)), "?")
            name = c[1] if len(c) > 1 else ""
            evidence = c[-1] if c[-1] not in (tier, par, src, status) else ""
            ch.atoms.append(Atom(id=c[0], name=name, tier=tier, paradigm=par,
                                 source=src, status=status, evidence=evidence,
                                 subject=subject, chapter=ch.title,
                                 file=filename, line=i, ncell=len(c)))
            continue
        if COMBO_ROW_RE.match(ln) and len(c) >= 6:
            m = COMBO_MODE_RE.match(c[1] if len(c) > 1 else "")
            chain, seen = [], set()
            for x in ATOM_ID_ANY_RE.findall(c[2] if len(c) > 2 else ""):
                if x not in seen:
                    seen.add(x)
                    chain.append(x)
            ch.combos.append(Combo(id=c[0], mode=m.group(1) if m else "?",
                                   chain=chain, shape=c[3] if len(c) > 3 else "",
                                   breakpoint=c[4] if len(c) > 4 else "",
                                   anchor=" | ".join(c[5:]) if len(c) > 5 else "",
                                   subject=subject, chapter=ch.title,
                                   file=filename, line=i, ncell=len(c)))
    for ln in lines:
        if "作废" in ln:
            ch.has_void_note = True
    return ch


def iter_chapter_files(kb_dir):
    """遍历知识库章节文件（跳过下划线开头的内部文件）。"""
    out = []
    if not os.path.isdir(kb_dir):
        return out
    for subj in sorted(os.listdir(kb_dir)):
        d = os.path.join(kb_dir, subj)
        if subj.startswith("_") or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md") and not fn.startswith("_"):
                out.append((subj, os.path.join(d, fn)))
    return out


def load_chapters(kb_dir):
    return [parse_chapter(p, s) for s, p in iter_chapter_files(kb_dir)]


def detect_book(chapter, books):
    """从章节头部/正文里找出这本书对应 pack.books 的哪个条目。"""
    if not books:
        return ""
    blob = "\n".join(chapter.header) or ""
    for name in books:
        if name in blob:
            return name
    return ""


def subject_of_chapter_filename(filename):
    parts = filename.split("_", 1)[0]
    return parts

