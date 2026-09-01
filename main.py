"""命令行入口。

两种模式：
  1. 单次执行：python main.py "任务描述" [--plan] [--ask] [--workdir ...] [--save ...] [--resume ...]
  2. 交互对话：python main.py （进入 REPL，支持多会话切换与持久化）

交互命令：/new、/list、/switch、/del、/save、/load、/clear、/plan、/usage、/help，exit 退出。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import Config
from llm import DeepSeekClient
from markdown import MarkdownRenderer, render_markdown
from session import SessionManager
from tools import RequestDenied, ToolRegistry, Workspace

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
    """构造一组显示回调：模型文本用 Markdown 渲染器流式输出。

    返回 (on_text, on_plan, on_tool_call, on_tool_result, flush)，
    其中 flush 在整轮结束后调用，把缓冲的尾部内容渲染输出。
    """
    md_r = MarkdownRenderer(sys.stdout.write)

    def on_text(delta: str) -> None:
        streamed.append(delta)
        md_r.feed(delta)

    def on_plan(delta: str) -> None:
        md_r.feed(delta)

    def on_tool_call(name: str, args_str: str) -> None:
        md_r.flush()
        sys.stdout.write("\n")
        print(f"{BOLD}{YELLOW}▶ 调用工具 {name}{RESET} {DIM}{args_str[:300]}{RESET}")

    def on_tool_result(result: str) -> None:
        print(f"{DIM}{result[:400]}{RESET}")

    def flush() -> None:
        md_r.flush()

    return on_text, on_plan, on_tool_call, on_tool_result, flush


def _plan_review_loop(plan: str, revise_fn) -> str | None:
    """计划的人工审核：可执行 / 修改意见重拟 / 取消，支持多轮往返。"""
    try:
        while True:
            print(f"{BOLD}{MAGENTA}──── 执行计划 ────{RESET}")
            print(render_markdown(plan))
            ans = input(f"{YELLOW}[y]按计划执行  [m]修改  [n]重拟  [c]取消{RESET}：").strip().lower()
            if ans in ("y", "yes"):
                return plan
            if ans in ("c", "cancel", "q"):
                print("已取消，未执行。")
                return None
            if ans.startswith("m"):
                fb = input("修改意见：").strip()
                if fb:
                    plan = revise_fn(plan, fb)
                    print()
                continue
            if ans in ("n", "no"):
                plan = revise_fn(plan, "请重新制定一个更清晰、更可执行的计划。")
                print()
                continue
            print("请输入 y / m / n / c")
    except (EOFError, KeyboardInterrupt):
        print(f"\n{DIM}（未获得确认，按计划执行）{RESET}")
        return plan


def _print_usage(client: DeepSeekClient) -> None:
    u = client.usage
    print(f"{DIM}本次消耗：{u.prompt_tokens} prompt + {u.completion_tokens} completion tokens"
          f"（共 {u.total_tokens}）{RESET}")


def _single_shot(manager: SessionManager, client: DeepSeekClient, config: Config, args) -> int:
    print(f"{DIM}工作目录：{config.workdir}{RESET}")
    print(f"{DIM}模型：{config.model}{RESET}\n")
    agent = manager.current_agent() or manager.new()
    agent.approve = bool(config.approve)
    streamed: list[str] = []
    on_text, on_plan, on_tool_call, on_tool_result, flush = _make_callbacks(streamed)
    if args.plan:
        print(f"{BOLD}{MAGENTA}── 制定执行计划（需你确认后执行）──{RESET}")

    final = agent.reply(
        args.task,
        plan_first=args.plan,
        plan_reviewer=_plan_review_loop if args.plan else None,
        on_text=on_text,
        on_plan=None,  # 计划不流式打印，统一在审查块展示
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )
    flush()
    sys.stdout.write("\n")
    print(f"{GREEN}{BOLD}===== 任务完成 ====={RESET}")
    if not streamed:  # 最终回答未流式输出（例如中途报错）时才补打印
        print(render_markdown(final))

    _print_usage(client)
    if args.save:
        try:
            manager.save(args.save)
            print(f"{DIM}会话已保存到 {args.save}{RESET}")
        except OSError as e:
            print(f"[错误] 保存会话失败：{e}")
    return 0


def _handle_command(line: str, plan_mode: bool, approve: bool, manager: SessionManager,
                    client: DeepSeekClient) -> tuple[bool, bool]:
    """处理 / 开头的交互命令，返回新的 (plan_mode, approve)。"""
    parts = line.split(maxsplit=1)
    c = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if c == "/help":
        print("可用命令：")
        print(f"  {BOLD}/new [名称]{RESET}      新建会话（缺省自动命名）")
        print(f"  {BOLD}/list{RESET}            列出所有会话")
        print(f"  {BOLD}/switch <名称>{RESET}   切换到指定会话")
        print(f"  {BOLD}/del <名称>{RESET}      删除指定会话")
        print(f"  {BOLD}/save [会话] [文件]{RESET} 保存会话（缺省全部 → sessions.json）")
        print(f"  {BOLD}/load <会话> [文件]{RESET} 从文件加载单个会话")
        print(f"  {BOLD}/clear{RESET}           清空当前会话")
        print(f"  {BOLD}/plan{RESET}            切换计划模式（当前 {'开' if plan_mode else '关'}）")
        print(f"  {BOLD}/approve{RESET}         切换审查模式（当前 {'开' if approve else '关'}）")
        print(f"  {BOLD}/usage{RESET}           显示累计 token 消耗")
        print(f"  {BOLD}exit{RESET}              退出")
    elif c == "/new":
        try:
            manager.new(arg or None)
            print(f"已新建并切换到会话 '{manager.current}'")
        except ValueError as e:
            print(f"[错误] {e}")
    elif c == "/list":
        if not manager.names():
            print("（暂无会话）")
        for n in manager.names():
            mark = " *" if n == manager.current else ""
            count = len(manager.sessions[n].dump_history())
            print(f"  {n}{mark}  （{count} 条消息）")
    elif c == "/switch":
        if not arg:
            print("用法：/switch <名称>")
        elif manager.switch(arg) is None:
            print(f"[错误] 会话 '{arg}' 不存在")
        else:
            print(f"已切换到会话 '{arg}'")
    elif c == "/del":
        if not arg:
            print("用法：/del <名称>")
        elif not manager.remove(arg):
            print(f"[错误] 会话 '{arg}' 不存在")
        else:
            print(f"已删除会话 '{arg}'，当前：'{manager.current}'")
    elif c == "/save":
        # /save [会话名] [文件]
        if arg:
            a = arg.split(maxsplit=1)
            name = a[0]
            fname = a[1] if len(a) > 1 else "sessions.json"
            try:
                manager.save_one(fname, name)
                print(f"已保存会话 '{name}' 到 {fname}")
            except (OSError, KeyError) as e:
                print(f"[错误] {e}")
        else:
            try:
                manager.save("sessions.json")
                print(f"已保存 {len(manager.names())} 个会话到 sessions.json")
            except OSError as e:
                print(f"[错误] 保存失败：{e}")
    elif c == "/load":
        # /load [会话名] [文件]
        if arg:
            a = arg.split(maxsplit=1)
            name = a[0]
            fname = a[1] if len(a) > 1 else "sessions.json"
            try:
                manager.load_one(fname, name)
                print(f"已加载会话 '{name}'，当前：'{manager.current}'")
            except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
                print(f"[错误] 加载失败：{e}")
        else:
            print("用法：/load <会话名> [文件]（缺省文件为 sessions.json）")
    elif c == "/clear":
        agent = manager.current_agent()
        if agent:
            agent.clear()
        print("当前会话已清空。")
    elif c == "/plan":
        plan_mode = not plan_mode
        print(f"计划模式：{'开' if plan_mode else '关'}")
    elif c == "/approve":
        approve = not approve
        manager.approve = approve
        agent = manager.current_agent()
        if agent:
            agent.approve = approve
        print(f"审查模式：{'开' if approve else '关'}（修改文件/执行命令前需你确认）")
    elif c == "/usage":
        u = client.usage
        print(f"{u.prompt_tokens} prompt + {u.completion_tokens} completion（共 {u.total_tokens}）")
    else:
        print(f"未知命令：{c}（输入 /help 查看）")
    return plan_mode, approve


def _colorize_diff(diff: str) -> str:
    """给统一 diff 着色：新增绿色、删除红色、块头青色。"""
    out = []
    for line in diff.splitlines():
        if line.startswith("+"):
            out.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"{CYAN}{line}{RESET}")
        else:
            out.append(line)
    return "\n".join(out)


def _repl(manager: SessionManager, client: DeepSeekClient, config: Config, args,
          ws: Workspace, approve_cb) -> int:
    print(f"{DIM}工作目录：{config.workdir}{RESET}")
    print(f"{DIM}模型：{config.model}{RESET}")
    print(f"{DIM}进入交互对话模式。{RESET}{DIM}{BOLD}/help{RESET}{DIM} 查看命令，{RESET}"
          f"{DIM}{BOLD}exit{RESET}{DIM} 退出。{RESET}\n")
    if manager.current_agent() is None:
        manager.new()
    plan_mode = bool(args.plan)
    approve = bool(config.approve)
    manager.approve = approve
    if manager.current_agent():
        manager.current_agent().approve = approve

    while True:
        name = manager.current or "?"
        try:
            line = input(f"{GREEN}{BOLD}[{name}]{RESET} 你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q", "/exit", "/quit"):
            print("再见。")
            break
        if line.startswith("/"):
            plan_mode, approve = _handle_command(line, plan_mode, approve, manager, client)
            continue

        agent = manager.current_agent() or manager.new()
        agent.approve = approve
        # 同步审查开关：approve 关闭时文件/命令工具不弹窗（--ask 的 confirm 仍生效）
        ws.approve = approve_cb if approve else None
        streamed: list[str] = []
        on_text, on_plan, on_tool_call, on_tool_result, flush = _make_callbacks(streamed)
        if plan_mode:
            print(f"{BOLD}{MAGENTA}── 制定执行计划（需你确认后执行）──{RESET}")
        final = agent.reply(
            line,
            plan_first=plan_mode,
            plan_reviewer=_plan_review_loop if plan_mode else None,
            on_text=on_text,
            on_plan=None,  # 计划不流式打印，统一在审查块展示
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )
        flush()
        sys.stdout.write("\n")
        if not streamed:
            print(render_markdown(final))
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="编程智能体（DeepSeek）")
    parser.add_argument("task", nargs="?", help="要完成的任务；缺省则进入交互对话模式")
    parser.add_argument("--workdir", "-w", default=None, help="工作目录（沙箱根目录）")
    parser.add_argument("--max-iter", type=int, default=None, help="最大迭代步数")
    parser.add_argument("--ask", action="store_true", help="执行命令前人工确认")
    parser.add_argument("--plan", action="store_true", help="先制定计划再执行")
    parser.add_argument("--approve", action="store_true",
                        help="审查模式：修改文件/执行命令前逐个人工确认（含 diff 预览）")
    parser.add_argument("--save", metavar="FILE", default=None, help="结束后把会话保存到 JSON 文件")
    parser.add_argument("--resume", metavar="FILE", default=None, help="从已保存的会话继续")
    args = parser.parse_args()

    config = Config.from_env()
    if args.workdir:
        config.workdir = Path(args.workdir).resolve()
    if args.max_iter:
        config.max_iterations = args.max_iter
    if args.approve:
        config.approve = True
    if args.ask or args.approve:
        # 交互确认/审查模式下禁用并发，避免多个工具同时在子线程里弹 input() 询问
        config.parallel_tools = False

    confirm = None
    if args.ask:
        def confirm(command: str) -> bool:
            ans = input(f"{RED}是否执行该命令？{RESET}\n  {command}\n[y/N] ").strip().lower()
            return ans in ("y", "yes")

    ws = Workspace(config.workdir, confirm=confirm, max_output_chars=config.max_output_chars)
    client = DeepSeekClient(config)
    manager = SessionManager(config, client, ToolRegistry(ws))

    def approve_cb(action: str, full_preview: str, short_preview: str) -> bool:
        # 动态读取当前开关：REPL 里 /approve 切换即时生效
        if not manager.approve:
            return True  # 未开启审查 → 自动放行
        if action.startswith("execute_command"):
            print(f"{BOLD}{YELLOW}将执行命令：{RESET}{full_preview}")
        else:
            print(f"{BOLD}{YELLOW}文件操作：{action}{RESET}")
            if full_preview and full_preview != "(内容无变化)":
                show = full_preview if len(full_preview) <= 4000 else full_preview[:4000] + "\n..."
                print(_colorize_diff(show))
        ans = input(f"{RED}允许吗？{RESET}{DIM}[y]允许 [n]拒绝 [m]拒绝并留言：{RESET} ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans.startswith("m"):
            msg = input("给模型留个言（为什么拒绝）：").strip()
            if msg:
                raise RequestDenied(f"用户拒绝并留言：{msg}")
        return False

    ws.approve = approve_cb

    if args.resume:
        try:
            manager.load(args.resume)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"[错误] 无法读取会话文件：{e}")
            return 1

    if args.task:
        return _single_shot(manager, client, config, args)
    return _repl(manager, client, config, args, ws, approve_cb)


if __name__ == "__main__":
    raise SystemExit(main())
