"""轻量 Markdown → ANSI 终端渲染器（纯标准库）。

模型回复是 Markdown，而终端不会渲染。这里实现一个够用的渲染器：
支持标题、加粗、行内代码、代码块、列表与分隔线。流式输出时用
MarkdownRenderer 按「行」缓冲——Markdown 结构（标题/列表/代码块）都是
按行组织的，攒满一行再渲染，避免流式分块把标记切碎。
"""
from __future__ import annotations

import re

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
DIM = "\033[2m"
FENCE = "\033[35m"


def _s(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"


def _inline(text: str) -> str:
    """行内样式：行内代码、加粗。"""
    text = re.sub(r"`([^`\n]+)`", lambda m: _s(m.group(1), CYAN), text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", lambda m: _s(m.group(1), BOLD), text)
    return text


class MarkdownRenderer:
    def __init__(self, write):
        self.write = write
        self.buf = ""
        self.in_fence = False

    def feed(self, delta: str) -> None:
        self.buf += delta
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.write(self._render_line(line))
            self.write("\n")

    def flush(self) -> None:
        if self.buf:
            self.write(self._render_line(self.buf))
            self.write("\n")
            self.buf = ""

    def _render_line(self, line: str) -> str:
        s = line.rstrip("\n")
        stripped = s.strip()
        if stripped.startswith("```"):
            self.in_fence = not self.in_fence
            return _s("```", FENCE)
        if self.in_fence:
            return _s("    " + s, DIM)
        if re.match(r"^#{1,6}\s+\S", stripped):
            return _s(_inline(re.sub(r"^#{1,6}\s*", "", line)), BOLD + CYAN)
        if re.match(r"^[-*]\s+\S", stripped) or re.match(r"^\d+[.)]\s+\S", stripped):
            return _inline(line)
        if re.match(r"^\s*-{3,}\s*$", stripped):
            return _s("─" * 40, DIM)
        return _inline(line)


def render_markdown(text: str) -> str:
    """整段渲染：内部复用流式渲染器，把结果收集成字符串。"""
    out: list[str] = []
    r = MarkdownRenderer(out.append)
    r.feed(text)
    r.flush()
    return "".join(out).rstrip("\n")
