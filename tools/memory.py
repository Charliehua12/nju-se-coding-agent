"""memory 工具：让模型主动读写长期记忆（跨会话生效）。"""


def memory(ws, args: dict) -> str:
    store = getattr(ws, "memory_store", None)
    if store is None:
        return "错误：长期记忆功能未启用。"
    action = args.get("action", "")
    if action == "add":
        return store.add(args.get("content", ""))
    if action == "replace":
        return store.replace(args.get("old_text", ""), args.get("new_content", ""))
    if action == "remove":
        return store.remove(args.get("old_text", ""))
    return f"错误：未知操作 {action}（可用 add / replace / remove）。"
