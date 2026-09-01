"""模型输出解析的单元测试。"""
import unittest

from llm import ToolCall
from parser import ParseError, extract_tool_call_from_content, parse_arguments


class TestParseArguments(unittest.TestCase):
    def test_valid_json(self):
        tc = ToolCall(id="1", name="read_file", arguments='{"path": "a.py"}')
        self.assertEqual(parse_arguments(tc), {"path": "a.py"})

    def test_empty_arguments(self):
        self.assertEqual(parse_arguments(ToolCall("1", "x", "")), {})
        self.assertEqual(parse_arguments(ToolCall("1", "x", "   ")), {})

    def test_malformed_with_prefix_text(self):
        # 模型偶尔会在 JSON 前加说明文字，应能抠出对象
        tc = ToolCall("1", "x", '说明：{"path": "a.py", "limit": 3}')
        self.assertEqual(parse_arguments(tc), {"path": "a.py", "limit": 3})

    def test_invalid_raises(self):
        with self.assertRaises(ParseError):
            parse_arguments(ToolCall("1", "x", "这不是 JSON"))

    def test_non_object_raises(self):
        with self.assertRaises(ParseError):
            parse_arguments(ToolCall("1", "x", "[1, 2, 3]"))


class TestExtractToolCallFromContent(unittest.TestCase):
    def test_extract_markdown_json_block(self):
        content = '我来调用工具：\n```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```'
        tc = extract_tool_call_from_content(content)
        self.assertIsNotNone(tc)
        self.assertEqual(tc.name, "read_file")
        self.assertEqual(parse_arguments(tc), {"path": "x"})

    def test_dict_arguments_serialized(self):
        content = '```json\n{"name": "add", "arguments": {"a": 1, "b": 2}}\n```'
        tc = extract_tool_call_from_content(content)
        self.assertEqual(parse_arguments(tc), {"a": 1, "b": 2})

    def test_no_tool_call_returns_none(self):
        self.assertIsNone(extract_tool_call_from_content(""))
        self.assertIsNone(extract_tool_call_from_content("这里没有任何工具调用"))


if __name__ == "__main__":
    unittest.main()
