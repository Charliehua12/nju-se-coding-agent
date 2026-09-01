编程智能体（DeepSeek 驱动）

一、仓库地址
https://github.com/Charliehua12/nju-se-coding-agent

二、如何运行
1. 环境：Python 3.10+，无需安装任何第三方库（仅用标准库）。
2. 配置密钥：复制 .env.example 为 .env 填入 DEEPSEEK_API_KEY，
   或 export DEEPSEEK_API_KEY=...。
3. 运行：python main.py "任务描述"；不带参数则进入交互对话模式（多会话可切换）。
   可选：--workdir 指定工作目录；--max-iter 最大步数；--ask 命令前人工确认。

三、特色功能
1. 零第三方依赖：HTTP 层、流式解析、token 估算、Markdown 渲染均标准库自写。
2. 审查模式（--approve 或 /approve）：写/改/删文件、执行命令前逐个展示
   diff 并请求许可，可拒绝或留言。
3. 改动留痕与回滚：文件改动全程记录，/review 查看 diff 并按需回滚。
4. Git 集成：工作目录自动 init + 每轮任务 checkpoint，/review git 查看与回滚。
5. 长期记忆：memory 工具写入 MEMORY.md，跨会话生效（冻结快照注入）。
6. 工具本地沙箱执行：读写文件、定点编辑、搜索、执行命令，带路径越界
   防护、命令超时与输出截断；批内全只读才并发，含写严格串行保因果。
7. 上下文管理：token 预算 + 三级压缩（截断/摘要/丢弃）+ 大结果落盘 +
   usage 动态校准 + 过载保护。
8. 交互式多会话：REPL 内可 /new /switch /del 多个会话，/save /load 支持
   单会话或全量持久化。
9. 计划先行：先制定计划，人工确认（可多轮修订/取消）后才执行。
10. 错误自愈：失败/拒绝结果回喂模型，使其自主修正。
11. 终止条件：无工具调用即完成、达最大步数收尾、异常退出。

四、架构
核心为「思考→行动→观察」循环：system+任务 → 调模型 → 解析 tool_calls
→ 本地执行 → 结果回填 → 循环，直至模型不再调用工具。
