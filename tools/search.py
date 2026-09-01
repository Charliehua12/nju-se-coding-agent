"""搜索工具：在文件内容中 grep，返回匹配行及位置。"""
from __future__ import annotations

from pathlib import Path

_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".pdf", ".zip", ".gz",
    ".tar", ".7z", ".exe", ".o", ".obj", ".pyc", ".so", ".dylib", ".dll",
    ".woff", ".ttf", ".mp4", ".mp3", ".class", ".jar", ".wasm",
}


def _is_text(p: Path) -> bool:
    return p.suffix.lower() not in _BINARY_EXT


def search_files(ws, args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "错误：query 为空。"
    path = ws.resolve(args.get("path", "."))
    if path.is_dir():
        targets = [p for p in path.rglob("*") if p.is_file() and _is_text(p)]
    elif path.is_file():
        targets = [path]
    else:
        return f"错误：路径不存在：{path}"

    hits: list[str] = []
    for p in targets:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if query in line:
                hits.append(f"{p.relative_to(ws.root)}:{lineno}: {line.strip()[:200]}")
    if not hits:
        return f"未找到匹配 '{query}' 的内容。"
    shown = hits[:200]
    tail = f"\n...（共 {len(hits)} 条，仅显示前 200 条）" if len(hits) > 200 else ""
    return "\n".join(shown) + tail
