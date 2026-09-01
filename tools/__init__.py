"""工具注册表：把「给模型看的 JSON Schema」与「本地执行函数」绑定在一起。

题目点名的「工具的定义与本地执行」核心：模型看到的是结构化 schema，
我们拿到结构化 tool_call 后在本地沙箱内执行，返回字符串结果回喂给模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .files import read_file, write_file, edit_file, list_files, delete_file
from .shell import execute_command
from .search import search_files


class Workspace:
    """工作目录沙箱：所有路径约束在 root 内，统一做越界防护与输出截断。"""

    def __init__(self, root: Path, confirm: Callable[[str], bool] | None = None,
                 max_output_chars: int = 12_000):
        self.root = root.resolve()
        self.confirm = confirm  # 命令执行前的人工确认回调（None 表示自动放行）
        self.max_output_chars = max_output_chars

    def resolve(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise ValueError(f"路径越界：'{p}' 不在工作目录 {self.root} 内")
        return path

    def truncate(self, text: str) -> str:
        if len(text) > self.max_output_chars:
            return f"{text[:self.max_output_chars]}\n...[输出过长已截断，原始 {len(text)} 字符]"
        return text


@dataclass
class Tool:
    schema: dict
    fn: Callable  # (ws: Workspace, args: dict) -> str


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _p(typ: str, description: str, **extra) -> dict:
    d = {"type": typ, "description": description}
    d.update(extra)
    return d


class ToolRegistry:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self._tools: dict[str, Tool] = {
            "read_file": Tool(
                _schema("read_file", "读取文本文件内容，返回带行号的内容。", {
                    "path": _p("string", "相对于工作目录的文件路径"),
                    "offset": _p("integer", "起始行号（从 0 起，缺省 0）"),
                    "limit": _p("integer", "最多读取行数（缺省读全部）"),
                }, ["path"]),
                read_file,
            ),
            "write_file": Tool(
                _schema("write_file", "创建或覆盖写入一个文件，自动创建父目录。", {
                    "path": _p("string", "相对于工作目录的文件路径"),
                    "content": _p("string", "要写入的完整文件内容"),
                }, ["path", "content"]),
                write_file,
            ),
            "edit_file": Tool(
                _schema("edit_file", "对文件做定点替换：把唯一出现的 old_string 替换为 new_string。", {
                    "path": _p("string", "相对于工作目录的文件路径"),
                    "old_string": _p("string", "要被替换的原文（必须唯一匹配）"),
                    "new_string": _p("string", "替换后的新内容"),
                }, ["path", "old_string", "new_string"]),
                edit_file,
            ),
            "list_files": Tool(
                _schema("list_files", "列出目录下的文件与子目录。", {
                    "path": _p("string", "相对于工作目录的路径，缺省为工作目录根"),
                }, []),
                list_files,
            ),
            "delete_file": Tool(
                _schema("delete_file", "删除一个文件（不能删除目录）。", {
                    "path": _p("string", "相对于工作目录的文件路径"),
                }, ["path"]),
                delete_file,
            ),
            "search_files": Tool(
                _schema("search_files", "在文件内容中搜索指定字符串，返回匹配行及其位置。", {
                    "query": _p("string", "要搜索的字符串"),
                    "path": _p("string", "搜索的文件或目录（缺省为整个工作目录）"),
                }, ["query"]),
                search_files,
            ),
            "execute_command": Tool(
                _schema("execute_command", "在工作目录下执行一条 shell 命令，返回退出码、stdout 与 stderr。", {
                    "command": _p("string", "要执行的命令"),
                    "cwd": _p("string", "命令执行的相对目录（缺省为工作目录根）"),
                    "timeout": _p("integer", "超时秒数（缺省 60）"),
                }, ["command"]),
                execute_command,
            ),
        }

    def schemas(self) -> list[dict]:
        return [t.schema for t in self._tools.values()]

    def run(self, name: str, args: dict) -> str:
        if name not in self._tools:
            return f"未知工具：{name}。可用工具：{', '.join(self._tools)}"
        try:
            result = self._tools[name].fn(self.ws, args)
        except Exception as e:  # 工具内部异常也回喂给模型，让其自愈
            return f"工具执行出错：{type(e).__name__}: {e}"
        return self.ws.truncate(result)
