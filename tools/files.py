"""文件工具：读取、写入、定点编辑、列目录、删除。

写/改/删属于「修改类」操作，在审查模式下会先请求用户审批：
  - dry_run 时只返回 diff 预览，不真正改动；
  - 用户批准后才真正写入；被拒绝则抛 RequestDenied。
"""
from __future__ import annotations

from pathlib import Path

from .errors import RequestDenied


def read_file(ws, args: dict) -> str:
    path = ws.resolve(args["path"])
    if not path.is_file():
        return f"错误：文件不存在或不是普通文件：{path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"读取失败：{e}"
    lines = text.splitlines()
    offset = max(int(args.get("offset") or 0), 0)
    limit = args.get("limit")
    end = len(lines) if limit is None else offset + int(limit)
    out = [f"{i + 1:>5} | {lines[i]}" for i in range(offset, min(end, len(lines)))]
    return "\n".join(out) if out else "(空文件)"


def write_file(ws, args: dict) -> str:
    path = ws.resolve(args["path"])
    content = args.get("content", "")
    preview = ws.preview_edit(path, content)
    if preview != "(内容无变化)":
        ws.request(f"write_file {path.relative_to(ws.root)}", preview)
    if ws.dry_run:
        return f"[预览] 将写入 {path}：\n{preview}"
    before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"写入失败：{e}"
    ws.record_change("write", path, before, content)
    return f"已写入 {path}（{len(content)} 字符）"


def edit_file(ws, args: dict) -> str:
    path = ws.resolve(args["path"])
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    if not path.is_file():
        return f"错误：文件不存在：{path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"读取失败：{e}"
    count = text.count(old)
    if count == 0:
        return "错误：未找到要替换的内容（old_string 不存在）。请用 read_file 确认精确文本。"
    if count > 1:
        return f"错误：old_string 出现 {count} 次，无法唯一确定。请提供更长的上下文使其唯一。"
    new_text = text.replace(old, new, 1)
    preview = ws.preview_edit(path, new_text)
    ws.request(f"edit_file {path.relative_to(ws.root)}", preview)
    if ws.dry_run:
        return f"[预览] 将编辑 {path}：\n{preview}"
    path.write_text(new_text, encoding="utf-8")
    ws.record_change("edit", path, text, new_text)
    return f"已替换 1 处（+{len(new)} -{len(old)} 字符）"


def list_files(ws, args: dict) -> str:
    path = ws.resolve(args.get("path", "."))
    if not path.is_dir():
        return f"错误：不是目录：{path}"
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines = [f"{'dir ' if p.is_dir() else 'file'} {p.name}" for p in entries]
    return "\n".join(lines) if lines else "(空目录)"


def delete_file(ws, args: dict) -> str:
    path = ws.resolve(args["path"])
    if not path.exists():
        return f"错误：文件不存在：{path}"
    if path.is_dir():
        return f"错误：{path} 是目录，请改用命令删除。"
    ws.request(f"delete_file {path.relative_to(ws.root)}", preview="")
    if ws.dry_run:
        return f"[预览] 将删除 {path}"
    before = path.read_text(encoding="utf-8", errors="replace")
    path.unlink()
    ws.record_change("delete", path, before, None)
    return f"已删除 {path}"
