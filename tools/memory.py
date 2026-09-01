"""memory 工具：让模型主动读写长期记忆（跨会话生效）。

target 选择记忆库：
  - memories（缺省）：项目事实 / 约定，写入 MEMORY.md；
  - user：用户偏好画像，写入 USER.md。
"""


def memory(ws, args: dict) -> str:
    target = args.get("target") or "memories"
    if target == "user":
        store = getattr(ws, "user_store", None)
        label = "用户画像记忆"
    else:
        store = getattr(ws, "memory_store", None)
        label = "长期记忆"
    if store is None:
        return f"错误：{label}功能未启用。"
    action = args.get("action", "")
    if action == "add":
        return store.add(args.get("content", ""))
    if action == "replace":
        return store.replace(args.get("old_text", ""), args.get("new_content", ""))
    if action == "remove":
        return store.remove(args.get("old_text", ""))
    return f"错误：未知操作 {action}（可用 add / replace / remove）。"
