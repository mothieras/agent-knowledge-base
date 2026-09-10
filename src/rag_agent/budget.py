"""请求级共享预算（DESIGN 3.2，2026-09-07 冻结）。

一个预算对象经 ``config["configurable"]["budget"]`` 贯穿主图与全部子图
（LangGraph 会把 configurable 传播到 Send 扇出的每个子图），因此工具调用
与迭代上限是父子图共享的，不是每个分支各拿一份完整上限。

调用工具前在路由里预留预算（``reserve_tool_calls``），不能在工具执行后
才发现超限；引用修复同样计入同一总预算。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config


@dataclass
class RequestBudget:
    max_subquestions: int = field(default_factory=lambda: config.MAX_SUBQUESTIONS)
    max_tool_calls: int = field(default_factory=lambda: config.MAX_TOOL_CALLS)
    max_iterations: int = field(default_factory=lambda: config.MAX_ITERATIONS)
    max_repairs: int = field(default_factory=lambda: config.MAX_CITATION_REPAIRS)

    tool_calls: int = 0
    iterations: int = 0
    repairs: int = 0

    def reserve_tool_calls(self, n: int = 1) -> bool:
        if self.tool_calls + n > self.max_tool_calls:
            return False
        self.tool_calls += n
        return True

    def reserve_repair(self) -> bool:
        if self.repairs >= self.max_repairs:
            return False
        self.repairs += 1
        return True


def get_budget(config: dict | None) -> RequestBudget:
    """从 RunnableConfig 取本请求预算；缺省（如单测直接 invoke 子图）时给独立预算。"""
    try:
        budget = config["configurable"]["budget"]  # type: ignore[index]
    except (KeyError, TypeError):
        return RequestBudget()
    return budget if isinstance(budget, RequestBudget) else RequestBudget()
