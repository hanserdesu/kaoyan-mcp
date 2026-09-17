# -*- coding: utf-8 -*-
"""知识库统计与 _INDEX 对账回写。

读：按科目/章节汇总原子数、档位分布、状态分布。
写：把统计结果回写到 _INDEX.md 的「状态统计」与「科目汇总」两张表（幂等）。
"""
from __future__ import annotations

import os
import re

from ..fsutil import read_lines, read_text, write_text
from .parse import STATUSES, load_chapters
from .quality import index_key


def summarize(chapters):
    per_chapter = []
    per_subject = {}
    for ch in chapters:
        counts = [sum(1 for a in ch.atoms if a.status == s) for s in STATUSES]
        per_chapter.append(dict(subject=ch.subject, chapter=ch.title, file=ch.filename,
                                key=index_key(ch.subject, ch.title, ch.filename),
                                atoms=len(ch.atoms), combos=len(ch.combos),
                                counts=counts))
        row = per_subject.setdefault(ch.subject, dict(files=0, atoms=0, combos=0,
                                                      counts=[0, 0, 0, 0]))
        row["files"] += 1
        row["atoms"] += len(ch.atoms)
        row["combos"] += len(ch.combos)
        for i in range(4):
            row["counts"][i] += counts[i]
    total = dict(files=0, atoms=0, combos=0, counts=[0, 0, 0, 0])
    for row in per_subject.values():
        total["files"] += row["files"]
        total["atoms"] += row["atoms"]
        total["combos"] += row["combos"]
        for i in range(4):
            total["counts"][i] += row["counts"][i]
    return dict(per_chapter=per_chapter, per_subject=per_subject, total=total)


def overview(ws):
    chapters = load_chapters(ws.kb_dir)
    s = summarize(chapters)
    tiers = {}
    for ch in chapters:
        for a in ch.atoms:
            tiers[a.tier or "?"] = tiers.get(a.tier or "?", 0) + 1
    s["tiers"] = tiers
    return s


STAT_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*$")
SUMMARY_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*$")
TOTAL_ROW_RE = re.compile(r"^\|\s*\*\*合计\*\*\s*\|")
SEP_ROW_RE = re.compile(r"^\|[\s:|-]+\|$")
STATUS_HEADER_RE = re.compile(r"^\|\s*科目·章节\s*\|")
SUMMARY_HEADER_RE = re.compile(r"^\|\s*科目\s*\|\s*文件数\s*\|")


def rewrite_index(ws, chapters=None):
    """按当前章节文件重算并回写 _INDEX.md 的两张统计表。返回 (ok, message, changed)。"""
    path = ws.kb_index_file
    text = read_text(path)
    if text is None:
        return False, "未找到 %s" % os.path.basename(path), 0
    chapters = chapters if chapters is not None else load_chapters(ws.kb_dir)
    s = summarize(chapters)
    by_key = dict((r["key"], r) for r in s["per_chapter"])
    lines = text.splitlines()
    changed = 0
    out = []
    seen = {"status": set(), "summary": set()}
    anchors = {}
    cur = None
    for ln in lines:
        if STATUS_HEADER_RE.match(ln):
            cur = "status"
        elif SUMMARY_HEADER_RE.match(ln):
            cur = "summary"
        elif cur and not ln.startswith("|"):
            cur = None
        if not cur or not ln.startswith("|"):
            out.append(ln)
            continue
        if STATUS_HEADER_RE.match(ln) or SUMMARY_HEADER_RE.match(ln):
            out.append(ln)
            continue
        if TOTAL_ROW_RE.match(ln):
            t = s["total"]
            c = t["counts"]
            new = "| **合计** | **%d** | **%d** | **%d** | **%d** | **%d** | **%d** |" % (
                t["files"], t["atoms"], c[0], c[1], c[2], c[3])
            if new != ln:
                changed += 1
            out.append(new)
            continue
        if SEP_ROW_RE.match(ln):
            out.append(ln)
            anchors[cur] = len(out)
            continue
        m = (STAT_ROW_RE if cur == "status" else SUMMARY_ROW_RE).match(ln)
        key = m.group(1).strip() if m else ""
        src = by_key if cur == "status" else s["per_subject"]
        if m and key in src:
            if cur == "status":
                c = src[key]["counts"]
                new = "| %s | %d | %d | %d | %d |" % (key, c[0], c[1], c[2], c[3])
            else:
                r = src[key]
                c = r["counts"]
                new = "| %s | %d | %d | %d | %d | %d | %d |" % (
                    key, r["files"], r["atoms"], c[0], c[1], c[2], c[3])
            if new != ln:
                changed += 1
            out.append(new)
            seen[cur].add(key)
            anchors[cur] = len(out)
            continue
        out.append(ln)
        anchors[cur] = len(out)
    # 表里没有对应行的章节补上：新章建模后不用手抄，账目也不会永远缺行。
    extra = [r for r in s["per_chapter"] if r["key"] not in seen["status"]]
    if anchors.get("status") is not None and extra:
        rows = ["| %s | %d | %d | %d | %d |"
                % (r["key"], r["counts"][0], r["counts"][1], r["counts"][2], r["counts"][3])
                for r in extra]
        out[anchors["status"]:anchors["status"]] = rows
        changed += len(rows)
        if anchors.get("summary") is not None and anchors["summary"] >= anchors["status"]:
            anchors["summary"] += len(rows)
    missing = [k for k in s["per_subject"] if k not in seen["summary"]]
    if anchors.get("summary") is not None and missing:
        rows = ["| %s | %d | %d | %d | %d | %d | %d |"
                % (k, s["per_subject"][k]["files"], s["per_subject"][k]["atoms"],
                   s["per_subject"][k]["counts"][0], s["per_subject"][k]["counts"][1],
                   s["per_subject"][k]["counts"][2], s["per_subject"][k]["counts"][3])
                for k in missing]
        out[anchors["summary"]:anchors["summary"]] = rows
        changed += len(rows)
    if changed:
        body = "\n".join(out)
        if text.endswith("\n"):
            body += "\n"
        write_text(path, body)
    return True, "状态统计/科目汇总已按章节文件重算（改动 %d 行）" % changed, changed


def read_state_counts(ws):
    """读 _INDEX 两张表里的现成数字（不重算），用于对账展示。"""
    text = read_text(ws.kb_index_file)
    if not text:
        return {}, {}
    chapters, subjects = {}, {}
    for ln in text.splitlines():
        m = STAT_ROW_RE.match(ln)
        if m:
            cells = [x.strip() for x in ln.strip().strip("|").split("|")]
            try:
                chapters[m.group(1).strip()] = [int(x) for x in cells[1:5]]
            except ValueError:
                pass
            continue
        m = SUMMARY_ROW_RE.match(ln)
        if m:
            cells = [x.strip().replace("*", "") for x in ln.strip().strip("|").split("|")]
            try:
                subjects[m.group(1).strip()] = [int(x) for x in cells[1:7]]
            except ValueError:
                pass
    return chapters, subjects
