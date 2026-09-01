"""配置加载：从环境变量或 .env 读取，密钥一律不进入版本库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 解析，仅补全缺失的环境变量（不覆盖已存在值）。

    不依赖 python-dotenv，避免引入第三方依赖。
    """
    if path is None:
        path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"  # 便宜快速的 flash 模型
    temperature: float = 0.2
    max_iterations: int = 25
    context_budget_tokens: int = 40_000  # 上下文预算（DeepSeek 上下文上限预留余量）
    max_output_chars: int = 12_000      # 单次工具输出截断上限
    command_timeout: int = 60           # 命令执行超时（秒）
    max_retries: int = 3
    parallel_tools: bool = True  # 同一轮返回的多个工具调用是否并发执行
    approve: bool = False        # 审查模式：写文件/删文件/执行命令前弹人工确认 + diff 预览
    max_input_chars: int = 30_000      # 单条用户输入长度上限
    max_response_chars: int = 16_000   # 单条模型输出长度上限
    workdir: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise SystemExit(
                "缺少 DEEPSEEK_API_KEY：请执行 export DEEPSEEK_API_KEY=... "
                "或写入项目根目录的 .env 文件"
            )
        workdir = Path(os.environ.get("AGENT_WORKDIR", str(Path.cwd()))).resolve()
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.2")),
            max_iterations=int(os.environ.get("AGENT_MAX_ITERATIONS", "25")),
            context_budget_tokens=int(os.environ.get("AGENT_CONTEXT_BUDGET", "40000")),
            max_output_chars=int(os.environ.get("AGENT_MAX_OUTPUT_CHARS", "12000")),
            command_timeout=int(os.environ.get("AGENT_COMMAND_TIMEOUT", "60")),
            max_retries=int(os.environ.get("AGENT_MAX_RETRIES", "3")),
            parallel_tools=os.environ.get("AGENT_PARALLEL", "1").lower() not in ("0", "false", "no"),
            approve=os.environ.get("AGENT_APPROVE", "0").lower() in ("1", "true", "yes"),
            max_input_chars=int(os.environ.get("AGENT_MAX_INPUT", "30000")),
            max_response_chars=int(os.environ.get("AGENT_MAX_RESPONSE", "16000")),
            workdir=workdir,
        )
