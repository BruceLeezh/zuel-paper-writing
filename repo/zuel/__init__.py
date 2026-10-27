"""中南财经政法大学人文社科本科论文写作助手 · 工程实现。

模块划分：
    model    数据结构
    net      取数（代理/证书/编码/错误提示）
    checker  文本检测 22 条规则
    feeds    数据源（文献 / 行情 / 宏观）
    tables   导出文件解析（题录 / CSMAR 面板）
"""

from .model import Item, Level, Problem

__all__ = ["Item", "Level", "Problem"]
__version__ = "1.0.0"
