"""工具注册表：把「给模型看的 JSON Schema」与「本地执行函数」绑定在一起。

题目点名的「工具的定义与本地执行」核心：模型看到的是结构化 schema，
我们拿到结构化 tool_call 后在本地沙箱内执行，返回字符串结果回喂给模型。

安全审查（Codex 式权限模型）：
  - Workspace.confirm：命令执行前的人工确认回调；
  - Workspace.approve：写文件 / 改文件 / 删文件前的人工审批回调；
  - dry_run 模式：先预览 diff（统一 diff 格式），用户批准后再真正执行；
  - 审批被拒绝时抛出 RequestDenied，由 Agent 回喂给模型并继续推进。
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import RequestDenied
from .files import read_file, write_file, edit_file, list_files, delete_file
from .memory import memory
from .shell import execute_command
from .search import search_files
from .skills import invoke_skill


@dataclass
class Change:
    """一次文件改动，供事后审查与回滚。"""
    index: int
    action: str          # "write" | "edit" | "delete"
    path: str            # 相对工作目录的路径
    before: str | None   # 改动前内容（新文件为 None）
    after: str | None    # 改动后内容（删除为 None）


class Workspace:
    """工作目录沙箱：路径约束、输出截断、审批回调、改动追踪。"""

    def __init__(self, root: Path, confirm: Callable[[str], bool] | None = None,
                 approve: Callable[[str, str, str], bool] | None = None,
                 dry_run: bool = False, max_output_chars: int = 12_000):
        self.root = root.resolve()
        self.confirm = confirm  # 命令执行前的确认（None 表示自动放行）
        self.approve = approve  # 文件写/改/删的审批（None 表示自动放行）
        self.dry_run = dry_run  # 为 True 时只预览 diff，不真正修改
        self.max_output_chars = max_output_chars
        self.changes: list[Change] = []  # 改动日志，供 /review 审查与回滚
        self.memory_store = None  # 项目长期记忆（MEMORY.md，由 main 注入，可选）
        self.user_store = None  # 用户画像记忆（USER.md，由 main 注入，可选）
        self.skills: list = []  # 可用技能（由 main 注入 discover_skills 结果）
        self._result_counter = 0  # 大结果落盘文件的编号

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
        """输出截断：超过阈值时把完整内容落盘，上下文只放头部预览 + 路径占位。

        大结果可重新读取（read_file），因此落盘比直接截断更保值。
        """
        if len(text) <= self.max_output_chars:
            return text
        results_dir = self.root / ".my_agent_core" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        name = f"result_{self._result_counter}.txt"
        self._result_counter += 1
        path = results_dir / name
        path.write_text(text, encoding="utf-8", errors="replace")
        head = text[:2000]
        return (f"{head}\n...[输出过长：完整 {len(text)} 字符已保存到 "
                f"{path.relative_to(self.root)}，可用 read_file 读取]")

    # ---- 改动追踪（供事后审查 / 回滚） ----
    def record_change(self, action: str, path: Path, before: str | None, after: str | None) -> None:
        self.changes.append(Change(
            index=len(self.changes) + 1,
            action=action,
            path=str(path.relative_to(self.root)),
            before=before,
            after=after,
        ))

    def revert_change(self, ch: Change) -> None:
        """回滚一项改动：逆操作写回磁盘。"""
        path = self.resolve(ch.path)
        if ch.action == "write":
            if ch.before is None:
                path.unlink(missing_ok=True)  # 新建 → 删除
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(ch.before, encoding="utf-8")  # 覆盖 → 还原
        elif ch.action == "edit":
            path.write_text(ch.before or "", encoding="utf-8")  # 还原为编辑前
        elif ch.action == "delete":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ch.before or "", encoding="utf-8")  # 删除 → 重建

    def make_diff(self, before: str, after: str, path: str) -> str:
        """基于历史内容生成统一 diff（用于审查展示）。"""
        if before == after:
            return "(无变化)"
        return "".join(difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        ))

    # ---- 审批辅助 ----
    def request(self, action: str, preview: str = "", full_preview: str | None = None) -> None:
        """请求用户审批；被拒绝则抛 RequestDenied。approve 为 None 时自动放行。"""
        if self.approve is None:
            return
        show = full_preview if full_preview is not None else preview
        if not self.approve(action, show, preview):
            raise RequestDenied(f"用户拒绝了该操作：{action}")

    def preview_edit(self, path: Path, new_content: str) -> str:
        """生成统一 diff（旧内容 → 新内容），供审批展示。"""
        old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if old == new_content:
            return "(内容无变化)"
        return self.make_diff(old, new_content, str(path))


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
                parallel_safe=True,
            ),
            "write_file": Tool(
                _schema("write_file", "创建或覆盖写入一个文件（自动创建父目录）。", {
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
                parallel_safe=True,
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
                parallel_safe=True,
            ),
            "execute_command": Tool(
                _schema("execute_command", "在工作目录下执行一条 shell 命令，返回退出码、stdout 与 stderr。", {
                    "command": _p("string", "要执行的命令"),
                    "cwd": _p("string", "命令执行的相对目录（缺省为工作目录根）"),
                    "timeout": _p("integer", "超时秒数（缺省 60）"),
                }, ["command"]),
                execute_command,
            ),
            "memory": Tool(
                _schema("memory", "读写长期记忆（跨会话生效）。把项目约定、踩坑记录、用户偏好等关键事实写入记忆，下次会话仍会保留。", {
                    "target": _p("string", "记忆库：memories 项目事实（缺省）/ user 用户偏好画像"),
                    "action": _p("string", "操作：add 添加 / replace 替换 / remove 删除"),
                    "content": _p("string", "add 时要写入的完整内容"),
                    "old_text": _p("string", "replace/remove 时要定位的旧内容子串"),
                    "new_content": _p("string", "replace 时的新内容"),
                }, ["action"]),
                memory,
            ),
            "invoke_skill": Tool(
                _schema("invoke_skill", "加载并返回某个技能的使用说明。当任务与某个技能匹配时先调用它，再严格按其说明执行。", {
                    "name": _p("string", "技能名称（见系统提示中的可用技能清单）"),
                }, ["name"]),
                invoke_skill,
                parallel_safe=True,
            ),
        }

    def schemas(self) -> list[dict]:
        return [t.schema for t in self._tools.values()]

    def is_parallel_safe(self, name: str) -> bool:
        """该工具是否可安全并发（只读）。用于批执行因果性判断。"""
        t = self._tools.get(name)
        return bool(t and t.parallel_safe)

    def run(self, name: str, args: dict, dry_run: bool = False) -> str:
        if name not in self._tools:
            return f"未知工具：{name}。可用工具：{', '.join(self._tools)}"
        prev = self.ws.dry_run
        self.ws.dry_run = bool(dry_run)
        try:
            # 只读工具永远允许在预览模式使用
            if dry_run and name in ("read_file", "list_files", "search_files"):
                try:
                    return self._tools[name].fn(self.ws, args)
                except Exception as e:
                    return f"工具执行出错：{type(e).__name__}: {e}"
            try:
                return self.ws.truncate(self._tools[name].fn(self.ws, args))
            except RequestDenied as e:
                return str(e)
            except Exception as e:  # 工具内部异常也回喂给模型，让其自愈
                return f"工具执行出错：{type(e).__name__}: {e}"
        finally:
            self.ws.dry_run = prev


@dataclass
class Tool:
    schema: dict
    fn: Callable  # (ws: Workspace, args: dict) -> str
    parallel_safe: bool = False  # 只读/无副作用，可安全并发；写操作须严格串行保因果
