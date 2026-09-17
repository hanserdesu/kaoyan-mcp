# -*- coding: utf-8 -*-
"""MCP 服务端（stdio，JSON-RPC 2.0，纯标准库）。

为什么手写而不依赖官方 SDK：这套引擎要能在「只有 Python、没有网络、没有 pip install」
的机器上跑起来——考生手里的电脑经常就是这种状态。协议实现只覆盖 MCP 必需的部分：
initialize / tools / resources / prompts / ping，足够所有主流客户端（Claude Desktop、
Codex、Cline、Continue、Cursor 等）直接接入。

stdout 只允许出现协议消息；一切日志走 stderr。
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

from . import __version__, tools
from .config import ConfigError
from .fsutil import read_text
from .kb.parse import load_chapters, iter_chapter_files

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
DEFAULT_PROTOCOL = PROTOCOL_VERSIONS[0]

INSTRUCTIONS = """这是一套「考研私教」知识库与学习算法服务。

使用顺序建议：
1. 先调 env_capabilities 看本机有哪些交付通道（打印 / PDF / HTML / 纯文本）。
2. 用 kb_overview / kb_search / kb_chapter 定位知识点；知识库是唯一事实源。
3. 每天开工先 study_plan 或 review_due，按配额收到期项；做完立刻 review_grade 写回，
   判定会进事件流，喂给学习算法越用越准。
4. 出卷用 paper_build 生成卷面与答案（答案永远与卷面分离），交付用 paper_deliver；
   策略 print_strict 表示必须打印（打不出来就报错），auto 表示能打就打、否则逐级退化。
5. 答题前先 learn_predict 预测通过概率（先预测后作答，才会得到诚实的校准曲线）。
6. 动过知识库后跑 kb_validate 与 kb_reconcile，保证账目对得上。
"""

TOOL_SPECS = [
    ("env_capabilities", "探查本机交付能力（打印机 / 浏览器 / PDF 工具 / 可用通道与策略）", {}),
    ("kb_overview", "知识库总览：科目、章节数、原子数、档位与状态分布",
     {"subject": {"type": "string", "description": "只看某个科目（可选）"}}),
    ("kb_search", "一次命中检索：按关键词找原子/组合/章节，返回 file:line 与状态",
     {"query": {"type": "string", "description": "关键词（多个词是 AND）"},
      "subject": {"type": "string"}, "tier": {"type": "string", "enum": ["S", "A", "B"]},
      "status": {"type": "string"}, "limit": {"type": "integer"}}),
    ("kb_chapter", "读单章：返回章节全文与结构化原子/组合行",
     {"subject": {"type": "string"}, "chapter": {"type": "string", "description": "文件名或标题关键词"},
      "max_chars": {"type": "integer"}}),
    ("kb_atoms", "原子清单（可按科目/档/状态过滤）",
     {"subject": {"type": "string"}, "tier": {"type": "string"},
      "status": {"type": "string"}, "limit": {"type": "integer"}}),
    ("kb_validate", "知识库质量门禁：结构契约、引用完整性、状态账目对账、出处页锚",
     {"index_check": {"type": "boolean"}, "limit": {"type": "integer"}}),
    ("kb_reconcile", "把 _INDEX.md 的统计表按章节文件重算回写（幂等）", {}),
    ("review_due", "到期复习项（分层 A/B/C + 分值档 + 每日配额）",
     {"date": {"type": "string"}, "quota": {"type": "integer"}, "subject": {"type": "string"}}),
    ("review_grade", "判定写回：复习队列 + 章节状态格 + 事件流 + _INDEX 对账",
     {"atom": {"type": "string"}, "verdict": {"type": "string", "description": "✅/⚠️/❌ 或 流畅/勉强/失败"},
      "note": {"type": "string"}, "mode": {"type": "string", "description": "混淆模式码，如 CM-02"},
      "teach": {"type": "string", "description": "讲法码，如 TH-03"},
      "dry_run": {"type": "boolean"}, "sync_index": {"type": "boolean"}}),
    ("study_plan", "今日计划：到期复习 + 未清错题 + 断点",
     {"date": {"type": "string"}, "quota": {"type": "integer"}}),
    ("paper_build", "出一张卷：生成卷面 HTML 与答案 md（答案与卷面分离）",
     {"spec": {"type": "object", "description": "title/subject/paper/questions[...](含 stem/answer/atoms/work_mm)"}}),
    ("paper_deliver", "出卷并按环境交付：打印 / PDF / HTML / 纯文本",
     {"spec": {"type": "object"}, "policy": {"type": "string", "enum": ["print_strict", "print", "pdf", "html", "text", "auto"]},
      "printer": {"type": "string"}}),
    ("learn_predict", "答题前预测通过概率（先预测后作答；预测写入预测日志）",
     {"atom": {"type": "string"}, "next": {"type": "boolean"}, "event": {"type": "string"},
      "tier": {"type": "string"}, "hint": {"type": "string"}, "log": {"type": "boolean"}}),
    ("learn_report", "学习算法报告：能力/难度、校准、模式风险、讲法排名、变体消融",
     {"write": {"type": "boolean"}, "ablation": {"type": "boolean"}}),
]

PROMPTS = [
    ("teaching", "教学协议：先考后讲、组合讲考、断点直讲、状态写回", []),
    ("grading", "判卷协议：按过程判、断哪讲哪、错因入账", []),
    ("modeling", "建模协议：原子/组合两层、证据纪律、出处用 PDF 页", []),
    ("paper", "出卷协议：来源标签、换数不重放、答案隔离、页面预算", []),
]

PROMPT_TEXT = {
    "teaching": """你是这位考生的私教。协议：
1. 先考后讲：新知识点先出一个小检验点，看他能不能自己说对，再决定讲什么。
2. 组合讲考：一组相关知识点一次讲透，再出一张组合卷考察整组（优先一题多点）；题量按分值档配比（S 2-3 / A 1-2 / B 0-1），单点变式最多 2 题。
3. 判定三级：流畅通过（秒答 + 完整 why）升一档；勉强通过同档重测；失败则回 1d 并直讲断点。
4. 断点直讲：断在哪讲哪，不讲整章；讲完立刻换数微验收。
5. 落盘：判定用 review_grade 写回（队列/章节状态/事件流），别只在聊天里说。
6. 出题来源：知识库原子 + 组合（先 kb_search 定位，再按组合的「断点」设计埋点）。""",
    "grading": """判卷协议：
1. 按过程判，不只看答案；步骤分与结果分分开说。
2. 错就断哪层：指出断在哪个原子（ID）与组合的哪个环节。
3. 错因入账：E1 概念 / E2 记号 / E3 计算 / E4 步骤 / E5 阅读 / E6 时间 / E7 心态（按包内口径）。
4. 判完立刻写回：review_grade（判定 + 模式码 + 讲法码），错题另行登记。
5. 讲评只讲断点，不重讲整章；讲完给一道换数题当场验证。""",
    "modeling": """建模协议（把教材变成可考的知识库）：
1. 粒度两层：原子（最小可得分单元，A 表）+ 组合（考点怎么拼成题，C 表）。
2. 出处必须可定位：PDF 页锚 @pNNN（+ 节号）；不用「大概某页」。
3. 证据纪律：【实测】与【建模推演】分开标；没实测过的不许写「已掌握」。
4. ID 命名：<科目码>-<讲/章>-<序号>，删除即作废、不改号补位。
5. 写完跑 kb_validate，把 FAIL 清零、WARN 逐条判定后再跑 kb_reconcile 对账。""",
    "paper": """出卷协议：
1. 取题：真题（已核实）> 真题题眼改编 > 仿真题；同一考点换数不重放。
2. 排序与配比：分值档 S > A > B，叠加弱度（⚠️/未清错因/模式复发）；题量按档位给（S 2-3 / A 1-2 / B 0-1），单点变式最多 2 题。
3. 卷面只放题目；答案写到独立文件（paper_build 已强制分离）。
4. 版面：先估页数，再按环境交付（print_strict 打不出来就报错，别偷偷降级）。
5. 打印后核对页数：页数超出估算说明书写区过多，压一压重出，别浪费纸。""",
}


class MCPServer(object):
    def __init__(self, ws, stdin=None, stdout=None, stderr=None, verbose=False):
        self.ws = ws
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.verbose = verbose
        self.initialized = False

    # ---- 协议管道 ----
    def log(self, msg):
        try:
            self.stderr.write("[kaoyan-mcp] %s\n" % msg)
            self.stderr.flush()
        except Exception:
            pass

    def send(self, obj):
        self.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def error(self, mid, code, message, data=None):
        err = dict(code=code, message=message)
        if data is not None:
            err["data"] = data
        self.send(dict(jsonrpc="2.0", id=mid, error=err))

    def result(self, mid, payload):
        self.send(dict(jsonrpc="2.0", id=mid, result=payload))

    def serve_forever(self):
        for raw in self.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                self.error(None, -32700, "JSON 解析失败")
                continue
            if isinstance(msg, list):
                for m in msg:
                    self.dispatch(m)
            else:
                self.dispatch(msg)
        return 0

    def dispatch(self, msg):
        if not isinstance(msg, dict):
            self.error(None, -32600, "请求必须是 JSON 对象")
            return
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if method is None:
            return  # 响应消息，忽略
        handler = getattr(self, "m_" + method.replace("/", "_").replace(".", "_"), None)
        if handler is None:
            if mid is not None:
                self.error(mid, -32601, "不支持的方法：%s" % method)
            return
        try:
            payload = handler(params)
        except Exception as exc:
            self.log(traceback.format_exc())
            if mid is not None:
                self.error(mid, -32603, "内部错误：%s" % exc)
            return
        if mid is None:
            return  # 通知，不回
        if payload is None:
            payload = {}
        self.result(mid, payload)

    # ---- 生命周期 ----
    def m_initialize(self, params):
        want = params.get("protocolVersion") or ""
        version = want if want in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL
        self.initialized = True
        return dict(protocolVersion=version,
                    capabilities=dict(tools=dict(listChanged=False),
                                      resources=dict(subscribe=False, listChanged=False),
                                      prompts=dict(listChanged=False)),
                    serverInfo=dict(name="kaoyan-mcp", version=__version__),
                    instructions=INSTRUCTIONS)

    def m_ping(self, params):
        return {}

    def m_notifications_initialized(self, params):
        return None

    def m_notifications_cancelled(self, params):
        return None

    def m_logging_setLevel(self, params):
        return {}

    # ---- 工具 ----
    def m_tools_list(self, params):
        out = []
        for name, desc, props in TOOL_SPECS:
            schema = {"type": "object", "properties": props or {},
                      "additionalProperties": False}
            out.append(dict(name=name, description=desc, inputSchema=schema))
        return dict(tools=out)

    def m_tools_call(self, params):
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        fn = tools.TOOLS.get(name)
        if fn is None:
            return dict(content=[dict(type="text", text="未知工具：%s" % name)], isError=True)
        try:
            ok, payload = fn(self.ws, args)
        except ConfigError as exc:
            return dict(content=[dict(type="text", text="配置错误：%s" % exc)], isError=True)
        except Exception as exc:
            self.log(traceback.format_exc())
            return dict(content=[dict(type="text", text="工具执行失败：%s" % exc)], isError=True)
        return dict(content=[_content(payload)], isError=not ok)

    # ---- 资源 ----
    def resource_list(self):
        out = [dict(uri="kb://index", name="知识库索引", mimeType="text/markdown"),
               dict(uri="kb://pack", name="内容包清单", mimeType="application/json")]
        for subj, path in iter_chapter_files(self.ws.kb_dir):
            out.append(dict(uri="kb://chapter/%s/%s" % (subj, os.path.basename(path)),
                            name="%s · %s" % (subj, os.path.basename(path)),
                            mimeType="text/markdown"))
        for name in os.listdir(self.ws.kb_dir) if os.path.isdir(self.ws.kb_dir) else []:
            if name.startswith("_规格") and name.endswith(".md"):
                out.append(dict(uri="kb://spec/%s" % name, name=name,
                                mimeType="text/markdown"))
        return out

    def m_resources_list(self, params):
        return dict(resources=self.resource_list())

    def m_resources_read(self, params):
        uri = params.get("uri") or ""
        text, mime = None, "text/markdown"
        if uri == "kb://index":
            text = read_text(self.ws.kb_index_file)
        elif uri == "kb://pack":
            text = read_text(self.ws.pack.get("_path") or "") or json.dumps(
                self.ws.pack, ensure_ascii=False, indent=2)
            mime = "application/json"
        elif uri.startswith("kb://chapter/"):
            rest = uri[len("kb://chapter/"):]
            subj, _, fn = rest.partition("/")
            path = os.path.join(self.ws.kb_dir, subj, fn)
            text = read_text(path)
            if text is None:
                return dict(contents=[])
        elif uri.startswith("kb://spec/"):
            name = uri[len("kb://spec/"):]
            text = read_text(os.path.join(self.ws.kb_dir, name))
            if text is None:
                return dict(contents=[])
        if text is None:
            return dict(contents=[])
        return dict(contents=[dict(uri=uri, mimeType=mime, text=text)])

    # ---- 提示词 ----
    def m_prompts_list(self, params):
        return dict(prompts=[dict(name=n, description=d, arguments=a) for n, d, a in PROMPTS])

    def m_prompts_get(self, params):
        name = params.get("name") or ""
        text = PROMPT_TEXT.get(name)
        if text is None:
            raise ValueError("未知提示词：%s" % name)
        return dict(description=next((d for n, d, _ in PROMPTS if n == name), ""),
                    messages=[dict(role="user", content=dict(type="text", text=text))])


def _content(payload):
    """把工具返回变成 MCP content 块：有 _text 就给人话，其余结构化数据放 JSON。"""
    if isinstance(payload, str):
        return dict(type="text", text=payload)
    if isinstance(payload, dict):
        text = payload.get("_text")
        rest = dict((k, v) for k, v in payload.items() if k != "_text")
        if text is None:
            text = json.dumps(rest, ensure_ascii=False, indent=2, default=str)
        if rest:
            text = text + "\n\n---\n" + json.dumps(rest, ensure_ascii=False, indent=2, default=str)
        return dict(type="text", text=text)
    return dict(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))


def run_server(ws, stdin=None, stdout=None, stderr=None, verbose=False):
    return MCPServer(ws, stdin, stdout, stderr, verbose).serve_forever()
