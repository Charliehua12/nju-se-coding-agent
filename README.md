# 编程智能体（Coding Agent）

一个运行在本地、通过与大语言模型交互来自主读写文件、执行命令、完成编程任务的智能体——类似一个极简的 Claude Code / Codex / OpenCode。

- **模型**：DeepSeek（OpenAI 兼容接口，`deepseek-chat`）
- **语言**：Python 3.10+
- **依赖**：**零第三方依赖**——连 HTTP 层、SSE 流式解析、token 估算、单元测试，全部只用标准库

## 快速开始

```bash
# 1. 配置密钥（.env 已被 .gitignore 排除，不会入库）
cp .env.example .env        # 编辑 .env 填入 DEEPSEEK_API_KEY
# 或：export DEEPSEEK_API_KEY=sk-...

# 2. 运行（单次执行）
python main.py "写一个快速排序的实现并跑通测试"

# 交互对话模式（持续多轮，像 ChatGPT 一样追问）
python main.py

# 3. 运行单元测试
python -m unittest discover -s tests -v
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--workdir DIR` / `-w` | 工作目录（沙箱根目录，路径约束在其中） |
| `--max-iter N` | 最大迭代步数（默认 25） |
| `--plan` | 先让模型制定执行计划，再进入执行循环 |
| `--ask` | 执行 shell 命令前人工确认 |
| `--save FILE` | 结束后把会话保存为 JSON |
| `--resume FILE` | 从已保存的会话继续（追问） |

### 交互对话模式

不带任务参数启动即进入 REPL，会话在内存中持续累积，可像聊天一样多轮追问：

```bash
python main.py
```

交互命令：

| 命令 | 作用 |
|---|---|
| `/new [名称]` | 新建会话（缺省自动命名），并切换到它 |
| `/list` | 列出所有会话（`*` 标记当前） |
| `/switch <名称>` | 切换到指定会话 |
| `/del <名称>` | 删除指定会话 |
| `/save [文件]` | 保存全部会话（默认 sessions.json） |
| `/load <文件>` | 从文件加载会话 |
| `/clear` | 清空当前会话 |
| `/plan` | 切换计划模式（先出计划再执行） |
| `/usage` | 显示累计 token 消耗 |
| `/help` | 显示帮助 |
| `exit` / `quit` | 退出 |

每个会话拥有独立的对话历史，可互不干扰地并行推进多个任务；`/save` + `/load` 支持跨进程持久化与恢复。

## 工作原理

核心是一个 **「思考 → 行动 → 观察」** 循环：

```
[system 提示 + 用户任务]
        │
        ▼
   调用 DeepSeek（带工具 schema）
        │
        ├── 返回 tool_calls ──► 本地沙箱并发执行 ──► 结果回填为 tool 消息 ──┐
        │                                                                   │
        └── 无 tool_calls ──► 任务完成，返回最终回答                          │
```

每轮请求**无状态**：完整消息历史在本地内存中重建后发送，不依赖服务端托管的任何状态、代码执行或文件工具。

## 设计思想

围绕「**可插拔、可观测、可恢复**」三个目标，做了几处工程化设计：

### 1. 依赖倒置：可插拔的模型层

`llm.py` 定义 `ChatProvider` 协议，`Agent` 只依赖该协议而非 DeepSeek 的具体实现。换成其它 OpenAI 兼容模型、或在测试里注入 mock，都不必改动 agent 核心。

### 2. 有预算的上下文管理（三级降级）

模型上下文有限，而工具输出（读文件、命令结果）往往占大头且可重新获取。逼近预算时按优先级压缩（`context.py`）：

1. **截断**最旧的工具输出正文（保留头部，可重新读取）；
2. **摘要**较早的对话为一段「记忆」（调用模型压缩）；
3. **兜底丢弃**最旧的工具调用轮次。

始终保留 `system` 提示与最初的用户任务。

### 3. 韧性错误处理

- API 层：网络错误 / 5xx / 429 指数退避重试，4xx 直接抛出；
- 模型层：工具名未知、参数非法、JSON 解析失败 → 以字符串形式**回喂**给模型，让它自行修正，而不是让循环崩溃；
- 执行层：命令超时、非零退出码、路径越界，都作为结果返回给模型判断。

### 4. 并发工具执行

模型在同一次响应里给出的多个工具调用天然互不依赖，用线程池**并发执行**（`--ask` 交互模式下自动退回顺序，避免并发询问）。

### 5. 计划先行与会话续跑

`--plan` 让模型先产出一份分步计划再动手，降低长任务的漂移；`--save` / `--resume` 支持会话持久化与追问。

### 6. 可观测

流式输出推理文本、可视化工具调用与结果、结尾统计 token 消耗（`prompt` / `completion`）。

## 目录结构

```
main.py          命令行入口（单次执行 + 交互 REPL + 多会话 + 持久化）
agent.py         主循环 + 终止条件 + 并发执行 + 计划/摘要编排
llm.py           ChatProvider 协议 + DeepSeek 客户端（标准库 HTTP + SSE 流式解析）
context.py       对话历史、token 估算、三级上下文压缩
session.py       会话管理：多会话创建/切换/删除/持久化
parser.py        模型输出解析（工具参数 JSON 容错、content 兜底识别）
config.py        配置（环境变量 / .env）
tools/
  __init__.py    工具注册表 + 工作目录沙箱（越界防护、输出截断）
  files.py       read_file / write_file / edit_file / list_files / delete_file
  shell.py       execute_command（subprocess + 超时）
  search.py      search_files（文件内容 grep）
tests/           核心逻辑的单元测试（标准库 unittest）
```

## 关键设计对照题目要求

| 题目要求 | 实现位置与做法 |
|---|---|
| 对话历史与上下文管理 | `context.py`：token 估算 + 三级压缩 |
| 工具定义与本地执行 | `tools/`：JSON Schema + 本地 executor，沙箱约束 |
| 模型输出解析 | `llm.py` 流式增量拼接 + `parser.py` JSON 容错 |
| 循环终止条件 | `agent.py`：无工具调用 → 完成；达最大步数 → 收尾；异常 → 退出 |
| 错误处理 | API 退避重试；工具错误回喂自愈；命令超时/截断/越界防护 |

## 安全边界

- 所有文件路径经 `Workspace.resolve` 归一化并做越界防护，约束在 `--workdir` 内；
- 命令在沙箱目录下执行，带超时（默认 60s）与输出截断（默认 12000 字符）；
- 密钥只从环境变量或 `.env` 读取，绝不进入代码、仓库或日志。
