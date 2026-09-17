# -*- coding: utf-8 -*-
"""知识库质量门禁：把「写得对不对、引用通不通、账对不对得上」变成可执行判据。

门禁分两级：
  FAIL —— 结构契约被破坏（ID 重复、组合引用了不存在的原子、账目对不上……）
  WARN —— 规格建议未满足（S 档未进组合、出处缺页锚、证据缺【实测】标记……）

门禁只读，不修改任何文件；修不修、怎么修由调用方决定。
"""
from __future__ import annotations

import os
import re

from ..fsutil import read_text
from .parse import (ATOM_ID_RE, COMBO_ID_RE, STATUSES, load_chapters)

EVIDENCE_MARK = re.compile(r"【实测")
# 「已讲未验收」「非验收，仅记录」这类写法会同时带【实测】时间戳和 ⬜ 状态，
# 字面上与「考过却忘了改状态」一模一样。判定条件里认下这句免责说明，
# 门禁才不会对着规范写法天天报警。
UNVERIFIED_NOTE = re.compile(r"(未验收|非验收|仅记录|待验收)")
PAGE_RANGE = re.compile(r"^\d+$")
STAT_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*$")
SUMMARY_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*$")
STATUS_HEADER_RE = re.compile(r"^\|\s*科目·章节\s*\|")
SUMMARY_HEADER_RE = re.compile(r"^\|\s*科目\s*\|\s*文件数\s*\|")


class Issue(object):
    def __init__(self, level, code, message, subject="", chapter="", line=0, fix=""):
        self.level = level
        self.code = code
        self.message = message
        self.subject = subject
        self.chapter = chapter
        self.line = line
        self.fix = fix

    def as_dict(self):
        return dict(level=self.level, code=self.code, message=self.message,
                    subject=self.subject, chapter=self.chapter, line=self.line, fix=self.fix)

    def __str__(self):
        loc = ""
        if self.subject:
            loc = " [%s/%s" % (self.subject, self.chapter)
            loc += ":%d]" % self.line if self.line else "]"
        return "%s %s%s %s" % (self.level, self.code, loc, self.message)


class Report(object):
    def __init__(self, issues, stats=None):
        self.issues = issues
        self.stats = stats or {}

    @property
    def fails(self):
        return [i for i in self.issues if i.level == "FAIL"]

    @property
    def warns(self):
        return [i for i in self.issues if i.level == "WARN"]

    @property
    def ok(self):
        return not self.fails

    def codes(self):
        out = {}
        for i in self.issues:
            out[i.code] = out.get(i.code, 0) + 1
        return out

    def as_dict(self):
        return dict(ok=self.ok, fails=len(self.fails), warns=len(self.warns),
                    issues=[i.as_dict() for i in self.issues], stats=self.stats)

    def text(self, limit=200):
        L = []
        for i in self.issues[:limit]:
            L.append(str(i))
        if len(self.issues) > limit:
            L.append("...（还有 %d 条）" % (len(self.issues) - limit))
        L.append("RESULT: %s（FAIL %d / WARN %d）" % ("PASS" if self.ok else "FAIL",
                                                     len(self.fails), len(self.warns)))
        return "\n".join(L)


def _index_status_table(kb_dir, index_name):
    """解析 _INDEX.md 的「状态统计」表：科目·章节 -> [✅,⚠️,❌,⬜]。"""
    path = os.path.join(kb_dir, index_name)
    text = read_text(path)
    if not text:
        return None
    lines = text.splitlines()
    rows = {}
    for ln in lines:
        m = STAT_ROW_RE.match(ln)
        if not m:
            continue
        key = m.group(1).strip()
        if key in ("科目·章节", "科目", "---"):
            continue
        try:
            rows[key] = [int(m.group(i)) for i in range(2, 6)]
        except ValueError:
            continue
    # 「文件在、表头在、一行都还没有」是新工作区的正常状态，不能当成「没找到表」；
    # 只有连表头都没有才算缺失。
    if not rows and not any(STATUS_HEADER_RE.match(ln) for ln in lines):
        return None
    return rows


def _index_summary_table(kb_dir, index_name):
    path = os.path.join(kb_dir, index_name)
    text = read_text(path)
    if not text:
        return None
    lines = text.splitlines()
    rows = {}
    for ln in lines:
        m = SUMMARY_ROW_RE.match(ln)
        if not m:
            continue
        key = m.group(1).strip()
        if key in ("科目", "---"):
            continue
        try:
            rows[key] = [int(m.group(i)) for i in range(2, 8)]
        except ValueError:
            continue
    if not rows and not any(SUMMARY_HEADER_RE.match(ln) for ln in lines):
        return None
    return rows


def index_key(subject, chapter_title, filename):
    """把章节映射到 _INDEX 统计表里的行首键：科目·第NN讲 / 科目·第N章 / 科目·附录。

    键名直接取文件名前缀（原样保留零填充：第01讲 / 第01章 / 附录），
    不做事后归一化——归一会把「第1章」和「第01章」写成两个键。
    """
    head = filename.split("_", 1)[0]
    return "%s·%s" % (subject, head)


def check_workspace(ws, chapters=None, index_check=True):
    """跑全套门禁，返回 Report。"""
    issues = []
    kb_dir = ws.kb_dir
    if not os.path.isdir(kb_dir):
        issues.append(Issue("FAIL", "kb.dir", "知识库目录不存在：%s" % kb_dir))
        return Report(issues)

    chapters = chapters if chapters is not None else load_chapters(kb_dir)
    all_atoms = {}
    dup = set()
    # 第一遍：先把全库原子读进来。组合经常跨讲/跨章引用（第 3 讲引第 4 讲的
    # 基础解系原子、第 3 章引第 4 章的 CIDR 原子），边读边查会把「引用到后面
    # 才读到的章节」误报成断裂。所以原子登记与组合核验必须分两遍走。
    for ch in chapters:
        kind = ws.subject_kind(ch.subject)
        expect = "M" if kind == "math" else "S"
        for a in ch.atoms:
            if a.id in all_atoms:
                dup.add(a.id)
            all_atoms[a.id] = a
            m = ATOM_ID_RE.match(a.id or "")
            if not m:
                issues.append(Issue("FAIL", "id.format", "原子 ID 不符合命名规范：%s" % a.id,
                                    ch.subject, ch.title, a.line))
            if a.tier not in ("S", "A", "B"):
                issues.append(Issue("FAIL", "tier.illegal", "%s 档位非法：%r" % (a.id, a.tier),
                                    ch.subject, ch.title, a.line))
            if not re.match(r"^[MS]-\S+$", a.paradigm or ""):
                issues.append(Issue("FAIL", "paradigm.illegal",
                                    "%s 得分范式非法：%r" % (a.id, a.paradigm),
                                    ch.subject, ch.title, a.line))
            elif not a.paradigm.startswith(expect + "-"):
                issues.append(Issue("FAIL", "paradigm.subject",
                                    "%s 范式 %s 与学科（%s -> %s-*）不符"
                                    % (a.id, a.paradigm, ch.subject, expect),
                                    ch.subject, ch.title, a.line))
            if a.status not in STATUSES:
                issues.append(Issue("FAIL", "status.illegal",
                                    "%s 状态列非法：%r" % (a.id, a.status),
                                    ch.subject, ch.title, a.line))
            if a.ncell != 7:
                issues.append(Issue("WARN", "table.stray_pipe",
                                    "%s 单元格数 %d（期望 7）——单元格里疑似有裸竖线，"
                                    "请改用 \\lvert / \\rvert" % (a.id, a.ncell),
                                    ch.subject, ch.title, a.line))
            if not a.pages:
                issues.append(Issue("WARN", "source.page",
                                    "%s 出处缺少 @pNNN 页锚：%r" % (a.id, a.source),
                                    ch.subject, ch.title, a.line))
            if a.status and a.status != "⬜" and not EVIDENCE_MARK.search(a.evidence or ""):
                issues.append(Issue("WARN", "evidence.missing",
                                    "%s 状态为 %s 但证据里没有【实测】标记" % (a.id, a.status),
                                    ch.subject, ch.title, a.line))
            if (a.status == "⬜" and EVIDENCE_MARK.search(a.evidence or "")
                    and not UNVERIFIED_NOTE.search(a.evidence or "")):
                issues.append(Issue("WARN", "evidence.unjudged",
                                    "%s 未考察（⬜）却带【实测】记录——若已实测应改状态"
                                    % a.id, ch.subject, ch.title, a.line))
    # 第二遍：章节内部结构体检 + 组合引用核验（此时全库原子已在册）
    for ch in chapters:
        by_code = {}
        for a in ch.atoms:
            m = ATOM_ID_RE.match(a.id or "")
            if m:
                by_code.setdefault(m.group(1), []).append(int(m.group(3)))
        for code, nums in sorted(by_code.items()):
            nums = sorted(nums)
            if nums and not ch.has_void_note and nums != list(range(1, len(nums) + 1)):
                issues.append(Issue("WARN", "id.sequence",
                                    "%s %s 段编号不连续且未见「作废」留痕：%s"
                                    % (ch.filename, code, nums[:8]),
                                    ch.subject, ch.title, 0))
        if not ch.atoms:
            issues.append(Issue("WARN", "chapter.empty", "%s 没解析出任何原子行" % ch.filename,
                                ch.subject, ch.title, 0))
        for c in ch.combos:
            if c.ncell != 6:
                issues.append(Issue("WARN", "table.stray_pipe",
                                    "%s 单元格数 %d（期望 6）" % (c.id, c.ncell),
                                    ch.subject, ch.title, c.line))
            if not COMBO_ID_RE.match(c.id or ""):
                issues.append(Issue("FAIL", "combo.id", "组合 ID 不符合规范：%s" % c.id,
                                    ch.subject, ch.title, c.line))
            if c.mode == "?":
                issues.append(Issue("WARN", "combo.mode", "%s 未标 C1-C7 组合模式" % c.id,
                                    ch.subject, ch.title, c.line))
            if not c.chain:
                issues.append(Issue("FAIL", "combo.chain", "%s 原子链为空" % c.id,
                                    ch.subject, ch.title, c.line))
            for x in c.chain:
                if x not in all_atoms:
                    issues.append(Issue("FAIL", "combo.ref", "%s 引用了不存在的原子 %s"
                                        % (c.id, x), ch.subject, ch.title, c.line))
            if not (c.breakpoint or "").strip():
                issues.append(Issue("WARN", "combo.breakpoint", "%s 断点列为空" % c.id,
                                    ch.subject, ch.title, c.line))

    if dup:
        issues.append(Issue("FAIL", "id.duplicate", "原子 ID 重复：%s" % ", ".join(sorted(dup))))

    used = set()
    for ch in chapters:
        for c in ch.combos:
            used.update(c.chain)
    uncovered = [a.id for a in all_atoms.values() if a.tier == "S" and a.id not in used]
    if uncovered:
        issues.append(Issue("WARN", "coverage.s_tier",
                            "S 档原子未被任何组合引用（背了不考）：%s" % ", ".join(sorted(uncovered)[:30])))

    books = ws.books
    if books:
        for ch in chapters:
            book = ""
            for name in books:
                if name in "\n".join(ch.header):
                    book = name
                    break
            if not book:
                continue
            meta = books.get(book) or {}
            pages = meta.get("pages")
            if not pages:
                continue
            for a in ch.atoms:
                for p in a.pages:
                    if p < 1 or p > int(pages):
                        issues.append(Issue("WARN", "source.range",
                                            "%s 出处 p%d 超出《%s》页数 %s"
                                            % (a.id, p, book, pages),
                                            ch.subject, ch.title, a.line))

    if index_check:
        table = _index_status_table(kb_dir, ws.dirs["kb_index"])
        if table is None:
            issues.append(Issue("WARN", "index.missing",
                                "未找到 _INDEX 状态统计表（%s）——账目无法对账" % ws.dirs["kb_index"]))
        else:
            for ch in chapters:
                key = index_key(ch.subject, ch.title, ch.filename)
                row = table.get(key)
                if row is None:
                    issues.append(Issue("WARN", "index.row_missing",
                                        "%s 在 _INDEX 状态统计里没有对应行" % key))
                    continue
                got = [ch_status_count(ch, s) for s in STATUSES]
                if row != got:
                    issues.append(Issue("FAIL", "index.mismatch",
                                        "%s 状态统计漂移：_INDEX=%s 实际=%s"
                                        % (key, row, got), ch.subject, ch.title, 0))
        summary = _index_summary_table(kb_dir, ws.dirs["kb_index"])
        if summary:
            agg = {}
            for ch in chapters:
                row = agg.setdefault(ch.subject, [0, 0, 0, 0, 0, 0])
                row[0] += 1
                row[1] += len(ch.atoms)
                for i, s in enumerate(STATUSES):
                    row[2 + i] += ch_status_count(ch, s)
            for subj, row in sorted(agg.items()):
                if subj in summary and summary[subj] != row:
                    issues.append(Issue("FAIL", "index.summary_mismatch",
                                        "%s 科目汇总漂移：_INDEX=%s 实际=%s"
                                        % (subj, summary[subj], row)))

    stats = {}
    totals = dict(chapters=len(chapters),
                  atoms=sum(len(c.atoms) for c in chapters),
                  combos=sum(len(c.combos) for c in chapters))
    totals["tiers"] = {}
    totals["status"] = dict((s, 0) for s in STATUSES)
    for ch in chapters:
        for a in ch.atoms:
            totals["tiers"][a.tier] = totals["tiers"].get(a.tier, 0) + 1
            if a.status in totals["status"]:
                totals["status"][a.status] += 1
    stats.update(totals)
    return Report(issues, stats)


def ch_status_count(ch, status):
    return sum(1 for a in ch.atoms if a.status == status)
