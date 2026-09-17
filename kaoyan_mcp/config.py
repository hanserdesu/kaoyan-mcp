# -*- coding: utf-8 -*-
"""工作区定位与配置解析。

解析优先级（从高到低）：
  1. 显式参数（--root）或环境变量 KAOYAN_ROOT / KB_BASE
  2. --config 指定的 kaoyan.config.json
  3. 从当前目录逐级向上查找 kaoyan.config.json
  4. 从当前目录逐级向上查找含「知识库」的目录（自动探测既有工作区）

配置文件示例::

    {
      "root": ".",
      "pack": "demo",
      "dirs": {"kb": "知识库", "queue": "复习"},
      "deliver": {"policy": "auto", "printer": "", "paper": "A4", "copies": 1},
      "learn": {"fit_gamma": "auto"}
    }
"""
from __future__ import annotations

import io
import json
import os

CONFIG_FILENAME = "kaoyan.config.json"
PACK_FILENAME = "pack.json"
# 内容包清单的格式版本：引擎向下兼容（读得懂 <= 本值），读不动的更高版本直接报错，
# 而不是猜着读——这是"以后我维护/更新项目"的兼容预留。
PACK_SCHEMA_VERSION = 1

# 目录名默认值：与既有工作区保持一致，可用 config["dirs"] 覆盖。
DEFAULT_DIRS = {
    "kb": "知识库",
    "tree": "_目录树",
    "atom_index": "_原子索引.md",
    "kb_index": "_INDEX.md",
    "queue": "复习",
    "queue_file": "复习队列.md",
    "mistakes": "错题本",
    "papers": "试卷",
    "answers": "答案",
    "analysis": "分析",
    "stream": "画像事件流.csv",
    "pred_log": "预测日志.csv",
    "calib": "校准历史.tsv",
    "algo_report": "学习算法.md",
    "state": "学习状态",
    "current": "CURRENT.md",
    "logs": "日志",
    "pack_dir": "packs",
}


class ConfigError(RuntimeError):
    pass


def _read_json(path):
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_config_file(path):
    if not path:
        return {}
    if not os.path.isfile(path):
        raise ConfigError("配置文件不存在：%s" % path)
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ConfigError("配置文件必须是 JSON 对象：%s" % path)
    return data


def find_root(start=None, explicit=None):
    """定位工作区根目录；找不到返回 None。"""
    if explicit:
        p = os.path.abspath(os.path.expanduser(explicit))
        if not os.path.isdir(p):
            raise ConfigError("工作区目录不存在：%s" % p)
        return p
    env = os.environ.get("KAOYAN_ROOT") or os.environ.get("KB_BASE")
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        if os.path.isdir(p):
            return p
        raise ConfigError("环境变量指向的目录不存在：%s" % p)
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, CONFIG_FILENAME)):
            return d
        if os.path.isdir(os.path.join(d, DEFAULT_DIRS["kb"])):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def find_config_upwards(start):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, CONFIG_FILENAME)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load_pack(root, config):
    """读取内容包清单：<root>/pack.json 或 <root>/packs/<name>/pack.json。"""
    name = (config or {}).get("pack") or ""
    candidates = []
    if name:
        candidates.append(os.path.join(root, DEFAULT_DIRS["pack_dir"], name, PACK_FILENAME))
        candidates.append(os.path.join(root, name, PACK_FILENAME))
    candidates.append(os.path.join(root, PACK_FILENAME))
    for p in candidates:
        if os.path.isfile(p):
            data = _read_json(p)
            if not isinstance(data, dict):
                raise ConfigError("pack.json 必须是 JSON 对象：%s" % p)
            data.setdefault("name", os.path.basename(os.path.dirname(p)) or "default")
            data["_path"] = p
            sv = data.get("schema_version")
            if isinstance(sv, int) and sv > PACK_SCHEMA_VERSION:
                raise ConfigError(
                    "内容包 %s 声明的 schema_version=%d 高于本引擎支持的 %d，请升级 kaoyan-mcp"
                    % (data.get("name"), sv, PACK_SCHEMA_VERSION))
            return data
    return {"name": "default", "title": "未声明内容包（按目录自动识别）", "_path": None}


def guess_kind(subject):
    """按目录名推断学科门类：数学（M-* 得分范式）还是 408（S-*）。"""
    for n in ("高等数学", "线性代数", "概率论", "概率统计", "数学"):
        if n in subject:
            return "math"
    return "408"


def guess_paradigm(subject):
    return "M" if guess_kind(subject) == "math" else "S"


class Workspace(object):
    """一个工作区 = 根目录 + 配置 + 内容包清单。只做路径解析与只读读取。"""

    def __init__(self, root, config=None, pack=None, config_path=None):
        self.root = os.path.abspath(root)
        self.config = dict(config or {})
        self.config_path = config_path
        self.dirs = dict(DEFAULT_DIRS)
        self.dirs.update(self.config.get("dirs") or {})
        if pack is None:
            pack = load_pack(self.root, self.config)
        self.pack = pack
        # 内容包可以自带知识库目录（相对 pack.json 所在目录），
        # 这样「引擎 / 内容 / 个人数据」可以分开放，也能整包共享。
        if "kb" not in (self.config.get("dirs") or {}) and self.pack.get("kb_dir"):
            base = os.path.dirname(self.pack.get("_path") or os.path.join(self.root, PACK_FILENAME))
            self.dirs["kb"] = os.path.join(base, self.pack["kb_dir"])

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def dir(self, key):
        value = self.dirs[key]
        if os.path.isabs(value):
            return value
        return self.path(value)

    @property
    def kb_dir(self):
        return self.dir("kb")

    @property
    def tree_dir(self):
        return os.path.join(self.kb_dir, self.dirs["tree"])

    @property
    def queue_file(self):
        return os.path.join(self.dir("queue"), self.dirs["queue_file"])

    @property
    def stream_file(self):
        return os.path.join(self.dir("analysis"), self.dirs["stream"])

    @property
    def pred_log_file(self):
        return os.path.join(self.dir("analysis"), self.dirs["pred_log"])

    @property
    def calib_file(self):
        return os.path.join(self.dir("analysis"), self.dirs["calib"])

    @property
    def algo_report_file(self):
        return os.path.join(self.dir("analysis"), self.dirs["algo_report"])

    @property
    def current_file(self):
        return os.path.join(self.dir("state"), self.dirs["current"])

    @property
    def kb_index_file(self):
        return os.path.join(self.kb_dir, self.dirs["kb_index"])

    @property
    def atom_index_file(self):
        return os.path.join(self.kb_dir, self.dirs["atom_index"])

    @property
    def deliver_policy(self):
        d = dict(self.config.get("deliver") or {})
        d.setdefault("policy", "auto")
        d.setdefault("printer", "")
        d.setdefault("paper", "A4")
        d.setdefault("copies", 1)
        return d

    @property
    def subjects(self):
        """科目清单：优先取 pack.json，缺失就从知识库目录推断。"""
        declared = self.pack.get("subjects")
        if declared:
            return dict(declared)
        out = {}
        if os.path.isdir(self.kb_dir):
            for name in sorted(os.listdir(self.kb_dir)):
                if name.startswith("_") or not os.path.isdir(os.path.join(self.kb_dir, name)):
                    continue
                out[name] = {"paradigm": guess_paradigm(name), "kind": guess_kind(name)}
        return out

    @property
    def books(self):
        return dict(self.pack.get("books") or {})

    def subject_kind(self, subject):
        s = self.subjects.get(subject) or {}
        return s.get("kind") or guess_kind(subject)

    def subject_paradigm(self, subject):
        s = self.subjects.get(subject) or {}
        return s.get("paradigm") or guess_paradigm(subject)

    def resolve_pack_file(self, *parts):
        base = os.path.dirname(self.pack.get("_path") or os.path.join(self.root, PACK_FILENAME))
        return os.path.join(base, *parts)


def load_workspace(root=None, config_path=None, cwd=None):
    """一站式加载：定位根目录 -> 读配置 -> 读内容包。"""
    start = cwd or os.getcwd()
    cfg, cfg_used = {}, None
    if config_path:
        cfg_used = os.path.abspath(config_path)
        cfg = load_config_file(cfg_used)
        base = os.path.dirname(cfg_used)
        hint = os.path.join(base, cfg["root"]) if cfg.get("root") else base
        resolved = find_root(start=base, explicit=root or hint)
    else:
        resolved = find_root(start=start, explicit=root)
        if resolved:
            candidate = os.path.join(resolved, CONFIG_FILENAME)
            if os.path.isfile(candidate):
                cfg_used = candidate
                cfg = load_config_file(candidate)
        if cfg_used is None:
            found = find_config_upwards(start)
            if found:
                cfg_used = found
                cfg = load_config_file(found)
    if resolved is None:
        raise ConfigError(
            "找不到工作区：请用 --root 指定目录，或设置环境变量 KAOYAN_ROOT，"
            "或在工作区根目录放置 %s。" % CONFIG_FILENAME)
    return Workspace(resolved, cfg, config_path=cfg_used)
