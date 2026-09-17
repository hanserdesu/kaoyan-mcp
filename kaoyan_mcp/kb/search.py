# -*- coding: utf-8 -*-
"""一次命中检索：在事实源（章节文件）里按相关度找回原子/组合/章节。

设计取舍：知识库规模在千行量级，全量解析约 0.2 s——与其维护缓存与失效逻辑，
不如每次现算，永远与磁盘一致。数据量真的涨上去，再加签名缓存也不迟。
"""
from __future__ import annotations

import re

from .parse import ATOM_ID_RE, load_chapters

TERM_SPLIT = re.compile(r"[\s,，、;；]+")


def _terms(query):
    return [t for t in TERM_SPLIT.split((query or "").strip()) if t]


def _norm(s):
    return (s or "").lower()


class Hit(object):
    def __init__(self, kind, score, subject, chapter, file, line, title,
                 atom=None, snippet="", tier="", status="", source=""):
        self.kind = kind
        self.score = score
        self.subject = subject
        self.chapter = chapter
        self.file = file
        self.line = line
        self.title = title
        self.atom = atom
        self.snippet = snippet
        self.tier = tier
        self.status = status
        self.source = source

    def as_dict(self):
        return dict(kind=self.kind, score=round(self.score, 1), subject=self.subject,
                    chapter=self.chapter, file=self.file, line=self.line,
                    title=self.title, atom=self.atom, tier=self.tier,
                    status=self.status, source=self.source, snippet=self.snippet)

    def __str__(self):
        loc = "%s/%s:%s" % (self.subject, self.file, self.line)
        tag = ""
        if self.atom:
            tag = " [%s %s %s]" % (self.atom, self.tier or "-", self.status or "-")
        return "%-5.0f %s%s %s" % (self.score, loc, tag, self.title)


def search(ws, query, subject=None, tier=None, status=None, kind="all", limit=20):
    """按关键词检索知识库。多个词之间是 AND 关系。"""
    terms = _terms(query)
    chapters = load_chapters(ws.kb_dir)
    hits = []
    for ch in chapters:
        if subject and ch.subject != subject:
            continue
        head = _norm(ch.title + " " + ch.filename)
        head_hit = all(t.lower() in head for t in terms) if terms else False
        if head_hit and kind in ("all", "chapter"):
            hits.append(Hit("chapter", 60.0, ch.subject, ch.title, ch.filename,
                            1, ch.title, snippet="%d 原子 / %d 组合"
                            % (len(ch.atoms), len(ch.combos))))
        for a in ch.atoms:
            if tier and a.tier != tier:
                continue
            if status and a.status != status:
                continue
            name = _norm(a.name)
            ev = _norm(a.evidence)
            src = _norm(a.source)
            blob = name + " " + ev + " " + src
            if terms and not all(t.lower() in blob for t in terms):
                continue
            score = 0.0
            if terms:
                q = " ".join(terms).lower()
                if q == (a.id or "").lower():
                    score += 1000.0
                elif len(terms) == 1 and (a.id or "").lower().startswith(terms[0].lower()):
                    score += 500.0
                for t in terms:
                    tl = t.lower()
                    if tl in name:
                        score += 100.0 + 5.0 * len(tl)
                    if tl in (a.id or "").lower():
                        score += 200.0
                    if tl in ev:
                        score += 20.0
                    if tl in src:
                        score += 15.0
            if a.tier == "S":
                score += 6.0
            elif a.tier == "A":
                score += 3.0
            if a.status in ("⚠️", "❌"):
                score += 4.0
            snippet = a.name if a.name else ""
            if a.evidence:
                snippet = (snippet + " ｜ " + a.evidence)[:220]
            hits.append(Hit("atom", score, ch.subject, ch.title, a.file, a.line,
                            a.name, atom=a.id, snippet=snippet, tier=a.tier,
                            status=a.status, source=a.source))
    hits.sort(key=lambda h: (-h.score, h.subject, h.file, h.line))
    return hits[:limit] if limit else hits


def find_atom(ws, atom_id, chapters=None):
    atom_id = (atom_id or "").strip().upper()
    if not ATOM_ID_RE.match(atom_id):
        return None
    for ch in (chapters if chapters is not None else load_chapters(ws.kb_dir)):
        for a in ch.atoms:
            if a.id.upper() == atom_id:
                return ch, a
    return None


def atom_index(ws, subject=None, tier=None, status=None):
    """全库原子清单（可按科目/档/状态过滤）。"""
    out = []
    for ch in load_chapters(ws.kb_dir):
        if subject and ch.subject != subject:
            continue
        for a in ch.atoms:
            if tier and a.tier != tier:
                continue
            if status and a.status != status:
                continue
            out.append(a)
    return out

