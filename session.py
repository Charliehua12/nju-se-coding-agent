"""会话管理：多个命名对话的创建、切换、删除与持久化。

每个会话是一个独立的 Agent，拥有独立的对话历史（ContextManager），
共享同一个模型客户端与工具注册表。持久化格式为 {会话名: [消息列表]}，
同时兼容旧版「单个消息列表」的格式。
"""
from __future__ import annotations

import json
from pathlib import Path

from agent import Agent
from config import Config
from llm import ChatProvider
from tools import ToolRegistry


class SessionManager:
    def __init__(self, config: Config, client: ChatProvider, tools: ToolRegistry):
        self.config = config
        self.client = client
        self.tools = tools
        self.sessions: dict[str, Agent] = {}
        self.current: str | None = None
        self.approve = bool(config.approve)  # 审查开关，可在运行中切换

    # ---- 会话操作 ----
    def new(self, name: str | None = None) -> Agent:
        name = name or self._auto_name()
        if name in self.sessions:
            raise ValueError(f"会话 '{name}' 已存在，请换一个名字或 /switch 切换")
        agent = Agent(self.config, self.client, self.tools)
        self.sessions[name] = agent
        self.current = name
        return agent

    def switch(self, name: str) -> Agent | None:
        if name not in self.sessions:
            return None
        self.current = name
        return self.sessions[name]

    def remove(self, name: str) -> bool:
        if name not in self.sessions:
            return False
        del self.sessions[name]
        if self.current == name:
            self.current = next(iter(self.sessions), None)
        return True

    def current_agent(self) -> Agent | None:
        return self.sessions.get(self.current) if self.current else None

    def names(self) -> list[str]:
        return list(self.sessions)

    def _auto_name(self) -> str:
        i = 1
        while f"会话{i}" in self.sessions:
            i += 1
        return f"会话{i}"

    # ---- 持久化 ----
    @staticmethod
    def _normalize(raw) -> dict | None:
        """把某会话的持久化数据规范化为 {messages, calibration}。

        兼容旧版纯消息列表格式（[message, ...]）；新格式为
        {"messages": [...], "calibration": 1.0}。
        """
        if isinstance(raw, list):
            return {"messages": raw, "calibration": 1.0}
        if isinstance(raw, dict) and isinstance(raw.get("messages"), list):
            return {"messages": raw["messages"], "calibration": float(raw.get("calibration") or 1.0)}
        return None

    def save(self, path: str) -> None:
        """保存全部会话为 {会话名: 会话状态}。"""
        data = {name: agent.dump_session() for name, agent in self.sessions.items()}
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_one(self, path: str, name: str) -> None:
        """只保存指定会话；文件仍写成 {会话名: 会话状态}，便于 /load 加载任意一个。"""
        if name not in self.sessions:
            raise KeyError(f"会话 '{name}' 不存在")
        Path(path).write_text(
            json.dumps({name: self.sessions[name].dump_session()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore(self, name: str, data: dict) -> None:
        agent = Agent(self.config, self.client, self.tools)
        agent.load_history(data["messages"])
        agent.context.calibration = data["calibration"]
        self.sessions[name] = agent

    def load(self, path: str) -> None:
        """从文件加载会话。若文件含多个会话，全部载入并切到第一个。"""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        # 兼容旧版单会话格式（纯消息列表）
        if isinstance(raw, list):
            raw = {"会话1": raw}
        if not isinstance(raw, dict):
            raise ValueError("会话文件格式不正确：应为 {会话名: 会话} 的 JSON 对象")
        self.sessions = {}
        for name, value in raw.items():
            data = self._normalize(value)
            if data is None:
                continue
            self._restore(name, data)
        self.current = next(iter(self.sessions), None)

    def load_one(self, path: str, name: str) -> None:
        """只加载文件中的指定会话（若文件只有一个会话，则直接加载它）。"""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = {"会话1": raw}
        if not isinstance(raw, dict):
            raise ValueError("会话文件格式不正确：应为 {会话名: 会话} 的 JSON 对象")
        if name not in raw:
            raise KeyError(f"会话 '{name}' 不在文件 {path} 中（可用：{', '.join(raw)}）")
        data = self._normalize(raw[name])
        if data is None:
            raise ValueError(f"会话 '{name}' 的数据格式不正确")
        self._restore(name, data)
        self.current = name
