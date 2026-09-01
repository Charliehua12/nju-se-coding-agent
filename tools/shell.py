"""命令执行工具：subprocess + 超时 + 输出捕获 + 人工确认。"""
from __future__ import annotations

import subprocess

from .errors import RequestDenied


def execute_command(ws, args: dict) -> str:
    command = args.get("command", "")
    if not command.strip():
        return "错误：命令为空。"
    if ws.confirm is not None and not ws.confirm(command):
        raise RequestDenied(f"用户拒绝了该命令的执行：{command}")
    if ws.dry_run:
        return f"[预览] 将执行命令：{command}"
    cwd = ws.resolve(args.get("cwd", "."))
    timeout = int(args.get("timeout") or 60)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"命令超时（>{timeout}s）：{command}"
    except OSError as e:
        return f"命令执行失败：{e}"

    parts = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append("[stderr]\n" + proc.stderr.rstrip())
    body = "\n".join(parts).strip() or "(无输出)"
    return f"退出码 {proc.returncode}\n{body}"
