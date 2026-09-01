"""命令行入口。

用法示例：
    python main.py "写一个快速排序的实现并跑通测试"
    python main.py --workdir /tmp/demo "在目录下实现一个 TODO 命令行工具"
    python main.py --plan "先制定计划，再实现一个计算器"
    python main.py --ask "执行命令前人工确认"
    python main.py --save session.json "..."   # 结束后保存会话
    python main.py --resume session.json "接着把测试补上"   # 从会话继续
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import Agent
from config import Config
from llm import DeepSeekClient
from tools import ToolRegistry, Workspace

# 轻量 ANSI 颜色（不引入 rich）
RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"


def main() -> int:
    parser = argparse.ArgumentParser(description="编程智能体（DeepSeek）")
    parser.add_argument("task", nargs="?", help="要完成的任务；缺省则交互输入")
    parser.add_argument("--workdir", "-w", default=None, help="工作目录（沙箱根目录）")
    parser.add_argument("--max-iter", type=int, default=None, help="最大迭代步数")
    parser.add_argument("--ask", action="store_true", help="执行命令前人工确认")
    parser.add_argument("--plan", action="store_true", help="先制定计划再执行")
    parser.add_argument("--save", metavar="FILE", default=None, help="结束后把会话保存到 JSON 文件")
    parser.add_argument("--resume", metavar="FILE", default=None, help="从已保存的会话继续")
    args = parser.parse_args()

    task = args.task
    if not task:
        task = input("请输入任务：").strip()
        if not task:
            print("未提供任务。")
            return 1

    config = Config.from_env()
    if args.workdir:
        config.workdir = Path(args.workdir).resolve()
    if args.max_iter:
        config.max_iterations = args.max_iter
    if args.ask:
        # 交互确认模式下禁用并发，避免多个命令同时在子线程里弹 input() 询问
        config.parallel_tools = False

    history = None
    if args.resume:
        try:
            history = json.loads(Path(args.resume).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[错误] 无法读取会话文件：{e}")
            return 1

    confirm = None
    if args.ask:
        def confirm(command: str) -> bool:
            ans = input(f"{RED}是否执行该命令？{RESET}\n  {command}\n[y/N] ").strip().lower()
            return ans in ("y", "yes")

    ws = Workspace(config.workdir, confirm=confirm, max_output_chars=config.max_output_chars)
    client = DeepSeekClient(config)
    agent = Agent(config, client, ToolRegistry(ws))

    print(f"{DIM}工作目录：{config.workdir}{RESET}")
    print(f"{DIM}模型：{config.model}{RESET}\n")

    streamed: list[str] = []

    def on_text(delta: str) -> None:
        streamed.append(delta)
        sys.stdout.write(f"{CYAN}{delta}{RESET}")
        sys.stdout.flush()

    def on_plan(delta: str) -> None:
        sys.stdout.write(f"{MAGENTA}{delta}{RESET}")
        sys.stdout.flush()

    def on_tool_call(name: str, args_str: str) -> None:
        sys.stdout.write("\n")
        print(f"{BOLD}{YELLOW}▶ 调用工具 {name}{RESET} {DIM}{args_str[:300]}{RESET}")

    def on_tool_result(result: str) -> None:
        print(f"{DIM}{result[:400]}{RESET}")

    if args.plan:
        print(f"{BOLD}{MAGENTA}── 制定执行计划 ──{RESET}")

    final = agent.run(
        task,
        plan_first=args.plan,
        history=history,
        on_text=on_text,
        on_plan=on_plan,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    sys.stdout.write("\n")
    print(f"{GREEN}{BOLD}===== 任务完成 ====={RESET}")
    if not streamed:  # 最终回答未流式输出（例如中途报错）时才补打印
        print(final)

    u = client.usage
    print(f"{DIM}本次消耗：{u.prompt_tokens} prompt + {u.completion_tokens} completion tokens"
          f"（共 {u.total_tokens}）{RESET}")

    if args.save:
        try:
            Path(args.save).write_text(
                json.dumps(agent.dump_history(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"{DIM}会话已保存到 {args.save}{RESET}")
        except OSError as e:
            print(f"[错误] 保存会话失败：{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
