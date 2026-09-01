"""对话历史与上下文管理：token 估算、预算控制、分层压缩。

题目点名的「重要逻辑」之一。模型上下文有限，而工具输出（读文件、命令结果）
往往占据大头且可重新获取，因此压缩时优先对它们下手。压缩按优先级三级降级：
  1. 截断最旧的工具输出正文（保留头部，内容可重新读取）；
  2. 用 LLM 把较早的对话摘要成一段「记忆」，保住最近上下文；
  3. 兜底：丢弃最旧的工具调用轮次 / 最旧消息。
始终保留 system 提示与最初的用户任务。
"""
from __future__ import annotations

import json
from typing import Callable


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（无第三方分词器）。

    中文约按 1 token/字，其余约按 4 字符/token，另加少量结构余量。
    只用于预算控制，不需要精确。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + other // 4 + 1


def _msg_tokens(msg: dict) -> int:
    n = estimate_tokens(msg.get("content") or "")
    for tc in msg.get("tool_calls") or []:
        n += estimate_tokens(json.dumps(tc.get("function") or {}, ensure_ascii=False))
    return n


def render_message(m: dict) -> str:
    """把一条消息渲染成纯文本，供摘要器使用。"""
    role = m.get("role")
    content = m.get("content") or ""
    if role == "tool":
        return f"[工具 {m.get('name', '')} 结果] {content[:500]}"
    if role == "assistant" and m.get("tool_calls"):
        calls = "; ".join(
            f"{t['function']['name']}({t['function']['arguments'][:100]})"
            for t in m["tool_calls"]
        )
        return f"[assistant 调用工具] {calls}"
    return f"[{role}] {content[:800]}"


class ContextManager:
    """持有消息列表并按预算做分层压缩。"""

    def __init__(
        self,
        budget_tokens: int,
        summarize: Callable[[list[dict]], str | None] | None = None,
        max_messages: int = 400,
    ):
        self.budget = budget_tokens
        self.summarize = summarize  # 摘要器（可选，通常由 Agent 注入，调用模型）
        self.max_messages = max_messages  # 消息总数上限（过载保护）
        self.messages: list[dict] = []
        self.calibration = 1.0  # token 估算校准系数（用真实 usage 动态锚定）

    def add(self, msg: dict) -> None:
        """追加消息并做过载保护：
        超大单条消息直接截断；消息总数超上限时逐轮丢弃最旧非 system/任务消息。
        """
        content = msg.get("content") or ""
        cap = 60_000
        if isinstance(content, str) and len(content) > cap:
            msg["content"] = content[:cap] + f"\n...[已截断，原始 {len(content)} 字符]"
        self.messages.append(msg)
        while len(self.messages) > self.max_messages:
            self._drop_tool_exchange(2)
            if len(self.messages) <= self.max_messages:
                break
            if len(self.messages) <= 2:
                break
            del self.messages[2]

    def total_tokens(self) -> int:
        return int(sum(_msg_tokens(m) for m in self.messages) * self.calibration)

    def record_usage(self, messages: list[dict], real_prompt_tokens: int) -> None:
        """用 API 返回的真实 prompt token 反向校准字符估算（usage anchoring）。

        字符估算（中文 1 token/字 等）与真实分词有偏差，每轮用真实值
        校正比例系数，让上下文预算更贴近实际。
        """
        if not real_prompt_tokens:
            return
        est = sum(_msg_tokens(m) for m in messages)
        if est > 0:
            self.calibration = max(0.3, min(3.0, real_prompt_tokens / est))

    def over_budget(self) -> bool:
        return self.total_tokens() > self.budget

    def compress(self) -> bool:
        """尝试压缩到预算内，返回是否有变化。

        与消息追加钩子不同，这里不重复调用 total_tokens()（它本身也是 O(n)），
        只在确实超预算时执行一次 O(n) 扫描。
        """
        changed = False
        # 1) 截断旧工具输出
        while self.over_budget():
            i = self._oldest_large_tool_output()
            if i is None:
                break
            self._truncate_tool_output(i)
            changed = True
        # 2) 摘要较早历史
        if self.over_budget() and self.summarize is not None:
            if self._summarize_old():
                changed = True
        # 3) 兜底丢弃
        while self.over_budget():
            i = self._oldest_tool_exchange()
            if i is None:
                break
            self._drop_tool_exchange(i)
            changed = True
        while self.over_budget():
            if len(self.messages) <= 2:
                break
            del self.messages[2]
            changed = True
        return changed

    # ---- 各层策略的内部定位 ----
    def _oldest_large_tool_output(self) -> int | None:
        for i, m in enumerate(self.messages):
            if m.get("role") == "tool" and len(m.get("content") or "") > 2000:
                return i
        return None

    def _truncate_tool_output(self, i: int) -> None:
        content = self.messages[i]["content"]
        self.messages[i]["content"] = (
            f"{content[:2000]}\n...[输出过长已截断，原始 {len(content)} 字符，"
            f"如需完整内容请用 read_file 重新读取]"
        )

    def _summarize_old(self, keep_tail: int = 6) -> bool:
        head = 2  # system + 原始任务，始终保留
        if len(self.messages) <= head + keep_tail:
            return False
        old = self.messages[head:-keep_tail]
        summary = self.summarize(old)
        if not summary:
            return False
        self.messages = (
            self.messages[:head]
            + [{"role": "user", "content": f"[早前对话摘要]\n{summary}"}]
            + self.messages[-keep_tail:]
        )
        return True

    def _oldest_tool_exchange(self) -> int | None:
        for i, m in enumerate(self.messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                return i
        return None

    def _drop_tool_exchange(self, i: int) -> None:
        # 连同紧随其后的所有 tool 结果消息一起删除，避免留下孤儿 tool 消息
        j = i + 1
        while j < len(self.messages) and self.messages[j].get("role") == "tool":
            j += 1
        del self.messages[i:j]
