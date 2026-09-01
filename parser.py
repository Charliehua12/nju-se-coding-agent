"""模型输出解析：把工具调用的参数从 JSON 字符串解析为 dict，并做畸形容错。

这也是题目点名的「重要逻辑」之一：模型输出的参数未必总是合法 JSON，
需要在不中断循环的前提下尽量修复或回喂错误。
"""
from __future__ import annotations

import json
import re

from llm import ToolCall


class ParseError(Exception):
    pass


def parse_arguments(tc: ToolCall) -> dict:
    """把 tool call 的 arguments 解析成 dict，容错空串 / 畸形 JSON。"""
    raw = (tc.arguments or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _extract_json_object(raw)
        if data is None:
            raise ParseError(f"工具 {tc.name} 的参数不是合法 JSON：{raw[:200]!r}")
    if not isinstance(data, dict):
        raise ParseError(f"工具 {tc.name} 的参数应为 JSON 对象，实际为 {type(data).__name__}")
    return data


def _extract_json_object(text: str) -> dict | None:
    """从夹杂说明文字的输出中，抠出第一个括号配平的 {...} 对象。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def extract_tool_call_from_content(content: str) -> ToolCall | None:
    """兜底：个别情况下模型会把工具调用以文本形式写进 content（而非 tool_calls 字段）。

    只识别 ```json { "name": ..., "arguments": ... } ``` 片段。
    """
    if not content:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    name = obj.get("name") or (obj.get("function") or {}).get("name")
    args = obj.get("arguments") or (obj.get("function") or {}).get("arguments")
    if not name:
        return None
    if isinstance(args, dict):
        args = json.dumps(args, ensure_ascii=False)
    return ToolCall(id="", name=name, arguments=args or "{}")
