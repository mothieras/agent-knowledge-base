"""结构化输出调用：JSON mode 解析失败重试一次（计入同一总预算）。

DeepSeek json_mode 偶发返回非法 JSON（OutputParserException），
网络重试之外补一次解析重试；仍失败则原样抛出，由 L2 映射 upstream_failed。
"""
from __future__ import annotations

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage


def invoke_structured(llm, schema, messages, *, method="json_mode", retries=1):
    runnable = llm.with_structured_output(schema, method=method)
    try:
        return runnable.invoke(messages)
    except OutputParserException:
        if retries <= 0:
            raise
        retry_messages = list(messages) + [HumanMessage(
            content="上一次输出不是合法 JSON。请重新输出一个完整的 JSON 对象，"
                    "不要输出任何 JSON 之外的内容。"
        )]
        return runnable.invoke(retry_messages)
