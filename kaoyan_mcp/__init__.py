# -*- coding: utf-8 -*-
"""kaoyan-mcp —— 考研私教 MCP 引擎（零依赖，内容包驱动）。

设计要点：
  * 零第三方依赖：只用 Python 标准库，任何有 Python 3.9+ 的机器都能跑。
  * 内容与个人数据分离：知识库（content）与学习数据（state）分目录，
    知识包可以共享，个人数据默认不出本地。
  * 交付通道自适应：有打印机就走打印，没有就出 PDF / HTML / 纯文本。
"""

__version__ = "0.1.0"
SERVER_NAME = "kaoyan-mcp"

