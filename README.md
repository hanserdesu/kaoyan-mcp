# kaoyan-mcp

把「知识库 + 间隔复习 + 出卷打印 + 学习算法」封装成 MCP 工具，任何支持 MCP 的 AI 客户端（Claude Desktop、Cursor、Codex 等）都能直接调用。

它要解决的是陪学 AI 最要命的那个毛病：**聊完就忘**。这次讲明白了，下次它并不知道你哪块虚。
这个服务把学习状态落到你本地的文件里，AI 每次通过工具读状态、写判定，于是「越用越准」是能被回测验证的，而不是嘴上说说。

零第三方依赖（只用 Python 标准库，Python 3.9+ 即可），内容与个人数据都在你自己的目录里，默认不出本地。

仓库自带的内容包目前覆盖 **11408（考研数学一 + 408）**，知识库随仓库一起分发；换考试方向只要换一个内容包，引擎不用动。

## 目录

- [它由什么组成](#它由什么组成)
- [快速开始](#快速开始)
- [内容包（知识库随包分发）](#内容包知识库随包分发)
- [接到 AI 客户端](#接到-ai-客户端)
- [没有打印机也能用](#没有打印机也能用)
- [工具清单](#工具清单)
- [工作区布局](#工作区布局)
- [知识库格式契约](#知识库格式契约)
- [质量门禁](#质量门禁)
- [学习算法](#学习算法)
- [配置](#配置)
- [开发](#开发)
- [许可](#许可)

## 它由什么组成

四层，都能单独用：

1. **知识库**：一章一个 markdown 文件，里面两张表——原子表（最小可得分单元）与组合表（考点怎么拼成一道题）。带出处页锚、掌握状态与证据。
2. **复习调度**：遗忘曲线档位 1/2/4/7/15/30/60 天，分 A/B/C 三层并限每日配额，按分值档 → 弱度 → 逾期排序取用。
3. **出卷与交付**：题目与答案分离；卷面渲染成 A4/A5/Letter 的 HTML，再按本机能力交付——能打印就打印，不能就退成 PDF、HTML、纯文本。
4. **学习算法**：能力与难度做 MAP 拟合，参数化到「原子 / 档位 / 科目 / 遗忘 / 同日复测」；预测先写日志、答完做走前回测，另有混淆模式风险、讲法排名与模型变体消融。

## 快速开始

需要 Python 3.9 或更新版本。

```bash
git clone https://github.com/hanserdesu/kaoyan-mcp.git
cd kaoyan-mcp
pip install -e .
```

铺一个空工作区：

```bash
python -m kaoyan_mcp init ./我的备考
cd 我的备考
```

再把自带的内容包装进来（不装也能跑，只是知识库是空的，见下一节）：

```bash
python -m kaoyan_mcp pack list                         # 看仓库自带哪些内容包
python -m kaoyan_mcp pack install 11408 --into .       # 把 11408 知识包装进当前工作区
```

看看这台机器能干什么、知识库健不健康：

```bash
python -m kaoyan_mcp capabilities          # 打印机 / 浏览器 / 可用通道
python -m kaoyan_mcp overview              # 科目、章节、原子、状态分布
python -m kaoyan_mcp validate              # 质量门禁
python -m kaoyan_mcp due                   # 今天到期的复习项
python -m kaoyan_mcp plan                  # 今日计划
```

工作区不用每次指定：从当前目录逐级向上找 `kaoyan.config.json` 或 `知识库` 目录；也可以用 `--root` 或环境变量 `KAOYAN_ROOT` 指定。

## 内容包（知识库随包分发）

引擎、内容、个人数据是三样东西：`kaoyan_mcp` 是引擎，`packs/<名字>/` 是内容包，学习状态存在你自己的工作区里。换考试方向等于换包，代码不动。

仓库自带一个包：

| 包名 | 覆盖范围 | 体量 |
|---|---|---|
| `11408` | 考研数学一（高数 / 线代 / 概率）+ 408（数据结构 / 组成原理 / 操作系统 / 计算机网络） | 58 章、2455 个原子、696 个组合 |

出处一律用教材 PDF 页锚，状态全部从 ⬜ 起步——个人掌握进度不进包，你在自己的工作区里积累。

```bash
python -m kaoyan_mcp pack list                    # 列出自带包（含 schema_version）
python -m kaoyan_mcp pack install 11408 --into ./我的备考
python -m kaoyan_mcp pack verify 11408            # 只读门禁：FAIL / WARN 逐条列出
```

装包做两件事：把 `packs/<名字>/` 复制进工作区，并把 `kaoyan.config.json` 的 `pack` 指向它。包里的 `kb_dir` 声明知识库位置，于是知识库随包走；个人数据（状态、复习队列、事件流、试卷）留在工作区，不会被包覆盖。

工作区里已经写过章节时默认拒绝装包：加 `--force` 才覆盖，或在配置里用 `dirs.kb` 指定自己的知识库目录。

加一个方向不用改引擎，照这个契约放一份目录就行：

```
packs/<名字>/
  pack.json      # schema_version / name / title / exam / subjects / books / kb_dir
  kb/            # 知识库：一章一个 markdown，格式见下文
```

`schema_version` 是留给以后换版的兼容位：引擎读得懂自己支持的版本；遇到比它新的包会直接报错让你升级引擎，而不是猜着读。目前 `11408` 是唯一随仓库分发的包。

## 接到 AI 客户端

服务走 MCP 的 stdio 传输。典型配置：

```json
{
  "mcpServers": {
    "kaoyan": {
      "command": "python",
      "args": ["-m", "kaoyan_mcp", "serve", "--root", "/绝对路径/我的备考"]
    }
  }
}
```

Windows 上把 `python` 换成 `python.exe` 的绝对路径更稳；也可以不写 `--root`，改成给客户端设一个 `KAOYAN_ROOT` 环境变量。

除了工具，服务还带 4 段提示词（`teaching` / `grading` / `modeling` / `paper`），客户端支持提示词时可以直接取用——里面写的是教学与判卷的口径，比如「先考后讲、逐点推进」「按过程判、断哪讲哪」「出处必须可定位」。

## 没有打印机也能用

同一个出卷工具，按本机探测到的能力自动选通道。交付策略写在配置里：

| 策略 | 行为 | 适合谁 |
|---|---|---|
| `auto`（默认） | 能打印就打印，否则依次退到 PDF、HTML、纯文本 | 大多数人 |
| `print_strict` | 必须打印，打不出来或页数超估算就报错、不退化 | 靠「必须落笔」逼自己的备考纪律党 |
| `print` | 优先打印，失败则退到 PDF | 有打印机但不稳定 |
| `pdf` | 只出 PDF | 想自己拿去打印或平板批注 |
| `html` | 只出 HTML 文件 | 没有浏览器自动化、想自己 Ctrl+P |
| `text` | 把卷面直接交给对话 | 平板、纯聊天客户端、没有文件系统 |

通道与探测方式：

| 通道 | 依赖 | 说明 |
|---|---|---|
| `print` | Windows：装有 SumatraPDF；macOS/Linux：有 `lp`/`lpr`；外加一个 Chromium 系浏览器和一台实体打印机 | 先用无头浏览器把 HTML 渲染成 PDF，再送去实体打印 |
| `pdf` | Edge / Chrome / Chromium 任一（可用 `KAOYAN_BROWSER` 指定） | 渲染完顺便核对页数 |
| `html` | 无 | 任何机器都能打开；离线可用 |
| `text` | 无 | 卷面转纯文本交给对话 |

`capabilities` 会把探测结果和「这条通道为什么不可用」一次说清，例如缺少 SumatraPDF 时会直接告诉你装什么，而不是静默失败。

打印的两条硬规矩：一是虚拟打印机不算打印机——OneNote、Microsoft Print to PDF、Fax 这类设备出不了纸，不会被自动选中，机器上只有这类设备时 `capabilities` 会直接说明，你要用就自己在 config 里写 `deliver.printer`；二是页数超估算不出纸——渲染出来比版面预算多页，就先退回 PDF 让你压缩书写区，不浪费纸（`print_strict` 下直接报错）。

## 工具清单

| 工具 | 做什么 |
|---|---|
| `env_capabilities` | 探测本机交付能力与可用通道 |
| `kb_overview` | 知识库总览：科目、章节、原子、档位与状态分布 |
| `kb_search` | 一次命中检索：按关键词找原子/组合/章节，返回 file:line 与状态 |
| `kb_chapter` | 读单章：全文加结构化原子/组合行 |
| `kb_atoms` | 原子清单，可按科目/档/状态过滤 |
| `kb_validate` | 质量门禁（只读） |
| `kb_reconcile` | 按章节文件重算并回写 `_INDEX.md` 统计（幂等） |
| `review_due` | 到期复习项：分层 + 分值档 + 每日配额 |
| `review_grade` | 判定写回：队列档位、章节状态格、事件流、账目对账 |
| `study_plan` | 今日计划：到期复习 + 未清错题 + 断点 |
| `paper_build` | 出一张卷：卷面 HTML 与答案 markdown（答案与题目分离） |
| `paper_deliver` | 出卷并按环境交付：打印 / PDF / HTML / 纯文本 |
| `learn_predict` | 答题前预测通过概率（先预测后作答，写入预测日志） |
| `learn_report` | 算法报告：能力与难度、校准、模式风险、讲法排名、变体消融 |

同一个引擎也有一套等价的命令行入口，方便不开客户端时直接跑，或写进脚本：

```bash
python -m kaoyan_mcp search 分布函数法
python -m kaoyan_mcp chapter 概率论 第02讲
python -m kaoyan_mcp grade GL-02-021 ✅ --mode CM-02
python -m kaoyan_mcp predict --next
python -m kaoyan_mcp paper 卷子.json --policy html
python -m kaoyan_mcp report --write
python -m kaoyan_mcp pack list
python -m kaoyan_mcp pack install 11408 --into ./我的备考
python -m kaoyan_mcp pack verify 11408
```

## 工作区布局

`kaoyan_mcp init` 会铺出这样一套目录，每一块的职责都不重叠：

| 目录 / 文件 | 角色 |
|---|---|
| `知识库/` | 一章一个 markdown，知识点状态的唯一来源 |
| `知识库/_INDEX.md` | 文件地图与状态统计（对账用） |
| `复习/复习队列.md` | 间隔复习账本 |
| `错题本/错题本.md` | 错因档案 |
| `试卷/` 与 `试卷/答案/` | 卷面与答案，永远分开 |
| `分析/画像事件流.csv` | append-only 的事件账本，算法唯一数据源 |
| `分析/预测日志.csv` | 先预测后作答的流水 |
| `分析/校准历史.tsv` | 每次回测的校准指标 |
| `学习状态/CURRENT.md` | 当前位置：断点、挂题、队列 |
| `packs/<包名>/` | 内容包：包清单 + 自带知识库目录 |
| `kaoyan.config.json` | 各目录名、交付策略、算法开关 |

知识包和个人数据是分开的：包可以分享给别人，个人数据（状态、队列、事件流）默认留在本地。

## 知识库格式契约

原子表按最小可得分单元切分，组合表记录考点是怎么拼成题的：

```markdown
| ID | 原子知识点 | 档 | 得分范式 | 出处 | 状态 | 证据 / 备注 |
|---|---|---|---|---|---|---|
| GL-02-001 | 分布函数法：F_Y(y)=P(g(X) ≤ y) | S | M-构造 | §2.4@p57 | ⚠️ | 【实测 2026-09-13】方向反，已直讲 |

| ID | 组合模式 | 原子链 | 题目形态 | 断点 | 真题锚点 |
|---|---|---|---|---|---|
| C-GL-02-01 | C2 多考点串联 | GL-02-001 → GL-02-002 | 解答题 | GL-02-001（翻译即错） | 2015 真题 |
```

- **ID 命名**：原子 `<科目码>-(NN|AP)-<三位序号>`，组合 `C-<科目码>-(NN|AP)-<两位序号>`。删除即作废、留「作废」字样，不用改号补位。
- **档位**：`S` / `A` / `B`（高频深度 / 常规 / 边角），决定复习配额与出卷优先级。
- **状态**：`✅` 掌握 / `⚠️` 不稳 / `❌` 薄弱 / `⬜` 未考察。
- **出处**：统一用 PDF 页锚 `@pNNN`，可加节号；不写「大概某页」。
- **证据纪律**：`【实测】` 与 `【建模推演】` 分开标；没实测过的不许写「已掌握」。写「已讲未验收」这类免责说明的记录，状态仍应是 ⬜。
- **得分范式**：数学用 `M-*`，408 用 `S-*`，门禁会检查范式前缀与学科是否匹配。

## 质量门禁

`kb_validate` 把「写得对不对、引用通不通、账对不对得上」变成可执行判据，并且**只读**——修不修、怎么修由你决定。分两级：

**FAIL（结构契约被破坏）**：`kb.dir` 目录缺失、`id.format` / `combo.id` ID 不合规范、`id.duplicate` ID 重复、`tier.illegal` 档位非法、`paradigm.illegal` / `paradigm.subject` 范式非法或与学科不符、`status.illegal` 状态非法、`combo.chain` 原子链为空、`combo.ref` 引用了不存在的原子、`index.mismatch` / `index.summary_mismatch` 统计与章节实际对不上。

**WARN（规格建议未满足）**：`source.page` 缺页锚、`source.range` 页锚超出该书页数、`evidence.missing` 有状态无实测标记、`evidence.unjudged` 未考察却带实测记录、`id.sequence` 段内编号断档且无作废留痕、`chapter.empty` 没解析出原子、`combo.mode` / `combo.breakpoint` 组合信息缺失、`coverage.s_tier` S 档原子没被任何组合引用（背了不考）、`table.stray_pipe` 单元格里混进裸竖线、`index.missing` / `index.row_missing` 账目缺行。

账目对不上时用 `kb_reconcile` 按章节文件重算回写，而不是手改统计表——手改迟早再漂一次。

## 学习算法

模型在 logit 尺度上做 MAP 拟合：

$$ \operatorname{logit} P(\text{流畅通过}) = \theta + \theta_{\text{subj}} - d(a) - \gamma \cdot dt + \delta_{\text{retry}} \cdot \text{retry} $$

$$ d(a) = \mu_{\text{tier}} + \Delta_a $$

先验（冷启动不胡说）：能力基座 N(0.9, 1.5²)，科目偏置 N(0, 0.6²)，档位均值按 S/A/B 给先验再各自收 N(·, 0.5²)，原子偏离 N(上次判定修正, 0.6²)，同日复测项 N(0, 0.5²)；遗忘常数 `gamma` 默认固定 `ln2/10`（10 天半衰期），样本足够时才自由拟合。

优化用全批梯度加 Armijo 回溯线搜索，所以「收没收敛」是可判定、可复现、可报告的：每次报告都给出迭代次数、梯度无穷范数与目标值。先验在拟合前按事件顺序回溯算好，既没有未来信息泄漏，也把每次迭代从 O(n²) 降到 O(n)。

预测纪律：**先预测、后作答**——答题前 `learn_predict` 把 P(流畅通过) 与 90% 区间写进 `分析/预测日志.csv`；答完 `learn_report` 用走前回测（第 k 条只用前 k-1 条）算 Brier、LogLoss、基线 Brier 与 ECE，并按预测区间给出可靠性表。还有三样：

- **模式风险**：按混淆模式码做 Beta 后验并按时近加权（默认 14 天半衰期），估计「这个坑再踩的概率」。
- **讲法排名**：把直讲事件的讲法码归因到该原子后面那次判定上，比各种讲法的一次通过率（Wilson 区间）。
- **变体消融**：`legacy` / `pooled` / `pooled_retry` / `full` 四个变体在同一事件流上跑走前回测对比，Brier 最低者优先；样本不到 20 条时明确告诉你差异不显著、先按默认。

判定少于 20 条时所有数字只当方向看，别当结论。

## 配置

工作区根目录放 `kaoyan.config.json`，全部字段可选：

```json
{
  "root": ".",
  "pack": "demo",
  "dirs": { "kb": "知识库", "queue": "复习" },
  "deliver": { "policy": "auto", "printer": "", "paper": "A4", "copies": 1 },
  "learn": { "fit_gamma": "auto" }
}
```

环境变量：`KAOYAN_ROOT`（工作区根目录，`KB_BASE` 是同义别名）、`KAOYAN_BROWSER`（浏览器可执行文件路径）。

内容包清单 `pack.json` 可以声明学科、书目页数与自带知识库目录：

```json
{
  "name": "demo",
  "title": "演示内容包",
  "kb_dir": "kb",
  "subjects": { "示例数学": { "kind": "math", "paradigm": "M" } },
  "books": { "示例教材": { "pages": 200 } }
}
```

`books` 里的页数用于校验出处页锚是否超出教材范围。

## 开发

```bash
pip install -e ".[dev]"
python -m pytest -q
```

测试全部在临时工作区里跑（复制 `examples/demo-workspace`），不会碰真实数据。改完知识库相关逻辑，建议按「门禁 → 对账 → 测试」的顺序过一遍。

## 许可

MIT，见 [LICENSE](LICENSE)。
