"""Skills 技能系统：声明式的可复用能力（参考 Claude Code 风格）。

技能以目录形式放置：
  - 项目技能：<工作目录>/.agents/skills/<技能名>/SKILL.md（随项目走）；
  - 内置技能：<agent 安装目录>/.agents/skills/<技能名>/SKILL.md（随 agent 分发）。
每个 SKILL.md 首部 `---` 分隔的 frontmatter 提供 name / description 元数据，
正文是 Markdown 指令，按需由 invoke_skill 工具完整加载（省 token）；
agent 启动时只把轻量技能清单（名称 + 描述）注入 system prompt，
既让模型知道有哪些能力，又不付出正文的 token 代价。

零第三方依赖：frontmatter 用极简逐行解析，不引入 YAML 解析库。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = ".agents/skills"
# agent 安装目录下的内置技能（随 agent 分发，作为兜底，如 code-review）
AGENT_SKILLS_DIR = Path(__file__).resolve().parent / SKILLS_DIR


@dataclass
class Skill:
    name: str
    description: str
    path: Path  # SKILL.md 文件路径
    body: str = ""  # 正文懒加载：首次 invoke 时才读入

    def load(self) -> str:
        """返回技能指令正文（去掉 frontmatter 头）；首次调用时从磁盘读取。"""
        if not self.body:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(text)
            body = text
            if fm:
                parts = text.split("---", 2)
                if len(parts) == 3:
                    body = parts[2].lstrip("\n")
            self.body = body
        return self.body


def parse_frontmatter(text: str) -> dict[str, str]:
    """极简 frontmatter 解析：返回 {key: value}；无 frontmatter 返回空 dict。"""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return {}
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta


def load_skill(path: Path) -> Skill | None:
    """从 SKILL.md 加载单个技能；文件不存在或无法读取则返回 None。"""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = parse_frontmatter(text)
    name = meta.get("name", "").strip() or path.parent.name
    description = meta.get("description", "").strip() or name
    return Skill(name=name, description=description, path=path)


def discover_skills(workdir: Path) -> list[Skill]:
    """合并发现技能：项目技能（<workdir>/.agents/skills/）+ 内置技能。

    内置技能随 agent 分发：把 agent 放到任意工作目录运行也能用上自带技能
    （如 code-review），无需手动复制。同名时以项目技能为准（项目可覆盖内置）。
    """
    bases = [(workdir / SKILLS_DIR).resolve(), AGENT_SKILLS_DIR]
    by_name: dict[str, Skill] = {}
    seen: set[Path] = set()
    for base in bases:
        if not base.is_dir() or base in seen:
            continue
        seen.add(base)
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            skill = load_skill(d / "SKILL.md")
            if skill:
                by_name.setdefault(skill.name, skill)  # 先扫到的（项目）优先
    return [by_name[k] for k in sorted(by_name)]


def skill_manifest(skills: list[Skill]) -> str:
    """生成注入 system prompt 的轻量技能清单（只有名称与描述，省 token）。"""
    if not skills:
        return ""
    lines = [f"- {s.name}: {s.description}" for s in skills]
    return "可用技能：\n" + "\n".join(lines)
