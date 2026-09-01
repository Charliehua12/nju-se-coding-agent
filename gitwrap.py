"""Git 集成：工作目录自动初始化 + 每轮任务自动 checkpoint。

两种模式：
  - checkpoint：工作目录本不是 git 仓库，由本工具 git init 并设置仓库内
    身份，每轮任务有改动时自动提交 checkpoint，供 /review 查看 diff 与回滚；
  - watch：工作目录已是 git 仓库（例如用户自己的项目），不做自动提交，
    /review 仍可只读查看 git diff，但不做破坏性回滚。

身份只写到仓库内（git config --local），不影响全局配置。
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(workdir: Path, *args: str, check: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(workdir),
        capture_output=True, text=True, timeout=30,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc.stdout


class GitWrap:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.mode = "watch"

    def init(self) -> str:
        """确保工作目录是 git 仓库；返回模式：checkpoint / watch。"""
        if self._is_repo():
            self.mode = "watch"
        else:
            _git(self.workdir, "init", "-q", check=True)
            # 仓库内身份，避免依赖/污染全局 git config
            _git(self.workdir, "config", "user.name", "coding-agent")
            _git(self.workdir, "config", "user.email", "agent@localhost")
            self._ignore_internal_dirs()
            self.mode = "checkpoint"
        return self.mode

    def _ignore_internal_dirs(self) -> None:
        """让 checkpoint 忽略内部状态目录（记忆、大结果落盘），保持提交干净。"""
        gi = self.workdir / ".gitignore"
        existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        if ".my_agent_core/" not in existing:
            gi.write_text(existing + "\n.my_agent_core/\n", encoding="utf-8")
        # 立即提交 .gitignore，避免它自身让仓库一直处于 dirty 状态
        _git(self.workdir, "add", "-A")
        _git(self.workdir, "commit", "-q", "-m", "init: ignore agent internal dirs")

    def _is_repo(self) -> bool:
        return _git(self.workdir, "rev-parse", "--is-inside-work-tree").strip() == "true"

    def is_dirty(self) -> bool:
        return bool(_git(self.workdir, "status", "--porcelain").strip())

    def _commit_count(self) -> int:
        out = _git(self.workdir, "rev-list", "--count", "HEAD").strip()
        return int(out) if out else 0

    def checkpoint(self, message: str) -> bool:
        """有改动且处于 checkpoint 模式时提交一次；返回是否提交。"""
        if self.mode != "checkpoint" or not self.is_dirty():
            return False
        _git(self.workdir, "add", "-A")
        msg = (message or "agent checkpoint").strip().splitlines()[0][:60] or "agent checkpoint"
        _git(self.workdir, "commit", "-q", "-m", msg, check=True)
        return True

    def show_diff(self) -> str:
        """审查用 diff：有历史则看最近一个 checkpoint，否则看工作区改动。"""
        n = self._commit_count()
        if self.mode == "checkpoint" and n >= 1:
            if n == 1:
                # 首个 checkpoint：对比空树
                return _git(self.workdir, "diff-tree", "-p", "--root", "HEAD")
            return _git(self.workdir, "diff", "HEAD~1", "HEAD")
        return _git(self.workdir, "diff")

    def reset_all(self) -> str:
        """回滚到上一个 checkpoint（仅 checkpoint 模式；丢弃未提交改动）。"""
        if self.mode == "watch":
            return "工作目录是已有 git 仓库，为安全起见不做自动回滚，请手动使用 git 操作。"
        n = self._commit_count()
        if n >= 2:
            _git(self.workdir, "reset", "--hard", "HEAD~1", check=True)
            return "已通过 git 回滚到上一个 checkpoint。"
        if n == 1:
            # 只有初始 checkpoint：清空工作区并记录一次回滚提交
            _git(self.workdir, "reset", "--hard", "HEAD", check=True)
            _git(self.workdir, "rm", "-r", "-f", "-q", ".", check=True)
            _git(self.workdir, "commit", "-q", "-m", "回滚到初始状态")
            return "已回滚到初始状态（删除 checkpoint 的全部改动）。"
        _git(self.workdir, "checkout", "--", ".", check=True)
        return "已丢弃全部未提交改动。"
