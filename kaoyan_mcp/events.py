# -*- coding: utf-8 -*-
"""画像事件流：append-only 的学习事件账本（学习算法的唯一数据源）。

列（逗号分隔的 CSV，UTF-8 with BOM，11 列；与 工具\\kb-profile.py 写的文件同格式）::

    日期  时间  来源  科目  章节  原子ID  模式  事件  判定  讲法  备注
    2026-09-17  18:20  判卷  概率论  第02讲  GL-02-021  CM-02  复习  ✅  TH-03  正常

分隔符按表头自动识别：逗号（既有工作区的 .csv）和制表符（手工维护）都能读。

判定 ∈ {✅, ⚠️, ❌}；非判定事件（直讲/提问）判定留空——算法只用带判定的事件，
但原样保留其余事件，便于追溯。
"""
from __future__ import annotations

import csv
import datetime
import io
import os

COLUMNS = ["日期", "时间", "来源", "科目", "章节", "原子ID", "模式", "事件", "判定", "讲法", "备注"]
JUDGED = ("✅", "⚠️", "❌")


class Event(object):
    __slots__ = ("date", "time", "src", "subject", "chapter", "atom", "mode",
                 "event", "verdict", "teach", "note", "line")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def judged(self):
        return self.verdict in JUDGED

    @property
    def y(self):
        return 1 if self.verdict == "✅" else 0

    def as_dict(self):
        return dict((k, getattr(self, k)) for k in self.__slots__)

    def __repr__(self):
        return "<Event %s %s %s %s>" % (self.date, self.atom, self.event, self.verdict)


def sniff_delimiter(path):
    """按表头判分隔符：逗号多的当 CSV，制表符多的当 TSV。"""
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            head = f.readline()
    except OSError:
        return ","
    return "\t" if head.count("\t") > head.count(",") else ","


def load_events(path):
    if not os.path.isfile(path):
        return []
    delim = sniff_delimiter(path)
    out = []
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.reader(f, delimiter=delim), 1):
            c = [x.strip() for x in row]
            if not any(c):
                continue
            if i == 1 and ("日期" in c[0] or "原子ID" in "".join(c)):
                continue
            if len(c) < len(COLUMNS):
                c = c + [""] * (len(COLUMNS) - len(c))
            out.append(Event(date=c[0], time=c[1], src=c[2], subject=c[3], chapter=c[4],
                             atom=c[5], mode=c[6], event=c[7], verdict=c[8],
                             teach=c[9], note=c[10], line=i))
    return out


def append_event(path, verdict="", event="", atom="", subject="", chapter="",
                 mode="", teach="", note="", src="", today=None, when=None):
    row = [today or datetime.date.today().isoformat(),
           when or datetime.datetime.now().strftime("%H:%M"),
           src, subject, chapter, atom, mode, event, verdict, teach, note]
    for x in row:
        if "\n" in str(x) or "\r" in str(x):
            raise ValueError("事件字段不允许包含换行")
    fresh = (not os.path.exists(path)) or os.path.getsize(path) == 0
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(path, "a", encoding="utf-8-sig" if fresh else "utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if fresh:
            w.writerow(COLUMNS)
        w.writerow(row)
    return row


def to_dataset(events, tier_of=None):
    """把事件流整理成算法用的数据集：每条判定事件一行。

    「直讲」事件没有判定、不进模型，但它的讲法码要挂到该原子接下来那次判定上——
    判定行自己的讲法列通常是空的，不这样归因，「哪种讲法管用」就永远没有数据。
    """
    tier_of = tier_of or {}
    last_date = {}
    last_verdict = {}
    last_teach = {}
    rows = []
    for e in events:
        if not e.judged:
            if (e.teach or "").strip():
                last_teach[e.atom] = (e.teach.strip(), e.date)
            continue
        prev = last_date.get(e.atom)
        dt = _days(e.date, prev) if prev else 0.0
        code, tdate = last_teach.get(e.atom, ("", ""))
        own = (e.teach or "").strip()
        if own:
            code, tdate = own, ""
        rows.append(dict(
            date=e.date, atom=e.atom, subject=e.subject or "?", chapter=e.chapter,
            tier=tier_of.get(e.atom, "?"), mode=e.mode, teach=code,
            teach_dt=_days(e.date, tdate) if tdate else 0.0,
            verdict=e.verdict, y=e.y, dt=dt, retry=1.0 if (prev and dt == 0.0) else 0.0,
            prior=last_verdict.get(e.atom, ""),
        ))
        last_date[e.atom] = e.date
        last_verdict[e.atom] = e.verdict
    return rows


def _days(a, b):
    try:
        return float((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days)
    except Exception:
        return 0.0
