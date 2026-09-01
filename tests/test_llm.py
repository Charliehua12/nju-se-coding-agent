"""LLM 客户端流式解析（SSE + 工具调用增量拼接）的单元测试。

不真正发起网络请求，而是喂入构造好的 SSE 字节流，验证解析逻辑。
"""
import json
import unittest

from config import Config
from llm import DeepSeekClient, _parse_usage


def sse(delta: dict | None = None, finish: str | None = None, usage: dict | None = None) -> bytes:
    obj = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = usage
    return f"data: {json.dumps(obj)}\n".encode()


class FakeResp:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def __iter__(self):
        return iter(self._chunks)


class TestSSEStreaming(unittest.TestCase):
    def setUp(self):
        self.client = DeepSeekClient(Config(api_key="dummy"))

    def test_text_and_tool_call_accumulation(self):
        chunks = [
            sse({"content": "我来"}),
            sse({"content": "计算"}),
            sse({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                 "function": {"name": "add", "arguments": ""}}]}),
            sse({"tool_calls": [{"index": 0, "function": {"arguments": "{\"a\": 1"}}]}),
            sse({"tool_calls": [{"index": 0, "function": {"arguments": ", \"b\": 2}"}}]}),
            sse(finish="tool_calls", usage={"prompt_tokens": 10, "completion_tokens": 5}),
            b"data: [DONE]\n",
        ]
        resp = self.client._read_sse(FakeResp(chunks), on_text=None)
        self.assertEqual(resp.content, "我来计算")
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "add")
        self.assertEqual(json.loads(resp.tool_calls[0].arguments), {"a": 1, "b": 2})
        self.assertEqual(resp.finish_reason, "tool_calls")
        self.assertEqual(resp.usage.prompt_tokens, 10)
        self.assertEqual(resp.usage.completion_tokens, 5)

    def test_on_text_callback(self):
        seen: list[str] = []
        chunks = [sse({"content": "a"}), sse({"content": "b"}), b"data: [DONE]\n"]
        self.client._read_sse(FakeResp(chunks), on_text=seen.append)
        self.assertEqual(seen, ["a", "b"])

    def test_multiple_tool_calls_by_index(self):
        chunks = [
            sse({"tool_calls": [{"index": 0, "id": "c0", "type": "function",
                                 "function": {"name": "read_file", "arguments": "{\"path\":"}},
                 {"index": 1, "id": "c1", "type": "function",
                  "function": {"name": "list_files", "arguments": "{}"}}]}),
            sse({"tool_calls": [{"index": 0, "function": {"arguments": "\"a.py\"}"}}]}),
            b"data: [DONE]\n",
        ]
        resp = self.client._read_sse(FakeResp(chunks), None)
        self.assertEqual([tc.name for tc in resp.tool_calls], ["read_file", "list_files"])
        self.assertEqual(json.loads(resp.tool_calls[0].arguments), {"path": "a.py"})


class TestNonStreaming(unittest.TestCase):
    def setUp(self):
        self.client = DeepSeekClient(Config(api_key="dummy"))

    def test_extract(self):
        obj = {
            "choices": [{"message": {
                "content": "答案",
                "tool_calls": [{"id": "c0", "type": "function",
                                "function": {"name": "add", "arguments": "{\"a\":1}"}}],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }
        resp = self.client._extract(obj)
        self.assertEqual(resp.content, "答案")
        self.assertEqual(resp.tool_calls[0].name, "add")
        self.assertEqual(resp.usage.total_tokens, 3)

    def test_parse_usage(self):
        self.assertEqual(_parse_usage(None).total_tokens, 0)
        self.assertEqual(_parse_usage({"prompt_tokens": 4, "completion_tokens": 6}).total_tokens, 10)


if __name__ == "__main__":
    unittest.main()
