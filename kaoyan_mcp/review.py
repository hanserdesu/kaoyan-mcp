# -*- coding: utf-8 -*-
"""间隔复习：队列解析、到期筛选、判定写回。

阶梯 1d -> 2d -> 4d -> 7d -> 15d -> 30d -> 60d；
判定三级：流畅通过升一档 / 勉强通过同档重测 / 失败回 1d 并降状态。

分层（A 盯防 / B 常规 / C 边角）与配额（每日 <= 3 单元）来自规则层，
本模块只负责把规则落到文件上，并把每次判定写进事件流（算法层的数据源）。
"""
from __future__ import annotations

import datetime
import os
import re

from . import events as events_mod
from .fsutil import read_lines, write_text
from .kb.parse import ATOM_ID_ANY_RE, ATOM_ROW_RE, STATUS_CELL, load_chapters

LADDER = ["1d", "2d", "4d", "7d", "15d", "30d", "60d"]
LAYER_RANK = {"A": 0, "B": 1, "C": 2}
TIER_RANK = {"S": 0, "A": 1, "B": 2, "?": 3}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|")

VERDICT_ALIASES = {
    "✅": "✅", "流畅": "✅", "对": "✅", "通过": "✅", "满": "✅", "ok": "✅",
    "⚠️": "⚠️", "勉强": "⚠️", "半错": "⚠️", "磕绊": "⚠️",
    "❌": "❌", "失败": "❌", "错": "❌", "不会": "❌",
}

VERDICT_LABEL = {"✅": "流畅通过", "⚠️": "勉强通过", "❌": "未过"}


def normalize_verdict(v):
    v = (v or "").strip()
    if v in VERDICT_ALIASES:
        return VERDICT_ALIASES[v]
    return VERDICT_ALIASES.get(v.lower(), "⚠️")


class QueueItem(object):
    def __init__(self, due, layer, topic, atom, chapter, stage, sealed, record, line):
        self.due = due
        self.layer = layer
        self.topic = topic
        self.atom = atom
        self.chapter = chapter
        self.stage = stage
        self.sealed = sealed
        self.record = record
        self.line = line
        self.tier = "?"

    def as_dict(self):
        return dict(due=self.due, layer=self.layer, atom=self.atom, topic=self.topic,
                    chapter=self.chapter, stage=self.stage, sealed=self.sealed,
                    tier=self.tier, record=self.record[:200], line=self.line)

    def __repr__(self):
        return "<QueueItem %s %s %s %s>" % (self.due, self.layer, self.atom, self.stage)


def parse_queue_text(text):
    items = []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ROW_RE.match(ln):
            continue
        c = [x.strip() for x in ln.strip().strip("|").split("|")]
        if len(c) < 7 or c[1] not in LAYER_RANK:
            continue
        m = ATOM_ID_ANY_RE.search(c[2])
        items.append(QueueItem(c[0], c[1], c[2], m.group(1) if m else "", c[3],
                               c[4], c[5], c[6], i))
    return items


def load_queue(ws):
    path = ws.queue_file
    if not os.path.isfile(path):
        return []
    return parse_queue_text("\n".join(read_lines(path)))


def _attach_tiers(ws, items):
    tier = {}
    for ch in load_chapters(ws.kb_dir):
        for a in ch.atoms:
            tier[a.id] = a.tier
    for it in items:
        it.tier = tier.get(it.atom, "?")
    return items


def plan(ws, today=None, quota=3, layers=("A", "B", "C"), subject=None):
    """到期项按「层 -> 分值档 -> 逾期天数」排序，并应用每日配额。"""
    today = today or datetime.date.today().isoformat()
    items = _attach_tiers(ws, load_queue(ws))
    due = []
    for it in items:
        if it.layer not in layers:
            continue
        if subject and subject not in (it.chapter or ""):
            continue
        if it.due <= today:
            overdue = _day_diff(today, it.due)
            due.append((LAYER_RANK.get(it.layer, 9), TIER_RANK.get(it.tier, 9),
                        -overdue, it))
    due.sort(key=lambda x: (x[0], x[1], x[2]))
    picked = [x[3] for x in due[:quota]]
    deferred = [x[3] for x in due[quota:]]
    return dict(today=today, quota=quota, due=picked, deferred=deferred,
                total_due=len(due), all_items=len(items))


def _day_diff(a, b):
    try:
        return (datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days
    except Exception:
        return 0


def next_stage(stage):
    if stage in LADDER:
        i = LADDER.index(stage)
        return LADDER[min(i + 1, len(LADDER) - 1)]
    return LADDER[1]


def grade_stage(stage, verdict):
    """判定三级 -> 新档位。"""
    if verdict == "✅":
        return next_stage(stage or LADDER[0])
    if verdict == "⚠️":
        return stage if stage in LADDER else LADDER[0]
    return LADDER[0]


def grade(ws, atom, verdict, note="", today=None, mode="", teach="",
          sync_index=True, write=True):
    """判定写回：复习队列行 + 章节状态格 + 事件流（+ _INDEX 对账）。

    返回改动明细（写盘前先算好，write=False 时只做演练）。
    """
    today = today or datetime.date.today().isoformat()
    v = normalize_verdict(verdict)
    atom = (atom or "").strip().upper()
    if not atom:
        return dict(ok=False, error="缺少原子 ID")
    result = dict(ok=True, atom=atom, verdict=v, today=today, note=note,
                  queue=[], chapter=[], index="", warnings=[])

    # --- 1) 复习队列 ---
    qpath = ws.queue_file
    lines = read_lines(qpath)
    if not lines:
        result["warnings"].append("复习队列文件不存在：%s" % qpath)
    else:
        hit = -1
        for i, ln in enumerate(lines):
            if atom in ln and ROW_RE.match(ln):
                hit = i
                break
        if hit < 0:
            result["warnings"].append("复习队列里没有 %s 的行（未封存过？）" % atom)
        else:
            c = [x.strip() for x in lines[hit].strip().strip("|").split("|")]
            stage = grade_stage(c[4] if len(c) > 4 else LADDER[0], v)
            days = int(stage[:-1]) if stage.endswith("d") else 1
            next_due = (datetime.date.fromisoformat(today) + datetime.timedelta(days=days)).isoformat()
            old_record = c[6] if len(c) > 6 else ""
            record = old_record.strip()
            joiner = "；" if record and record != "—" else ""
            entry = "%s 复习%s%s" % (today[5:], VERDICT_LABEL[v],
                                    ("：" + note) if note else "")
            c[0], c[4], c[6] = next_due, stage, record + joiner + entry
            new_line = "| " + " | ".join(c) + " |"
            result["queue"].append(dict(line=hit + 1, old=lines[hit], new=new_line,
                                        next_due=next_due, stage=stage))
            if write:
                lines[hit] = new_line
                write_text(qpath, "\n".join(lines) + "\n")

    # --- 2) 章节文件状态格 + 证据 ---
    chapters = load_chapters(ws.kb_dir)
    target = None
    for ch in chapters:
        for a in ch.atoms:
            if a.id == atom:
                target = (ch, a)
                break
        if target:
            break
    if not target:
        result["warnings"].append("章节文件里找不到原子 %s" % atom)
    else:
        ch, a = target
        new_status = "✅" if v == "✅" else "⚠️"
        cpath = ch.path
        clines = read_lines(cpath)
        idx = a.line - 1
        row = clines[idx] if 0 <= idx < len(clines) else ""
        if not ATOM_ROW_RE.match(row):
            result["warnings"].append("章节行定位失败：%s:%d" % (ch.filename, a.line))
        else:
            cells = [x.strip() for x in row.strip().strip("|").split("|")]
            pos = -1
            for i, x in enumerate(cells):
                if STATUS_CELL.match(x):
                    pos = i
                    break
            if pos < 0:
                result["warnings"].append("未找到状态格：%s" % atom)
            else:
                stamp = "【实测 %s %s】" % (today, VERDICT_LABEL[v])
                old_status = cells[pos]
                cells[pos] = new_status
                if note:
                    cells[-1] = (cells[-1] + " " + stamp + note).strip()
                else:
                    cells[-1] = (cells[-1] + " " + stamp).strip()
                new_row = "| " + " | ".join(cells) + " |"
                result["chapter"].append(dict(file=ch.rel, line=a.line, old=row,
                                              new=new_row, old_status=old_status,
                                              new_status=new_status))
                if write:
                    clines[idx] = new_row
                    text = "\n".join(clines)
                    if not text.endswith("\n"):
                        text += "\n"
                    text = sync_row_tally(text)
                    write_text(cpath, text)

    # --- 3) 事件流（算法层数据源，只追加） ---
    if write:
        events_mod.append_event(ws.stream_file, verdict=v, event="复习", atom=atom,
                                subject=(target[0].subject if target else ""),
                                chapter=(target[0].title if target else ""),
                                mode=mode, teach=teach, note=note, src="判卷", today=today)

    # --- 4) _INDEX 对账 ---
    if sync_index and write:
        from .kb import stats as stats_mod
        ok, msg, changed = stats_mod.rewrite_index(ws)
        result["index"] = msg if ok else ("对账失败：" + msg)
    return result


TALLY_RE = re.compile(r"状态\s*(\d+)\s*✅\s*｜\s*(\d+)\s*⚠️\s*｜\s*(\d+)\s*❌\s*｜\s*(\d+)\s*⬜")


def sync_row_tally(text):
    """同步章节文件里「状态 N ✅ ｜ N ⚠️ ｜ N ❌ ｜ N ⬜」这行的数字（幂等）。"""
    counts = [0, 0, 0, 0]
    mark = ["✅", "⚠️", "❌", "⬜"]
    for ln in text.splitlines():
        if ATOM_ROW_RE.match(ln):
            for i, s in enumerate(mark):
                if re.search(r"\|\s*" + s + r"\s*\|", ln):
                    counts[i] += 1
                    break
    def _sub(m):
        return "状态 %d ✅ ｜ %d ⚠️ ｜ %d ❌ ｜ %d ⬜" % tuple(counts)
    return TALLY_RE.sub(_sub, text)

