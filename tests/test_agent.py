"""Agent 会话生命周期（多轮对话 / 清空 / 工具回填）的单元测试。

用 FakeClient 替换真实模型，不发起网络请求。
"""
import tempfile
import unittest
from pathlib import Path

from agent import _extract_summary, Agent
from config import Config
from llm import LLMResponse, ToolCall
from tools import ToolRegistry, Workspace


class FakeClient:
    """按顺序返回预设响应，并记录每次调用收到的 messages。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, stream=True, on_text=None) -> LLMResponse:
        self.calls.append(list(messages))
        return self.responses.pop(0)


def make_agent(responses):
    cfg = Config(api_key="dummy", max_iterations=3)
    ws = Workspace(Path(tempfile.mkdtemp()))
    return Agent(cfg, FakeClient(responses), ToolRegistry(ws)), cfg


class TestConversation(unittest.TestCase):
    def test_reply_accumulates_context_across_turns(self):
        agent, _ = make_agent([
            LLMResponse(content="第一次回答"),
            LLMResponse(content="第二次回答"),
        ])
        self.assertEqual(agent.reply("问题1"), "第一次回答")
        self.assertEqual(agent.reply("问题2"), "第二次回答")

        roles = [m["role"] for m in agent.context.messages]
        self.assertEqual(roles[0], "system")
        self.assertEqual(roles.count("user"), 2)
        self.assertEqual(roles.count("assistant"), 2)
        # 第二轮时，模型收到完整历史（system + user1 + assistant1 + user2）
        self.assertEqual(len(agent.client.calls[1]), 4)

    def test_clear_resets_conversation(self):
        agent, _ = make_agent([LLMResponse(content="a"), LLMResponse(content="b")])
        agent.reply("x")
        agent.clear()
        self.assertEqual(agent.context.messages, [])
        agent.reply("y")
        # 重新开始时仍会先补 system，再追加用户消息
        self.assertEqual(agent.context.messages[0]["role"], "system")
        self.assertEqual(agent.context.messages[1], {"role": "user", "content": "y"})

    def test_tool_call_result_is_fed_back(self):
        agent, _ = make_agent([
            LLMResponse(content="", tool_calls=[
                ToolCall(id="c1", name="write_file",
                         arguments='{"path": "a.txt", "content": "hi"}'),
            ]),
            LLMResponse(content="写好了"),
        ])
        self.assertEqual(agent.reply("写个文件"), "写好了")
        tool_msgs = [m for m in agent.context.messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("已写入", tool_msgs[0]["content"])

    def test_load_history_then_reply(self):
        agent, _ = make_agent([LLMResponse(content="续跑的答案")])
        agent.load_history([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "原始任务"},
            {"role": "assistant", "content": "之前做了一半"},
        ])
        self.assertEqual(agent.reply("继续完成"), "续跑的答案")
        # 不应重复追加 system
        self.assertEqual([m["role"] for m in agent.context.messages].count("system"), 1)


class TestPlanReview(unittest.TestCase):
    def test_review_cancel_stops_execution(self):
        agent, _ = make_agent([LLMResponse(content="计划内容")])
        result = agent.reply("做个任务", plan_first=True,
                             plan_reviewer=lambda plan, revise: None)
        self.assertIn("已取消", result)
        self.assertEqual(len(agent.client.calls), 1)  # 只生成了计划，未进入执行

    def test_review_confirm_then_execute(self):
        agent, _ = make_agent([
            LLMResponse(content="计划内容"),
            LLMResponse(content="完成"),
        ])
        result = agent.reply("任务", plan_first=True,
                             plan_reviewer=lambda plan, revise: plan)
        self.assertEqual(result, "完成")
        # 确认后的计划进入上下文
        roles = [m["role"] for m in agent.context.messages]
        self.assertEqual(roles.count("user"), 2)  # 任务 + "计划已确认"
        self.assertEqual(roles.count("assistant"), 2)  # 计划 + 最终回答

    def test_review_revise_loop(self):
        agent, _ = make_agent([
            LLMResponse(content="初版计划"),
            LLMResponse(content="修订后计划"),
            LLMResponse(content="完成"),
        ])

        def reviewer(plan, revise):
            return revise(plan, "不够详细")

        result = agent.reply("任务", plan_first=True, plan_reviewer=reviewer)
        self.assertEqual(result, "完成")
        # 修订版计划被写入上下文
        plans = [m["content"] for m in agent.context.messages
                 if m.get("role") == "assistant"]
        self.assertIn("修订后计划", plans)


class TestAgentExtras(unittest.TestCase):
    def test_extract_summary_strips_tags(self):
        self.assertEqual(_extract_summary("<analysis>分析</analysis>\n<summary>摘要正文</summary>"), "摘要正文")
        self.assertEqual(_extract_summary("<summary> 带空格 </summary>"), "带空格")
        self.assertIsNone(_extract_summary(""))
        self.assertEqual(_extract_summary("无标签文本"), "无标签文本")

    def test_memory_snapshot_injected_into_system(self):
        agent, _ = make_agent([LLMResponse(content="ok")])
        agent.tools.ws.memory_store = type("S", (), {"snapshot": "项目使用 pytest"})()
        agent.reply("hi")
        self.assertIn("项目使用 pytest", agent.context.messages[0]["content"])

    def test_user_profile_and_skills_injected(self):
        agent, _ = make_agent([LLMResponse(content="ok")])
        agent.tools.ws.user_store = type("S", (), {"snapshot": "用户偏好简洁代码"})()
        agent.tools.ws.skills = [
            type("K", (), {"name": "code-review", "description": "审查代码"})()
        ]
        agent.reply("hi")
        content = agent.context.messages[0]["content"]
        self.assertIn("用户偏好简洁代码", content)
        self.assertIn("code-review", content)
        self.assertIn("审查代码", content)
        # 未注入技能正文，只注入清单（省 token）
        self.assertNotIn("审查步骤", content)

    def test_all_parallel_safe_requires_read_only(self):
        agent, _ = make_agent([])
        read_only = [ToolCall("1", "read_file", "{}"), ToolCall("2", "list_files", "{}")]
        self.assertTrue(agent._all_parallel_safe(read_only))
        mixed = [ToolCall("1", "read_file", "{}"), ToolCall("2", "write_file", "{}")]
        self.assertFalse(agent._all_parallel_safe(mixed))


if __name__ == "__main__":
    unittest.main()
