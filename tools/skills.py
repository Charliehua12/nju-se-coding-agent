"""invoke_skill 工具：按需加载技能全文，让模型严格按技能指令执行。"""


def invoke_skill(ws, args: dict) -> str:
    """加载并返回指定技能的使用说明（技能正文）。"""
    name = args.get("name", "").strip()
    skills = getattr(ws, "skills", None) or []
    for skill in skills:
        if skill.name == name:
            body = skill.load()
            if not body:
                return f"技能「{name}」没有正文说明。"
            return f"技能「{name}」使用说明：\n{body}"
    names = ", ".join(s.name for s in skills) or "无"
    return f"错误：未找到技能 '{name}'。可用技能：{names}"
