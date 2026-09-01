"""长期记忆：跨会话沉淀项目事实、用户偏好与工作约定。

借鉴参考框架的「冻结快照」设计：
  - 启动时读取记忆文件生成冻结快照（注入 system，本会话全程静止，保护前缀缓存）；
  - 运行中维护一份 live 条目列表：add/replace/remove 即时更新 live 状态并原子落盘，
    但绝不修改冻结快照；下次启动新会话时重新读取磁盘，新记忆生效。

两类记忆分文件存储：
  - MEMORY.md：项目事实 / 约定（模型与 agent 的公共记忆，上限较大）；
  - USER.md：用户偏好画像（跨会话的用户个人记忆，上限较小）。
两者共用同一 MemoryStore 实现，仅文件名与字符上限不同。
"""
from __future__ import annotations

from pathlib import Path

MEMORY_FILE = ".my_agent_core/memory/MEMORY.md"
USER_FILE = ".my_agent_core/memory/USER.md"
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375
ENTRY_DELIMITER = "\n§\n"


class MemoryStore:
    def __init__(self, workdir: Path, file: str = MEMORY_FILE,
                 char_limit: int = MEMORY_CHAR_LIMIT):
        self.path = workdir / file
        self.char_limit = char_limit
        self.snapshot = ""      # 冻结快照：会话内静止，注入 system prompt
        self._entries: list[str] = []  # live 状态：本次会话可读写
        self._load()

    def _load(self) -> None:
        text = self.path.read_text(encoding="utf-8", errors="replace") if self.path.is_file() else ""
        self.snapshot = text
        self._entries = self._split(text)

    @staticmethod
    def _split(text: str) -> list[str]:
        return [e.strip() for e in text.split(ENTRY_DELIMITER) if e.strip()]

    # ---- 受控维护操作（操作 live 状态 + 原子落盘） ----
    def add(self, content: str) -> str:
        content = content.strip()
        if not content:
            return "错误：内容为空。"
        if content in self._entries:
            return "已存在相同内容，未重复添加。"
        entries = list(self._entries) + [content]
        return self._write(entries, "已写入长期记忆。")

    def replace(self, old_text: str, new_content: str) -> str:
        matches = [e for e in self._entries if old_text and old_text in e]
        if len(matches) != 1:
            return f"错误：匹配到 {len(matches)} 条，无法唯一替换：{matches[:3]}"
        new_content = new_content.strip()
        entries = [new_content if e == matches[0] else e for e in self._entries]
        return self._write(entries, "已替换。")

    def remove(self, old_text: str) -> str:
        matches = [e for e in self._entries if old_text and old_text in e]
        if len(matches) != 1:
            return f"错误：匹配到 {len(matches)} 条，无法唯一删除：{matches[:3]}"
        entries = [e for e in self._entries if e != matches[0]]
        return self._write(entries, "已删除。")

    def _write(self, entries: list[str], ok_msg: str) -> str:
        text = ENTRY_DELIMITER.join(entries)
        if len(text) > self.char_limit:
            return (f"错误：写入后超出上限 {self.char_limit} 字符。"
                    f"请先用 replace/remove 精简旧记忆，或合并内容后再添加。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)  # 原子替换，避免写一半损坏
        self._entries = entries  # 更新 live 状态（冻结快照不动）
        return ok_msg
