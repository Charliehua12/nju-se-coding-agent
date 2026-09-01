"""命令行入口。

两种模式：
  1. 单次执行：python main.py "任务描述" [--plan] [--ask] [--workdir ...] [--save ...]
  2. 交互对话：python main.py （不带任务参数进入 REPL，持续多轮对话）

交互模式下可用的命令：/plan、/save <文件>、/clear、/usage、/help，exit 退出。
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


def _make_callbacks(streamed: list[str]):
    """构造一组显示回调，共享 streamed 列表以判断最终回答是否已流式输出。"""
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

    return on_text, on_plan, on_tool_call, on_tool_result


def _print_usage(client: DeepSeekClient) -> None:
    u = client.usage
    print(f"{DIM}本次消耗：{u.prompt_tokens} prompt + {u.completion_tokens} completion tokens"
          f"（共 {u.total_tokens}）{RESET}")


def _single_shot(agent: Agent, client: DeepSeekClient, config: Config, args, history) -> int:
    print(f"{DIM}工作目录：{config.workdir}{RESET}")
    print(f"{DIM}模型：{config.model}{RESET}\n")
    streamed: list[str] = []
    on_text, on_plan, on_tool_call, on_tool_result = _make_callbacks(streamed)
    if args.plan:
        print(f"{BOLD}{MAGENTA}── 制定执行计划 ──{RESET}")

    final = agent.run(
        args.task,
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

    _print_usage(client)
    if args.save:
        _save(agent, args.save)
    return 0


def _handle_command(line: str, plan_mode: bool, agent: Agent, client: DeepSeekClient) -> bool:
    """处理 / 开头的交互命令，返回新的 plan_mode。"""
    parts = line.split(maxsplit=1)
    c = parts[0].lower()
    if c == "/help":
        print("可用命令：")
        print(f"  {BOLD}/plan{RESET}        切换计划模式（当前 {'开' if plan_mode else '关'}）")
        print(f"  {BOLD}/save <文件>{RESET}  保存当前会话（默认 session.json）")
        print(f"  {BOLD}/clear{RESET}       清空对话，重新开始")
        print(f"  {BOLD}/usage{RESET}       显示累计 token 消耗")
        print(f"  {BOLD}/help{RESET}        显示本帮助")
        print(f"  {BOLD}exit{RESET}          退出")
    elif c == "/plan":
        plan_mode = not plan_mode
        print(f"计划模式：{'开' if plan_mode else '关'}")
    elif c == "/save":
        fname = parts[1].strip() if len(parts) > 1 else "session.json"
        _save(agent, fname)
    elif c == "/clear":
        agent.clear()
        print("对话已清空，重新开始。")
    elif c == "/usage":
        u = client.usage
        print(f"{u.prompt_tokens} prompt + {u.completion_tokens} completion（共 {u.total_tokens}）")
    else:
        print(f"未知命令：{c}（输入 /help 查看）")
    return plan_mode


def _save(agent: Agent, fname: str) -> None:
    try:
        Path(fname).write_text(
            json.dumps(agent.dump_history(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"{DIM}会话已保存到 {fname}{RESET}")
    except OSError as e:
        print(f"[错误] 保存会话失败：{e}")


def _repl(agent: Agent, client: DeepSeekClient, config: Config, args) -> int:
    print(f"{DIM}工作目录：{config.workdir}{RESET}")
    print(f"{DIM}模型：{config.model}{RESET}")
    print(f"{DIM}进入交互对话模式，直接输入任务即可。{RESET}"
          f"{DIM}{BOLD}/help{RESET}{DIM} 查看命令，{RESET}{DIM}{BOLD}exit{RESET}{DIM} 退出。{RESET}\n")

    plan_mode = bool(args.plan)
    while True:
        try:
            line = input(f"{GREEN}{BOLD}你>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q", "/exit", "/quit"):
            print("再见。")
            break
        if line.startswith("/"):
            plan_mode = _handle_command(line, plan_mode, agent, client)
            continue

        streamed: list[str] = []
        on_text, on_plan, on_tool_call, on_tool_result = _make_callbacks(streamed)
        if plan_mode:
            print(f"{BOLD}{MAGENTA}── 制定执行计划 ──{RESET}")
        final = agent.reply(
            line,
            plan_first=plan_mode,
            on_text=on_text,
            on_plan=on_plan,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )
        sys.stdout.write("\n")
        if not streamed:
            print(final)
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="编程智能体（DeepSeek）")
    parser.add_argument("task", nargs="?", help="要完成的任务；缺省则进入交互对话模式")
    parser.add_argument("--workdir", "-w", default=None, help="工作目录（沙箱根目录）")
    parser.add_argument("--max-iter", type=int, default=None, help="最大迭代步数")
    parser.add_argument("--ask", action="store_true", help="执行命令前人工确认")
    parser.add_argument("--plan", action="store_true", help="先制定计划再执行")
    parser.add_argument("--save", metavar="FILE", default=None, help="结束后把会话保存到 JSON 文件")
    parser.add_argument("--resume", metavar="FILE", default=None, help="从已保存的会话继续")
    args = parser.parse_args()

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

    if args.task:
        return _single_shot(agent, client, config, args, history)

    if history:
        agent.load_history(history)
    return _repl(agent, client, config, args)


if __name__ == "__main__":
    raise SystemExit(main())
