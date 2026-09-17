# -*- coding: utf-8 -*-
"""出卷：题目与答案分离，版面按纸张预算排版，页数可校验。

口径：
  * 卷面只放题目；答案永远写到 试卷/答案/ 下的独立文件。
  * 版面预算按纸张可用高度算（A4 269mm / A5 190mm / Letter 251mm，含安全边距），
    估算超页时给出警告，真正确认靠渲染出来的 PDF 页数。
  * 默认不引外部资源（离线可用）；需要数学排版时把 spec.mathjax 设为 true。
"""
from __future__ import annotations

import datetime
import os
import re

from .fsutil import write_text

PAPERS = {
    "A4": dict(w=210, h=297, top=14, side=16, usable=269, line_chars=42, line_mm=5.6),
    "A5": dict(w=148, h=210, top=10, side=12, usable=190, line_chars=28, line_mm=5.2),
    "Letter": dict(w=216, h=279, top=14, side=16, usable=251, line_chars=44, line_mm=5.6),
}

HTML_HEAD = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
{mathjax}
<style>
  @page {{ size: {paper} portrait; margin: {top}mm {side}mm {top}mm {side}mm; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: {serif}; color: #111; font-size: {font}pt; line-height: 1.55; }}
  .page {{ width: {w}mm; min-height: {h}mm; padding: {top}mm {side}mm; margin: 0 auto 8mm;
           background: #fff; page-break-after: always; display: flex; flex-direction: column; }}
  .page:last-child {{ page-break-after: auto; }}
  .head {{ border-bottom: 1.2pt solid #111; padding-bottom: 1.5mm; margin-bottom: 3mm;
           display: flex; justify-content: space-between; align-items: baseline; }}
  .head h1 {{ font-size: {h1}pt; margin: 0; }}
  .head .meta {{ font-size: 9.5pt; color: #444; }}
  .hint {{ font-size: 9pt; color: #555; margin-bottom: 3mm; }}
  .q {{ margin: 0 0 4mm; break-inside: avoid; page-break-inside: avoid; }}
  .q .stem {{ margin: 0 0 1.5mm; }}
  .q .tag {{ font-size: 8.5pt; color: #666; margin-left: 2mm; }}
  .work {{ border: 0.6pt solid #9aa0a6; border-radius: 1mm; }}
  .work.ruled {{ background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 7mm, #d8dcdf 7mm, #d8dcdf 7.2mm); }}
  .foot {{ margin-top: auto; padding-top: 2mm; border-top: 0.6pt solid #bbb;
           font-size: 8.5pt; color: #666; display: flex; justify-content: space-between; }}
  @media print {{ body {{ background: #fff; }} .page {{ margin: 0; box-shadow: none; }} }}
</style>
</head>
<body>
"""

HTML_TAIL = """
</body>
</html>
"""

MATHJAX = """<script>
window.MathJax = { tex: { inlineMath: [['$', '$']], displayMath: [['$$', '$$']] }, svg: { fontCache: 'global' } };
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>"""


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _rich(s):
    """允许题目里用最少量的行内标记：**粗体** 与换行；其余一律转义。"""
    t = _esc(s)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = t.replace("\n", "<br>")
    return t


def _q_mm(q, paper):
    p = PAPERS[paper]
    stem = _rich(q.get("stem", ""))
    plain = re.sub(r"<[^>]+>", "", stem)
    lines = 0
    for seg in plain.split("<br>"):
        lines += max(1, int(len(seg) / p["line_chars"]) + 1)
    work = float(q.get("work_mm") or _default_work(q))
    return 6.0 + lines * p["line_mm"] + work + 3.0, work


def _default_work(q):
    pts = float(q.get("points") or 10)
    return min(70.0, max(24.0, pts * 4.0))


def estimate_pages(spec):
    return len(_paginate(spec))


def _paginate(spec):
    """按纸张可用高度把题目分页；渲染与估算共用同一套分页，避免两边不一致。"""
    paper = spec.get("paper") or "A4"
    p = PAPERS.get(paper) or PAPERS["A4"]
    pages, cur = [], []
    left = p["usable"] - 22.0
    for q in spec.get("questions") or []:
        need, work = _q_mm(q, paper)
        if cur and need > left:
            pages.append(cur)
            cur = []
            left = p["usable"]
        cur.append((q, need, work))
        left -= need
    if cur or not pages:
        pages.append(cur)
    return pages


def render_html(spec):
    paper = spec.get("paper") or "A4"
    p = PAPERS.get(paper) or PAPERS["A4"]
    title = spec.get("title") or "练习卷"
    lang = spec.get("lang") or "zh-CN"
    serif = spec.get("font") or '"Songti SC", "SimSun", "STSong", "Noto Serif CJK SC", serif'
    font = 11 if paper != "A5" else 10
    html = HTML_HEAD.format(lang=lang, title=_esc(title), paper=paper, top=p["top"],
                            side=p["side"], w=p["w"], h=p["h"], serif=serif, font=font,
                            h1=font + 4, mathjax=(MATHJAX if spec.get("mathjax") else ""))
    pages = _paginate(spec)
    total = len(pages)
    for pi, page in enumerate(pages, 1):
        html += '<section class="page">\n'
        html += '<div class="head"><h1>%s</h1><div class="meta">%s%s%s</div></div>\n' % (
            _esc(title),
            _esc(spec.get("subject") or ""),
            (" ｜ " if spec.get("subject") else ""),
            _esc(spec.get("date") or datetime.date.today().isoformat()))
        if spec.get("instructions"):
            html += '<div class="hint">%s</div>\n' % _rich(spec["instructions"])
        for q, need, work in page:
            tag = ""
            if q.get("points"):
                tag = '<span class="tag">(%s 分)</span>' % _esc(q["points"])
            if q.get("atoms"):
                tag += '<span class="tag">%s</span>' % _esc(",".join(q["atoms"]))
            html += '<div class="q"><div class="stem"><b>%s</b> %s%s</div>' % (
                _esc(q.get("id") or ""), _rich(q.get("stem", "")), tag)
            html += '<div class="work ruled" style="height:%.1fmm"></div></div>\n' % work
        html += '<div class="foot"><span>%s</span><span>第 %d / %d 页</span></div>\n' % (
            _esc(spec.get("foot_note") or ""), pi, total)
        html += "</section>\n"
    html += HTML_TAIL
    return html, total


def render_answers(spec):
    lines = ["# %s · 答案与讲评" % (spec.get("title") or "练习卷"), ""]
    lines.append("> 日期：%s ｜ 科目：%s ｜ 题目数：%d"
                 % (spec.get("date") or datetime.date.today().isoformat(),
                    spec.get("subject") or "-", len(spec.get("questions") or [])))
    lines.append("")
    for q in spec.get("questions") or []:
        lines.append("## %s" % (q.get("id") or ""))
        lines.append("")
        lines.append("**题目**：%s" % (q.get("stem") or "").replace("\n", " "))
        lines.append("")
        lines.append("**答案**：%s" % (q.get("answer") or "（待补）").replace("\n", "\n\n"))
        if q.get("atoms"):
            lines.append("")
            lines.append("**考点原子**：%s" % ", ".join(q["atoms"]))
        if q.get("breakpoint"):
            lines.append("")
            lines.append("**断点提示**：%s" % q["breakpoint"])
        lines.append("")
    return "\n".join(lines) + "\n"


def slugify(text):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "sheet").strip())
    return s.strip("-")[:60] or "sheet"


def write_sheet(ws, spec):
    """写出卷面 HTML 与答案 md，返回产物信息。"""
    paper = spec.get("paper") or "A4"
    date = spec.get("date") or datetime.date.today().isoformat()
    slug = slugify(spec.get("slug") or spec.get("title") or "sheet")
    base = "%s_%s_%s" % (date, slug, paper)
    paper_dir = ws.dir("papers")
    ans_dir = os.path.join(paper_dir, ws.dirs["answers"])
    html, pages = render_html(spec)
    html_path = os.path.join(paper_dir, base + ".html")
    ans_path = os.path.join(ans_dir, base + "_答案.md")
    write_text(html_path, html)
    write_text(ans_path, render_answers(spec))
    return dict(ok=True, html=html_path, answer=ans_path, pages_estimate=pages,
                paper=paper, title=spec.get("title") or "", questions=len(spec.get("questions") or []),
                estimate_note="页数为按版面预算的估算；有浏览器时可渲染 PDF 核对实际页数")
