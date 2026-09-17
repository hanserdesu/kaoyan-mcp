# -*- coding: utf-8 -*-
"""命令行入口：既能当 MCP 服务跑，也能当普通 CLI 用。

    python -m kaoyan_mcp serve --root <工作区>          # MCP（stdio）
    python -m kaoyan_mcp capabilities --root <工作区>   # 看本机有哪些交付通道
    python -m kaoyan_mcp validate --root <工作区>       # 知识库质量门禁
    python -m kaoyan_mcp overview / search / due / plan / report ...
    python -m kaoyan_mcp init <目录>                    # 铺一套空工作区
    python -m kaoyan_mcp pack list                      # 看仓库自带哪些内容包
    python -m kaoyan_mcp pack install 11408 --into <目录>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import ConfigError, load_workspace
from . import tools as tools_mod


def _add_common(p):
    p.add_argument("--root", help="工作区根目录（默认自动探测，或读环境变量 KAOYAN_ROOT）")
    p.add_argument("--config", help="kaoyan.config.json 路径")


def _print_payload(ok, payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        text = payload.get("_text") if isinstance(payload, dict) else payload
        print(text if text is not None else json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="kaoyan-mcp", description="考研私教 MCP 服务")
    ap.add_argument("--version", action="version", version="kaoyan-mcp %s" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="以 MCP stdio 服务运行")
    _add_common(p)

    p = sub.add_parser("init", help="在目录里铺一套空工作区")
    p.add_argument("dir")
    p.add_argument("--pack", default="demo")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("pack", help="内容包：列表 / 装入工作区 / 门禁（不需要工作区）")
    pack_parser = p
    pk = p.add_subparsers(dest="pack_cmd")
    pk.add_parser("list", help="列出仓库自带的内容包").add_argument(
        "--json", action="store_true")
    p_install = pk.add_parser("install", help="把自带内容包复制进工作区并指向它")
    p_install.add_argument("name")
    p_install.add_argument("--into", help="工作区目录（也可用 --root）")
    p_install.add_argument("--force", action="store_true", help="覆盖同名包 / 已存在的章节")
    p_verify = pk.add_parser("verify", help="对自带内容包跑知识库质量门禁（只读）")
    p_verify.add_argument("name")
    p_verify.add_argument("--json", action="store_true")

    for name, help_text in (("capabilities", "环境能力探查"), ("overview", "知识库总览"),
                            ("validate", "知识库质量门禁"), ("reconcile", "统计对账回写"),
                            ("due", "到期复习项"), ("plan", "今日计划"),
                            ("atoms", "原子清单")):
        p = sub.add_parser(name, help=help_text)
        _add_common(p)
        p.add_argument("--json", action="store_true", help="输出结构化 JSON")

    p = sub.add_parser("report", help="学习算法报告（校准 / 模式风险 / 讲法排名 / 变体消融）")
    _add_common(p)
    p.add_argument("--write", action="store_true", help="写 分析/学习算法.md 并追加校准历史")
    p.add_argument("--no-ablation", action="store_true", help="跳过模型变体消融（省时间）")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="检索知识库")
    _add_common(p)
    p.add_argument("query")
    p.add_argument("--subject")
    p.add_argument("--tier")
    p.add_argument("--status")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("chapter", help="读单章")
    _add_common(p)
    p.add_argument("subject")
    p.add_argument("chapter")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("grade", help="判定写回")
    _add_common(p)
    p.add_argument("atom")
    p.add_argument("verdict")
    p.add_argument("--note", default="")
    p.add_argument("--mode", default="")
    p.add_argument("--teach", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("predict", help="先预测后作答")
    _add_common(p)
    p.add_argument("--atom")
    p.add_argument("--next", action="store_true")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("paper", help="出卷并按环境交付")
    _add_common(p)
    p.add_argument("spec", help="卷子 JSON 文件路径（或 - 从标准输入读）")
    p.add_argument("--policy", help="print_strict / print / pdf / html / text / auto")
    p.add_argument("--printer")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    if args.cmd == "init":
        root, created = tools_mod.init_workspace(args.dir, args.pack, args.force)
        print("工作区已就绪：%s" % root)
        for p in created:
            print("  + %s" % p)
        print("下一步：装内容包  python -m kaoyan_mcp pack install <包名> --into %s" % root)
        print("       或把知识库（章节 md）放进「%s\\知识库」，然后 kaoyan-mcp validate --root %s"
              % (root, root))
        return 0

    if args.cmd == "pack":
        from . import pack as pack_mod
        if args.pack_cmd == "list":
            found = pack_mod.discover()
            if args.json:
                print(json.dumps(found, ensure_ascii=False, indent=2, default=str))
            else:
                print(pack_mod.list_text())
            return 0
        if args.pack_cmd == "install":
            into = args.into or args.root
            if not into:
                print("错误：请用 --into <工作区目录> 指定装到哪个工作区", file=sys.stderr)
                return 2
            try:
                print(pack_mod.install(args.name, into, force=args.force))
            except ConfigError as exc:
                print("错误：%s" % exc, file=sys.stderr)
                return 3
            return 0
        if args.pack_cmd == "verify":
            try:
                ok, payload = pack_mod.verify_text(args.name)
            except ConfigError as exc:
                print("错误：%s" % exc, file=sys.stderr)
                return 3
            return _print_payload(ok, payload, as_json=args.json)
        pack_parser.print_help()
        return 2

    try:
        ws = load_workspace(root=args.root, config_path=args.config)
    except ConfigError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 3

    d = lambda: {"root": args.root, "config": args.config}

    if args.cmd == "serve":
        from .server import run_server
        return run_server(ws)

    if args.cmd == "capabilities":
        return _print_payload(*tools_mod.env_capabilities(ws, d()), as_json=args.json)
    if args.cmd == "overview":
        return _print_payload(*tools_mod.kb_overview(ws, d()), as_json=args.json)
    if args.cmd == "validate":
        return _print_payload(*tools_mod.kb_validate(ws, d()), as_json=args.json)
    if args.cmd == "reconcile":
        return _print_payload(*tools_mod.kb_reconcile(ws, d()), as_json=args.json)
    if args.cmd == "due":
        return _print_payload(*tools_mod.review_due(ws, d()), as_json=args.json)
    if args.cmd == "plan":
        return _print_payload(*tools_mod.study_plan(ws, d()), as_json=args.json)
    if args.cmd == "atoms":
        return _print_payload(*tools_mod.kb_atoms(ws, d()), as_json=args.json)
    if args.cmd == "report":
        return _print_payload(*tools_mod.learn_report(
            ws, {"write": args.write, "ablation": not args.no_ablation}), as_json=args.json)
    if args.cmd == "search":
        return _print_payload(*tools_mod.kb_search(ws, dict(d(), query=args.query,
                                                            subject=args.subject, tier=args.tier,
                                                            status=args.status, limit=args.limit)),
                              as_json=args.json)
    if args.cmd == "chapter":
        return _print_payload(*tools_mod.kb_chapter(ws, dict(d(), subject=args.subject,
                                                             chapter=args.chapter)),
                              as_json=args.json)
    if args.cmd == "grade":
        return _print_payload(*tools_mod.review_grade(ws, dict(
            d(), atom=args.atom, verdict=args.verdict, note=args.note, mode=args.mode,
            teach=args.teach, dry_run=args.dry_run)), as_json=args.json)
    if args.cmd == "predict":
        return _print_payload(*tools_mod.learn_predict(ws, dict(
            d(), atom=args.atom, next=args.next, log=not args.no_log)), as_json=args.json)
    if args.cmd == "paper":
        raw = sys.stdin.read() if args.spec == "-" else open(args.spec, encoding="utf-8").read()
        spec = json.loads(raw)
        return _print_payload(*tools_mod.paper_deliver(ws, dict(
            d(), spec=spec, policy=args.policy, printer=args.printer)), as_json=args.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())
