"""数据结构定义。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum


class Level(str, Enum):
    """问题严重程度。"""

    BLOCK = "block"      # 必须改：编造、缺结构要素
    WARN = "warn"        # 建议改：论证断裂、语体问题
    NOTE = "note"        # 提示：可优化项


@dataclass
class Problem:
    """一条检测结果。"""

    code: str                    # 规则编号，如 "R3-01"
    level: Level
    what: str                    # 问题是什么
    line: int = 0                # 行号
    where: str = ""              # 上下文片段
    how: str = ""                # 怎么改

    def as_dict(self) -> dict:
        d = asdict(self)
        d["level"] = self.level.value
        d["where"] = (self.where or "")[:100]
        return d


@dataclass
class Item:
    """跨数据源的统一记录。"""

    src: str                       # 来源标识
    field: str                     # literature / market / macro
    title: str
    people: list[str] = field(default_factory=list)
    when: str = ""
    ident: str = ""
    note: str = ""                 # 摘要或备注
    link: str = ""
    pulled: str = field(default_factory=lambda: date.today().isoformat())
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)
