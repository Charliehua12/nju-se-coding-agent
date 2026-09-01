"""Markdown → ANSI 渲染器的单元测试。"""
import unittest

from markdown import MarkdownRenderer, render_markdown


class TestRenderMarkdown(unittest.TestCase):
    def test_plain_passthrough(self):
        self.assertEqual(render_markdown("你好 world"), "你好 world")

    def test_bold(self):
        out = render_markdown("**加粗**")
        self.assertIn("\033[1m加粗\033[0m", out)
        self.assertNotIn("**", out)

    def test_inline_code(self):
        out = render_markdown("用 `quicksort` 表示")
        self.assertIn("\033[36mquicksort\033[0m", out)
        self.assertNotIn("`", out)

    def test_heading(self):
        out = render_markdown("# 标题")
        self.assertIn("标题", out)
        self.assertNotIn("#", out)

    def test_code_fence(self):
        out = render_markdown("```py\nprint(1)\n```")
        self.assertIn("print(1)", out)
        self.assertIn("\033[2m", out)  # 代码块用 dim 样式

    def test_hr(self):
        out = render_markdown("---")
        self.assertIn("\033[2m", out)

    def test_list_kept(self):
        self.assertIn("- 项目", render_markdown("- 项目"))


class TestMarkdownRendererStreaming(unittest.TestCase):
    def test_split_marker_across_chunks(self):
        out: list[str] = []
        r = MarkdownRenderer(out.append)
        r.feed("这是 **加")
        r.feed("粗** 结尾")
        r.flush()
        text = "".join(out)
        self.assertIn("\033[1m加粗\033[0m", text)
        self.assertNotIn("**", text)

    def test_line_by_line(self):
        out: list[str] = []
        r = MarkdownRenderer(out.append)
        r.feed("第一行\n第二")
        r.feed("行")
        r.flush()
        text = "".join(out)
        self.assertIn("第一行", text)
        self.assertIn("第二行", text)


if __name__ == "__main__":
    unittest.main()
