# -*- coding: utf-8 -*-
"""交付通道自适应：探查本机能力，按「够用就好」的顺序把卷子交出去。

通道优先级（严格到宽松）：
  print  —— 实体打印（Windows 走 SumatraPDF，macOS/Linux 走 lp/lpr）
  pdf    —— 无头浏览器渲染 PDF（Edge/Chrome/Chromium）
  html   —— 直接给 HTML 文件（任何机器都能用浏览器打开并自行打印）
  text   —— 把卷面直接交给对话（无文件系统、平板、纯聊天客户端的兜底）

策略（config.deliver.policy）：
  print_strict —— 必须打印，打不出来就报错（备考纪律场景）
  auto         —— 能打就打，否则逐级退化（默认，适合大多数人）
  print / pdf / html / text —— 指定通道
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

BROWSER_CANDIDATES_WIN = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]
BROWSER_CANDIDATES_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BROWSER_NAMES_POSIX = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                       "microsoft-edge", "microsoft-edge-stable", "brave-browser"]
SUMATRA_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"SumatraPDF\SumatraPDF.exe"),
    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
]

# 虚拟打印机（打印到笔记/文件）出不了纸，不能当实体打印用。
VIRTUAL_PRINTER_HINTS = ("onenote", "print to pdf", "xps", "fax", "pdf24", "cutepdf",
                         "foxit", "adobe pdf", "virtual", "remote")


def is_virtual_printer(name):
    n = (name or "").lower()
    return any(h in n for h in VIRTUAL_PRINTER_HINTS)


def pick_printer(policy, printers):
    """选打印机：显式指定优先，否则第一台实体打印机。

    只剩虚拟打印机时返回空 + 原因，不做"打出去其实是存成文件"的静默降级。
    """
    explicit = ((policy or {}).get("printer") or "").strip()
    if explicit:
        return explicit, ""
    for n in printers:
        if not is_virtual_printer(n):
            return n, ""
    if printers:
        return "", ("只检测到虚拟打印机（%s）：要出纸请用 config 的 deliver.printer 指定实体打印机"
                    % "、".join(printers))
    return "", "未检测到打印机（可用 config.deliver.printer 指定）"


def _first_file(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return ""


def find_browser():
    env = os.environ.get("KAOYAN_BROWSER")
    if env and os.path.isfile(env):
        return env
    if IS_WINDOWS:
        return _first_file(BROWSER_CANDIDATES_WIN)
    if IS_MAC:
        return _first_file(BROWSER_CANDIDATES_MAC)
    for n in BROWSER_NAMES_POSIX:
        p = shutil.which(n)
        if p:
            return p
    return ""


def find_sumatra():
    if not IS_WINDOWS:
        return ""
    p = shutil.which("SumatraPDF")
    if p:
        return p
    return _first_file(SUMATRA_CANDIDATES)


def detect_print_tool():
    """本机能用来静默打印的工具：Windows 认 SumatraPDF，类 Unix 认 lp/lpr。"""
    if IS_WINDOWS and find_sumatra():
        return "sumatra"
    if not IS_WINDOWS and (shutil.which("lp") or shutil.which("lpr")):
        return "lp"
    return ""


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:  # 超时/文件缺失都归到「这条通道不可用」
        return -1, str(exc)


def list_printers():
    """尽力列出可用打印机；列不出来不算错（很多环境没有查询权限）。"""
    if IS_WINDOWS:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return []
        code, out = _run([ps, "-NoProfile", "-Command",
                          "Get-Printer | Select-Object -ExpandProperty Name"], timeout=20)
        if code != 0:
            return []
        return [x.strip() for x in out.splitlines() if x.strip()]
    code, out = _run(["lpstat", "-a"], timeout=15)
    if code != 0:
        code, out = _run(["lpstat", "-p"], timeout=15)
        if code != 0:
            return []
    names = []
    for ln in out.splitlines():
        head = ln.split()[0] if ln.split() else ""
        if head and head not in names and ":" in ln:
            names.append(head)
    return names


def probe(ws=None):
    policy = (ws.deliver_policy if ws is not None else {})
    browser = find_browser()
    sumatra = find_sumatra()
    lp = shutil.which("lp") or shutil.which("lpr") or ""
    printers = list_printers()
    default_printer, no_printer_reason = pick_printer(policy, printers)
    print_tool = detect_print_tool()
    channels = {
        "pdf": bool(browser),
        "html": True,
        "text": True,
        "print": bool(default_printer and print_tool and browser),
    }
    reasons = {
        "pdf": "" if browser else "未找到 Chromium 系浏览器（Edge/Chrome/Chromium）——可用 KAOYAN_BROWSER 指定路径",
        "print": "",
    }
    if no_printer_reason:
        reasons["print"] = no_printer_reason
    elif not print_tool:
        reasons["print"] = ("Windows 需要 SumatraPDF 才能静默打印"
                            if IS_WINDOWS else "未找到 lp/lpr 命令")
    elif not browser:
        reasons["print"] = "打印前需要浏览器把 HTML 渲染成 PDF"
    order = [c for c in ("print", "pdf", "html", "text") if channels[c]]
    return dict(os="windows" if IS_WINDOWS else ("macos" if IS_MAC else "linux"),
                python=sys.version.split()[0], browser=browser, sumatra=sumatra, lp=lp,
                printers=printers, virtual_printers=[n for n in printers if is_virtual_printer(n)],
                printer=default_printer, print_tool=print_tool,
                channels=channels, reasons=reasons, order=order,
                policy=policy.get("policy", "auto"))


def render_pdf(html_path, pdf_path=None, timeout=90, browser=""):
    """HTML -> PDF（无头 Chromium）。返回 (ok, pdf_path, message)。"""
    browser = browser or find_browser()
    if not browser:
        return False, "", "未找到浏览器，无法渲染 PDF"
    pdf_path = pdf_path or (os.path.splitext(html_path)[0] + ".pdf")
    if os.path.exists(pdf_path):
        try:
            os.remove(pdf_path)
        except OSError:
            pass
    uri = "file:///" + os.path.abspath(html_path).replace("\\", "/").lstrip("/")
    if not uri.startswith("file:///"):
        uri = "file://" + os.path.abspath(html_path).replace("\\", "/")
    code, out = _run([browser, "--headless=new", "--disable-gpu", "--no-first-run",
                      "--no-pdf-header-footer", "--print-to-pdf=" + pdf_path, uri],
                     timeout=timeout)
    if not os.path.exists(pdf_path):
        code2, out2 = _run([browser, "--headless", "--disable-gpu",
                            "--print-to-pdf=" + pdf_path, uri], timeout=timeout)
        if not os.path.exists(pdf_path):
            return False, "", "浏览器渲染 PDF 失败：%s" % (out.strip()[:300] or out2.strip()[:300])
    return True, pdf_path, ""


def count_pdf_pages(pdf_path):
    """数 PDF 页数：只读文件头部的 /Count，不依赖任何第三方库。"""
    try:
        with open(pdf_path, "rb") as f:
            blob = f.read(4 * 1024 * 1024)
        m = re.search(rb"/Count\s+(\d+)", blob)
        if m:
            return int(m.group(1))
        return len(re.findall(rb"/Type\s*/Page[^s]", blob))
    except Exception:
        return 0


def print_pdf(pdf_path, printer, copies=1, timeout=180):
    """把 PDF 送去实体打印。返回 (ok, evidence, message)。"""
    evidence = []
    if IS_WINDOWS:
        sumatra = find_sumatra()
        if not sumatra:
            return False, evidence, ("Windows 静默打印需要 SumatraPDF（winget install SumatraPDF），"
                                     "或用浏览器打开 PDF 手动打印")
        args = [sumatra, "-print-to", printer, "-silent", "-exit-when-done"]
        for i in range(copies):
            code, out = _run(args + [pdf_path], timeout=timeout)
            evidence.append("SumatraPDF 第 %d 份：exit=%s %s" % (i + 1, code, out.strip()[:150]))
            if code != 0:
                return False, evidence, "SumatraPDF 打印失败（exit=%s）" % code
        return True, evidence, ""
    lp = shutil.which("lp") or shutil.which("lpr")
    if not lp:
        return False, evidence, "未找到 lp/lpr，无法打印"
    if os.path.basename(lp).startswith("lp"):
        cmd = [lp, "-d", printer, "-n", str(copies), pdf_path]
    else:
        cmd = [lp, "-P", printer, "-#" + str(copies), pdf_path]
    code, out = _run(cmd, timeout=timeout)
    evidence.append("%s：exit=%s %s" % (os.path.basename(lp), code, out.strip()[:200]))
    return code == 0, evidence, "" if code == 0 else "打印命令返回非零退出码"


def deliver(ws, built, spec=None, policy=None):
    """按策略交付一张卷子。返回交付结果（含用了哪条通道、证据与告警）。"""
    spec = spec or {}
    policy = policy or ws.deliver_policy
    want = (policy.get("policy") or "auto").strip()
    info = probe(ws)
    result = dict(ok=False, requested=want, channel="", artifacts={}, evidence=[],
                  warnings=[], probe=info)
    html_path = built.get("html") or ""
    if not html_path or not os.path.isfile(html_path):
        result["warnings"].append("卷面 HTML 不存在：%s" % html_path)
        return result

    def _pdf():
        ok, pdf, msg = render_pdf(html_path, browser=info["browser"])
        if ok:
            result["artifacts"]["pdf"] = pdf
            pages = count_pdf_pages(pdf)
            result["artifacts"]["pdf_pages"] = pages
            result["evidence"].append("渲染 PDF：%s（%d 页）" % (pdf, pages))
            expect = built.get("pages_estimate")
            if expect and pages and pages > expect:
                result["warnings"].append(
                    "实际 %d 页 > 估算 %d 页：书写区过多，建议压缩后重出（别浪费纸）" % (pages, expect))
        else:
            result["warnings"].append(msg)
        return ok

    def _print():
        if not _pdf():
            return False
        pages = result["artifacts"].get("pdf_pages") or 0
        expect = built.get("pages_estimate")
        if expect and pages and pages > expect:
            result["warnings"].append(
                "实际 %d 页 > 估算 %d 页：先压缩书写区再打印，本次不出纸（别浪费纸）" % (pages, expect))
            return False
        printer = policy.get("printer") or info["printer"]
        copies = int(policy.get("copies") or 1)
        ok, ev, msg = print_pdf(result["artifacts"]["pdf"], printer, copies)
        result["evidence"].extend(ev)
        if not ok:
            result["warnings"].append(msg)
        return ok

    def _text():
        result["artifacts"]["text"] = render_sheet_text(spec)
        return True

    if want in ("print_strict", "print", "auto") and info["channels"]["print"]:
        if _print():
            result.update(ok=True, channel="print")
            return result
        result["warnings"].append("这次没出纸，退化为文件通道（原因见上）")
        if want == "print_strict":
            result["warnings"].append("策略为 print_strict（必须打印），不退化：请处理打印机后重试")
            return result
    elif want in ("print_strict", "print"):
        result["warnings"].append("打印通道不可用：" + (info["reasons"]["print"] or "未知原因"))
        if want == "print_strict":
            result["warnings"].append("策略为 print_strict（必须打印），未退化到文件通道")
            return result
    if want == "text":
        _text()
        result.update(ok=True, channel="text",
                      artifacts=dict(result["artifacts"], answer_path=built.get("answer") or ""))
        return result
    if want in ("print_strict", "print", "auto", "pdf") and info["channels"]["pdf"] and _pdf():
        result.update(ok=True, channel="pdf")
        return result
    # 最后一档：HTML 文件（到处都能打开，用户自己在浏览器里 Ctrl+P）
    result.update(ok=True, channel="html",
                  artifacts=dict(result["artifacts"], html=html_path,
                                 answer=built.get("answer") or ""))
    if want in ("auto", "pdf"):
        result["warnings"].append("本机没有打印/PDF 通道，已交付 HTML：浏览器打开后自行打印即可")
    return result


def render_sheet_text(spec):
    """纯文本卷面：给没有文件系统/打印机的客户端（平板、纯聊天）用。"""
    L = []
    L.append("【%s】%s ｜ %s" % (spec.get("subject") or "", spec.get("title") or "练习卷",
                                spec.get("date") or datetime.date.today().isoformat()))
    if spec.get("instructions"):
        L.append(spec["instructions"])
    L.append("")
    for q in spec.get("questions") or []:
        pts = "（%s 分）" % q.get("points") if q.get("points") else ""
        L.append("%s. %s%s" % (q.get("id") or "", (q.get("stem") or "").replace("\n", " "), pts))
        L.append("")
    return "\n".join(L)
