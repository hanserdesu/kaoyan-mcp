# -*- coding: utf-8 -*-
"""工具实现层：MCP 工具与命令行共用同一批函数。

约定：每个工具返回 (ok, payload)。payload 是 dict（结构化）或 str（给人看的文本）；
协议层负责把它序列化成 MCP 的 content 块。
"""
from __future__ import annotations

import datetime
import json
import os

from . import events as events_mod
from . import learn as learn_mod
from . import paper as paper_mod
from . import channels as ch_mod
from .config import ConfigError
from .fsutil import read_text, write_text
from .kb import quality, search as search_mod, stats as stats_mod
from .kb.parse import load_chapters


def _json_ready(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _tier_map(ws):
    out = {}
    for ch in load_chapters(ws.kb_dir):
        for a in ch.atoms:
            out[a.id] = a.tier
    return out


# ---------------------------------------------------------------- 环境能力
def env_capabilities(ws, args=None):
    info = ch_mod.probe(ws)
    lines = ["环境能力探查", "",
             "- 系统：%s ｜ Python %s" % (info["os"], info["python"]),
             "- 浏览器（HTML->PDF）：%s" % (info["browser"] or "未找到"),
             "- 打印工具：%s" % (info["print_tool"] or "无"),
             "- 打印机：%s" % (", ".join(info["printers"]) or "未检测到"),
             "- 可用通道：%s" % ", ".join(info["order"]),
             "- 当前策略：%s" % info["policy"], ""]
    for k, v in info["reasons"].items():
        if v:
            lines.append("- %s 通道受限：%s" % (k, v))
    lines.append("")
    lines.append("政策说明：print_strict = 必须打印（打不出来就报错）；auto = 能打就打，否则依次退化到 pdf / html / text。")
    payload = dict(info, _text="\n".join(lines))
    return True, payload


# ---------------------------------------------------------------- 知识库
def kb_overview(ws, args=None):
    args = args or {}
    s = stats_mod.overview(ws)
    subject = args.get("subject")
    lines = ["知识库概览", ""]
    lines.append("- 章节 %d ｜ 原子 %d ｜ 组合 %d" % (
        s["total"]["files"], s["total"]["atoms"], s["total"]["combos"]))
    lines.append("- 档位分布：%s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(s["tiers"].items())))
    c = s["total"]["counts"]
    lines.append("- 状态：✅ %d ｜ ⚠️ %d ｜ ❌ %d ｜ ⬜ %d" % tuple(c))
    lines.append("")
    lines.append("| 科目 | 章节 | 原子 | 组合 | ✅ | ⚠️ | ❌ | ⬜ |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for subj in sorted(s["per_subject"]):
        if subject and subj != subject:
            continue
        r = s["per_subject"][subj]
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d |"
                     % (subj, r["files"], r["atoms"], r["combos"], *r["counts"]))
    if subject:
        for row in s["per_chapter"]:
            if row["subject"] != subject:
                continue
            lines.append("| %s·%s | — | %d | %d | %d | %d | %d | %d |"
                         % (row["subject"], row["chapter"], row["atoms"], row["combos"],
                            *row["counts"]))
    payload = dict(summary=dict(files=s["total"]["files"], atoms=s["total"]["atoms"],
                                combos=s["total"]["combos"], tiers=s["tiers"],
                                status=dict(zip(["✅", "⚠️", "❌", "⬜"], c))),
                   per_subject=s["per_subject"], _text="\n".join(lines))
    return True, payload


def kb_search(ws, args=None):
    args = args or {}
    q = args.get("query") or ""
    hits = search_mod.search(ws, q, subject=args.get("subject"), tier=args.get("tier"),
                             status=args.get("status"), limit=int(args.get("limit") or 20))
    if not hits:
        return True, dict(count=0, hits=[], _text="没有命中。换个关键词，或用 kb_overview 看科目清单。")
    lines = ["命中 %d 条（按相关度）：" % len(hits), ""]
    for h in hits:
        lines.append(str(h))
    return True, dict(count=len(hits), hits=[h.as_dict() for h in hits],
                      _text="\n".join(lines))


def kb_chapter(ws, args=None):
    args = args or {}
    subject = args.get("subject") or ""
    chapter = args.get("chapter") or args.get("file") or ""
    if not subject or not chapter:
        return False, dict(error="需要 subject 与 chapter（章节可用文件名或标题关键词）",
                           subjects=sorted(ws.subjects))
    chapters = load_chapters(ws.kb_dir)
    picked = None
    for ch in chapters:
        if ch.subject != subject:
            continue
        if chapter in ch.filename or chapter in ch.title:
            picked = ch
            break
    if picked is None:
        return False, dict(error="找不到章节：%s/%s" % (subject, chapter),
                           available=[c.filename for c in chapters if c.subject == subject])
    text = read_text(picked.path) or ""
    max_chars = int(args.get("max_chars") or 200000)
    truncated = len(text) > max_chars
    body = text[:max_chars]
    return True, dict(file=picked.rel, path=picked.path, truncated=truncated,
                      subject=picked.subject, chapter=picked.title, content=body,
                      atom_rows=[a.as_dict() for a in picked.atoms],
                      combo_rows=[c.as_dict() for c in picked.combos],
                      _text=body)


def kb_atoms(ws, args=None):
    args = args or {}
    rows = search_mod.atom_index(ws, subject=args.get("subject"), tier=args.get("tier"),
                                 status=args.get("status"))
    limit = int(args.get("limit") or 200)
    lines = ["原子清单（%d 条，显示前 %d）：" % (len(rows), min(limit, len(rows))), ""]
    lines.append("| ID | 档 | 范式 | 状态 | 出处 | 章节 |")
    lines.append("|---|---|---|---|---|---|")
    for a in rows[:limit]:
        lines.append("| %s | %s | %s | %s | %s | %s/%s |"
                     % (a.id, a.tier, a.paradigm, a.status, a.source, a.subject, a.chapter))
    return True, dict(count=len(rows), atoms=[a.as_dict() for a in rows[:limit]],
                      _text="\n".join(lines))


def kb_validate(ws, args=None):
    args = args or {}
    rep = quality.check_workspace(ws, index_check=bool(args.get("index_check", True)))
    text = rep.text(limit=int(args.get("limit") or 300))
    return rep.ok, dict(ok=rep.ok, fails=len(rep.fails), warns=len(rep.warns),
                        codes=rep.codes(), stats=rep.stats,
                        issues=[i.as_dict() for i in rep.issues[:200]], _text=text)


def kb_reconcile(ws, args=None):
    """把 _INDEX.md 的两张统计表按章节文件重算回写（幂等）。"""
    ok, msg, changed = stats_mod.rewrite_index(ws)
    return ok, dict(ok=ok, changed=changed, _text=msg)


# ---------------------------------------------------------------- 复习
def review_due(ws, args=None):
    args = args or {}
    plan = review_plan(ws, args)
    lines = ["到期复习（%s，配额 %d）：共 %d 项到期" % (plan["today"], plan["quota"], plan["total_due"]), ""]
    if not plan["due"]:
        lines.append("今天没有到期项（队列共 %d 项）。" % plan["all_items"])
    for it in plan["due"]:
        lines.append("- [%s类·%s档] %s %s ｜ 档位 %s ｜ 章节 %s ｜ 逾期 %d 天"
                     % (it.layer, it.tier, it.atom, _topic_of(it)[:40], it.stage,
                        it.chapter, _overdue(plan["today"], it.due)))
    if plan["deferred"]:
        lines.append("")
        lines.append("超额顺延 %d 项（不罚，间隔自然拉长）：%s"
                     % (len(plan["deferred"]), ", ".join(x.atom for x in plan["deferred"])))
    payload = dict(plan)
    payload["due"] = [x.as_dict() for x in plan["due"]]
    payload["deferred"] = [x.as_dict() for x in plan["deferred"]]
    payload["_text"] = "\n".join(lines)
    return True, payload


def _topic_of(item):
    """队列里的知识点格自带「原子ID 名称」，展示时不再重复一遍 ID。"""
    topic = (item.topic or "").strip()
    if item.atom and topic.startswith(item.atom):
        topic = topic[len(item.atom):].strip()
    return topic


def _overdue(today, due):
    try:
        return max(0, (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(due)).days)
    except Exception:
        return 0


def review_grade(ws, args=None):
    args = args or {}
    atom = args.get("atom") or ""
    verdict = args.get("verdict") or "⚠️"
    dry = bool(args.get("dry_run"))
    res = review_grade_impl(ws, atom, verdict, note=args.get("note") or "",
                            mode=args.get("mode") or "", teach=args.get("teach") or "",
                            sync_index=bool(args.get("sync_index", True)), write=not dry)
    if not res.get("ok"):
        return False, dict(**res, _text="判定写回失败：%s" % res.get("error"))
    lines = ["判定写回（%s）" % ("演练，未落盘" if dry else "已落盘"), ""]
    lines.append("- 原子 %s -> %s（%s）" % (res["atom"], res["verdict"], learn_mod.HINT_SHIFT.get(res["verdict"], "")))
    for q in res["queue"]:
        lines.append("- 队列：下次 %s ｜ 档位 %s" % (q["next_due"], q["stage"]))
    for c in res["chapter"]:
        lines.append("- 章节：%s:%d 状态 %s -> %s" % (c["file"], c["line"], c["old_status"], c["new_status"]))
    if res.get("index"):
        lines.append("- 对账：%s" % res["index"])
    for w in res["warnings"]:
        lines.append("- WARN %s" % w)
    return True, dict(**res, _text="\n".join(lines))


def review_grade_impl(ws, atom, verdict, note="", mode="", teach="",
                      sync_index=True, write=True, today=None):
    from . import review as review_mod
    return review_mod.grade(ws, atom, verdict, note=note, mode=mode, teach=teach,
                            sync_index=sync_index, write=write, today=today)


def review_plan(ws, args=None):
    from . import review as review_mod
    args = args or {}
    return review_mod.plan(ws, today=args.get("date"), quota=int(args.get("quota") or 3),
                           subject=args.get("subject"))


def study_plan(ws, args=None):
    args = args or {}
    plan = review_plan(ws, args)
    mistakes = _unresolved_mistakes(ws)
    lines = ["今日计划（%s）" % plan["today"], ""]
    lines.append("1) 到期复习：%d 项（配额 %d）" % (plan["total_due"], plan["quota"]))
    for it in plan["due"]:
        lines.append("   - [%s·%s档] %s %s（档位 %s）" % (it.layer, it.tier, it.atom,
                                                            _topic_of(it)[:36], it.stage))
    lines.append("2) 未清错题：%d 条" % len(mistakes))
    for m in mistakes[:8]:
        lines.append("   - %s" % m)
    cur = _current_excerpt(ws)
    if cur:
        lines.append("3) 断点：%s" % cur)
    lines.append("4) 做完用 review_grade 写回判定（一次一条，判定入事件流喂算法）。")
    return True, dict(plan=dict(today=plan["today"], total_due=plan["total_due"],
                                quota=plan["quota"]),
                      due=[x.as_dict() for x in plan["due"]],
                      unresolved_mistakes=mistakes, current=cur,
                      _text="\n".join(lines))


def _unresolved_mistakes(ws):
    path = os.path.join(ws.dir("mistakes"), "错题本.md")
    text = read_text(path)
    if not text:
        return []
    out = []
    for ln in text.splitlines():
        if "待重做" in ln and ln.strip().startswith("|"):
            c = [x.strip() for x in ln.strip().strip("|").split("|")]
            if len(c) >= 3:
                out.append("%s ｜ %s" % (c[0], " ｜ ".join(c[1:3])))
    return out


def _current_excerpt(ws, limit=400):
    text = read_text(ws.current_file)
    if not text:
        return ""
    for marker in ("## 断点", "## 当前位置"):
        i = text.find(marker)
        if i >= 0:
            out = []
            for ln in text[i:].splitlines()[1:8]:
                s = ln.strip()
                if not s:
                    if out:
                        break
                    continue
                out.append(s.lstrip("-").strip())
            return " ｜ ".join(out)[:limit]
    return " ".join(ln.strip() for ln in text.splitlines()[:6] if ln.strip())[:limit]


# ---------------------------------------------------------------- 出卷与交付
def paper_build(ws, args=None):
    args = args or {}
    spec = args.get("spec") or args
    if isinstance(spec, str):
        spec = json.loads(spec)
    if not isinstance(spec, dict) or not spec.get("questions"):
        return False, dict(error="spec 需要包含 questions 数组")
    built = paper_mod.write_sheet(ws, spec)
    built["_text"] = ("卷面：%s\n答案：%s\n估算 %d 页（%s）"
                      % (built["html"], built["answer"], built["pages_estimate"],
                         built["estimate_note"]))
    return True, built


def paper_deliver(ws, args=None):
    args = args or {}
    spec = args.get("spec")
    if isinstance(spec, str):
        spec = json.loads(spec)
    if not spec or not isinstance(spec, dict):
        return False, dict(error="需要 spec（含 questions）；或先用 paper_build 生成卷面")
    built = paper_mod.write_sheet(ws, spec)
    policy = dict(ws.deliver_policy)
    if args.get("policy"):
        policy["policy"] = args["policy"]
    if args.get("printer"):
        policy["printer"] = args["printer"]
    res = ch_mod.deliver(ws, built, spec=spec, policy=policy)
    lines = ["交付结果：通道 = %s ｜ 成功 = %s" % (res["channel"] or "无", res["ok"]), ""]
    for k, v in res["artifacts"].items():
        lines.append("- %s：%s" % (k, v))
    for e in res["evidence"]:
        lines.append("- 证据：%s" % e)
    for w in res["warnings"]:
        lines.append("- WARN %s" % w)
    res["_text"] = "\n".join(lines)
    return res["ok"], res


# ---------------------------------------------------------------- 算法层
def learn_predict(ws, args=None):
    args = args or {}
    rows = _dataset(ws)
    atom = (args.get("atom") or "").upper()
    tier = args.get("tier") or "?"
    subject = args.get("subject") or ""
    hint = args.get("hint") or ""
    event = args.get("event") or "首测"
    if args.get("next") or not atom:
        plan = review_plan(ws, {})
        if plan["due"]:
            it = plan["due"][0]
            atom, subject, hint, tier = it.atom, it.chapter.split("\\")[0], "", it.tier
        elif not atom:
            return False, dict(error="没有待复习项；请用 atom 指定原子 ID")
    if not subject:
        for r in reversed(rows):
            if r["atom"] == atom:
                subject = r["subject"]
                break
    if not hint:
        for r in reversed(rows):
            if r["atom"] == atom:
                hint = r["verdict"]
                break
    if tier == "?":
        tier = _tier_map(ws).get(atom, "?")
    dt = 0.0
    last_date = ""
    for r in rows:
        if r["atom"] == atom:
            last_date = r["date"]
    today = args.get("date") or datetime.date.today().isoformat()
    if last_date:
        try:
            dt = float((datetime.date.fromisoformat(today)
                        - datetime.date.fromisoformat(last_date)).days)
        except Exception:
            dt = 0.0
        event = "复测" if dt > 0 else "同日复测"
    model = learn_mod.fit(rows)
    p, lo, hi = model.predict(atom=atom, subject=subject, tier=tier, dt=dt,
                              retry=0.0, hint=hint)
    logged = ""
    if args.get("log", True):
        learn_mod.append_prediction(ws.pred_log_file, atom, event, tier, p, lo, hi, today=today)
        logged = os.path.relpath(ws.pred_log_file, ws.root)
    lines = ["预测（%s %s）P(流畅通过) = %.0f%% ｜ 90%% 区间 %.0f%%-%.0f%%"
             % (atom, event, 100 * p, 100 * lo, 100 * hi),
             "  依据：theta=%.2f ｜ 难度 d=%.2f ｜ 档位 %s ｜ 距上次判定 %.0f 天 ｜ 样本 %d 条判定"
             % (model.theta, model.difficulty(atom, tier, hint), tier, dt, model.n),
             "  拟合：迭代 %d 次 ｜ 收敛 %s ｜ 梯度∞范数 %.2e" % (model.iters, model.converged, model.grad_inf)]
    if logged:
        lines.append("  已记 %s（先预测、后作答）" % logged)
    return True, dict(atom=atom, event=event, tier=tier, dt=dt, p=round(p, 3),
                      lo=round(lo, 3), hi=round(hi, 3), theta=round(model.theta, 3),
                      d=round(model.difficulty(atom, tier, hint), 3), n=model.n,
                      iters=model.iters, converged=model.converged, _text="\n".join(lines))


def _dataset(ws):
    return events_mod.to_dataset(events_mod.load_events(ws.stream_file), _tier_map(ws))


def learn_report(ws, args=None):
    args = args or {}
    rows = _dataset(ws)
    if not rows:
        return True, dict(n=0, _text="事件流里还没有判定记录——先答题，算法才有数据。")
    model = learn_mod.fit(rows)
    wf = learn_mod.walk_forward(rows)
    risks = learn_mod.pattern_risk(rows)
    teaches = learn_mod.teach_rank(rows)
    curve = learn_mod.learning_curve(wf)
    abl = learn_mod.ablation(rows) if args.get("ablation", True) else []
    L = ["# 学习算法报告", "",
         "> 数据源：%s（判定 %d 条）｜ 生成：%s"
         % (os.path.relpath(ws.stream_file, ws.root), len(rows),
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")), "",
         "## 一、能力与难度", "",
         "- 能力 theta = **%.2f**" % model.theta,
         "- 拟合：迭代 %d 次 ｜ 收敛 **%s** ｜ 梯度∞范数 %.2e ｜ 目标值 %.4f"
         % (model.iters, model.converged, model.grad_inf, model.nll),
         "- 遗忘常数 gamma = %.5f（%s）" % (model.gamma, "拟合得到" if model.fit_gamma else "固定 ln2/10"),
         "- 档位难度：%s" % ", ".join("%s=%.2f" % (k, v) for k, v in sorted(model.tier.items())),
         "- 最难 5 个原子：%s" % ", ".join("%s(%.2f)" % (a, d) for a, d in
                                           sorted(model.delta.items(), key=lambda kv: -kv[1])[:5]),
         "", "## 二、校准（walk-forward，第 k 条只用前 k-1 条）", ""]
    if wf["n"]:
        L.append("- Brier **%.3f** ｜ LogLoss %.3f ｜ 基线 Brier %.3f ｜ ECE %.3f（n=%d）"
                 % (wf["brier"], wf["logloss"], wf["base_brier"], wf["ece"] or 0.0, wf["n"]))
        L.append("- 结论：%s" % ("优于基线" if wf["brier"] < wf["base_brier"]
                                else "尚未优于基线（样本少时正常，继续积累）"))
        L.append("")
        L.append("| 预测区间 | 条数 | 预测均值 | 实际通过率 |")
        L.append("|---|---|---|---|")
        for r in learn_mod.reliability([p for _, p in wf["rows"]], [x["y"] for x, _ in wf["rows"]]):
            if r["n"]:
                L.append("| %.0f%%-%.0f%% | %d | %.0f%% | %.0f%% |"
                         % (100 * r["lo"], 100 * r["hi"], r["n"], 100 * (r["predicted"] or 0),
                            100 * (r["actual"] or 0)))
        if curve:
            L.append("")
            L.append("- 学习曲线（Brier 逐段变化）：%s"
                     % " -> ".join("%.3f" % c["brier"] for c in curve))
    L.append("")
    L.append("## 三、模式风险（P(再次失败)）")
    L.append("")
    if risks:
        L.append("| 模式 | 事件 | 后验 P(失败) | 90% 区间 | 触发器 |")
        L.append("|---|---|---|---|---|")
        for r in risks:
            L.append("| %s | %d | %.0f%% | %.0f%%-%.0f%% | %s |"
                     % (r["mode"], r["n"], 100 * r["p"], 100 * r["lo"], 100 * r["hi"],
                        "**触发**" if r["trigger"] else "—"))
    else:
        L.append("（还没有带模式码的判定）")
    L.append("")
    L.append("## 四、讲法排名（直讲后一次通过）")
    L.append("")
    if teaches:
        L.append("| 讲法 | 判定 | 通过 | 通过率 | 备注 |")
        L.append("|---|---|---|---|---|")
        for t in teaches:
            L.append("| %s | %d | %d/%d | %.0f%% | %s |"
                     % (t["teach"], t["n"], t["ok"], t["n"], 100 * t["p"],
                        "探索中（样本<3）" if t["explore"] else "可依赖"))
    else:
        L.append("（还没有直讲记录）")
    if abl:
        L.append("")
        L.append("## 五、模型变体消融（同一事件流上的 walk-forward 对比）")
        L.append("")
        L.append("| 变体 | Brier | LogLoss | ECE | 重拟合次数 |")
        L.append("|---|---|---|---|---|")
        for r in abl:
            L.append("| %s | %.3f | %.3f | %.3f | %d |"
                     % (r["variant"], r["brier"] or 0, r["logloss"] or 0, r["ece"] or 0, r["refits"]))
        L.append("")
        best = abl[0]["variant"] if abl else learn_mod.DEFAULT_VARIANT
        if len(rows) >= 20:
            L.append("- 当前采用：**%s**（样本 %d 条，按 Brier 最低者胜出）" % (best, len(rows)))
        else:
            L.append("- 当前采用：**%s**（样本只有 %d 条，变体间差异不显著；"
                     "本次 Brier 最低的是 %s，攒够 20 条判定再换）"
                     % (learn_mod.DEFAULT_VARIANT, len(rows), best))
    text = "\n".join(L) + "\n"
    if args.get("write"):
        write_text(ws.algo_report_file, text)
        row = [datetime.date.today().isoformat(), len(events_mod.load_events(ws.stream_file)),
               len(rows), len(read_lines_cached(ws.pred_log_file)),
               "%.3f" % wf["brier"] if wf["brier"] is not None else "-",
               "%.3f" % wf["logloss"] if wf["logloss"] is not None else "-",
               "%.3f" % wf["base_brier"] if wf["base_brier"] is not None else "-"]
        from .fsutil import append_text
        fresh = not os.path.exists(ws.calib_file) or os.path.getsize(ws.calib_file) == 0
        header = "\t".join(learn_mod.CALIB_COLS) + "\n" if fresh else ""
        append_text(ws.calib_file, header + "\t".join(str(x) for x in row) + "\n", bom_if_new=False)
        text = "（已写 %s，并追加一行到 %s）\n\n" % (
            os.path.relpath(ws.algo_report_file, ws.root), os.path.relpath(ws.calib_file, ws.root)) + text
    return True, dict(n=len(rows), theta=round(model.theta, 3), converged=model.converged,
                      iters=model.iters, brier=wf["brier"], base_brier=wf["base_brier"],
                      ece=wf["ece"], ablation=abl, risks=risks, teach=teaches,
                      curve=curve, model=model.as_dict(), _text=text)


def read_lines_cached(path):
    from .fsutil import read_lines
    return read_lines(path)


# ---------------------------------------------------------------- 脚手架
INDEX_SCAFFOLD = """# 知识库索引

> 本文件只放**文件地图**与**账目统计**；知识点明细与状态存在各章节文件里（单一事实源）。
> 下面两张表的数字由 `kb_reconcile` 按章节文件重算回写，别手改——缺行也会自动补上。

## 文件地图

| 文件 | 覆盖范围 |
|---|---|
| 科目/第NN讲_标题.md | 这一章覆盖的考点（一行一个文件） |

## 状态统计

> 口径：一行 = 一个章节文件；行首键 = 科目·第NN讲 / 科目·第N章（与文件名前缀一致）。
> 四列 = ✅ / ⚠️ / ❌ / ⬜ 的**知识点行数**。

| 科目·章节 | ✅ | ⚠️ | ❌ | ⬜ |
|---|---|---|---|---|

## 科目汇总

| 科目 | 文件数 | 知识点行 | ✅ | ⚠️ | ❌ | ⬜ |
|---|---|---|---|---|---|---|
| **合计** | **0** | **0** | **0** | **0** | **0** | **0** |
"""


def init_workspace(root, pack="demo", force=False):
    """在空目录里铺一套可跑的工作区（目录 + 示例包 + 配置文件）。"""
    from .config import DEFAULT_DIRS
    root = os.path.abspath(root)
    created = []
    for key in ("kb", "queue", "mistakes", "papers", "analysis", "state", "logs"):
        p = os.path.join(root, DEFAULT_DIRS[key])
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)
    for key in ("answers",):
        p = os.path.join(root, DEFAULT_DIRS["papers"], DEFAULT_DIRS[key])
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)
    cfg_path = os.path.join(root, "kaoyan.config.json")
    if not os.path.exists(cfg_path) or force:
        write_text(cfg_path, json.dumps({
            "pack": pack,
            "deliver": {"policy": "auto", "printer": "", "paper": "A4", "copies": 1},
            "learn": {"variant": "pooled_retry"},
        }, ensure_ascii=False, indent=2) + "\n")
        created.append(cfg_path)
    index_path = os.path.join(root, DEFAULT_DIRS["kb"], DEFAULT_DIRS["kb_index"])
    if not os.path.exists(index_path) or force:
        write_text(index_path, INDEX_SCAFFOLD)
        created.append(index_path)
    return root, created


TOOLS = {}

def _register():
    TOOLS.update({
        "env_capabilities": env_capabilities,
        "kb_overview": kb_overview,
        "kb_search": kb_search,
        "kb_chapter": kb_chapter,
        "kb_atoms": kb_atoms,
        "kb_validate": kb_validate,
        "kb_reconcile": kb_reconcile,
        "review_due": review_due,
        "review_grade": review_grade,
        "study_plan": study_plan,
        "paper_build": paper_build,
        "paper_deliver": paper_deliver,
        "learn_predict": learn_predict,
        "learn_report": learn_report,
    })


_register()
