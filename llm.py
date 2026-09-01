"""DeepSeek 客户端：基于标准库的 OpenAI 兼容 HTTP 客户端 + 流式解析。

只用 http.client + json + ssl，连 HTTP 层都自行实现，不依赖 openai/httpx 等库。
通过 ChatProvider 协议面向接口编程：Agent 只依赖该协议而非具体实现（依赖倒置），
因此可无缝替换为其它 OpenAI 兼容模型或测试用的 mock。
"""
from __future__ import annotations

import http.client
import json
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlparse

from config import Config


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 原始 JSON 字符串，交由 parser 解析


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ChatProvider(Protocol):
    """Agent 依赖的最小接口，而非具体实现（依赖倒置）。"""

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResponse: ...


def _parse_usage(obj: dict | None) -> Usage:
    if not obj:
        return Usage()
    return Usage(
        prompt_tokens=int(obj.get("prompt_tokens") or 0),
        completion_tokens=int(obj.get("completion_tokens") or 0),
    )


class DeepSeekClient:
    def __init__(self, config: Config):
        self.config = config
        self._usage = Usage()
        url = urlparse(config.base_url)
        self.host = url.netloc
        base = (url.path or "").rstrip("/")
        self.endpoint = f"{base}/chat/completions"

    @property
    def usage(self) -> Usage:
        """累计 token 消耗，用于结尾的成本/可观测统计。"""
        return self._usage

    # ---- 对外入口 ----
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._do_request(payload, stream, on_text)
                self._usage.prompt_tokens += resp.usage.prompt_tokens
                self._usage.completion_tokens += resp.usage.completion_tokens
                return resp
            except LLMError as e:
                # 4xx（非 429）属于请求本身的问题，重试无意义
                if e.status is not None and e.status < 500 and e.status != 429:
                    raise
                last = e
            except (OSError, TimeoutError) as e:
                last = e
            if attempt < self.config.max_retries:
                time.sleep(2 ** attempt)  # 指数退避：1s / 2s / 4s
        raise LLMError(f"请求失败（已重试 {self.config.max_retries} 次）：{last}")

    # ---- HTTP 底层 ----
    def _do_request(self, payload: dict, stream: bool, on_text) -> LLMResponse:
        body = json.dumps(payload)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        conn = http.client.HTTPSConnection(
            self.host, timeout=600, context=ssl.create_default_context()
        )
        try:
            conn.request("POST", self.endpoint, body=body.encode("utf-8"), headers=headers)
            resp = conn.getresponse()
            if resp.status >= 400:
                err_body = resp.read().decode("utf-8", "replace")
                raise LLMError(f"HTTP {resp.status}: {err_body[:500]}", status=resp.status)
            if stream:
                return self._read_sse(resp, on_text)
            data = resp.read().decode("utf-8")
            return self._extract(json.loads(data))
        finally:
            conn.close()

    # ---- 流式解析（SSE + 工具调用增量拼接） ----
    def _read_sse(self, resp: http.client.HTTPResponse, on_text) -> LLMResponse:
        content_parts: list[str] = []
        # index -> {"id":..., "name":..., "arguments":...}，流式片段按 index 归位
        slots: dict[int, dict[str, str]] = {}
        finish = "stop"
        usage = Usage()
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = _parse_usage(obj["usage"])
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            text = delta.get("content")
            if text:
                content_parts.append(text)
                if on_text:
                    on_text(text)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=s["arguments"])
            for i, s in sorted(slots.items())
        ]
        return LLMResponse(
            content="".join(content_parts), tool_calls=tool_calls,
            finish_reason=finish, usage=usage,
        )

    # ---- 非流式解析 ----
    def _extract(self, obj: dict) -> LLMResponse:
        choice = (obj.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", ""),
                )
            )
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage=_parse_usage(obj.get("usage")),
        )
