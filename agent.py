"""主循环：驱动「思考 → 行动 → 观察」直到满足终止条件。

终止条件（题目点名的核心逻辑）：
  1. 模型返回无 tool_calls → 视为任务完成；
  2. 达到最大迭代步数 → 注入收尾指令，让模型基于已有信息给出结论；
  3. 与模型通信失败 → 报错退出。

其它工程化设计：
  - 同一轮返回的多个工具调用可并发执行（线程池）；
  - 可选「计划先行」：先用模型产出一份计划，再进入执行循环；
  - 上下文压缩时用模型做摘要（通过回调注入 ContextManager）；
  - 会话可复用：reply() 在既有上下文中追加新指令，实现持续对话。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from config import Config
from context import ContextManager, render_message
from llm import ChatProvider, LLMError, LLMResponse, ToolCall
from parser import extract_tool_call_from_content, parse_arguments, ParseError
from tools import ToolRegistry

SYSTEM_PROMPT = """你是一个编程智能体，运行在用户的机器上。你可以调用工具来读写文件、执行命令，从而自主完成用户交给你的编程任务。

工作目录：{workdir}

行为准则：
1. 先理解任务，再动手；每一步调用一个或少数几个工具，观察结果后决定下一步。
2. 修改文件前，先用 read_file 查看当前内容，避免覆盖已有实现。
3. 优先用 edit_file 做小范围、精准的修改，避免整文件重写浪费上下文。
4. 当命令失败或工具报错时，根据错误信息调整后重试，不要机械重复同样的失败操作。
5. 所有路径相对于工作目录 {workdir}；命令默认在工作目录下执行。
6. 不要臆造不存在的文件或命令结果，一切以工具返回为准。
7. 任务完成后，用简洁的几句话总结：改了什么、为什么这样改、如何验证。
"""

PLAN_INSTRUCTION = "请先不要调用任何工具，只输出一个清晰、可执行的分步计划（用编号列表）。"

SUMMARIZE_PROMPT = (
    "你是上下文压缩器。请把下面的对话历史压缩成简洁摘要，"
    "保留：关键事实、已做的修改、未完成事项、下一步行动。用中文、分点列出。"
)


class Agent:
    def __init__(self, config: Config, client: ChatProvider, tools: ToolRegistry):
        self.config = config
        self.client = client
        self.tools = tools
        self.context = ContextManager(config.context_budget_tokens, summarize=self._summarize)
        self._result_cache: dict[tuple[str, str], str] = {}
        self._started = False
        self.approve = False  # 审查开关：可在运行中切换（REPL 的 /approve）

    # ---- 会话生命周期 ----
    def _ensure_started(self) -> None:
        if not self._started:
            self.context.add({
                "role": "system",
                "content": SYSTEM_PROMPT.format(workdir=self.config.workdir),
            })
            self._started = True

    def load_history(self, history: list[dict]) -> None:
        """从保存的会话恢复（--resume / 续跑）。"""
        self.context.messages = list(history)
        self._started = True

    def clear(self) -> None:
        """清空对话，重新开始。"""
        self.context.messages = []
        self._started = False

    def dump_history(self) -> list[dict]:
        """导出当前会话，用于持久化。"""
        return list(self.context.messages)

    # ---- 对外入口 ----
    def reply(
        self,
        message: str,
        plan_first: bool = False,
        on_text: Callable[[str], None] | None = None,
        on_plan: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, str], None] | None = None,
        on_tool_result: Callable[[str], None] | None = None,
    ) -> str:
        """在既有会话中追加一条用户指令并执行一轮，返回最终回答。"""
        self._ensure_started()
        self.context.add({"role": "user", "content": message[: self.config.max_input_chars]})
        if plan_first:
            plan = self._make_plan(on_plan)
            if plan:
                self.context.add({"role": "assistant", "content": plan})
                self.context.add({"role": "user", "content": "计划已确认，请按计划逐步执行。"})
        return self._loop(on_text, on_tool_call, on_tool_result)

    def run(
        self,
        task: str,
        plan_first: bool = False,
        history: list[dict] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_plan: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, str], None] | None = None,
        on_tool_result: Callable[[str], None] | None = None,
    ) -> str:
        """单次执行的兼容入口（--resume 时先载入历史）。"""
        if history:
            self.load_history(history)
        return self.reply(
            task, plan_first=plan_first,
            on_text=on_text, on_plan=on_plan,
            on_tool_call=on_tool_call, on_tool_result=on_tool_result,
        )

    # ---- 主循环 ----
    def _loop(self, on_text, on_tool_call, on_tool_result) -> str:
        for _ in range(self.config.max_iterations):
            self.context.compress()
            try:
                resp = self.client.chat(
                    self.context.messages, self.tools.schemas(),
                    stream=True, on_text=on_text,
                )
            except LLMError as e:
                return f"[错误] 与模型通信失败：{e}"
            # 模型输出限长：超出部分截断并提示，避免单条内容撑爆上下文
            if len(resp.content) > self.config.max_response_chars:
                resp.content = resp.content[: self.config.max_response_chars]
                self.context.add({
                    "role": "user",
                    "content": "[注意] 你上一条回答过长已被截断，请继续完成。",
                })

            # 兜底：模型把工具调用写进 content 而非 tool_calls 字段的情况
            if not resp.tool_calls:
                fallback = extract_tool_call_from_content(resp.content)
                if fallback is not None:
                    resp.tool_calls = [fallback]

            if not resp.tool_calls:
                # 终止条件 1：无工具调用 → 完成
                self.context.add({"role": "assistant", "content": resp.content or ""})
                return resp.content

            # 有工具调用 → 执行并回填结果
            self.context.add(self._assistant_message(resp))
            results, denied = self._execute_all(
                resp.tool_calls, on_tool_call, on_tool_result
            )
            for tc, result in zip(resp.tool_calls, results):
                self.context.add({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })

        # 终止条件 2：达到最大步数 → 收尾
        self.context.add({
            "role": "user",
            "content": "已达到最大执行步数，请停止调用工具，基于已有信息直接给出最终结论。",
        })
        try:
            resp = self.client.chat(self.context.messages, stream=True, on_text=on_text)
        except LLMError as e:
            return f"[错误] {e}"
        return resp.content

    # ---- 计划先行 ----
    def _make_plan(self, on_plan) -> str:
        msgs = self.context.messages + [{"role": "user", "content": PLAN_INSTRUCTION}]
        try:
            resp = self.client.chat(msgs, stream=True, on_text=on_plan)
        except LLMError:
            return ""
        return resp.content or ""

    # ---- 工具执行 ----
    def _execute_all(self, tcs: list[ToolCall], on_tool_call, on_tool_result):
        for tc in tcs:
            if on_tool_call:
                on_tool_call(tc.name, tc.arguments)
        # 审查模式下逐条执行（每步都要弹确认，且用户可能改主意）；否则可并发
        if self.approve:
            results = [self._execute(tc) for tc in tcs]
            denied = [r.startswith("已拒绝") for r in results]
        elif len(tcs) > 1 and self.config.parallel_tools:
            results = [None] * len(tcs)
            with ThreadPoolExecutor(max_workers=min(len(tcs), 4)) as pool:
                futures = [pool.submit(self._execute, tc) for tc in tcs]
                for i, f in enumerate(futures):
                    try:
                        results[i] = f.result()
                    except Exception as e:
                        results[i] = f"工具执行出错：{type(e).__name__}: {e}"
            denied = [False] * len(tcs)
        else:
            results = [self._execute(tc) for tc in tcs]
            denied = [r.startswith("已拒绝") for r in results]
        for result in results:
            if on_tool_result:
                on_tool_result(result)
        return results, any(denied)

    def _execute(self, tc: ToolCall) -> str:
        key = (tc.name, tc.arguments)
        if key in self._result_cache:
            return self._result_cache[key]
        try:
            args = parse_arguments(tc)
        except ParseError as e:
            result = f"参数解析失败：{e}。请修正参数后重试。"
        else:
            # 审批由 Workspace.approve 回调驱动（工具内部 request），无需在此传 dry_run
            result = self.tools.run(tc.name, args)
        self._result_cache[key] = result
        return result

    # ---- 上下文摘要 ----
    def _summarize(self, msgs: list[dict]) -> str | None:
        text = "\n".join(render_message(m) for m in msgs)
        try:
            resp = self.client.chat(
                [
                    {"role": "system", "content": SUMMARIZE_PROMPT},
                    {"role": "user", "content": text},
                ],
                stream=False,
            )
        except LLMError:
            return None
        return resp.content or None

    # ---- 消息构造 ----
    def _assistant_message(self, resp: LLMResponse) -> dict:
        msg: dict = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for i, tc in enumerate(resp.tool_calls)
            ]
        return msg
