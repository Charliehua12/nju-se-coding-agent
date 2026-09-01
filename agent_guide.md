# 编程智能体（Coding Agent）——框架与功能解读

> 本文档用于理解本项目、应对面试提问。**配合源码一起读效果最佳**，所有设计决策
> 都能在对应文件中找到落点。仓库地址：https://github.com/Charliehua12/nju-se-coding-agent

---

## 0. 一句话定位

一个运行在本地、通过与 DeepSeek 大语言模型交互来自主**读写文件、执行命令、完成编程任务**的智能体——
类似一个极简的 Claude Code / Codex / OpenCode。**零第三方依赖**：HTTP 层、SSE 流式解析、token 估算、Markdown 渲染、单元测试全部只用 Python 标准库自写。

---

## 1. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  main.py  命令行入口：单次执行 / 交互 REPL / 多会话 / 审查模式  │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│  agent.py  Agent 主循环（ReAct）                              │
│  · 组装 system+历史+工具 schema → 调模型 → 解析 → 执行 → 回填  │
│  · 终止条件 / 计划先行 / 审批回喂 / 摘要编排 / 并发因果保护      │
└───────┬──────────────┬───────────────┬───────────────────────┘
        │              │               │
┌───────▼──────┐ ┌─────▼───────┐ ┌─────▼────────────────────┐
│ llm.py      │ │ context.py  │ │ tools/ 工具系统           │
│ ChatProvider │ │ token 估算  │ │ · Workspace 沙箱          │
│ DeepSeek 客户端│ │ 三级压缩    │ │ · 9 个工具（文件/命令/    │
│ SSE 流式解析  │ │ usage 校准  │ │   搜索/记忆/技能）        │
└───────┬──────┘ │ 过载保护    │ │ · 审批 / diff / 改动追踪   │
        │        └────────────┘ └────────────┬──────────────┘
┌───────▼──────────────┐         ┌───────────▼──────────────┐
│ session.py  多会话    │         │ gitwrap.py  Git 集成      │
│ /save /load 持久化    │         │ 自动 checkpoint / 只读监视 │
└───────┬──────────────┘         └───────────┬──────────────┘
┌───────▼──────────────┐         ┌───────────▼──────────────┐
│ memory.py 长期记忆    │         │ skills.py  声明式技能     │
│ MEMORY.md + USER.md  │         │ .agents/skills/SKILL.md  │
│ 冻结快照注入          │         │ invoke_skill 按需加载     │
└──────────────────────┘         └──────────────────────────┘
   markdown.py（终端渲染） · parser.py（输出解析） · config.py（配置）
```

**数据流（一轮）**：
```
[system + 历史 + 工具 schema] → DeepSeek 流式响应
      ↓ 有 tool_calls
解析参数 → Workspace 沙箱本地执行（并发仅限只读批）→ 结果字符串
      ↓
回填为 tool 消息 → 下一轮 …直到模型不再调用工具 → 返回最终回答
```

---

## 2. 核心循环与终止条件（`agent.py` 的 `_loop`）

循环每轮：
1. `context.compress()` 先尝试把上下文压回预算；
2. 调模型（带工具 schema，流式）；
3. `record_usage()` 用真实 token 校准估算；
4. 模型输出超长则截断并提示；
5. **兜底**：模型把工具调用写进 content 而非 tool_calls 字段时，用 `extract_tool_call_from_content` 识别；
6. 有工具调用 → 执行（`_execute_all`）→ 结果回填；无 → 完成。

**三种终止条件**（考核点名要求）：
| 条件 | 处理 |
|---|---|
| 模型返回无 tool_calls | 任务完成，返回最终回答 |
| 达到 `max_iterations` | 注入收尾指令，让模型基于已有信息给结论 |
| 与模型通信失败 | 报错退出（重试耗尽后） |

---

## 3. 模块详解

### 3.1 `llm.py` — 模型边界层
- **`ChatProvider`（Protocol）**：定义 `chat(messages, tools, stream, on_text)` 最小接口——**依赖倒置**，`Agent` 只依赖协议不依赖 DeepSeek 实现，可替换为任意 OpenAI 兼容模型或用 mock 测试。
- **`DeepSeekClient`**：只用 `http.client + ssl + json` 自写 HTTP。SSE 流式解析里，工具调用增量片段**按 index 归位累加**（`arguments` 是分片拼接的 JSON 字符串）。
- **重试**：网络错误 / 5xx / 429 指数退避（1s/2s/4s）；4xx（非 429）重试无意义直接抛。
- **`Usage`**：累计 prompt/completion token，供结尾统计与上下文校准。

### 3.2 `parser.py` — 模型输出解析
- `parse_arguments`：对工具参数的 JSON 字符串做**容错解析**（截取最外层 `{...}`、剥离多余字符、缺省补空），解析失败回喂模型自愈。
- `extract_tool_call_from_content`：当模型把 `tool_call` 混写在自然语言 content 里时，用正则兜底识别，避免漏执行。

### 3.3 `tools/` — 工具定义与本地执行（考核点名要求）
- **`Workspace`** 沙箱：`resolve()` 归一化路径并做**越界防护**（必须落在工作目录内）；`truncate()` 超长输出**落盘**到 `.my_agent_core/results/`，上下文只留头部预览 + 路径占位（可用 `read_file` 找回）。
- **工具清单**：`read_file / write_file / edit_file / list_files / delete_file / search_files / execute_command / memory / invoke_skill`。
- **`Tool`** 带 `parallel_safe` 标记（只读工具为 True），供并发因果判断。
- **`ToolRegistry.run`**：**Never-Throw 架构**——工具内部任何异常（`RequestDenied` / 未知工具 / 参数错误 / 运行时错误）都不抛崩溃，而是包装成**字符串结果回喂给模型**，让它自主修正。
- **改动追踪**：写/改/删全部记入 `ws.changes`（`Change` 记录前后内容），支撑 `/review` 审查与回滚。

### 3.4 `context.py` — 上下文管理（考核点名要求）
- **token 估算** `estimate_tokens`：中文≈1 token/字，其余≈4 字符/token + 结构余量；**无分词器也能近似预算**。
- **三级压缩**（预算超限时按优先级降级）：
  1. 截断最旧的大工具输出（保留头部，内容可重读）；
  2. 用 LLM 把较早对话摘要成一条「记忆」（调用模型压缩，保留 system 与原始任务）；
  3. 兜底丢弃最旧的工具调用轮次。
- **过载保护**：单条消息超 60000 字符截断；消息总数超上限逐轮清理。
- **usage 锚定校准**：每轮用 API 真实 `prompt_tokens` 反向校准字符估算，系数钳制在 0.3~3.0。
- **摘要防注入**：摘要提示词要求 `<analysis>/<summary>` 双标签，提取时只取 `<summary>` 正文，防历史中恶意内容伪造摘要。

### 3.5 `agent.py` 的工程化行为
- **计划先行**：`make_plan` 先让模型出计划（不写上下文，便于未获批时丢弃）；`_revise_plan` 支持多轮按反馈修订；获批后才把计划写入上下文执行。
- **审批回喂**：被拒绝的调用以 `RequestDenied` 消息回喂，模型据此调整；结果缓存避免重复弹窗。
- **并发因果保护**：同一轮多个工具调用**仅当全部只读才并发**（`ThreadPoolExecutor`），批内含写操作则严格按模型输出顺序串行，避免 `read` 先于 `write` 读到旧数据。

### 3.6 `session.py` — 多会话管理
- 每个会话是一个独立 `Agent`（独立 `ContextManager` 历史），共享同一客户端与工具注册表。
- `/new /switch /del /clear` 多会话切换；`/save /load` 支持**单会话或全量**持久化。
- 保存格式 `{会话名: {"messages": [...], "calibration": 系数}}`，**校准系数随会话恢复**；兼容旧版纯消息列表格式。

### 3.7 `gitwrap.py` — Git 集成（自动 checkpoint）
- **两种模式**：
  - `checkpoint`：工作目录本不是 git 仓库 → 自动 `git init`，**仓库内身份**（`coding-agent <agent@localhost>`，不污染全局配置），`.gitignore` 忽略内部目录（`.my_agent_core/`），每轮任务有改动自动提交 checkpoint（commit message 用模型总结）；
  - `watch`：工作目录已是 git 仓库 → 只读监视，**不自动提交、不做破坏性回滚**，`/review git` 仍可看 diff。
- `show_diff` 处理首提交（`diff-tree --root`）；`reset_all` 按提交数分情况回滚（n≥2 `reset --hard HEAD~1`；n==1 清空工作区并记录回滚提交；n==0 丢弃未提交改动）。
- **价值**：能覆盖 `execute_command` 的副作用（构建产物、安装的依赖等文件工具追踪不到的部分）。

### 3.8 `memory.py` — 长期记忆（跨会话）
- **两类记忆分文件**：`MEMORY.md`（项目事实/约定，上限 2200 字符）与 `USER.md`（用户偏好画像，上限 1375 字符），`memory` 工具用 `target=memories|user` 选择。
- **冻结快照**：启动时读取生成快照注入 system prompt，**本会话全程静止**——既保证会话内一致，又保护大模型前缀缓存稳定；写操作只落盘、不动快照，新记忆下个会话生效。
- 原子落盘（临时文件 + `replace`），崩溃不损坏；`replace/remove` 用唯原子串定位，歧义时拒绝防误删。

### 3.9 `skills.py` — 声明式技能
- 技能 = `.agents/skills/<技能名>/SKILL.md`：首部 frontmatter 提供 `name/description`，正文是分步指令。
- 启动时只把**轻量清单**（名称+描述）注入 system prompt，省 token；模型匹配任务后调 `invoke_skill(name)` **按需加载全文**再照做。
- 零依赖：frontmatter 用极简逐行解析，不引入 YAML 库。附示例 `code-review`（分级审查）。

### 3.10 `markdown.py` — 可观测
- 纯标准库的 Markdown → ANSI 渲染器，**按行缓冲**避免流式切碎标记；支持标题、加粗、行内代码、代码块、列表。

### 3.11 `config.py` — 配置
- 从环境变量 / `.env` 读取（`load_dotenv` 自写极简解析）。密钥**绝不进入代码或仓库**。

---

## 4. 工程化亮点（面试重点，按权重排序）

1. **零第三方依赖**：HTTP、SSE、token 估算、渲染、测试全自写——证明对协议与底层原理的理解，也满足考核「不得使用 agent 框架」的硬约束。
2. **Codex 式审查链路**：计划审查（事前）→ 工具审批（事中，`--approve` + diff 预览 + 拒绝留言回喂）→ 改动审查回滚（事后，`/review`）。
3. **Never-Throw 自愈**：工具错误、参数错误、审批拒绝全部作为结果回喂模型，循环永不因单步失败崩溃。
4. **上下文三级压缩 + usage 锚定校准 + 大结果落盘 + 摘要防注入**：把「上下文有限」做成一套有预算、有上限、会校准的体系。
5. **并发因果时序保护**：只读批并发、含写严格串行，避免因果倒置。
6. **Git checkpoint 双模式**：自动提交 + 可回滚；已有仓库时只读监视，安全优先。
7. **冻结快照记忆**：跨会话生效同时保护前缀缓存。
8. **声明式技能**：用纯文本扩展 agent 能力，无需改代码。
9. **依赖倒置**：`ChatProvider` 协议让模型层可插拔、可 mock。
10. **双文件记忆**：项目事实与用户画像分离，`USER.md` 更小上限更聚焦。

---

## 5. 与考核要求逐条对照

| 考核要求 | 实现位置与做法 |
|---|---|
| 对话历史与上下文管理 | `context.py`：token 估算 + 三级压缩 + 过载保护 + usage 校准 |
| 工具定义与本地执行 | `tools/`：JSON Schema + 本地 executor + Workspace 沙箱 |
| 模型输出解析 | `llm.py` 流式增量拼接 + `parser.py` JSON 容错 + content 兜底 |
| 循环终止条件 | `agent.py`：无工具调用→完成；达最大步数→收尾；异常→退出 |
| 错误处理 | API 退避重试；工具错误回喂自愈；命令超时/截断/越界防护 |

**禁用项合规**：未使用 LangChain / LlamaIndex / OpenAI Agents SDK / Claude Agent SDK / AutoGen / CrewAI 等任何 agent 框架或 SDK；模型侧仅用官方 OpenAI 兼容 API + 原生函数调用。

---

## 6. 安全与合规（务必牢记）

- **API key 只从环境变量或 `.env` 读取**；`.env` 已在 `.gitignore` 中，绝不入库、不进 README、不进视频。若曾误提交应立即作废更换。
- 文件路径经 `Workspace.resolve` 越界防护，命令沙箱内执行、带超时与截断。
- 审查模式/计划模式是**用户主权**的体现：任何写操作与命令都经用户放行。

---

## 7. 面试可能追问及应答要点

**Q1 为什么坚持零第三方依赖？**
> 一满足考核「不得套用框架」的硬约束；二证明我对 HTTP/SSE/协议层的真实理解，而不是只会调库；三让整个系统完全可控、可审计。

**Q2 工具调用是怎么"调用"的？**
> 模型看到的是 JSON Schema，返回结构化 `tool_calls`（流式时按 index 拼参数 JSON），`parse_arguments` 容错解析后由本地函数执行，结果作为 tool 消息回填。工具本身只是"函数 + schema + 沙箱"。

**Q3 模型把工具调用写在 content 里、不用 tool_calls 字段怎么办？**
> 有兜底 `extract_tool_call_from_content` 用正则识别；识别失败也会把这段 content 原样回填，模型能自纠。

**Q4 上下文超预算怎么办？**
> 三级降级：截断最旧的大工具输出（可重读）→ LLM 摘要早期历史 → 兜底丢弃最旧轮次；加上单条上限、消息数上限、usage 动态校准、大结果落盘，四道防线。

**Q5 多个工具调用为什么不全并发？**
> 因果性。同一轮 `read` 与 `write` 并发时，read 可能读到旧数据。只有全部只读才并发，含写严格按序。

**Q6 审批怎么实现？拒绝后呢？**
> `Workspace.request` → 用户回调（`--approve` 或 REPL `/approve`）→ 同意则执行、拒绝抛 `RequestDenied`，被 `ToolRegistry` 捕获转成字符串回喂模型，模型据此调整方案（如改名重写）。

**Q7 记忆为什么要"冻结快照"？**
> 记忆在启动时注入 system prompt 后本会话静止：一是会话内看到一致的事实，二是 system 前缀不变、利于大模型前缀缓存复用；写操作只落盘，下个会话重新读取才生效。

**Q8 git checkpoint 为什么分两种模式？**
> 新目录由 agent 自己 init 并自动 checkpoint（方便演示与回滚）；但用户**已有仓库**里不搞破坏性操作，只读监视，尊重用户既有 git 历史——安全优先。

**Q9 怎么防 prompt 注入？**
> 摘要提取只认 `<summary>` 标签、路径沙箱越界防护、工具输出截断落盘、命令沙箱执行，四层防御。

**Q10 如果做成生产级，还缺什么？**
> 我会加：生命周期事件/Hook 拦截点、子代理任务委派、MCP 协议接入、精确 tokenizer、会话树 rewind/fork、速率与费用控制、持久化队列。这些方向我在参考架构分析里已经梳理过演进路线。

---

## 8. 一句话总结（可作面试开场）

> 这是一个**零依赖、可插拔、可观测、可恢复**的编程智能体：用「思考→行动→观察」循环驱动 DeepSeek，
> 通过计划审查、逐工具审批、改动回滚三道关卡把用户主权握在手里，
> 再用上下文三级压缩、冻结快照记忆与声明式技能把工程纵深做扎实。
