"""文件工具：读取、写入、定点编辑、列目录、删除。"""
from __future__ import annotations

from pathlib import Path


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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"写入失败：{e}"
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
    path.write_text(text.replace(old, new), encoding="utf-8")
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
    path.unlink()
    return f"已删除 {path}"
