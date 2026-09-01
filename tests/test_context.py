"""上下文管理（token 估算与分层压缩）的单元测试。"""
import unittest

from context import ContextManager, estimate_tokens


class TestEstimateTokens(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_cjk_heavier_than_ascii(self):
        self.assertGreater(estimate_tokens("中文中文"), estimate_tokens("abcdabcd"))

    def test_positive(self):
        self.assertGreater(estimate_tokens("hello world"), 0)


class TestContextCompression(unittest.TestCase):
    def _ctx(self, budget=3000):
        cm = ContextManager(budget)
        cm.add({"role": "system", "content": "sys"})
        cm.add({"role": "user", "content": "原始任务"})
        return cm

    def test_truncates_large_tool_output(self):
        cm = self._ctx()
        cm.add({"role": "tool", "content": "x" * 20_000})  # 约 5000 token，超预算
        self.assertTrue(cm.over_budget())
        cm.compress()
        self.assertFalse(cm.over_budget())
        # system 与原始任务仍在
        self.assertEqual(cm.messages[0]["role"], "system")
        self.assertEqual(cm.messages[1]["role"], "user")
        # 工具输出被截断标记
        self.assertIn("已截断", cm.messages[2]["content"])

    def test_drops_oldest_tool_exchange_when_no_summarizer(self):
        cm = self._ctx()
        cm.add({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": "{}"}}]})
        cm.add({"role": "tool", "content": "y" * 20_000})
        cm.add({"role": "assistant", "content": "最终答案"})
        cm.compress()
        # 最终答案仍在最后
        self.assertEqual(cm.messages[-1]["content"], "最终答案")
        self.assertEqual(cm.messages[1]["role"], "user")

    def test_summarize_replaces_old_history(self):
        cm = ContextManager(3000, summarize=lambda msgs: "摘要内容")
        cm.add({"role": "system", "content": "sys"})
        cm.add({"role": "user", "content": "任务"})
        for _ in range(10):
            cm.add({"role": "assistant", "content": "步骤", "tool_calls": [
                {"function": {"name": "read_file", "arguments": "{}"}}]})
            cm.add({"role": "tool", "content": "z" * 2000})
        self.assertTrue(cm.over_budget())
        cm.compress()
        # 摘要被插入为第 3 条消息
        self.assertIn("早前对话摘要", cm.messages[2]["content"])
        self.assertEqual(cm.messages[0]["role"], "system")
        self.assertEqual(cm.messages[1]["role"], "user")

    def test_add_caps_message_count(self):
        cm = ContextManager(10_000, max_messages=6)
        cm.add({"role": "system", "content": "sys"})
        cm.add({"role": "user", "content": "任务"})
        for i in range(20):
            cm.add({"role": "assistant", "content": f"步骤{i}"})
        self.assertLessEqual(len(cm.messages), 6)
        # 最早的 system 与原始任务仍在
        self.assertEqual(cm.messages[0]["content"], "sys")
        self.assertEqual(cm.messages[1]["content"], "任务")
        # 最后一条是最近的消息
        self.assertEqual(cm.messages[-1]["content"], "步骤19")

    def test_add_truncates_oversized_message(self):
        cm = ContextManager(1_000_000, max_messages=100)
        cm.add({"role": "user", "content": "x" * 100_000})
        self.assertIn("已截断", cm.messages[0]["content"])
        self.assertLess(len(cm.messages[0]["content"]), 70_000)

    def test_record_usage_calibrates_estimate(self):
        cm = ContextManager(100_000)
        cm.add({"role": "system", "content": "sys"})
        cm.add({"role": "user", "content": "任务内容"})
        est = cm.total_tokens()
        cm.record_usage(cm.messages, est * 2)  # 真实 token 是估算的 2 倍
        self.assertGreater(cm.total_tokens(), est)
        self.assertAlmostEqual(cm.total_tokens(), est * 2, delta=2)


if __name__ == "__main__":
    unittest.main()
